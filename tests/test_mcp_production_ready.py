"""Verify the production-readiness fixes are wired up correctly:
  - Server-level instructions populated (A-1)
  - Tool docstrings carry USE WHEN / DO NOT USE / RETURNS (A-2)
  - Error responses include recovery_hint (B-2)
  - 30+ edits succeed in one call via auto-batching (C)
  - Cell + paragraph conflict is auto-resolved (C)
  - length_warnings still surfaced (regression)
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from report_mcp.server import (
    SERVER_INSTRUCTIONS,
    fill_and_save,
    inspect_template,
    list_template_targets,
    mcp,
)

P = Path(__file__).parent
PROJECT_ROOT = P.parent
TEMPLATE = next((PROJECT_ROOT / "output" / "templates").glob("*.hwpx"))


def main() -> int:
    failures: list[str] = []

    # A-1: server-level instructions
    print("[A-1] server instructions")
    assert "USE THIS SERVER WHEN" in SERVER_INSTRUCTIONS, "missing trigger guidance"
    assert "DO NOT USE" in SERVER_INSTRUCTIONS, "missing negative trigger"
    assert "CALL ORDER" in SERVER_INSTRUCTIONS, "missing workflow"
    print("  ok — server instructions cover when/when-not/order/failure")

    # A-2: tool docstring quality
    print("[A-2] tool docstrings")
    for tool_name in ("inspect_template", "list_template_targets", "fill_and_save"):
        doc = globals()[tool_name].__doc__ or ""
        if "USE WHEN" not in doc:
            failures.append(f"{tool_name} missing USE WHEN")
        if "DO NOT USE" not in doc:
            failures.append(f"{tool_name} missing DO NOT USE")
        print(f"  {tool_name}: USE WHEN={'USE WHEN' in doc}  DO NOT USE={'DO NOT USE' in doc}")

    # B-2: recovery_hint on error responses
    print("[B-2] recovery_hint on failure paths")
    r = inspect_template("c:/does/not/exist.docx")
    assert r.get("status") == "not_found", r
    assert "recovery_hint" in r, "no recovery_hint on not_found"
    print(f"  not_found recovery_hint: {r['recovery_hint'][:80]}...")

    r2 = list_template_targets(str(TEMPLATE), target_kinds=["bogus_kind"])
    assert r2.get("status") == "bad_argument", r2
    assert "recovery_hint" in r2, "no recovery_hint on bad_argument"
    print(f"  bad_argument recovery_hint: {r2['recovery_hint'][:80]}...")

    # E: truncated meta
    print("[E] truncated flag")
    r3 = list_template_targets(str(TEMPLATE), target_kinds=["paragraph"], max_targets=5)
    assert "truncated" in r3, "truncated flag missing"
    print(f"  max_targets=5 truncated={r3['truncated']} actual_returned={len(r3.get('targets', []))}")

    # C: 30+ edits in one call (auto-batching path)
    print("[C] auto-batching of 30+ edits")
    tmp = Path(tempfile.mkdtemp(prefix="rmcp_prod_"))
    src = tmp / "t.hwpx"
    shutil.copy2(TEMPLATE, src)
    out = tmp / "out.hwpx"

    paras = list_template_targets(str(src), target_kinds=["paragraph"], max_targets=400).get("targets", [])
    # Build 32 edits — past _BATCH_SIZE=8, so we hit batching
    candidates = [t for t in paras if (t.get("current_text") or "").strip()][:32]
    edits = [{
        "edit_type": "text",
        "target_kind": "paragraph",
        "target_id": t["target_id"],
        "expected_text_hash": t["text_hash"],
        "new_text": "테스트로 교체된 본문",
    } for t in candidates]
    r4 = fill_and_save(str(src), edits, str(out))
    print(f"  status={r4.get('status')}  edits_applied={r4.get('edits_applied')}")
    if r4.get("status") != "ok":
        failures.append(f"32-edit single-call failed: {r4}")
    elif r4.get("edits_applied") != 32:
        failures.append(f"expected edits_applied=32, got {r4.get('edits_applied')}")

    # C: cell + paragraph conflict auto-resolution
    print("[C] cell/paragraph conflict auto-skip")
    src2 = tmp / "t2.hwpx"
    shutil.copy2(TEMPLATE, src2)
    out2 = tmp / "out2.hwpx"
    paras2 = list_template_targets(str(src2), target_kinds=["paragraph"], max_targets=400).get("targets", [])
    cells2 = list_template_targets(str(src2), target_kinds=["cell"], max_targets=400).get("targets", [])
    cover_para = next(t for t in paras2 if (t.get("current_text") or "").strip() == "PoC 구성용 아키텍쳐")
    cover_cell = next((t for t in cells2 if (t.get("current_text") or "").strip() == "PoC 구성용 아키텍쳐"), None)
    conflict_edits = [
        {"edit_type": "text", "target_kind": "paragraph",
         "target_id": cover_para["target_id"], "expected_text_hash": cover_para["text_hash"],
         "new_text": "테스트 제목"},
    ]
    if cover_cell:
        conflict_edits.append({
            "edit_type": "text", "target_kind": "cell",
            "target_id": cover_cell["target_id"], "expected_text_hash": cover_cell["text_hash"],
            "new_text": "다른 제목",
        })
    r5 = fill_and_save(str(src2), conflict_edits, str(out2))
    print(f"  status={r5.get('status')}  skipped_redundant_edits={len(r5.get('skipped_redundant_edits', []))}")
    if r5.get("status") != "ok":
        failures.append(f"cell-conflict scenario failed: {r5}")
    if cover_cell and not r5.get("skipped_redundant_edits"):
        failures.append("expected cell edit to be skipped, but it was not")

    # Regression: length warnings still flow through
    print("[regression] length_warnings still surfaced")
    long_anchor = next(t for t in paras2 if "본 시스템은 외부망 수집" in (t.get("current_text") or ""))
    r6 = fill_and_save(str(src2), [{
        "edit_type": "text", "target_kind": "paragraph",
        "target_id": long_anchor["target_id"], "expected_text_hash": long_anchor["text_hash"],
        "new_text": "본 시스템은 외부망 수집 → 반입 인터페이스 → 폐쇄망 분석의 3단계 망분리 구조로 구성되며 보안 USB 일방향 이관과 LLM 기반 자동 요약·분류·키워드 추출 기능을 제공한다." * 3,
    }], str(tmp / "out6.hwpx"))
    warnings = r6.get("length_warnings", [])
    print(f"  status={r6.get('status')}  warnings={len(warnings)}")
    if not warnings:
        failures.append("expected length_warnings, got none")

    print()
    if failures:
        print(f"FAIL ({len(failures)} issues):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"PASS — all production-readiness checks green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
