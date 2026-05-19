"""Translate document-processor / OS exceptions into structured MCP
responses. Centralized so a chatbot never sees a transport-level crash
or stack trace string."""

from __future__ import annotations

from typing import Any

from .responses import error


class ExceptionClassifier:
    @staticmethod
    def to_response(exc: BaseException) -> dict[str, Any]:
        msg = str(exc)
        exc_type = type(exc).__name__

        if "Java runtime not found" in msg or ("jvm" in msg.lower() and "find" in msg.lower()):
            return error(
                "format_requires_java",
                msg,
                "This input format needs a Java 11+ runtime on PATH (used by "
                "document-processor's PDF / binary-HWP backends). Either install "
                "Adoptium Temurin 17+ and re-try, or ask the user to provide the "
                "same content in DOCX or HWPX which do not need Java.",
            )
        if isinstance(exc, FileNotFoundError):
            return error(
                "file_error",
                f"{exc_type}: {msg}",
                "A file referenced by document-processor was missing. Verify "
                "template_path and that the directory is writable.",
            )
        if isinstance(exc, PermissionError):
            return error(
                "permission_error",
                f"{exc_type}: {msg}",
                "Underlying file is locked or read-only. Close any editor "
                "(Word / Hangul) that has the file open and retry.",
            )
        return error(
            "runtime_error",
            f"{exc_type}: {msg}",
            "Underlying document library raised an unexpected error. Check the "
            "error message; if it persists, try a different format (DOCX/HWPX "
            "are the most reliably supported) or a smaller batch.",
        )
