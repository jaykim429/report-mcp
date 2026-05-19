"""Audit numbering patterns in original vs filled HWPX."""

import re
import sys
from pathlib import Path
from report_mcp.server import list_template_targets

P = Path(__file__).parent
ORIG = next((P / "output" / "templates").glob("*.hwpx"))
NEW = P / "output" / "오늘의_날씨_보고서.hwpx"

NUM_RE = re.compile(r"^\s*(\d+\.|[①②③④⑤⑥⑦⑧⑨⑩])\s*")

for label, f in [("ORIGINAL", ORIG), ("FILLED  ", NEW)]:
    print(f"\n=== {label}  ({f.name}) ===")
    tgts = list_template_targets(str(f), target_kinds=["paragraph"], max_targets=400).get("targets", [])
    found = []
    for t in tgts:
        txt = (t.get("current_text") or "").lstrip()
        m = NUM_RE.match(txt)
        if m:
            page = t.get("page_number")
            found.append((page, m.group(1), txt[:90]))
    print(f"  total: {len(found)} paragraphs with numeric markers")
    for page, marker, txt in found:
        print(f"  p{page}  {marker:4s}  {txt}")
