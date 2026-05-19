"""Server-side template cache so chatbots don't re-upload the same base64
payload on every tool call.

Typical workflow without caching: describe + list + inspect + fill = 4
uploads of the same 290KB base64 = ~1.2MB of stdio traffic per template.
With caching: 1 upload + 4 short id-based calls.

Entries expire after `_TTL_SECONDS` to bound memory; the cache is also
size-capped at `_MAX_ENTRIES` (oldest-first eviction when full).
"""

from __future__ import annotations

import base64
import binascii
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from .responses import bad_argument, error, ok

_TTL_SECONDS = 3600  # 1 hour
_MAX_ENTRIES = 50


class _Entry:
    __slots__ = ("path", "size", "filename", "created_at")

    def __init__(self, path: str, size: int, filename: str):
        self.path = path
        self.size = size
        self.filename = filename
        self.created_at = time.time()


class TemplateSessionCache:
    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    # ── public API ────────────────────────────────────────────────────────

    def register(self, template_b64: str, template_filename: str) -> dict[str, Any]:
        if not template_b64:
            return bad_argument(
                "template_b64 is required.",
                "Provide the base64-encoded file bytes.",
            )
        if not template_filename:
            return bad_argument(
                "template_filename is required.",
                "Provide the original filename (e.g. 'PoC.hwpx') so the "
                "server knows the format.",
            )
        ext = Path(template_filename).suffix.lower()
        if ext == ".hwtx":
            ext = ".hwpx"
        if ext not in {".docx", ".hwp", ".hwpx", ".pdf"}:
            return bad_argument(
                f"Unsupported template_filename extension: {ext!r}",
                "template_filename must end in .docx, .hwp, .hwpx, .hwtx, "
                "or .pdf.",
            )
        try:
            data = base64.b64decode(template_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            return bad_argument(
                f"Invalid base64: {exc}",
                "template_b64 must be a standard base64-encoded string.",
            )
        if not data:
            return bad_argument(
                "template_b64 decoded to zero bytes.",
                "Verify the chatbot encoded the file contents.",
            )

        self._evict_expired()
        with self._lock:
            if len(self._entries) >= _MAX_ENTRIES:
                self._drop_oldest_locked()

        fd, tmp_name = tempfile.mkstemp(prefix="rmcp_cache_", suffix=ext)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
        except OSError as exc:
            Path(tmp_name).unlink(missing_ok=True)
            return error(
                "file_error",
                f"Could not write cache file: {exc}",
                "Check temp directory permissions / disk space.",
            )

        template_id = secrets.token_hex(16)
        with self._lock:
            self._entries[template_id] = _Entry(tmp_name, len(data), template_filename)

        return ok(
            template_id=template_id,
            size_bytes=len(data),
            filename=template_filename,
            expires_in_seconds=_TTL_SECONDS,
        )

    def lookup_path(self, template_id: str) -> str | None:
        self._evict_expired()
        with self._lock:
            e = self._entries.get(template_id)
            return e.path if e else None

    def unregister(self, template_id: str) -> dict[str, Any]:
        with self._lock:
            dropped = self._drop_entry_locked(template_id)
        if dropped:
            return ok(template_id=template_id, freed=True)
        return error(
            "not_found",
            f"No cached template with id {template_id!r}.",
            "Either the id was wrong, the template already expired (TTL "
            f"{_TTL_SECONDS}s), or it was already unregistered.",
        )

    # ── internals ─────────────────────────────────────────────────────────

    def _drop_entry_locked(self, template_id: str) -> bool:
        e = self._entries.pop(template_id, None)
        if e is None:
            return False
        try:
            Path(e.path).unlink(missing_ok=True)
        except OSError:
            pass
        return True

    def _drop_oldest_locked(self) -> None:
        if not self._entries:
            return
        oldest_id = min(self._entries, key=lambda i: self._entries[i].created_at)
        self._drop_entry_locked(oldest_id)

    def _evict_expired(self) -> None:
        now = time.time()
        with self._lock:
            expired = [
                tid for tid, e in self._entries.items()
                if now - e.created_at > _TTL_SECONDS
            ]
            for tid in expired:
                self._drop_entry_locked(tid)


_global_cache = TemplateSessionCache()


def register_template_in_cache(template_b64: str, template_filename: str) -> dict[str, Any]:
    return _global_cache.register(template_b64, template_filename)


def lookup_cached_template(template_id: str) -> str | None:
    return _global_cache.lookup_path(template_id)


def unregister_template_from_cache(template_id: str) -> dict[str, Any]:
    return _global_cache.unregister(template_id)
