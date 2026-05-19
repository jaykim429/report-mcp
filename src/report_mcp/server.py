"""FastMCP server: fill user-supplied document templates with chatbot answers.

The server is told WHEN to engage via the FastMCP `instructions` payload and
each tool's docstring describes its precise USE WHEN / DO NOT USE WHEN trigger,
its prerequisite calls, and the recovery hint a chatbot should follow on
failure. Tool bodies stay thin — TemplateReader handles the read-side calls
and FillPipeline runs the validate→filter→apply chain for fill_and_save.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from .documents import TemplateReader, VALID_TARGET_KINDS
from .patches import apply_library_patches
from .pipeline import FillPipeline

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

CALL ORDER:
  1) (optional, cheap) describe_template(template_path) — one-shot shape
     summary (format, page count, table/image counts, top paragraphs) for
     deciding whether the template is suitable.
  2) list_template_targets(template_path) — every editable spot, each with
     target_id, text_hash, char_count, max_recommended_chars.
  3) (optional) inspect_template(template_path) — paginated paragraph view
     for deeper context.
  4) Compose `edits` mapping chatbot content into target_ids, keeping each
     new_text within its target's max_recommended_chars to avoid wrap /
     overflow / overlap in the rendered document.
  5) fill_and_save(template_path, edits, output_path) — applies the batch
     and writes the result. Output format follows the template:
     DOCX→DOCX, HWP/HWPX→HWPX. PDF cannot be written back as PDF.

ON FAILURE:
  The response always includes `status` and (when not "ok") a `recovery_hint`
  describing exactly what to do next — re-fetch hashes, shorten new_text,
  drop a conflicting edit, etc.
"""

mcp = FastMCP("report-mcp", instructions=SERVER_INSTRUCTIONS)


@mcp.tool()
def inspect_template(
    template_path: str,
    start: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    """Read a template's paragraphs (paginated) so the chatbot can understand
    its structure before composing edits.

    USE WHEN: You want a sequential, page-aware view of the template — e.g.
    to scan section headings, see how bullets are organized, or quote
    surrounding context for the user. Returns text + target_id +
    expected_text_hash + char_count + max_recommended_chars for each paragraph.
    DO NOT USE WHEN: You just need a flat list of every editable spot. Use
    list_template_targets() instead — it covers cells/runs/tables/images
    too and is the canonical first call before fill_and_save.

    Supports DOCX / HWP / HWPX / PDF as input. Paginate large documents with
    `start` and `limit`; `next_start` in the response says where to resume.

    Returns: dict with `paragraphs` (each carrying char_count and
    max_recommended_chars), `total_paragraphs`, `next_start`, `source_doc_type`.
    """
    return TemplateReader(template_path).inspect(start=start, limit=limit)


@mcp.tool()
def list_template_targets(
    template_path: str,
    target_kinds: list[str] | None = None,
    start: int = 0,
    limit: int = 200,
    max_targets: int | None = None,  # deprecated alias for `limit`
) -> dict[str, Any]:
    """List every editable target in the template (paragraphs, runs, cells,
    tables, images) so the chatbot can build a precise edit list.

    USE WHEN: This is the canonical first call before fill_and_save. You need
    target_ids and text_hashes to construct edits; you also need char_count,
    display_width, and max_recommended_chars to keep generated content within
    the space the template was designed for.
    DO NOT USE WHEN: You only need a human-readable overview — use
    inspect_template instead.

    Filter with `target_kinds` (any subset of {paragraph, run, cell, table,
    image}); leave None to get all kinds. Paginate large documents with
    `start` + `limit`; `next_start` in the response is non-None when more
    targets remain. (`max_targets` is accepted as a deprecated alias for
    `limit`.)

    Each target carries (where applicable):
      - target_id, target_kind, current_text, text_hash
      - char_count (code points), display_width (EAW-aware), max_recommended_chars
      - page_number
      - parent_paragraph_id / parent_table_id — the container; lets the
        chatbot tell that a paragraph lives inside a table cell, etc.
      - row_index / column_index / rowspan / colspan — for cells
      - writable / writable_reason — only False entries appear if you pass
        only_writable=False (this tool always uses True)
    """
    if max_targets is not None:
        limit = max_targets
    return TemplateReader(template_path).list_targets(
        target_kinds=target_kinds, start=start, limit=limit,
    )


@mcp.tool()
def describe_template(template_path: str) -> dict[str, Any]:
    """One-call summary of a template's overall shape — page count, target
    counts by kind, and a small text sample.

    USE WHEN: first encounter with a template. You want to know what you're
    dealing with (DOCX or HWPX? how many pages? does it have tables and
    images? what does the first page look like?) before pulling the full
    target list or composing edits.
    DO NOT USE WHEN: you already need the full edit list — go straight to
    list_template_targets().

    Returns: dict with `source_doc_type`, `total_paragraphs`, `target_counts`
    (per target_kind), `page_count`, `top_paragraphs` (first 5 non-empty),
    `has_tables`, `has_images`.
    """
    return TemplateReader(template_path).describe()


@mcp.tool()
def fill_and_save(
    template_path: str,
    edits: list[dict[str, Any]],
    output_path: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Apply a batch of edits to the template and write the result.

    USE WHEN: You have called list_template_targets() and composed an edits
    list mapping the chatbot's generated content into the right target_ids.
    DO NOT USE WHEN: You have not yet fetched target_ids and text_hashes from
    list_template_targets() — fill_and_save will fail validation.

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
      Mixing the two raises a clean `style_field_target_mismatch` response
      with guidance on how to retry.

    BUILT-IN ROBUSTNESS (so chatbots don't have to handle these themselves):
      1. Auto-skip of TextEdit-on-cell entries whose cell content equals a
         paragraph already being edited, and TextEdit-on-run entries whose
         parent paragraph is already being edited. Returned in
         `skipped_redundant_edits` for transparency. StructuralEdit with
         `set_cell_text` is NOT filtered.
      2. Pre-check for duplicate target_id within the same edits batch.
      3. Output extension validation against the template's writeback format.
      4. Defensive coercion: None edits → empty list; non-list → clean error.

    Output format follows the template: DOCX→DOCX, HWP/HWPX→HWPX. PDF input
    cannot be written back as PDF — request a DOCX output_path instead.

    Returns: dict with
      - `status`: ok / dry_run_ok / validation_failed / apply_failed /
        edit_parse_failed / not_found / duplicate_target_id /
        style_field_target_mismatch / format_requires_java / file_error /
        permission_error / runtime_error / bad_argument /
        output_extension_mismatch / format_not_writable
      - `output_path`: absolute path of the written file (when status=ok)
      - `length_safe`: True if no replacement exceeded max_recommended_chars
      - `length_warnings`: details of any overflow (non-blocking)
      - `skipped_redundant_edits`: container/child edits auto-dropped
      - `recovery_hint`: specific next-step guidance when status != ok
      - `edits_applied`: count of edits actually applied
    """
    n = len(edits) if isinstance(edits, list) else type(edits).__name__
    log.info(
        "fill_and_save invoked: template=%s, edits=%s, output=%s, dry_run=%s",
        template_path, n, output_path, dry_run,
    )
    result = FillPipeline(template_path, edits, output_path).run(dry_run=dry_run)
    log.info(
        "fill_and_save result: status=%s, edits_applied=%s, length_safe=%s, skipped=%d",
        result.get("status"),
        result.get("edits_applied"),
        result.get("length_safe"),
        len(result.get("skipped_redundant_edits") or []),
    )
    return result


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
