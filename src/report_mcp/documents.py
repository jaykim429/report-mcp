"""Read-side wrapper around document-processor. `TemplateReader` owns
the template path so each tool can call `.inspect()`, `.list_targets()`,
`.describe()` without re-passing arguments and without re-implementing
existence/exception handling."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from document_processor import list_editable_targets, read_document

from .errors import ExceptionClassifier
from .length import LengthGuardrail
from .responses import bad_argument, not_found, ok

VALID_TARGET_KINDS = ["paragraph", "run", "cell", "table", "image"]


class TemplateReader:
    def __init__(self, template_path: str):
        self.path = Path(template_path)

    def _guard(self) -> dict[str, Any] | None:
        if not self.path.is_file():
            return not_found(str(self.path))
        return None

    def inspect(self, start: int = 0, limit: int = 50) -> dict[str, Any]:
        guard = self._guard()
        if guard:
            return guard
        try:
            result = read_document(
                source_path=str(self.path),
                start=start, limit=limit, include_runs=True,
            )
        except Exception as exc:
            return ExceptionClassifier.to_response(exc)
        dumped = result.model_dump(mode="json")
        LengthGuardrail.annotate(dumped.get("paragraphs") or [], "text")
        dumped["status"] = "ok"
        return dumped

    def list_targets(
        self,
        target_kinds: list[str] | None = None,
        start: int = 0,
        limit: int = 200,
    ) -> dict[str, Any]:
        guard = self._guard()
        if guard:
            return guard
        if target_kinds:
            bad = [k for k in target_kinds if k not in VALID_TARGET_KINDS]
            if bad:
                return bad_argument(
                    f"Unknown target_kinds: {bad}",
                    f"target_kinds must be a subset of {VALID_TARGET_KINDS}. "
                    "Drop the invalid entries and call again.",
                )
        if start < 0 or limit < 0:
            return bad_argument(
                f"start and limit must be non-negative; got start={start}, limit={limit}.",
                "Use start>=0 (first page is start=0) and limit>=0.",
            )
        try:
            result = list_editable_targets(
                source_path=str(self.path),
                target_kinds=target_kinds,
                max_targets=start + limit + 1,
                only_writable=True,
            )
        except Exception as exc:
            return ExceptionClassifier.to_response(exc)
        dumped = result.model_dump(mode="json")
        all_items = dumped.get("targets") or []
        page = all_items[start : start + limit]
        LengthGuardrail.annotate(page, "current_text")
        has_more = len(all_items) > start + limit
        dumped.update(
            targets=page,
            status="ok",
            start=start,
            limit=limit,
            returned=len(page),
            next_start=(start + limit) if has_more else None,
            truncated=has_more,
        )
        return dumped

    def all_writable_targets(self) -> list[dict[str, Any]]:
        """Raw list of every writable target as dicts. Internal use."""
        result = list_editable_targets(
            source_path=str(self.path),
            max_targets=5000,
            only_writable=True,
        ).model_dump(mode="json")
        return result.get("targets") or []

    def describe(self) -> dict[str, Any]:
        guard = self._guard()
        if guard:
            return guard
        try:
            head = read_document(
                source_path=str(self.path),
                start=0, limit=10, include_runs=False,
            ).model_dump(mode="json")
            writable = self.all_writable_targets()
            structural = list_editable_targets(
                source_path=str(self.path),
                target_kinds=["table", "image"],
                max_targets=5000, only_writable=False,
            ).model_dump(mode="json").get("targets") or []
        except Exception as exc:
            return ExceptionClassifier.to_response(exc)
        by_kind: dict[str, int] = {}
        max_page = 0
        for t in writable + structural:
            kind = t.get("target_kind", "?")
            by_kind[kind] = by_kind.get(kind, 0) + 1
            pn = t.get("page_number") or 0
            if pn > max_page:
                max_page = pn
        return ok(
            source_doc_type=head.get("source_doc_type"),
            total_paragraphs=head.get("total_paragraphs"),
            target_counts=by_kind,
            page_count=max_page or None,
            has_tables=(by_kind.get("table", 0) > 0),
            has_images=(by_kind.get("image", 0) > 0),
            top_paragraphs=[
                (pp.get("text") or "").strip()
                for pp in (head.get("paragraphs") or [])
                if (pp.get("text") or "").strip()
            ][:5],
        )
