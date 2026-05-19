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
import os
import platform
import tempfile
from pathlib import Path
from typing import Any

from .responses import bad_argument, error


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
    ) -> tuple[ResolvedInput | None, dict[str, Any] | None]:
        """Returns (resolved_input, error_response). Exactly one is None."""

        if template_path and template_b64:
            return None, bad_argument(
                "Provide either template_path or template_b64, not both.",
                "Choose one mode and call again. template_path is for files on "
                "the MCP server's machine; template_b64 is for callers that "
                "live in a different filesystem (e.g. a sandboxed chatbot).",
            )

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
            ext = Path(template_filename).suffix.lower()
            if ext not in {".docx", ".hwp", ".hwpx", ".pdf"}:
                return None, bad_argument(
                    f"Unsupported template_filename extension: {ext!r}",
                    "template_filename must end in .docx, .hwp, .hwpx, or .pdf "
                    "so the server can dispatch the right format reader.",
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
