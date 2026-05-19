"""FastMCP server: fill user-supplied document templates with chatbot answers.

Every tool accepts the template either as a local file path
(`template_path`) or as inline base64 bytes (`template_b64` +
`template_filename`). The bytes form is essential when the chatbot session
lives in a different filesystem than the MCP server (e.g. a hosted Linux
sandbox calling out to a Windows-hosted server).

`fill_and_save` similarly returns the result either by writing to
`output_path` or by returning `output_b64` (when `return_output_bytes=True`)
so the chatbot can hand the bytes back to the user without any shared path.
"""

from __future__ import annotations

import base64
import logging
import tempfile
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .documents import TemplateReader
from .inputs import TemplateInputResolver
from .patches import apply_library_patches
from .pipeline import FillPipeline, _expected_output_ext
from .responses import bad_argument
from .session import register_template_in_cache, unregister_template_from_cache

apply_library_patches()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("report-mcp")

SERVER_INSTRUCTIONS = """\
report-mcp — fill a user-uploaded report template with chatbot-generated content.

USE THIS SERVER WHEN:
  • The user attaches a document file (.docx / .hwp / .hwpx / .pdf) AND
  • asks the chatbot to fill it / convert content into it / produce a report
    that follows its layout — e.g. "이 양식에 맞춰 만들어줘",
    "첨부한 템플릿대로 보고서로 떨궈줘", "이 PoC 제안서 양식으로 정리해줘",
    "this form, populated with my answer".
  • The whole point is to preserve the original styles, fonts, table shapes,
    and bullet markers and only change the text inside.

DO NOT USE THIS SERVER WHEN:
  • No template file is attached (free-form report generation has no anchor).
  • The user wants the content discussed in chat only, not saved to a file.
  • The user wants a brand-new file in a format unrelated to any uploaded
    template.

INPUT MODES (every tool accepts both):
  • template_path = "C:\\path\\to\\file.hwpx" — file on the MCP server's
    machine. Fast (no copy).
  • template_b64 = "<base64>", template_filename = "file.hwpx" — inline
    bytes when chatbot and server live in different filesystems (e.g.
    sandboxed chatbot ↔ Windows-hosted server).

OUTPUT MODES (fill_and_save):
  • output_path = "C:\\out\\report.hwpx" — write to a path on the server.
  • return_output_bytes = True — return `output_b64` inline so the chatbot
    can hand the bytes back to the user without needing a shared path.

CALL ORDER:
  1) (optional, cheap) describe_template — one-shot shape summary for
     deciding whether the template is suitable.
  2) list_template_targets — every editable spot with target_id, text_hash,
     char_count, display_width, max_recommended_chars.
  3) (optional) inspect_template — paginated paragraph view for deeper
     context.
  4) Compose `edits` mapping chatbot content into target_ids, keeping each
     new_text within its target's max_recommended_chars to avoid wrap /
     overflow / overlap in the rendered document.
  5) fill_and_save — applies the batch and writes/returns the result.
     Output format follows the template: DOCX→DOCX, HWP/HWPX→HWPX.

ON FAILURE:
  The response always includes `status` and (when not "ok") a `recovery_hint`
  describing exactly what to do next — re-fetch hashes, shorten new_text,
  switch to template_b64, etc.
"""

mcp = FastMCP("report-mcp", instructions=SERVER_INSTRUCTIONS)


# ──────────────────────────────────────────────────────────────────────────
# tools
# ──────────────────────────────────────────────────────────────────────────

@mcp.tool()
def register_template(template_b64: str, template_filename: str) -> dict[str, Any]:
    """Cache a template on the server and return a `template_id` reusable in
    every other tool. Use this when you'll call multiple tools on the same
    template (describe + list + inspect + fill) to avoid re-uploading the
    same base64 payload each time.

    Entries auto-expire after 1 hour; call `unregister_template` to free
    earlier. The cache holds at most 50 templates (oldest evicted).

    Returns: {status: "ok", template_id, size_bytes, filename, expires_in_seconds}
    """
    return register_template_in_cache(template_b64, template_filename)


@mcp.tool()
def unregister_template(template_id: str) -> dict[str, Any]:
    """Free the server-side cache entry for `template_id`. Optional —
    entries auto-expire after 1 hour, but explicit cleanup is polite when
    you're done with a template."""
    return unregister_template_from_cache(template_id)


@mcp.tool()
def inspect_template(
    template_path: str | None = None,
    template_b64: str | None = None,
    template_filename: str | None = None,
    template_id: str | None = None,
    start: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    """Read a template's paragraphs (paginated) so the chatbot can understand
    its structure before composing edits.

    USE WHEN: You want a sequential, page-aware view of the template — e.g.
    to scan section headings, see how bullets are organized, or quote
    surrounding context for the user.
    DO NOT USE WHEN: You just need a flat list of every editable spot — use
    list_template_targets() instead.

    Provide the template via ONE of:
      - `template_id` (preferred for repeat calls, from register_template)
      - `template_path` (file on the server's machine)
      - `template_b64` + `template_filename` (inline base64, cross-filesystem)

    Paginate large documents with `start` + `limit`; `next_start` in the
    response says where to resume.
    """
    resolved, err = TemplateInputResolver.resolve(
        template_path, template_b64, template_filename, template_id,
    )
    if err:
        return err
    with resolved:
        return TemplateReader(resolved.path).inspect(start=start, limit=limit)


@mcp.tool()
def list_template_targets(
    template_path: str | None = None,
    template_b64: str | None = None,
    template_filename: str | None = None,
    template_id: str | None = None,
    target_kinds: list[str] | None = None,
    start: int = 0,
    limit: int = 200,
    max_targets: int | None = None,  # deprecated alias for `limit`
) -> dict[str, Any]:
    """List every editable target in the template (paragraphs, runs, cells,
    tables, images) so the chatbot can build a precise edit list.

    USE WHEN: Canonical first call before fill_and_save. You need target_ids
    + text_hashes to construct edits, and char_count / display_width /
    max_recommended_chars to keep generated content within container limits.
    DO NOT USE WHEN: You only need a human-readable overview — use
    inspect_template instead.

    Provide the template either as `template_path` or as `template_b64` +
    `template_filename`. Filter with `target_kinds` (any subset of
    {paragraph, run, cell, table, image}); leave None for all kinds.
    Paginate via `start` + `limit`; `next_start` is non-None when more
    targets remain. (`max_targets` is a deprecated alias for `limit`.)

    Each target carries (where applicable):
      - target_id, target_kind, current_text, text_hash
      - char_count (code points), display_width (EAW-aware), max_recommended_chars
      - page_number
      - parent_paragraph_id / parent_table_id — the container
      - row_index / column_index / rowspan / colspan — for cells
    """
    if max_targets is not None:
        limit = max_targets
    resolved, err = TemplateInputResolver.resolve(
        template_path, template_b64, template_filename, template_id,
    )
    if err:
        return err
    with resolved:
        return TemplateReader(resolved.path).list_targets(
            target_kinds=target_kinds, start=start, limit=limit,
        )


@mcp.tool()
def describe_template(
    template_path: str | None = None,
    template_b64: str | None = None,
    template_filename: str | None = None,
    template_id: str | None = None,
) -> dict[str, Any]:
    """One-call summary of a template's overall shape — page count, target
    counts by kind, and a small text sample.

    USE WHEN: first encounter with a template. You want to know format,
    page count, presence of tables/images, and a sample of headings
    before pulling the full target list.
    DO NOT USE WHEN: you already need the full edit list — go straight to
    list_template_targets().

    Provide the template either as `template_path` or as `template_b64` +
    `template_filename`.

    Returns: dict with `source_doc_type`, `total_paragraphs`, `target_counts`
    (per target_kind), `page_count`, `top_paragraphs` (first 5 non-empty),
    `has_tables`, `has_images`.
    """
    resolved, err = TemplateInputResolver.resolve(
        template_path, template_b64, template_filename, template_id,
    )
    if err:
        return err
    with resolved:
        return TemplateReader(resolved.path).describe()


@mcp.tool()
def fill_and_save(
    template_path: str | None = None,
    edits: list[dict[str, Any]] | None = None,
    output_path: str | None = None,
    dry_run: bool = False,
    template_b64: str | None = None,
    template_filename: str | None = None,
    template_id: str | None = None,
    return_output_bytes: bool = False,
) -> dict[str, Any]:
    """Apply a batch of edits to the template and write/return the result.

    USE WHEN: You have called list_template_targets() and composed an edits
    list mapping the chatbot's generated content into the right target_ids.
    DO NOT USE WHEN: You have not yet fetched target_ids and text_hashes from
    list_template_targets() — fill_and_save will fail validation.

    INPUT modes: provide the template via `template_path` OR
    `template_b64` + `template_filename`.

    OUTPUT modes: provide `output_path` (server-side write) OR set
    `return_output_bytes=True` (response includes `output_b64`). Required
    unless `dry_run=True`. Use bytes when the chatbot is in a different
    filesystem than the MCP server.

    Each edit dict must include `edit_type` ("text" | "structural" | "style")
    plus the fields required for that type:
      - text:        target_id, expected_text_hash, new_text
                     (+ optional target_kind, reason)
      - structural:  operation, target_id
                     (+ position, text/rows/values, row_index, column_index)
      - style:       target_id
                     (+ any style fields: bold, color, font_size_pt, etc.)

    STYLE EDIT TARGETING:
      Run-level fields (bold / italic / underline / color / font_size_pt) must
      use target_kind='run' against a run target. Paragraph-level fields
      (paragraph_align / left_indent_pt / etc.) use target_kind='paragraph'.
      Mixing the two raises a clean `style_field_target_mismatch` response.

    BUILT-IN ROBUSTNESS:
      1. Auto-skip of container/child edit conflicts (cell↔paragraph,
         paragraph↔run). Skipped entries in `skipped_redundant_edits`.
      2. Pre-check for duplicate target_id.
      3. Output extension validation.
      4. Defensive type coercion (None edits → empty list).

    Output format follows the template: DOCX→DOCX, HWP/HWPX→HWPX. PDF input
    cannot be written back as PDF.

    Returns: dict with
      - `status`: ok / dry_run_ok / validation_failed / apply_failed /
        edit_parse_failed / not_found / duplicate_target_id /
        style_field_target_mismatch / format_requires_java / file_error /
        permission_error / runtime_error / bad_argument /
        output_extension_mismatch / format_not_writable
      - `output_path` OR `output_b64` + `output_size_bytes` (status=ok)
      - `length_safe`, `length_warnings`, `skipped_redundant_edits`
      - `recovery_hint` (when status != ok)
      - `edits_applied`
    """
    n = len(edits) if isinstance(edits, list) else type(edits).__name__
    log.info(
        "fill_and_save invoked: template_path=%s, template_b64=%s, edits=%s, output_path=%s, "
        "return_output_bytes=%s, dry_run=%s",
        template_path,
        "<%d chars>" % len(template_b64) if template_b64 else None,
        n, output_path, return_output_bytes, dry_run,
    )

    if not dry_run and not output_path and not return_output_bytes:
        return bad_argument(
            "Either output_path or return_output_bytes=True is required.",
            "Provide output_path (a path on the MCP server's machine) or set "
            "return_output_bytes=True to receive the bytes inline (base64). "
            "Required unless dry_run=True.",
        )
    if output_path and return_output_bytes:
        return bad_argument(
            "Provide either output_path or return_output_bytes=True, not both.",
            "Pick one output channel and call again.",
        )

    resolved, err = TemplateInputResolver.resolve(
        template_path, template_b64, template_filename, template_id,
    )
    if err:
        return err

    with resolved:
        temp_output: Path | None = None
        effective_output = output_path or ""
        if return_output_bytes and not dry_run:
            ext = _expected_output_ext(resolved.path)
            if ext is None:
                return bad_argument(
                    "return_output_bytes requested but input format cannot be written.",
                    "PDF input has no writeable output format. Provide a DOCX/HWPX/HWP "
                    "template instead.",
                )
            fd, tmp_name = tempfile.mkstemp(prefix="rmcp_out_", suffix=ext)
            import os
            os.close(fd)
            temp_output = Path(tmp_name)
            effective_output = str(temp_output)
        elif dry_run and not effective_output:
            # FillPipeline still wants a path string for extension validation
            # in dry_run mode; the file is never opened or written.
            ext = _expected_output_ext(resolved.path) or ".docx"
            effective_output = f"_dry_run_unused{ext}"

        try:
            result = FillPipeline(resolved.path, edits, effective_output).run(dry_run=dry_run)

            if return_output_bytes and not dry_run and result.get("status") == "ok":
                data = temp_output.read_bytes() if temp_output else b""
                result["output_b64"] = base64.b64encode(data).decode("ascii")
                result["output_size_bytes"] = len(data)
                result.pop("output_path", None)
        finally:
            if temp_output is not None:
                try:
                    temp_output.unlink(missing_ok=True)
                except OSError:
                    pass

    log.info(
        "fill_and_save result: status=%s, edits_applied=%s, length_safe=%s, skipped=%d",
        result.get("status"),
        result.get("edits_applied"),
        result.get("length_safe"),
        len(result.get("skipped_redundant_edits") or []),
    )
    return result


@mcp.tool()
def convert_to_hwpx(
    template_path: str | None = None,
    template_b64: str | None = None,
    template_filename: str | None = None,
    template_id: str | None = None,
    output_path: str | None = None,
    return_output_bytes: bool = False,
) -> dict[str, Any]:
    """Convert an .hwp / .hwpx / .hwtx input to canonical .hwpx without
    modifying any content. Equivalent to fill_and_save with an empty edits
    list — convenience wrapper for the common "I just want hwpx" flow.

    USE WHEN: User uploads a binary .hwp and you want the .hwpx equivalent,
    or wants a .hwtx template normalized to .hwpx. The linesegarray cache
    cleanup still runs so the output renders cleanly in Hangul.

    DO NOT USE WHEN: input is DOCX or PDF (no conversion target exists).

    Requires Java 11+ on the server only for binary .hwp inputs.
    .hwpx and .hwtx work without Java.

    Returns the same shape as fill_and_save.
    """
    return fill_and_save(
        template_path=template_path,
        edits=[],
        output_path=output_path,
        dry_run=False,
        template_b64=template_b64,
        template_filename=template_filename,
        template_id=template_id,
        return_output_bytes=return_output_bytes,
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
