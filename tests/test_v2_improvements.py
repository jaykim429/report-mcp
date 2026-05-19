"""Verify the post-proposal improvements:
  - linesegarray cache cleanup after every HWPX edit
  - template_id session caching (register/lookup/unregister)
  - .hwtx extension treated as HWPX
"""

from __future__ import annotations

import base64
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from report_mcp.server import (
    convert_to_hwpx,
    describe_template,
    fill_and_save,
    list_template_targets,
    register_template,
    unregister_template,
)

P = Path(__file__).parent
PROJECT_ROOT = P.parent
HWPX = next((PROJECT_ROOT / "output" / "templates").glob("*.hwpx"))


def count_nonempty_linesegarrays(hwpx_path: Path) -> int:
    n = 0
    with zipfile.ZipFile(hwpx_path) as zf:
        for name in zf.namelist():
            if name.startswith("Contents/section") and name.endswith(".xml"):
                xml = zf.read(name).decode("utf-8")
                for m in re.finditer(r"<hp:linesegarray>(.*?)</hp:linesegarray>", xml, re.DOTALL):
                    if m.group(1).strip():
                        n += 1
    return n


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="rmcp_v2_"))
    failures: list[str] = []

    # ── 1. linesegarray cleanup after every edit ─────────────────────────
    print("[1] linesegarray cleanup")
    src = tmp / "ls.hwpx"; out = tmp / "ls_out.hwpx"
    shutil.copy2(HWPX, src)
    paras = list_template_targets(template_path=str(src), target_kinds=["paragraph"], limit=400).get("targets", [])
    date_t = next(t for t in paras if (t.get("current_text") or "").strip() == "2026. 03. 23.")
    r = fill_and_save(template_path=str(src), edits=[{
        "edit_type": "text", "target_kind": "paragraph",
        "target_id": date_t["target_id"], "expected_text_hash": date_t["text_hash"],
        "new_text": "2026. 05. 19.",
    }], output_path=str(out))
    pre_caches = count_nonempty_linesegarrays(src)
    post_caches = count_nonempty_linesegarrays(out)
    print(f"  pre_caches={pre_caches}  post_caches={post_caches}  cleared={r.get('linesegarray_sections_cleared')}")
    if post_caches != 0:
        failures.append(f"linesegarray not fully cleared: {post_caches} caches remain")
    if r.get("linesegarray_sections_cleared", 0) <= 0:
        failures.append(f"response should report sections cleared > 0")

    # ── 2. template_id session caching ───────────────────────────────────
    print("\n[2] template_id session caching")
    raw = HWPX.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    reg = register_template(template_b64=b64, template_filename=HWPX.name)
    print(f"  register status={reg.get('status')} id={(reg.get('template_id') or '')[:12]}... size={reg.get('size_bytes')}")
    if reg.get("status") != "ok" or not reg.get("template_id"):
        failures.append(f"register_template failed: {reg}")
        tid = None
    else:
        tid = reg["template_id"]

    if tid:
        # Reuse the same id across 3 tools
        d = describe_template(template_id=tid)
        l = list_template_targets(template_id=tid, target_kinds=["paragraph"], limit=10)
        i_ = describe_template(template_id=tid)  # again, idempotent
        ok = (d.get("status") == "ok" and l.get("status") == "ok" and i_.get("status") == "ok")
        print(f"  reuse 3x via template_id: all ok={ok}")
        if not ok:
            failures.append(f"reuse via template_id failed: d={d.get('status')} l={l.get('status')} i={i_.get('status')}")

        # Fill via template_id + return bytes
        all_paras = list_template_targets(template_id=tid, target_kinds=["paragraph"], limit=400).get("targets", [])
        date_t = next(t for t in all_paras if (t.get("current_text") or "").strip() == "2026. 03. 23.")
        r = fill_and_save(
            template_id=tid,
            edits=[{
                "edit_type": "text", "target_kind": "paragraph",
                "target_id": date_t["target_id"], "expected_text_hash": date_t["text_hash"],
                "new_text": "2026. 05. 19.",
            }],
            return_output_bytes=True,
        )
        print(f"  fill via template_id (bytes out): status={r.get('status')} cleared={r.get('linesegarray_sections_cleared')}")
        if r.get("status") != "ok":
            failures.append(f"fill via template_id: {r}")

        # Unregister
        u = unregister_template(tid)
        print(f"  unregister status={u.get('status')}")
        if u.get("status") != "ok" or not u.get("freed"):
            failures.append(f"unregister failed: {u}")

        # Lookup after unregister should fail cleanly
        d2 = describe_template(template_id=tid)
        if d2.get("status") != "not_found":
            failures.append(f"lookup after unregister: expected not_found, got {d2.get('status')}")
        else:
            print(f"  post-unregister lookup correctly returns not_found")

    # ── 3. mutual-exclusion of input modes ───────────────────────────────
    print("\n[3] input-mode exclusivity")
    r = describe_template(template_path=str(HWPX), template_id="abc")
    if r.get("status") != "bad_argument":
        failures.append(f"path+id should reject: {r.get('status')}")
    else:
        print(f"  path + id rejected: {r.get('status')}")

    # ── 4. .hwtx extension treated as HWPX ───────────────────────────────
    print("\n[4] .hwtx alias")
    reg2 = register_template(template_b64=b64, template_filename="cooperation_form.hwtx")
    print(f"  register .hwtx: status={reg2.get('status')}")
    if reg2.get("status") != "ok":
        failures.append(f".hwtx register failed: {reg2}")
    else:
        d = describe_template(template_id=reg2["template_id"])
        if d.get("source_doc_type") != "hwpx":
            failures.append(f".hwtx not treated as hwpx: source_doc_type={d.get('source_doc_type')}")
        else:
            print(f"  .hwtx auto-routed to hwpx reader; source_doc_type={d.get('source_doc_type')}")
        unregister_template(reg2["template_id"])

    # ── 5. magic-byte validation on corrupted/truncated base64 ───────────
    print("\n[5] magic-byte validation catches transit corruption")
    # Truncate the base64 to simulate transport loss
    truncated_b64 = b64[: len(b64) // 2]
    r = describe_template(template_b64=truncated_b64, template_filename="x.hwpx")
    print(f"  truncated b64 → status={r.get('status')}")
    if r.get("status") != "bad_argument":
        failures.append(f"truncated b64 should give bad_argument, got {r.get('status')}")
    elif "ZIP" not in (r.get("error") or "") and "PDF" not in (r.get("error") or ""):
        failures.append(f"error message should mention ZIP/PDF magic: {r.get('error')}")
    else:
        print(f"  clean rejection: {r.get('error')[:80]}...")

    # Wrong-format bytes for declared extension
    r2 = describe_template(template_b64=base64.b64encode(b"not a zip file").decode(),
                            template_filename="x.hwpx")
    print(f"  wrong-format bytes → status={r2.get('status')}")
    if r2.get("status") != "bad_argument":
        failures.append(f"wrong-format should give bad_argument, got {r2.get('status')}")

    # ── 6. convert_to_hwpx as no-op-edit wrapper ─────────────────────────
    print("\n[6] convert_to_hwpx convenience tool")
    r = convert_to_hwpx(template_b64=b64, template_filename="cooperation.hwtx",
                        return_output_bytes=True)
    print(f"  convert .hwtx → status={r.get('status')} size={r.get('output_size_bytes')} "
          f"cleared={r.get('linesegarray_sections_cleared')}")
    if r.get("status") != "ok":
        failures.append(f"convert_to_hwpx failed: {r}")
    elif not r.get("output_b64"):
        failures.append("convert_to_hwpx didn't return output_b64")
    else:
        decoded = base64.b64decode(r["output_b64"])
        if not decoded.startswith(b"PK"):
            failures.append("convert_to_hwpx output isn't a valid ZIP/HWPX")

    print()
    if failures:
        print(f"FAIL ({len(failures)} issues):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS — v2 improvements all green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
