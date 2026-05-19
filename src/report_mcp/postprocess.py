"""HWPX-specific post-processing after document-processor write-back.

document-processor edits text in the IR and writes back, but does NOT
recompute or clear the <hp:linesegarray> blocks inside each section XML.
Those blocks cache the Hangul viewer's line-break decisions for the
ORIGINAL text. After an edit, the cached segments point at the wrong
positions, and the viewer renders new text using stale line breaks —
typically crushing a multi-line replacement into a single overflowing
line, or splitting a single line at the wrong position.

The fix is to empty every linesegarray block: `<hp:linesegarray/>`.
The viewer then recomputes segmentation from the new text. This is a
zero-data-loss operation; the cache is regenerated on next open.

Probe `tests/probe_hwpx_postprocess.py` measures the defect (95 stale
caches in an edited HWPX) and validates this fix.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

_LINESEGARRAY_RE = re.compile(rb"<hp:linesegarray>.*?</hp:linesegarray>", re.DOTALL)


def clear_linesegarray_cache(hwpx_path: str) -> int:
    """Replace every non-empty <hp:linesegarray>...</hp:linesegarray> with
    <hp:linesegarray/> in every Contents/section*.xml inside the HWPX zip.

    Returns: number of section XMLs that contained stale caches and were
    rewritten. Zero means nothing needed cleanup (e.g. no edits, or the
    library happened to write empty caches).
    """
    p = Path(hwpx_path)
    if not p.is_file():
        return 0

    sections_changed: list[tuple[str, bytes]] = []
    other_files: list[tuple[str, bytes]] = []
    with zipfile.ZipFile(p, "r") as zf:
        for info in zf.infolist():
            data = zf.read(info.filename)
            if info.filename.startswith("Contents/section") and info.filename.endswith(".xml"):
                new_data, n = _LINESEGARRAY_RE.subn(b"<hp:linesegarray/>", data)
                if n > 0:
                    sections_changed.append((info.filename, new_data))
                else:
                    other_files.append((info.filename, data))
            else:
                other_files.append((info.filename, data))

    if not sections_changed:
        return 0

    # Atomic rewrite via sibling tempfile.
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf_out:
            for name, data in other_files:
                zf_out.writestr(name, data)
            for name, data in sections_changed:
                zf_out.writestr(name, data)
        tmp.replace(p)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return len(sections_changed)
