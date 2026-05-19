"""Inspect the user-uploaded PoC architecture template."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from report_mcp.server import inspect_template, list_template_targets

TEMPLATE = Path(__file__).parent / "output" / "templates" / "PoC 구성용 아키텍쳐.hwpx"


def main() -> int:
    if not TEMPLATE.is_file():
        print(f"missing: {TEMPLATE}")
        return 1
    print(f"file: {TEMPLATE}")
    print(f"size: {TEMPLATE.stat().st_size:,} bytes\n")

    print("=" * 70)
    print("inspect_template (first 200 paragraphs)")
    print("=" * 70)
    insp = inspect_template(str(TEMPLATE), start=0, limit=200)
    print(f"source_doc_type: {insp.get('source_doc_type')}")
    print(f"total_paragraphs: {insp.get('total_paragraphs')}")
    print(f"next_start: {insp.get('next_start')}")
    print()
    paras = insp.get("paragraphs", [])
    for p in paras:
        txt = (p.get("text") or "").replace("\n", "\\n")
        if not txt.strip():
            continue
        marker = ""
        if p.get("has_tables"):
            marker += " [table]"
        if p.get("has_images"):
            marker += " [image]"
        print(f"  ¶ {p.get('node_id')[:18]}{marker}  page={p.get('page_number')}")
        print(f"     {txt[:140]}")

    print()
    print("=" * 70)
    print("list_template_targets — paragraphs (top 40)")
    print("=" * 70)
    pt = list_template_targets(str(TEMPLATE), target_kinds=["paragraph"], max_targets=400).get("targets", [])
    print(f"total paragraph targets: {len(pt)}")
    for t in pt[:40]:
        txt = (t.get("current_text") or "").replace("\n", "\\n")
        if not txt.strip():
            continue
        print(f"  • {t['target_id'][:18]}  {txt[:140]}")

    print()
    print("=" * 70)
    print("list_template_targets — table cells (all)")
    print("=" * 70)
    ct = list_template_targets(str(TEMPLATE), target_kinds=["cell"], max_targets=400).get("targets", [])
    print(f"total cell targets: {len(ct)}")
    # group by parent_table_id
    by_table: dict[str | None, list[dict]] = {}
    for t in ct:
        by_table.setdefault(t.get("parent_table_id"), []).append(t)
    for tid, cells in by_table.items():
        print(f"\n  ┌─ table {tid} ({len(cells)} cells) ─")
        for c in cells:
            txt = (c.get("current_text") or "").replace("\n", " | ")[:80]
            rc = f"r{c.get('row_index')}c{c.get('column_index')}"
            print(f"  │  {rc:8s} {c['target_id'][:18]}  {txt}")
        print(f"  └─")

    print()
    print("=" * 70)
    print("tables + images summary")
    print("=" * 70)
    table_targets = list_template_targets(str(TEMPLATE), target_kinds=["table"], max_targets=100).get("targets", [])
    image_targets = list_template_targets(str(TEMPLATE), target_kinds=["image"], max_targets=100).get("targets", [])
    print(f"tables: {len(table_targets)}")
    for t in table_targets:
        print(f"  • {t.get('target_id')}  rows={t.get('row_count')} cols={t.get('column_count')}")
    print(f"images: {len(image_targets)}")
    for t in image_targets:
        print(f"  • {t.get('target_id')}  page={t.get('page_number')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
