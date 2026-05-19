"""Verify the new length-guardrail behavior:
  1. inspect_template / list_template_targets expose char_count + max_recommended_chars
  2. fill_and_save returns length_warnings for overlong replacements (without blocking)
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from report_mcp.server import (
    fill_and_save,
    inspect_template,
    list_template_targets,
)

P = Path(__file__).parent
PROJECT_ROOT = P.parent
TEMPLATE = next((PROJECT_ROOT / "output" / "templates").glob("*.hwpx"))


def main() -> int:
    # Copy to short-named tempfile (existing HWPX library quirk)
    tmp = Path(tempfile.mkdtemp(prefix="rmcp_len_"))
    src = tmp / "t.hwpx"
    shutil.copy2(TEMPLATE, src)

    print("=" * 70)
    print("1) inspect_template now carries char_count + max_recommended_chars")
    print("=" * 70)
    insp = inspect_template(str(src), start=0, limit=8)
    for p in insp.get("paragraphs", []):
        txt = (p.get("text") or "").strip()
        if not txt:
            continue
        print(f"  char_count={p.get('char_count'):3d}  "
              f"max_rec={p.get('max_recommended_chars'):3d}  "
              f"text={txt[:60]!r}")

    print()
    print("=" * 70)
    print("2) list_template_targets exposes the same fields per target")
    print("=" * 70)
    lt = list_template_targets(str(src), target_kinds=["paragraph"], max_targets=8)
    for t in lt.get("targets", [])[:8]:
        txt = (t.get("current_text") or "").strip()
        if not txt:
            continue
        print(f"  char_count={t.get('char_count'):3d}  "
              f"max_rec={t.get('max_recommended_chars'):3d}  "
              f"text={txt[:60]!r}")

    print()
    print("=" * 70)
    print("3) fill_and_save returns length_warnings (non-blocking)")
    print("=" * 70)

    # Build two edits: one safely short, one deliberately way too long
    targets = list_template_targets(str(src), target_kinds=["paragraph"], max_targets=400).get("targets", [])
    short_anchor = next(t for t in targets if (t.get("current_text") or "").strip() == "2026. 03. 23.")
    long_anchor = next(t for t in targets if "본 시스템은 외부망 수집" in (t.get("current_text") or ""))

    edits = [
        {  # within limit (date keeps similar length)
            "edit_type": "text",
            "target_kind": "paragraph",
            "target_id": short_anchor["target_id"],
            "expected_text_hash": short_anchor["text_hash"],
            "new_text": "2026. 05. 15.",
        },
        {  # deliberately ~3× longer than original
            "edit_type": "text",
            "target_kind": "paragraph",
            "target_id": long_anchor["target_id"],
            "expected_text_hash": long_anchor["text_hash"],
            "new_text": (
                "― 본 시스템은 외부 데이터 수집부터 폐쇄망 내부 LLM 분석에 이르는 전체 파이프라인을 자동화함으로써, "
                "기존 수작업 대비 처리 시간을 약 80% 절감하고, 운영 인력 의존도를 낮추며, 보안 USB 기반 일방향 이관을 "
                "통해 외부 위협 침투 경로를 원천 차단하는 동시에, 분석 결과의 정확도와 일관성을 정량적으로 보장하는 "
                "통합 분석 환경을 제공함을 핵심 목적으로 함."
            ),
        },
    ]

    out = tmp / "out.hwpx"
    res = fill_and_save(str(src), edits, str(out))
    print(f"status: {res.get('status')}")
    warnings = res.get("length_warnings") or []
    print(f"length_warnings: {len(warnings)} entries")
    for w in warnings:
        print(f"  - target {w['target_id']} ({w['target_kind']}): "
              f"{w['original_display_width']} -> {w['new_display_width']} cells "
              f"(cap {w['max_recommended_width']}, overflow +{w['overflow_cells']})")
        print(f"    hint: {w['hint']}")

    assert res.get("status") == "ok", "should not block on overflow"
    assert len(warnings) == 1, f"expected exactly 1 warning, got {len(warnings)}"
    assert warnings[0]["target_id"] == long_anchor["target_id"]
    print()
    print("PASS: length guardrail is reported as a non-blocking warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
