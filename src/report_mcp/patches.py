"""Library defense for document-processor's cumulative `_edited` filename
bug.

The library's `_apply_mixed_edits_to_native_source` regenerates
"X_edited.hwpx" each iteration and feeds it back as the next source_name,
so after ~30 edits the path "X_edited_edited_..._edited.hwpx" busts
Windows MAX_PATH. We patched the call site directly (see
patches/document_processor_edited_cumulation.patch), and this monkey-patch
makes the helper idempotent so the fix survives `pip install --upgrade`.
"""

from __future__ import annotations

from pathlib import Path

import document_processor.api as _dp_api

_applied = False


def apply_library_patches() -> None:
    """Make `_default_output_filename` idempotent against repeated
    `_edited` suffixes. Safe to call multiple times."""
    global _applied
    if _applied:
        return
    orig = _dp_api._default_output_filename

    def patched(*, source_name, source_doc_type):
        if source_name:
            stem = Path(source_name).stem
            ext = Path(source_name).suffix
            while stem.endswith("_edited"):
                stem = stem[: -len("_edited")]
            source_name = stem + ext
        return orig(source_name=source_name, source_doc_type=source_doc_type)

    _dp_api._default_output_filename = patched
    _applied = True
