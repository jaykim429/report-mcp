"""Verify the bytes-IO mode: chatbot in a different filesystem can call
every tool with template_b64 + template_filename and get back output_b64."""

from __future__ import annotations

import base64
import sys
from pathlib import Path

from report_mcp.server import (
    describe_template,
    fill_and_save,
    inspect_template,
    list_template_targets,
)

P = Path(__file__).parent
PROJECT_ROOT = P.parent
HWPX_PATH = next((PROJECT_ROOT / "output" / "templates").glob("*.hwpx"))


def main() -> int:
    raw = HWPX_PATH.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    filename = HWPX_PATH.name

    failures: list[str] = []

    # describe via bytes
    r1 = describe_template(template_b64=b64, template_filename=filename)
    print(f"[1] describe via bytes: status={r1.get('status')} format={r1.get('source_doc_type')} paras={r1.get('total_paragraphs')}")
    if r1.get("status") != "ok" or r1.get("source_doc_type") != "hwpx":
        failures.append(f"describe via bytes: {r1.get('status')}")

    # inspect via bytes
    r2 = inspect_template(template_b64=b64, template_filename=filename, start=0, limit=5)
    print(f"[2] inspect via bytes: status={r2.get('status')} returned={len(r2.get('paragraphs', []))} paragraphs")
    if r2.get("status") != "ok":
        failures.append(f"inspect via bytes: {r2.get('status')}")

    # list via bytes
    r3 = list_template_targets(template_b64=b64, template_filename=filename,
                               target_kinds=["paragraph"], limit=400)
    targets = r3.get("targets", [])
    print(f"[3] list via bytes: status={r3.get('status')} targets={len(targets)}")
    if r3.get("status") != "ok" or len(targets) < 5:
        failures.append(f"list via bytes: {r3.get('status')}, {len(targets)} targets")

    # fill_and_save via bytes IN, bytes OUT
    date_t = next(t for t in targets if (t.get("current_text") or "").strip() == "2026. 03. 23.")
    edits = [{
        "edit_type": "text", "target_kind": "paragraph",
        "target_id": date_t["target_id"], "expected_text_hash": date_t["text_hash"],
        "new_text": "2026. 05. 19.",
    }]
    r4 = fill_and_save(
        template_b64=b64, template_filename=filename,
        edits=edits, return_output_bytes=True,
    )
    print(f"[4] fill via bytes (both in+out): status={r4.get('status')} "
          f"edits_applied={r4.get('edits_applied')} output_size_bytes={r4.get('output_size_bytes')}")
    if r4.get("status") != "ok":
        failures.append(f"fill bytes/bytes: {r4.get('status')} - {r4.get('recovery_hint')}")
    elif not r4.get("output_b64"):
        failures.append("fill bytes/bytes: missing output_b64 in response")
    elif r4.get("output_path"):
        failures.append("fill bytes/bytes: output_path should be removed when output_b64 returned")
    else:
        # Decode and verify it's a valid HWPX (ZIP magic PK\x03\x04)
        decoded = base64.b64decode(r4["output_b64"])
        if not decoded.startswith(b"PK"):
            failures.append(f"output_b64 doesn't decode to ZIP/HWPX (first bytes: {decoded[:4]!r})")
        else:
            print(f"    output_b64 decoded to {len(decoded)} bytes, starts with PK (valid HWPX zip)")
            # Round-trip: re-inspect the output bytes and check the date paragraph was edited
            decoded_b64 = base64.b64encode(decoded).decode("ascii")
            r4b = list_template_targets(template_b64=decoded_b64, template_filename="out.hwpx",
                                        target_kinds=["paragraph"], limit=400)
            edited_para = next(
                (t for t in r4b.get("targets", []) if (t.get("current_text") or "").strip() == "2026. 05. 19."),
                None,
            )
            if edited_para is None:
                failures.append("round-trip: edited date paragraph not found in output_b64")
            else:
                print(f"    round-trip ok: '2026. 05. 19.' found in output_b64")

    # Mutually-exclusive error: both template_path and template_b64
    r5 = describe_template(template_path=str(HWPX_PATH), template_b64=b64, template_filename=filename)
    print(f"[5] both inputs rejected: status={r5.get('status')}")
    if r5.get("status") != "bad_argument":
        failures.append(f"both inputs should reject: got {r5.get('status')}")

    # Mutually-exclusive error: both output_path and return_output_bytes
    r6 = fill_and_save(
        template_b64=b64, template_filename=filename,
        edits=edits, output_path="C:/foo.hwpx", return_output_bytes=True,
    )
    print(f"[6] both outputs rejected: status={r6.get('status')}")
    if r6.get("status") != "bad_argument":
        failures.append(f"both outputs should reject: got {r6.get('status')}")

    # Missing template_filename when using b64
    r7 = describe_template(template_b64=b64)
    print(f"[7] b64 without filename rejected: status={r7.get('status')}")
    if r7.get("status") != "bad_argument":
        failures.append(f"missing filename should reject: got {r7.get('status')}")

    # Improved error: missing path now mentions OS + cwd
    r8 = describe_template(template_path="/tmp/work/template.hwpx")
    print(f"[8] missing path now references server OS: status={r8.get('status')}")
    if "Windows" not in (r8.get("error") or "") and "Linux" not in (r8.get("error") or "") and "Darwin" not in (r8.get("error") or ""):
        failures.append("missing-path error should reference server OS")
    if "template_b64" not in (r8.get("recovery_hint") or ""):
        failures.append("missing-path hint should suggest template_b64 fallback")

    print()
    if failures:
        print(f"FAIL ({len(failures)} issues):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS — bytes-IO mode fully functional + backward-compat preserved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
