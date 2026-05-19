"""Apply realistic chatbot-style edits to the user's PoC HWPX template
and save the result as a new HWPX file. Verify by re-inspecting and
showing the before/after for each edited paragraph.
"""

from __future__ import annotations

import sys
from pathlib import Path

from report_mcp.server import fill_and_save, list_template_targets

PROJECT = Path(__file__).parent
TEMPLATE = PROJECT / "output" / "templates" / "PoC 구성용 아키텍쳐.hwpx"
OUTPUT = PROJECT / "output" / "PoC 구성용 아키텍쳐_채움.hwpx"


def find(targets: list[dict], needle: str) -> dict | None:
    for t in targets:
        if needle in (t.get("current_text") or ""):
            return t
    return None


def show(label: str, text: str, max_len: int = 120) -> None:
    t = text.replace("\n", "\\n")
    if len(t) > max_len:
        t = t[:max_len] + "…"
    print(f"  {label:7s} | {t}")


def main() -> int:
    if not TEMPLATE.is_file():
        print(f"missing: {TEMPLATE}")
        return 1

    print(f"template: {TEMPLATE.name} ({TEMPLATE.stat().st_size:,} bytes)\n")

    paras = list_template_targets(str(TEMPLATE), target_kinds=["paragraph"], max_targets=400).get("targets", [])
    print(f"discovered {len(paras)} editable paragraph targets")

    # Find specific edit anchors by content.
    date_tgt    = find(paras, "2026. 03. 23.")
    overview_tgt = find(paras, "본 시스템은 외부망 수집")
    external_tgt = find(paras, "외부 공개 데이터 소스로부터")
    physical_tgt = find(paras, "(물리적 단절 구간)")

    anchors = {
        "date":     date_tgt,
        "overview": overview_tgt,
        "external": external_tgt,
        "physical": physical_tgt,
    }
    for name, t in anchors.items():
        if t is None:
            print(f"FAIL: could not find anchor '{name}'")
            return 1
        print(f"  anchor {name:9s} -> {t['target_id']}")

    edits = [
        {"edit_type": "text", "target_kind": "paragraph",
         "target_id": date_tgt["target_id"], "expected_text_hash": date_tgt["text_hash"],
         "new_text": "2026. 05. 15."},
        {"edit_type": "text", "target_kind": "paragraph",
         "target_id": overview_tgt["target_id"], "expected_text_hash": overview_tgt["text_hash"],
         "new_text": ("― 본 시스템은 외부망 수집 → 반입 인터페이스 → 폐쇄망 분석의 3단계 망분리 구조로 구성되며, "
                      "외부에서 수집한 데이터를 보안 USB 기반의 일방향 이관을 통해 폐쇄망에 반입한 뒤 "
                      "LLM 기반 AI 엔진으로 요약·분류·키워드 추출을 자동 수행함")},
        {"edit_type": "text", "target_kind": "paragraph",
         "target_id": external_tgt["target_id"], "expected_text_hash": external_tgt["text_hash"],
         "new_text": "― 외부 공개 데이터 소스로부터 정보를 정기 수집·정제하는 구간 (뉴스, 공공 OpenAPI, 백서, 학술 사이트 등)"},
        {"edit_type": "text", "target_kind": "paragraph",
         "target_id": physical_tgt["target_id"], "expected_text_hash": physical_tgt["text_hash"],
         "new_text": ("― (물리적 단절 구간) 외부망과 폐쇄망 사이에 어떠한 네트워크 경로도 두지 않고, "
                      "검수 통과 데이터를 보안 USB로만 일방향 이관함으로써 외부 위협의 폐쇄망 침투 경로를 원천 차단")},
    ]

    print(f"\napplying {len(edits)} edits -> {OUTPUT}")
    result = fill_and_save(str(TEMPLATE), edits, str(OUTPUT))
    print(f"status: {result.get('status')}")
    if result.get("status") != "ok":
        print(result)
        return 1
    print(f"output exists: {OUTPUT.is_file()}  size: {OUTPUT.stat().st_size:,} bytes")

    # Re-inspect the output to confirm the new text round-trips correctly.
    post = list_template_targets(str(OUTPUT), target_kinds=["paragraph"], max_targets=400).get("targets", [])

    def find_post_by_new(new_text: str) -> str | None:
        head = new_text[:30]
        for t in post:
            if head in (t.get("current_text") or ""):
                return t.get("current_text")
        return None

    print("\n=== before / after on each edited paragraph ===\n")
    for name, edit in zip(anchors.keys(), edits):
        before = anchors[name]["current_text"]
        after = find_post_by_new(edit["new_text"])
        print(f"[{name}]")
        show("before", before)
        show("after",  after or "<NOT FOUND IN OUTPUT>")
        if after is None or edit["new_text"] not in after:
            print("  ✗ FAIL: edited text not found in output")
            return 1
        print("  ✓ ok")
        print()

    print(f"FINAL OUTPUT: {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
