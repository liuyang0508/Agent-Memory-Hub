"""Unicode script helpers for search tokenization."""
from __future__ import annotations

import unicodedata


def is_cjk_search_char(ch: str) -> bool:
    """Return True for CJK-family characters that should index as single tokens."""
    if not ch or ch.isascii():
        return False
    name = unicodedata.name(ch, "")
    return (
        name.startswith("CJK UNIFIED IDEOGRAPH")
        or name.startswith("CJK COMPATIBILITY IDEOGRAPH")
        or name.startswith("HIRAGANA")
        or name.startswith("KATAKANA")
        or name.startswith("HANGUL SYLLABLE")
        or name.startswith("HANGUL JAMO")
        or name.startswith("HANGUL COMPATIBILITY JAMO")
    )


def is_unicode_search_char(ch: str) -> bool:
    """Return True for Unicode letters, numbers, and combining marks."""
    if not ch or ch.isascii():
        return False
    return unicodedata.category(ch).startswith(("L", "N", "M"))


__all__ = ["is_cjk_search_char", "is_unicode_search_char"]
