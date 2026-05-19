"""Validation + apply pipeline for `fill_and_save`.

`FillPipeline` runs each precheck as its own method; on the first error
the pipeline short-circuits with a structured response. This replaces a
180-line straight-line cascade in server.py with named, single-purpose
steps and a 6-line `run()` driver.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from document_processor import (
    DocumentEdit,
    apply_document_edits,
    validate_document_edits,
)
from pydantic import TypeAdapter

from .documents import TemplateReader
from .errors import ExceptionClassifier
from .length import LengthGuardrail
from .postprocess import clear_linesegarray_cache
from .responses import bad_argument, error, not_found, ok

_edits_adapter = TypeAdapter(list[DocumentEdit])

_OUTPUT_FORMAT_FOR_INPUT = {
    ".docx": ".docx",
    ".hwpx": ".hwpx",
    ".hwtx": ".hwpx",  # Hancom HWPX template — same ZIP+XML structure, written as .hwpx
    ".hwp": ".hwpx",
    ".pdf": None,  # PDF cannot be written back
}


def _expected_output_ext(template_path: str) -> str | None:
    return _OUTPUT_FORMAT_FOR_INPUT.get(Path(template_path).suffix.lower())


def _find_duplicate_target_ids(edits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, int] = {}
    dups: list[dict[str, Any]] = []
    for i, e in enumerate(edits):
        if e.get("edit_type") != "text":
            continue
        tid = e.get("target_id")
        if not tid:
            continue
        if tid in seen:
            dups.append({"target_id": tid, "first_index": seen[tid], "duplicate_index": i})
        else:
            seen[tid] = i
    return dups


def _filter_redundant_edits(
    targets: list[dict[str, Any]],
    edits: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Drop edits that conflict with another edit in the same batch because
    one targets a container and the other targets something inside it:
      - cell whose text equals a being-edited paragraph
      - run whose parent paragraph is being edited
    StructuralEdit and StyleEdit are never filtered.
    """
    para_text_edit_ids = {
        e["target_id"] for e in edits
        if e.get("edit_type") == "text" and e.get("target_kind") == "paragraph"
    }
    if not para_text_edit_ids:
        return edits, []

    by_id = {t["target_id"]: t for t in targets}
    edited_para_texts = {
        (by_id.get(pid, {}).get("current_text") or "").strip()
        for pid in para_text_edit_ids
    } - {""}

    kept: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for e in edits:
        if e.get("edit_type") != "text":
            kept.append(e)
            continue
        tk = e.get("target_kind")
        tgt = by_id.get(e.get("target_id"))
        if tk == "cell" and tgt is not None:
            cell_text = (tgt.get("current_text") or "").strip()
            if cell_text and cell_text in edited_para_texts:
                skipped.append({
                    "target_id": e["target_id"], "target_kind": "cell",
                    "reason": "Cell content equals a paragraph that is also being edited. "
                              "The paragraph edit already updates the cell; sending both "
                              "would conflict on text_hash. Cell edit dropped.",
                })
                continue
        if tk == "run" and tgt is not None:
            parent = tgt.get("parent_paragraph_id")
            if parent and parent in para_text_edit_ids:
                skipped.append({
                    "target_id": e["target_id"], "target_kind": "run",
                    "parent_paragraph_id": parent,
                    "reason": "Run sits inside a paragraph that is also being edited. "
                              "The paragraph edit rewrites all of its runs; the run-level "
                              "edit would race on text_hash. Run edit dropped.",
                })
                continue
        kept.append(e)
    return kept, skipped


class FillPipeline:
    def __init__(self, template_path: str, edits: Any, output_path: str):
        self.template_path = template_path
        self.template = Path(template_path)
        self.raw_edits = edits
        self.output_path = output_path
        self.out_p = Path(output_path)
        # Populated by precheck steps:
        self.edits: list[dict[str, Any]] = []
        self.original_targets: list[dict[str, Any]] = []
        self.length_warnings: list[dict[str, Any]] = []
        self.kept_edits: list[dict[str, Any]] = []
        self.skipped: list[dict[str, Any]] = []

    def run(self, dry_run: bool = False) -> dict[str, Any]:
        for step in (
            self._check_template,
            self._coerce_edits_input,
            self._check_output_path,
            self._parse_edits,
            self._check_duplicates,
            self._snapshot_and_filter,
        ):
            err = step()
            if err is not None:
                return err
        if dry_run:
            return self._dry_run_response()
        return self._apply()

    # ── precheck steps (each returns an error dict to short-circuit) ──────

    def _check_template(self) -> dict[str, Any] | None:
        if not self.template.is_file():
            return not_found(self.template_path)
        return None

    def _coerce_edits_input(self) -> dict[str, Any] | None:
        if self.raw_edits is None:
            self.edits = []
            return None
        if not isinstance(self.raw_edits, list):
            return error(
                "edit_parse_failed",
                f"`edits` must be a list, got {type(self.raw_edits).__name__}.",
                "Pass an empty list `[]` if there's nothing to apply, or a list "
                "of edit dicts otherwise.",
            )
        self.edits = self.raw_edits
        return None

    def _check_output_path(self) -> dict[str, Any] | None:
        if self.out_p.exists() and self.out_p.is_dir():
            return bad_argument(
                f"output_path points to a directory, not a file: {self.output_path}",
                "Pass a full file path including the filename and extension, "
                "e.g. C:\\out\\report.hwpx — not the containing folder.",
            )
        expected_ext = _expected_output_ext(self.template_path)
        if expected_ext is None:
            return error(
                "format_not_writable",
                f"Cannot write {self.template.suffix} as output — document-processor "
                "does not support writing this format.",
                "Use a DOCX or HWPX template if you need to write the result. "
                "PDF can be read but not written.",
            )
        out_ext = self.out_p.suffix.lower()
        if not out_ext:
            return error(
                "output_extension_mismatch",
                f"output_path has no file extension: {self.output_path}",
                f"Add the {expected_ext} extension. Suggested: {self.out_p.with_suffix(expected_ext)}",
                expected_extension=expected_ext,
                received_extension="",
            )
        if out_ext != expected_ext:
            return error(
                "output_extension_mismatch",
                f"Template is {self.template.suffix} so writer produces {expected_ext}, "
                f"but output_path ends in {out_ext}.",
                f"Change output_path to end in {expected_ext}. Suggested: "
                f"{self.out_p.with_suffix(expected_ext)}",
                expected_extension=expected_ext,
                received_extension=out_ext,
            )
        return None

    def _parse_edits(self) -> dict[str, Any] | None:
        try:
            _edits_adapter.validate_python(self.edits)
        except Exception as exc:
            msg = str(exc)
            if "style edits do not support fields" in msg:
                return error(
                    "style_field_target_mismatch",
                    msg.split("\n")[1].strip() if "\n" in msg else msg,
                    "Style fields like bold / italic / color / font_size_pt apply "
                    "to a run (inline span), not a whole paragraph. Re-issue the "
                    "StyleEdit with target_kind='run' against a run target_id from "
                    "list_template_targets(target_kinds=['run']). For paragraph-wide "
                    "formatting (alignment, indent), keep target_kind='paragraph' "
                    "and only set paragraph-level fields.",
                )
            return error(
                "edit_parse_failed",
                msg,
                "An edit dict has wrong/missing fields. TextEdit needs "
                "{edit_type:'text', target_id, expected_text_hash, new_text}. "
                "StructuralEdit needs {edit_type:'structural', operation, target_id, ...}. "
                "StyleEdit fields depend on target_kind (run-level for bold/color, "
                "paragraph-level for alignment/indent). Fix and retry.",
            )
        return None

    def _check_duplicates(self) -> dict[str, Any] | None:
        dups = _find_duplicate_target_ids(self.edits)
        if dups:
            return error(
                "duplicate_target_id",
                "Multiple TextEdits target the same target_id.",
                "Multiple TextEdits target the same target_id. Merge their "
                "content into a single edit with one new_text value, then retry. "
                "(Re-fetching hashes will NOT fix this — the second edit will "
                "always fail because the first one mutated the text.)",
                duplicates=dups,
            )
        return None

    def _snapshot_and_filter(self) -> dict[str, Any] | None:
        reader = TemplateReader(self.template_path)
        try:
            self.original_targets = reader.all_writable_targets()
        except Exception as exc:
            return ExceptionClassifier.to_response(exc)
        self.length_warnings = LengthGuardrail.warnings(self.original_targets, self.edits)
        self.kept_edits, self.skipped = _filter_redundant_edits(
            self.original_targets, self.edits
        )
        return None

    # ── output builders ───────────────────────────────────────────────────

    def _dry_run_response(self) -> dict[str, Any]:
        target_by_id = {t["target_id"]: t for t in self.original_targets}
        preview: list[dict[str, Any]] = []
        for e in self.kept_edits:
            tid = e.get("target_id")
            tgt = target_by_id.get(tid)
            entry: dict[str, Any] = {
                "edit_type": e.get("edit_type"),
                "target_id": tid,
                "target_kind": e.get("target_kind") or (tgt.get("target_kind") if tgt else None),
            }
            etype = e.get("edit_type")
            if etype == "text":
                entry["before"] = (tgt.get("current_text") if tgt else None) or ""
                entry["after"] = e.get("new_text") or ""
            elif etype == "structural":
                entry["operation"] = e.get("operation")
                entry["before"] = (tgt.get("current_text") if tgt else None)
                entry["after"] = e.get("text") or e.get("rows") or e.get("values")
            elif etype == "style":
                entry["before_style"] = "<unchanged text>"
                entry["after_style"] = {
                    k: v for k, v in e.items()
                    if k not in {"edit_type", "target_id", "target_kind",
                                 "client_edit_id", "reason"}
                }
            preview.append(entry)
        return {
            "status": "dry_run_ok",
            "edits_count_input": len(self.edits),
            "edits_count_after_filter": len(self.kept_edits),
            "skipped_redundant_edits": self.skipped,
            "length_warnings": self.length_warnings,
            "length_safe": (len(self.length_warnings) == 0),
            "preview": preview,
        }

    def _apply(self) -> dict[str, Any]:
        coerced = _edits_adapter.validate_python(self.kept_edits)
        validation = validate_document_edits(source_path=self.template_path, edits=coerced)
        v_dump = validation.model_dump(mode="json")
        if v_dump.get("issues"):
            return self._with_meta(self._validation_failure(v_dump))

        in_place = self.out_p.resolve() == self.template.resolve()
        self.out_p.parent.mkdir(parents=True, exist_ok=True)
        if in_place:
            tmp_dir = Path(tempfile.mkdtemp(prefix="rmcp_inplace_"))
            write_target = tmp_dir / self.out_p.name
        else:
            tmp_dir = None
            write_target = self.out_p
            if write_target.exists():
                write_target.unlink()

        try:
            apply_document_edits(
                source_path=self.template_path,
                edits=coerced,
                output_path=str(write_target),
            )
        except Exception as exc:
            if tmp_dir is not None:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            return self._with_meta(error(
                "apply_failed",
                f"{type(exc).__name__}: {exc}",
                "Underlying document writer raised an exception. Inspect the "
                "error message and retry with a smaller edits list to isolate "
                "the problematic edit.",
            ))

        if in_place:
            if self.out_p.exists():
                self.out_p.unlink()
            shutil.move(str(write_target), str(self.out_p))
            shutil.rmtree(tmp_dir, ignore_errors=True)

        # HWPX post-processing: clear stale <hp:linesegarray> cache that
        # document-processor leaves behind. Without this, Hangul viewer
        # renders the new text with line-breaks computed for the old text.
        linesegarray_cleared = 0
        if self.out_p.suffix.lower() == ".hwpx":
            try:
                linesegarray_cleared = clear_linesegarray_cache(str(self.out_p))
            except Exception:
                # Non-fatal: the file is still valid HWPX, just may render
                # with stale line breaks on first open.
                linesegarray_cleared = -1

        return self._with_meta(ok(
            output_path=str(self.out_p),
            edits_applied=len(coerced),
            linesegarray_sections_cleared=linesegarray_cleared,
        ))

    def _validation_failure(self, v_dump: dict[str, Any]) -> dict[str, Any]:
        fails = [
            {
                "target_id": i.get("target_id"),
                "target_kind": i.get("target_kind"),
                "code": i.get("code"),
                "expected_text_hash": i.get("expected_text_hash"),
                "current_text_hash": i.get("current_text_hash"),
                "current_text": i.get("current_text"),
            }
            for i in (v_dump.get("issues") or [])
        ]
        bullets = "; ".join(
            f"{f.get('target_id')} ({f.get('code')}: expected={(f.get('expected_text_hash') or '')[:8]} "
            f"current={(f.get('current_text_hash') or '')[:8]})"
            for f in fails[:5]
        )
        more = f" (+{len(fails) - 5} more)" if len(fails) > 5 else ""
        return error(
            "validation_failed",
            "One or more edits failed validation.",
            f"{len(fails)} edit(s) failed: {bullets}{more}. "
            f"For each, use the `current_text_hash` from `failed_targets` "
            f"as the new `expected_text_hash` (or re-call list_template_targets "
            f"to refetch everything) and rebuild any edits whose `current_text` "
            f"shows the file has drifted from what you expected.",
            validation=v_dump,
            failed_targets=fails,
        )

    def _with_meta(self, response: dict[str, Any]) -> dict[str, Any]:
        """Attach skipped/length metadata that every applied result carries."""
        response["skipped_redundant_edits"] = self.skipped
        response["length_warnings"] = self.length_warnings
        response["length_safe"] = (len(self.length_warnings) == 0)
        return response
