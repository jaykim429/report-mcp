"""East Asian Width-aware length guardrail.

Pure code-point counting under-counts overflow risk for Korean/Japanese/
Chinese text by ~2× (CJK glyphs occupy 2 display cells). We measure in
display cells and let chatbots gate replacement length against a 20%
headroom (with a +10-cell floor for short labels).
"""

from __future__ import annotations

import unicodedata
from typing import Any


class LengthGuardrail:
    HEADROOM = 1.2
    FLOOR = 10

    @staticmethod
    def display_width(text: str) -> int:
        """Visual cell count: CJK/fullwidth=2, ASCII/Latin=1."""
        return sum(
            2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
            for ch in text
        )

    @classmethod
    def cap(cls, original_width: int) -> int:
        return max(int(original_width * cls.HEADROOM), original_width + cls.FLOOR)

    @classmethod
    def annotate(cls, items: list[dict[str, Any]], text_key: str) -> None:
        """In-place: attach char_count / display_width / max_recommended_chars."""
        for item in items:
            text = item.get(text_key) or ""
            dw = cls.display_width(text)
            item["char_count"] = len(text)
            item["display_width"] = dw
            item["max_recommended_chars"] = cls.cap(dw)

    @classmethod
    def warnings(
        cls,
        targets: list[dict[str, Any]],
        edits: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        by_id = {t["target_id"]: t for t in targets}
        out: list[dict[str, Any]] = []
        for e in edits:
            if e.get("edit_type") != "text":
                continue
            tgt = by_id.get(e.get("target_id"))
            if tgt is None:
                continue
            orig_text = tgt.get("current_text") or ""
            new_text = e.get("new_text") or ""
            orig_w = cls.display_width(orig_text)
            new_w = cls.display_width(new_text)
            cap = cls.cap(orig_w)
            if new_w > cap:
                out.append({
                    "target_id": e["target_id"],
                    "target_kind": tgt.get("target_kind"),
                    "original_display_width": orig_w,
                    "max_recommended_width": cap,
                    "new_display_width": new_w,
                    "overflow_cells": new_w - cap,
                    "original_chars": len(orig_text),
                    "new_chars": len(new_text),
                    "hint": "Replacement exceeds the container's design width; "
                            "expect wrap, overflow, or visual overlap. CJK glyphs "
                            "count as 2 cells. Shorten new_text and retry.",
                })
        return out
