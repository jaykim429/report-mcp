"""Verify the api.py patch removed the need for our batching workaround:
call document_processor.apply_document_edits directly with 32 edits and see
if it completes without the FileNotFoundError from cumulative _edited.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from document_processor import apply_document_edits, list_editable_targets
from pydantic import TypeAdapter
from document_processor import DocumentEdit

P = Path(__file__).parent
PROJECT_ROOT = P.parent
HWPX = next((PROJECT_ROOT / "output" / "templates").glob("*.hwpx"))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="rmcp_patch_"))
    src = tmp / "t.hwpx"
    shutil.copy2(HWPX, src)
    out = tmp / "out.hwpx"

    paras = list_editable_targets(
        source_path=str(src),
        target_kinds=["paragraph"],
        max_targets=400,
        only_writable=True,
    ).model_dump(mode="json").get("targets", [])

    candidates = [t for t in paras if (t.get("current_text") or "").strip()][:32]
    print(f"applying 32 edits to {src.name} in ONE apply_document_edits call (no batching)")

    edits_raw = [{
        "edit_type": "text",
        "target_kind": "paragraph",
        "target_id": t["target_id"],
        "expected_text_hash": t["text_hash"],
        "new_text": f"테스트 단락 {i}",
    } for i, t in enumerate(candidates)]

    coerced = TypeAdapter(list[DocumentEdit]).validate_python(edits_raw)
    try:
        result = apply_document_edits(
            source_path=str(src),
            edits=coerced,
            output_path=str(out),
        )
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1

    print(f"PASS: status=ok, output_path={result.output_path}")
    print(f"      size={out.stat().st_size:,} bytes")
    # Spot check one edit landed
    post = list_editable_targets(
        source_path=str(out),
        target_kinds=["paragraph"],
        max_targets=400,
        only_writable=True,
    ).model_dump(mode="json").get("targets", [])
    if any((t.get("current_text") or "").startswith("테스트 단락 ") for t in post):
        print("PASS: edits visible in output")
        return 0
    print("FAIL: no edited paragraph found")
    return 1


if __name__ == "__main__":
    sys.exit(main())
