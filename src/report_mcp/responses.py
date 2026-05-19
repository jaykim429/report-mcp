"""Standard MCP tool response shapes.

Every tool returns a dict with at least `status`. On non-ok results,
`recovery_hint` tells the calling chatbot what to do next. Keeping the
factory in one place avoids 15+ near-identical dict literals scattered
across the codebase.
"""

from __future__ import annotations

from typing import Any


def ok(**extras: Any) -> dict[str, Any]:
    return {"status": "ok", **extras}


def error(status: str, message: str, hint: str, **extras: Any) -> dict[str, Any]:
    return {"status": status, "error": message, "recovery_hint": hint, **extras}


def not_found(path: str) -> dict[str, Any]:
    return error(
        "not_found",
        f"Template not found: {path}",
        "Verify the absolute path. The file must exist before any other "
        "report-mcp tool can be called.",
    )


def bad_argument(message: str, hint: str, **extras: Any) -> dict[str, Any]:
    return error("bad_argument", message, hint, **extras)
