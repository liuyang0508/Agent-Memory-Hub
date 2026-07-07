from __future__ import annotations

import re


def _tokenize_mixed(text: str) -> list[str]:
    """Split mixed CJK/ASCII text into FTS5-compatible tokens."""
    tokens: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if "一" <= ch <= "鿿" or "㐀" <= ch <= "䶿":
            tokens.append(ch)
            i += 1
        elif re.match(r"[a-zA-Z0-9]", ch):
            j = i
            while j < len(text) and re.match(r"[a-zA-Z0-9_]", text[j]):
                j += 1
            tokens.append(text[i:j])
            i = j
        else:
            i += 1
    return tokens


__all__ = ["_tokenize_mixed"]
