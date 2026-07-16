from __future__ import annotations

import re

from agent_brain.memory.recall.query_synonyms import _expand_with_synonyms, _extract_words
from agent_brain.memory.recall.query_tokens import _tokenize_mixed
from agent_brain.platform.indexing.text_scripts import (
    is_cjk_search_char,
    is_unicode_search_char,
)

_ASCII_TERM_RE = re.compile(r"[a-zA-Z0-9_]+")


def expand_query(query: str, use_or: bool = True, synonyms: bool = True) -> str:
    """Build an FTS5 safe query from free-form text with optional synonym expansion.

    Always quotes tokens to prevent FTS5 syntax injection (colons,
    operators).  With *use_or=True* (default), joins with OR for fuzzy
    recall; with *use_or=False*, joins with AND for strict matching.
    When *synonyms=True*, expands known abbreviations and their synonyms.
    """
    groups = _token_groups(query)
    if not groups:
        return '""'
    tokens = [token for group in groups for token in group]
    if synonyms:
        original = {token.lower() for token in tokens}
        for token in _expand_with_synonyms(tokens, raw_query=query):
            if token.lower() in original:
                continue
            groups.append([token])
    seen: set[str] = set()
    deduped = []
    for group in groups:
        key = "\x1f".join(token.lower() for token in group)
        if key not in seen:
            seen.add(key)
            deduped.append(group)
    escaped = [_format_group(group, parenthesize=use_or and len(deduped) > 1) for group in deduped]
    joiner = " OR " if use_or else " "
    return joiner.join(escaped)


def _token_groups(query: str) -> list[list[str]]:
    groups: list[list[str]] = []
    for raw in _raw_terms(query):
        if raw.isascii():
            tokens = _tokenize_mixed(raw)
        elif all(is_cjk_search_char(character) for character in raw):
            tokens = list(raw)
        else:
            tokens = [raw]
        if tokens:
            groups.append(tokens)
    return groups


def _raw_terms(query: str) -> list[str]:
    terms: list[str] = []
    current: list[str] = []
    current_kind: str | None = None
    for ch in query:
        kind = (
            "ascii"
            if _ASCII_TERM_RE.fullmatch(ch)
            else "cjk"
            if is_cjk_search_char(ch)
            else "unicode"
            if is_unicode_search_char(ch)
            else None
        )
        if kind is None:
            if current:
                terms.append("".join(current))
                current = []
                current_kind = None
            continue
        if current_kind is not None and kind != current_kind:
            terms.append("".join(current))
            current = []
        current.append(ch)
        current_kind = kind
    if current:
        terms.append("".join(current))
    return terms


def _format_group(group: list[str], *, parenthesize: bool) -> str:
    phrase = " ".join(f'"{token}"' for token in group)
    if parenthesize and len(group) > 1:
        return f"({phrase})"
    return phrase


__all__ = [
    "_expand_with_synonyms",
    "_extract_words",
    "_tokenize_mixed",
    "expand_query",
]
