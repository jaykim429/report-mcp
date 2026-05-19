"""Probe: how does the server handle PDF input?
PDF should at least be readable (inspect / list). Writing as PDF is not
supported by document-processor — what happens if we try?
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from report_mcp.server import (
    fill_and_save,
    inspect_template,
    list_template_targets,
)


def make_pdf(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(72, 800, "Quarterly Report Template")
    c.setFont("Helvetica", 11)
    c.drawString(72, 770, "Author: [AUTHOR]")
    c.drawString(72, 750, "Date:   [DATE]")
    c.drawString(72, 700, "Summary")
    c.drawString(72, 680, "[SUMMARY]")
    c.showPage()
    c.save()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="rmcp_pdf_"))
    pdf = tmp / "q_report.pdf"
    make_pdf(pdf)
    print(f"made {pdf} ({pdf.stat().st_size:,} bytes)")

    print("\n[1] inspect_template on PDF")
    r1 = inspect_template(str(pdf), start=0, limit=20)
    print(f"  status: {r1.get('status')}")
    print(f"  source_doc_type: {r1.get('source_doc_type')}")
    print(f"  total_paragraphs: {r1.get('total_paragraphs')}")
    for p in (r1.get("paragraphs") or [])[:6]:
        print(f"    ¶ {(p.get('text') or '').strip()[:60]!r}")

    print("\n[2] list_template_targets on PDF")
    r2 = list_template_targets(str(pdf), max_targets=20)
    print(f"  status: {r2.get('status')}")
    print(f"  targets: {len(r2.get('targets') or [])}")
    for t in (r2.get("targets") or [])[:6]:
        print(f"    • {t.get('target_kind')}  {(t.get('current_text') or '')[:60]!r}")

    print("\n[3] fill_and_save with PDF input, .docx output")
    out_docx = tmp / "q_filled.docx"
    targets = r2.get("targets") or []
    author_t = next((t for t in targets if "[AUTHOR]" in (t.get("current_text") or "")), None)
    if author_t is None:
        print("  WARN: no target containing [AUTHOR] — chatbot won't have an anchor")
    else:
        edits = [{"edit_type": "text", "target_kind": author_t.get("target_kind", "paragraph"),
                  "target_id": author_t["target_id"], "expected_text_hash": author_t["text_hash"],
                  "new_text": "Author: 김정훈"}]
        r3 = fill_and_save(str(pdf), edits, str(out_docx))
        print(f"  status: {r3.get('status')}")
        if r3.get("status") == "ok":
            print(f"  output: {r3.get('output_path')} ({Path(r3['output_path']).stat().st_size if Path(r3['output_path']).is_file() else 'MISSING'} bytes)")
        else:
            print(f"  error: {r3.get('error')}")
            print(f"  hint:  {r3.get('recovery_hint')}")

    print("\n[4] fill_and_save with PDF input, .pdf output (should fail or auto-redirect)")
    out_pdf = tmp / "q_filled.pdf"
    if author_t is not None:
        edits = [{"edit_type": "text", "target_kind": author_t.get("target_kind", "paragraph"),
                  "target_id": author_t["target_id"], "expected_text_hash": author_t["text_hash"],
                  "new_text": "Author: 김정훈"}]
        r4 = fill_and_save(str(pdf), edits, str(out_pdf))
        print(f"  status: {r4.get('status')}")
        print(f"  error/hint: {r4.get('error') or r4.get('recovery_hint')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
