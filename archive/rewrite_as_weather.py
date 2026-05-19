"""Aggressive content-rewrite test:
Take the user's PoC architecture HWPX template and replace EVERY editable
paragraph + cell with content for today's weather report (2026-05-15),
preserving leading whitespace, bullet markers, section numbers, and the
overall structural layout (□ / ○ / ― / ① / ② / ③ / 1. 2. 3. / Ⅰ / Ⅱ).
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
from pathlib import Path

from report_mcp.server import fill_and_save, list_template_targets

PROJECT = Path(__file__).parent
_candidates = sorted((PROJECT / "output" / "templates").glob("*.hwpx"))
TEMPLATE = _candidates[0] if _candidates else PROJECT / "output" / "templates" / "MISSING.hwpx"
OUTPUT = PROJECT / "output" / "오늘의_날씨_보고서.hwpx"

WEATHER_TITLE = "오늘의 날씨 종합 보고서"
WEATHER_DATE = "2026. 05. 15."

H_DOUBLE = [
    "오늘 날씨 핵심 요약",
    "주요 기상 지표",
    "지역별·시간대별 상세",
    "단계별 기상 변화 흐름",
    "주의보 및 권고 사항",
    "주말 전망 요약",
]
H_CIRCLE = [
    "종합 날씨 개요",
    "기온 분포",
    "강수 및 습도",
    "바람 및 체감온도",
    "대기질 및 자외선",
    "주요 도시별 비교",
    "오전·오후·저녁 변화",
    "주의보·특보 현황",
    "주말 전망",
    "추가 권고 사항",
]
BODY = [
    "오늘 서울 지역은 아침 최저 12°C, 한낮 최고 23°C로 전형적인 5월 봄날씨를 보이며, 미세먼지 농도는 '보통' 단계임",
    "전국 대부분 지역에서 맑은 하늘이 우세하며, 일부 남부 해안에 가벼운 비구름이 통과할 가능성이 있음",
    "체감 온도는 실제 기온과 거의 유사하나, 한낮 직사광선 아래에서는 26~27°C 수준까지 올라갈 수 있음",
    "강수 확률은 전국 평균 10% 미만이며, 우산은 별도로 준비하지 않아도 무방함",
    "상대 습도는 오전 65%, 오후 45% 수준으로 쾌적한 상태가 유지될 전망",
    "북서풍 약 3~5m/s로 약한 바람이 종일 이어지며, 해안가 일부 구역은 7m/s 내외로 다소 강하게 불 수 있음",
    "자외선 지수는 '높음' 단계로, 11시~15시 사이 야외 활동 시 자외선 차단제 사용을 권장함",
    "초미세먼지(PM2.5) 농도는 18㎍/㎥로 '보통' 등급, 호흡기 민감군 외에는 야외 활동에 지장 없음",
    "주요 도시별 한낮 최고기온은 서울 23°C, 부산 22°C, 대구 25°C, 광주 24°C, 강릉 21°C, 제주 23°C로 관측됨",
    "오전 6~9시는 다소 쌀쌀한 12~15°C 구간으로 가벼운 외투를 챙기는 것이 좋음",
    "오전 9~12시에는 15~20°C로 빠르게 상승하며 본격적인 봄날씨에 진입함",
    "오후 12~15시는 21~23°C로 한낮 최고치를 기록하며 야외 활동에 최적의 시간대임",
    "오후 15~18시에는 20~22°C 수준을 유지하다 점진적으로 하강함",
    "저녁 18~21시에는 17~19°C로 다시 가벼운 외투가 필요한 수준까지 떨어짐",
    "현재 발효 중인 기상 특보는 없으며, 향후 24시간 내 추가 발령 가능성도 낮음",
    "주말(5/17~5/18) 전망은 토요일 흐림과 일시적 비, 일요일은 다시 맑은 날씨로 회복될 것으로 예상됨",
    "꽃가루 농도는 소나무·참나무류를 중심으로 '높음' 등급이며, 알레르기 민감군은 마스크 착용을 권장함",
    "해상 풍랑 특보는 없으며 동해·남해·서해 모두 항해 및 조업에 무리 없는 상태임",
    "황사 영향권에서 벗어나 있으며, 향후 3일간 황사 유입 가능성은 매우 낮음",
    "일출 05:23 / 일몰 19:34, 낮 길이는 14시간 11분으로 5월 중순 기준 일조량이 풍부함",
    "오존 농도는 0.045ppm 수준으로 환경 기준치 이하이며, 호흡기 부담은 크지 않음",
    "구름양은 전국 평균 3할 내외, 일부 산간 지역에서만 6할 수준의 흐림이 관측됨",
    "기압은 1018hPa 안팎의 안정적 흐름으로, 두통 등 기압 변화에 민감한 분도 무리 없는 하루임",
]
NUMBERED = [
    "기온 및 체감온도",
    "강수 및 습도",
    "대기질 및 자외선",
    "바람 및 일조량",
    "지역별 비교",
    "주말 전망",
]
CIRCLED_MAP = {
    "①": "오전 시간대 (06~12시)",
    "②": "한낮 시간대 (12~18시)",
    "③": "저녁·야간 시간대 (18~24시)",
}

_state = {"double": 0, "circle": 0, "body": 0, "num": 0}


def _next(key: str, pool: list[str]) -> str:
    i = _state[key]
    _state[key] = i + 1
    return pool[i % len(pool)]


def rewrite(original: str) -> str:
    stripped = original.strip()
    # Exact-match special cases (covers both paragraphs and cells).
    # The doc uses "아키텍쳐" (with 텍) consistently.
    if stripped == "PoC 구성용 아키텍쳐":
        return WEATHER_TITLE
    if stripped == "2026. 03. 23.":
        return WEATHER_DATE
    if stripped == "주요 아키텍쳐":
        return "주요 기상 정보"
    if stripped == "PoC 개념 아키텍쳐":
        return "오늘의 기상 개요"
    if stripped == "적용 아키텍쳐":
        return "지역별·시간대별 상세"
    if stripped in {"Ⅰ", "Ⅱ"}:
        return original  # keep section numerals untouched

    # Compound first-line cell like "Ⅰ\nPoC 개념 아키텍쳐"
    if "\n" in original:
        parts = original.split("\n", 1)
        head = parts[0].strip()
        if head in {"Ⅰ", "Ⅱ"}:
            new_tail = "오늘의 기상 개요" if head == "Ⅰ" else "지역별·시간대별 상세"
            return f"{head}\n{new_tail}"

    # Pattern-based: leading whitespace + marker
    m = re.match(r"^(\s*)(.*)$", original, re.DOTALL)
    indent, body_text = m.group(1), m.group(2)

    if body_text.startswith("□"):
        return f"{indent}□ {_next('double', H_DOUBLE)}"
    if body_text.startswith("○"):
        return f"{indent}○ {_next('circle', H_CIRCLE)}"
    if body_text.startswith("―"):
        return f"{indent}― {_next('body', BODY)}"
    for ci, label in CIRCLED_MAP.items():
        if body_text.startswith(ci):
            return f"{indent}{ci} {label}"
    nm = re.match(r"^(\d+)\.\s*(.*)$", body_text)
    if nm:
        return f"{indent}{nm.group(1)}. {_next('num', NUMBERED)}"

    # Fallback: any other non-empty paragraph -> next body line
    if stripped:
        return f"{indent}{_next('body', BODY)}"
    return original


def main() -> int:
    if not TEMPLATE.is_file():
        print(f"missing: {TEMPLATE}")
        return 1
    print(f"template: {TEMPLATE.name} ({TEMPLATE.stat().st_size:,} bytes)")

    # Workaround: document-processor builds intermediate filenames per edit
    # by appending "_edited", so a long original name + many edits blows past
    # Windows 260-char path limit. Copy to a short-named tempfile first.
    tmpdir = Path(tempfile.mkdtemp(prefix="rmcp_"))
    short_src = tmpdir / "t.hwpx"
    shutil.copy2(TEMPLATE, short_src)
    print(f"short-name copy: {short_src}")

    paras = list_template_targets(str(short_src), target_kinds=["paragraph"], max_targets=400).get("targets", [])
    cells = list_template_targets(str(short_src), target_kinds=["cell"], max_targets=400).get("targets", [])
    print(f"discovered: {len(paras)} paragraph targets, {len(cells)} cell targets")

    # Edit only paragraph targets — cells reflect the text of their inner
    # paragraphs, so editing the cell separately would conflict with the
    # paragraph edit that already changed the cell's content.
    edits: list[dict] = []
    skipped_empty = 0
    for t in paras:
        original = t.get("current_text") or ""
        if not original.strip():
            skipped_empty += 1
            continue
        new_text = rewrite(original)
        if new_text == original:
            continue
        edits.append({
            "edit_type": "text",
            "target_kind": t["target_kind"],
            "target_id": t["target_id"],
            "expected_text_hash": t["text_hash"],
            "new_text": new_text,
        })
    _ = cells  # discovered for reporting only; not directly edited

    print(f"skipped empty targets: {skipped_empty}")
    BATCH = 8  # keep cumulative "_edited" suffix well under Windows MAX_PATH
    print(f"applying {len(edits)} edits in batches of {BATCH} -> {OUTPUT}")

    cur_src = short_src
    result = None
    for batch_idx in range(0, len(edits), BATCH):
        chunk = edits[batch_idx : batch_idx + BATCH]
        # Re-hash this chunk against the current intermediate file (hashes
        # change every time we round-trip, even for unchanged targets).
        live = list_template_targets(str(cur_src), target_kinds=["paragraph"], max_targets=400).get("targets", [])
        live_by_text: dict[str, str] = {}
        for t in live:
            txt = (t.get("current_text") or "")
            live_by_text[txt] = t["text_hash"]
        for e in chunk:
            # Look up by current_text from the freshly-read targets, using
            # the original paragraph id to find the matching live target.
            for t in live:
                if t["target_id"] == e["target_id"]:
                    e["expected_text_hash"] = t["text_hash"]
                    break

        next_out = tmpdir / f"step_{batch_idx // BATCH:02d}.hwpx"
        result = fill_and_save(str(cur_src), chunk, str(next_out))
        if result.get("status") != "ok":
            print(f"FAIL on batch {batch_idx // BATCH}: {result}")
            return 1
        print(f"  batch {batch_idx // BATCH:02d}: +{len(chunk)} edits -> {next_out.name} ({next_out.stat().st_size:,} bytes)")
        cur_src = next_out

    short_out = cur_src
    print(f"status: {result.get('status')}")
    if result.get("status") != "ok":
        # Show first few validation issues if any
        v = result.get("validation") or {}
        for i, issue in enumerate((v.get("issues") or [])[:5]):
            print(f"  issue[{i}]: {issue}")
        if "error" in result:
            print(f"  error: {result['error']}")
        return 1

    # Move the temp output to the final location
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    if OUTPUT.exists():
        OUTPUT.unlink()
    shutil.move(str(short_out), str(OUTPUT))
    print(f"output: {OUTPUT}  ({OUTPUT.stat().st_size:,} bytes)")

    # Read back the output and show before/after for the first 12 edits
    post_paras = list_template_targets(str(OUTPUT), target_kinds=["paragraph"], max_targets=400).get("targets", [])
    post_cells = list_template_targets(str(OUTPUT), target_kinds=["cell"], max_targets=400).get("targets", [])
    post_by_id = {t["target_id"]: t for t in post_paras + post_cells}

    print("\n" + "=" * 78)
    print("BEFORE  →  AFTER  (first 12 edited targets)")
    print("=" * 78)
    for e in edits[:12]:
        before = next((t for t in paras + cells if t["target_id"] == e["target_id"]), {}).get("current_text", "")
        after = post_by_id.get(e["target_id"], {}).get("current_text", "")

        def trim(s: str, n: int = 110) -> str:
            s = s.replace("\n", "\\n").strip()
            return s if len(s) <= n else s[:n] + "…"

        print(f"\n[{e['target_id']}]  ({e['target_kind']})")
        print(f"  before: {trim(before)}")
        print(f"  after : {trim(after)}")
        if after.strip() != e["new_text"].strip():
            # Some HWPX cells normalize whitespace differently; allow trim equality
            if after.strip() in e["new_text"] or e["new_text"].strip() in after:
                pass
            else:
                print("  ⚠ mismatch on round-trip read")

    mismatches = 0
    for e in edits:
        after = post_by_id.get(e["target_id"], {}).get("current_text", "")
        if e["new_text"].strip() not in after and after.strip() not in e["new_text"]:
            mismatches += 1
    print(f"\nround-trip check: {len(edits) - mismatches}/{len(edits)} edits match exactly or contain target text")
    print(f"\nFINAL OUTPUT: {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
