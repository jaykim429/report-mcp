"""Probe: does document-processor handle the three HWPX-specific
post-processing concerns raised by the proposal?
  - XML special chars (&, <, >, \", ')
  - <hp:linesegarray> stale-cache reset
  - Namespace declarations preserved on write-back
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from report_mcp.server import fill_and_save, list_template_targets

P = Path(__file__).parent
PROJECT_ROOT = P.parent
HWPX = next((PROJECT_ROOT / "output" / "templates").glob("*.hwpx"))

findings: list[tuple[str, str, str]] = []


def rec(s, pid, note=""):
    findings.append((s, pid, note))
    print(f"  {s:4s} {pid}: {note}")


def hwpx_section_xml_contents(path: Path) -> list[tuple[str, str]]:
    """Return (filename, decoded XML) for every section XML in the HWPX zip."""
    out = []
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            if name.startswith("Contents/section") and name.endswith(".xml"):
                out.append((name, zf.read(name).decode("utf-8", errors="replace")))
    return out


def probe_xml_escape(workdir: Path) -> None:
    print("\n[1] XML special chars in new_text — handled or corrupted?")
    src = workdir / "esc.hwpx"
    shutil.copy2(HWPX, src)
    out = workdir / "esc_out.hwpx"

    paras = list_template_targets(template_path=str(src), target_kinds=["paragraph"], limit=400).get("targets", [])
    date_t = next(t for t in paras if (t.get("current_text") or "").strip() == "2026. 03. 23.")

    # All five XML special chars in one new_text
    new_text = "AT&T <foo> \"R&D 'A'\" — 2026"
    r = fill_and_save(template_path=str(src), edits=[{
        "edit_type": "text", "target_kind": "paragraph",
        "target_id": date_t["target_id"],
        "expected_text_hash": date_t["text_hash"],
        "new_text": new_text,
    }], output_path=str(out))

    if r.get("status") != "ok":
        rec("FAIL", "esc.apply", f"{r.get('status')} {r.get('error')}")
        return

    # Reopen output, check that the text round-trips AND the XML is well-formed
    post = list_template_targets(template_path=str(out), target_kinds=["paragraph"], limit=400).get("targets", [])
    found = next((t for t in post if t["target_id"] == date_t["target_id"]), None)
    if found is None:
        rec("FAIL", "esc.lookup", "edited paragraph missing from output")
        return
    if found["current_text"].strip() != new_text:
        rec("FAIL", "esc.text", f"round-trip text mismatch: {found['current_text']!r} != {new_text!r}")
        return

    # Verify XML is valid: parse every section XML
    import xml.etree.ElementTree as ET
    parse_errors = []
    for name, xml in hwpx_section_xml_contents(out):
        try:
            ET.fromstring(xml)
        except ET.ParseError as exc:
            parse_errors.append(f"{name}: {exc}")
    if parse_errors:
        rec("FAIL", "esc.xml_invalid", "; ".join(parse_errors[:2]))
    else:
        rec("PASS", "esc.handled", "all 5 XML specials in new_text round-trip cleanly + XML stays valid")


def probe_linesegarray(workdir: Path) -> None:
    print("\n[2] linesegarray cache — stale after edit?")
    src = workdir / "lineseg.hwpx"
    shutil.copy2(HWPX, src)
    out = workdir / "lineseg_out.hwpx"

    paras = list_template_targets(template_path=str(src), target_kinds=["paragraph"], limit=400).get("targets", [])
    # Big text length change: replace a short date with a long sentence
    date_t = next(t for t in paras if (t.get("current_text") or "").strip() == "2026. 03. 23.")
    long_replacement = "이 단락은 의도적으로 긴 텍스트로 교체되었습니다 — 라인 분할이 재계산되어야 함"
    r = fill_and_save(template_path=str(src), edits=[{
        "edit_type": "text", "target_kind": "paragraph",
        "target_id": date_t["target_id"],
        "expected_text_hash": date_t["text_hash"],
        "new_text": long_replacement,
    }], output_path=str(out))
    if r.get("status") != "ok":
        rec("FAIL", "lineseg.apply", f"{r.get('status')}")
        return

    # Inspect the new section XML: does linesegarray still reference the original
    # short text's segmentation? If document-processor handles it, linesegarray
    # should either be empty (<hp:linesegarray/>) or recomputed.
    edited_sections = []
    stale_lineseg_blocks = 0
    for name, xml in hwpx_section_xml_contents(out):
        if long_replacement[:15] in xml:  # this section was edited
            edited_sections.append(name)
            # Look for <hp:linesegarray>...</hp:linesegarray> with content
            for m in re.finditer(r"<hp:linesegarray>(.*?)</hp:linesegarray>", xml, re.DOTALL):
                inner = m.group(1).strip()
                if inner:  # non-empty cache present
                    stale_lineseg_blocks += 1
    if not edited_sections:
        rec("WARN", "lineseg.notfound", "could not locate edited section in output")
        return
    if stale_lineseg_blocks > 0:
        rec("WARN", "lineseg.stale",
            f"{stale_lineseg_blocks} non-empty linesegarray cache(s) remain in edited section(s) — "
            "may cause text-overflow rendering in Hangul. Library does NOT auto-clear.")
    else:
        rec("PASS", "lineseg.clean",
            "no stale linesegarray cache after edit — library handles it")


def probe_namespaces(workdir: Path) -> None:
    print("\n[3] namespace declarations on write-back")
    src = workdir / "ns.hwpx"
    shutil.copy2(HWPX, src)
    out = workdir / "ns_out.hwpx"

    paras = list_template_targets(template_path=str(src), target_kinds=["paragraph"], limit=400).get("targets", [])
    date_t = next(t for t in paras if (t.get("current_text") or "").strip() == "2026. 03. 23.")
    r = fill_and_save(template_path=str(src), edits=[{
        "edit_type": "text", "target_kind": "paragraph",
        "target_id": date_t["target_id"],
        "expected_text_hash": date_t["text_hash"],
        "new_text": "2026. 05. 19.",
    }], output_path=str(out))
    if r.get("status") != "ok":
        rec("FAIL", "ns.apply", f"{r.get('status')}")
        return

    pre_sections = dict(hwpx_section_xml_contents(src))
    post_sections = dict(hwpx_section_xml_contents(out))

    # Extract xmlns declarations from the root <hs:sec ... > or equivalent
    def extract_namespaces(xml: str) -> set[str]:
        return set(re.findall(r'xmlns(?::\w+)?="[^"]+"', xml[:2000]))

    issues = []
    for name, pre_xml in pre_sections.items():
        post_xml = post_sections.get(name)
        if post_xml is None:
            continue
        pre_ns = extract_namespaces(pre_xml)
        post_ns = extract_namespaces(post_xml)
        missing = pre_ns - post_ns
        if missing:
            issues.append(f"{name} dropped: {missing}")
    if issues:
        rec("FAIL", "ns.dropped", "; ".join(issues[:2]))
    else:
        rec("PASS", "ns.preserved", "every section preserves its xmlns declarations after edit")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="rmcp_pp_"))
    for fn in [probe_xml_escape, probe_linesegarray, probe_namespaces]:
        try:
            fn(tmp)
        except Exception as exc:
            rec("FAIL", f"crash.{fn.__name__}", f"{type(exc).__name__}: {exc}")
    print()
    p = sum(1 for s, *_ in findings if s == "PASS")
    w = sum(1 for s, *_ in findings if s == "WARN")
    f = sum(1 for s, *_ in findings if s == "FAIL")
    print(f"PASS={p}  WARN={w}  FAIL={f}")
    return 1 if f else 0


if __name__ == "__main__":
    sys.exit(main())
