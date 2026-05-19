"""Resolve template input from either a local path or inline base64 bytes.

When the MCP server runs in a different filesystem than the chatbot session
(Claude Desktop on Windows vs. a hosted Linux sandbox), passing a file path
fails because the file doesn't exist on the server's side. `TemplateInputResolver`
lets every tool accept either:

  - `template_path`: a file on the MCP server's machine (fast, no copy)
  - `template_b64` + `template_filename`: base64-encoded bytes plus the
    original filename so the writer can dispatch the right format

It returns a `ResolvedInput` context manager that owns any temp file made
from decoded bytes and cleans it up on exit.
"""

from __future__ import annotations

import base64
import binascii
import io
import os
import platform
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from .responses import bad_argument, error
from .session import lookup_cached_template


class ResolvedInput:
    """Path to a template file, possibly backed by a temp file written from
    inline bytes. Use as a context manager so the temp file is cleaned up
    after the tool returns."""

    def __init__(self, path: str, _temp: Path | None = None):
        self.path = path
        self._temp = _temp

    def __enter__(self) -> "ResolvedInput":
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._temp is not None:
            try:
                self._temp.unlink(missing_ok=True)
            except OSError:
                pass


class TemplateInputResolver:
    @staticmethod
    def resolve(
        template_path: str | None,
        template_b64: str | None,
        template_filename: str | None,
        template_id: str | None = None,
    ) -> tuple[ResolvedInput | None, dict[str, Any] | None]:
        """Returns (resolved_input, error_response). Exactly one is None.

        Three input modes (mutually exclusive):
          - template_id:   resolve via the server-side session cache (fastest,
                           assumes register_template was called earlier).
          - template_path: file on the MCP server's machine.
          - template_b64 + template_filename: inline bytes (writes a temp file).
        """

        provided = sum(bool(x) for x in (template_id, template_path, template_b64))
        if provided > 1:
            return None, bad_argument(
                "Provide exactly one of template_id, template_path, or template_b64.",
                "Choose one input mode and call again. template_id is the most "
                "efficient when reusing a template across multiple tool calls.",
            )

        if template_id:
            cached_path = lookup_cached_template(template_id)
            if cached_path is None:
                return None, error(
                    "not_found",
                    f"No cached template with template_id={template_id!r}.",
                    "The template_id is unknown or expired. Call register_template "
                    "again to upload the file and obtain a fresh id.",
                )
            return ResolvedInput(cached_path, _temp=None), None

        if template_path:
            p = Path(template_path)
            if not p.is_file():
                return None, error(
                    "not_found",
                    f"Template not found: {template_path} "
                    f"(server is {platform.system()} {platform.release()}, "
                    f"resolved to {p!s}, cwd={os.getcwd()})",
                    "Verify the absolute path exists on the MCP server's "
                    "machine. If the chatbot session lives in a different "
                    "filesystem (e.g. a Linux sandbox while the server runs "
                    "on Windows), use template_b64 + template_filename "
                    "instead of template_path.",
                )
            return ResolvedInput(str(p), _temp=None), None

        if template_b64:
            if not template_filename:
                return None, bad_argument(
                    "template_filename is required when template_b64 is used.",
                    "Provide the original filename (e.g. 'PoC.hwpx') so the "
                    "server knows the format. Extension is used for format "
                    "dispatch — must be one of .docx, .hwp, .hwpx, .pdf.",
                )
            try:
                data = base64.b64decode(template_b64, validate=True)
            except (binascii.Error, ValueError) as exc:
                return None, bad_argument(
                    f"Invalid base64 in template_b64: {exc}",
                    "template_b64 must be a standard base64-encoded string of "
                    "the raw file bytes. No URL-safe variant; no whitespace "
                    "padding errors.",
                )
            if not data:
                return None, bad_argument(
                    "template_b64 decoded to zero bytes.",
                    "Verify the chatbot encoded the file contents, not an "
                    "empty buffer.",
                )
            # Sanity-check the decoded bytes BEFORE handing to document-processor
            # (which crashes with cryptic "negative seek value" errors deep
            # inside zipfile when bytes are truncated in transit).
            ext_lower = Path(template_filename).suffix.lower()
            zip_like = ext_lower in {".docx", ".hwpx", ".hwtx"}
            if zip_like:
                if not data.startswith(b"PK\x03\x04"):
                    return None, bad_argument(
                        f"Decoded bytes are not a valid ZIP archive. Expected "
                        f"ZIP magic 'PK\\x03\\x04' at offset 0; got {data[:4]!r}. "
                        f"Total decoded length: {len(data):,} bytes.",
                        "The base64 likely got mangled in transit. Re-encode "
                        "the original file (no extra whitespace, no URL-safe "
                        "variant) and retry.",
                    )
                # Catch tail truncation: a valid PK header but missing/corrupt
                # End of Central Directory. document-processor's zipfile reader
                # raises 'negative seek value' otherwise.
                try:
                    with zipfile.ZipFile(io.BytesIO(data)) as zf:
                        zf.namelist()  # forces EOCD parsing
                except (zipfile.BadZipFile, OSError, ValueError) as exc:
                    return None, bad_argument(
                        f"Decoded bytes have a ZIP header but a corrupted End "
                        f"of Central Directory ({type(exc).__name__}: {exc}). "
                        f"Total decoded length: {len(data):,} bytes — likely "
                        "truncated in transit.",
                        "Re-encode the full file and retry. If the transport "
                        "keeps dropping the tail, register_template the file "
                        "once and reuse template_id, or copy the file locally "
                        "and pass template_path.",
                    )
            if ext_lower == ".pdf" and not data.startswith(b"%PDF-"):
                return None, bad_argument(
                    f"Decoded bytes are not a valid PDF. Expected '%PDF-' at "
                    f"offset 0; got {data[:5]!r}. Total decoded length: "
                    f"{len(data):,} bytes.",
                    "The base64 likely got truncated. Re-encode and retry.",
                )
            ext = Path(template_filename).suffix.lower()
            # .hwtx is the Hancom HWPX template format (same ZIP+XML structure
            # as .hwpx, different MIME). Rename to .hwpx so document-processor
            # dispatches the HWPX reader.
            if ext == ".hwtx":
                ext = ".hwpx"
            if ext not in {".docx", ".hwp", ".hwpx", ".pdf"}:
                return None, bad_argument(
                    f"Unsupported template_filename extension: {ext!r}",
                    "template_filename must end in .docx, .hwp, .hwpx, .hwtx, "
                    "or .pdf so the server can dispatch the right format reader.",
                )
            fd, tmp_name = tempfile.mkstemp(prefix="rmcp_in_", suffix=ext)
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
            except OSError as exc:
                Path(tmp_name).unlink(missing_ok=True)
                return None, error(
                    "file_error",
                    f"Could not write temp file for inline bytes: {exc}",
                    "Check temp directory permissions / disk space.",
                )
            return ResolvedInput(tmp_name, _temp=Path(tmp_name)), None

        return None, bad_argument(
            "No template provided.",
            "Pass either template_path (a file on the MCP server's machine) "
            "or template_b64 + template_filename (inline base64 bytes).",
        )
