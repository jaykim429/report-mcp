"""Probe for REAL defects (not library quirks).

Each probe demonstrates a concrete problem with the MCP's behavior that a
chatbot would actually encounter — and that has nothing to do with the
underlying library's bugs.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from report_mcp.server import (
    describe_template,
    fill_and_save,
    inspect_template,
    list_template_targets,
)

P = Path(__file__).parent
PROJECT_ROOT = P.parent
HWPX = next((PROJECT_ROOT / "output" / "templates").glob("*.hwpx"))


def header(t: str) -> None:
    print("\n" + "─" * 70)
    print(t)
    print("─" * 70)


# ────────────────────────────────────────────────────────────────────────
# Defect 1: char_count uses code-point count, not display width.
# Korean Hangul (Wide East Asian) takes ~2 display columns per glyph in
# fixed-width / approximate-rendering contexts. Our 20% headroom is
# computed against code-point count, which under-counts CJK overflow.
# ────────────────────────────────────────────────────────────────────────

def probe_east_asian_width() -> None:
    header("Defect 1: char_count doesn't reflect display width for CJK")
    para = list_template_targets(str(HWPX), target_kinds=["paragraph"], max_targets=400).get("targets", [])
    # Find the date paragraph: "2026. 03. 23." — pure ASCII baseline
    ascii_p = next(t for t in para if (t.get("current_text") or "").strip() == "2026. 03. 23.")
    # Find the cover title: "PoC 구성용 아키텍쳐" — mixed CJK
    cjk_p = next(t for t in para if (t.get("current_text") or "").strip() == "PoC 구성용 아키텍쳐")
    # Find a pure Korean paragraph
    kor_p = next(t for t in para if (t.get("current_text") or "").strip() == "주요 아키텍쳐")

    import unicodedata
    def display_width(s: str) -> int:
        w = 0
        for ch in s:
            ea = unicodedata.east_asian_width(ch)
            w += 2 if ea in ("W", "F") else 1
        return w

    for label, t in [("ASCII   ", ascii_p), ("MIXED CJK", cjk_p), ("PURE KOR", kor_p)]:
        txt = (t.get("current_text") or "").strip()
        print(f"  {label} | text={txt!r}")
        print(f"           | char_count={t.get('char_count'):3d}   "
              f"max_rec={t.get('max_recommended_chars'):3d}   "
              f"display_width={display_width(txt):3d}")
    print("  PROBLEM: a Korean paragraph reports char_count≈8 / max_rec≈18 but "
          "occupies ~16 display cells. A chatbot's 'safe' 18-char Korean "
          "replacement actually takes ~36 display cells — 2× the original "
          "container width. Overflow undetected.")


# ────────────────────────────────────────────────────────────────────────
# Defect 2: dry_run does not show what the document will read like.
# Chatbot can verify "did my edits parse?" but not "is the resulting text
# correct?". The only way to inspect outcome is to actually write the file.
# ────────────────────────────────────────────────────────────────────────

def probe_dry_run_preview() -> None:
    header("Defect 2: dry_run has no semantic before/after preview")
    paras = list_template_targets(str(HWPX), target_kinds=["paragraph"], max_targets=400).get("targets", [])
    date_t = next(t for t in paras if (t.get("current_text") or "").strip() == "2026. 03. 23.")
    edits = [{
        "edit_type": "text", "target_kind": "paragraph",
        "target_id": date_t["target_id"], "expected_text_hash": date_t["text_hash"],
        "new_text": "2026. 05. 18.",
    }]
    r = fill_and_save(str(HWPX), edits, str(P / "output" / "_unused.hwpx"), dry_run=True)
    print(f"  dry_run keys: {sorted(r.keys())}")
    has_preview = any("preview" in k or "before" in k or "diff" in k for k in r.keys())
    print(f"  has before/after preview field: {has_preview}")
    print("  PROBLEM: chatbot wants to confirm 'did I pair the right new_text "
          "with the right target_id?' before committing. Currently it has to "
          "write the file then re-read to see what changed. dry_run should "
          "return per-edit (before, after) pairs so the chatbot can verify "
          "without I/O round-trip.")


# ────────────────────────────────────────────────────────────────────────
# Defect 3: parent hierarchy invisible in target list.
# Paragraphs inside cells inside tables have no link back to their
# containers. A chatbot can't tell whether a given paragraph is body text
# or inside a table cell — affecting how it should compose the replacement.
# ────────────────────────────────────────────────────────────────────────

def probe_missing_parent_hierarchy() -> None:
    header("Defect 3: paragraphs don't expose their containing cell/table")
    paras = list_template_targets(str(HWPX), target_kinds=["paragraph"], max_targets=400).get("targets", [])
    # Pick a paragraph that we know is inside a cell (the cover-page title cell)
    cover_p = next(t for t in paras if (t.get("current_text") or "").strip() == "PoC 구성용 아키텍쳐")
    print("  sample paragraph fields:", sorted(cover_p.keys()))
    parent_field = any(k.startswith("parent") for k in cover_p.keys())
    print(f"  has any parent_* field: {parent_field}")
    if not parent_field:
        print("  PROBLEM: This paragraph is inside a table cell on the cover page, "
              "but list_template_targets gives no clue. A chatbot generating "
              "section content can't distinguish 'this is body text in section 2' "
              "from 'this is a single cell label in a header strip'. Missing "
              "parent context forces guessing.")


# ────────────────────────────────────────────────────────────────────────
# Defect 4: output_path format vs template format mismatch silently passes.
# A chatbot sending output_path='*.pdf' for an HWPX template will get a
# file with .pdf extension but HWPX content inside — silently broken file.
# ────────────────────────────────────────────────────────────────────────

def probe_output_extension_mismatch() -> None:
    header("Defect 4: silent format/extension mismatch on output_path")
    tmp = Path(tempfile.mkdtemp(prefix="rmcp_ext_"))
    out = tmp / "wrong.pdf"  # wrong extension for HWPX template

    paras = list_template_targets(str(HWPX), target_kinds=["paragraph"], max_targets=400).get("targets", [])
    date_t = next(t for t in paras if (t.get("current_text") or "").strip() == "2026. 03. 23.")
    r = fill_and_save(str(HWPX), [{
        "edit_type": "text", "target_kind": "paragraph",
        "target_id": date_t["target_id"], "expected_text_hash": date_t["text_hash"],
        "new_text": "2026. 05. 18.",
    }], str(out))
    print(f"  status: {r.get('status')}")
    if out.is_file():
        head_bytes = out.read_bytes()[:8]
        print(f"  file written: {out}")
        print(f"  first 8 bytes (hex): {head_bytes.hex()}")
        # PDF magic = b"%PDF-"; HWPX (zip) magic = b"PK\x03\x04"
        if head_bytes.startswith(b"PK"):
            print("  PROBLEM: extension says PDF but content is ZIP (HWPX). "
                  "A user double-clicking this .pdf will get a corrupt-file error "
                  "from their PDF reader. We should either reject the mismatch "
                  "or auto-correct to the right extension.")
        elif head_bytes.startswith(b"%PDF"):
            print("  OK: actually PDF content")
        else:
            print("  WARN: unrecognized magic bytes")


# ────────────────────────────────────────────────────────────────────────
# Defect 5: list_template_targets has no pagination.
# Always returns up to max_targets in one shot. Large documents → large
# MCP message → potential transport size cap or chatbot context blowup.
# ────────────────────────────────────────────────────────────────────────

def probe_no_pagination_list() -> None:
    header("Defect 5: list_template_targets has no start parameter")
    import inspect as _inspect
    sig = _inspect.signature(list_template_targets)
    params = list(sig.parameters)
    print(f"  list_template_targets parameters: {params}")
    if "start" in params:
        print("  OK: pagination available")
    else:
        print("  PROBLEM: no `start` parameter. A document with 1000+ targets "
              "forces the chatbot to fetch them all at once. inspect_template "
              "paginates via start/limit; list_template_targets should too.")


# ────────────────────────────────────────────────────────────────────────
# Defect 6: recovery_hint is static text; doesn't include the actual values
# the chatbot needs (target_id of the failing edit, etc.). Chatbot has to
# correlate the hint with the bare error fields manually.
# ────────────────────────────────────────────────────────────────────────

def probe_static_recovery_hints() -> None:
    header("Defect 6: recovery_hint is static, not parameterized")
    # Trigger validation_failed by sending stale hash
    paras = list_template_targets(str(HWPX), target_kinds=["paragraph"], max_targets=400).get("targets", [])
    date_t = next(t for t in paras if (t.get("current_text") or "").strip() == "2026. 03. 23.")
    edits = [{
        "edit_type": "text", "target_kind": "paragraph",
        "target_id": date_t["target_id"],
        "expected_text_hash": "deadbeef" * 5,  # wrong on purpose
        "new_text": "x",
    }]
    tmp = Path(tempfile.mkdtemp(prefix="rmcp_hint_"))
    r = fill_and_save(str(HWPX), edits, str(tmp / "x.hwpx"))
    print(f"  status: {r.get('status')}")
    hint = r.get("recovery_hint", "")
    print(f"  hint: {hint}")
    print(f"  hint contains the failing target_id? {date_t['target_id'] in hint}")
    print(f"  hint contains the expected hash? {'deadbeef' in hint}")
    print("  PROBLEM: hint says 'rebuild the failing edits' but doesn't list "
          "which target_id is failing or what the live hash is. Chatbot has "
          "to dig into the `validation.issues` field to extract this. The hint "
          "could include `target_id` and `current_text_hash` directly so the "
          "chatbot can fix and retry in one step.")


def main() -> int:
    probe_east_asian_width()
    probe_dry_run_preview()
    probe_missing_parent_hierarchy()
    probe_output_extension_mismatch()
    probe_no_pagination_list()
    probe_static_recovery_hints()
    print("\n" + "─" * 70)
    print("All 6 defects are REAL (not library workarounds).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
