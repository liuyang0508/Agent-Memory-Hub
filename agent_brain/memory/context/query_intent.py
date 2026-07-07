"""Small intent helpers for prompt-time memory recall gating."""

from __future__ import annotations

import re

_FILE_OR_MODULE_RE = re.compile(
    r"(?<![\w.-])(?:[A-Za-z0-9_./-]+/)?[A-Za-z_][A-Za-z0-9_./-]*\."
    r"[A-Za-z0-9]{1,12}"
    r"(?![\w.-])",
    re.IGNORECASE,
)


def file_or_module_terms(prompt: str) -> list[str]:
    """Return path-like or file-like anchors in prompt order."""
    seen: set[str] = set()
    terms: list[str] = []
    for match in _FILE_OR_MODULE_RE.finditer(prompt):
        term = match.group(0).strip(".,:;，。；：")
        lowered = term.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        terms.append(lowered)
    return terms


def recall_domain_terms(prompt: str) -> list[str]:
    """Return no policy terms.

    Kept for compatibility with older imports. Prompt-time auto injection no
    longer carries a hand-maintained domain vocabulary; precise recall must be
    anchored by metadata, a file/module locator, or structural token evidence.
    """
    return []


def has_recall_domain_anchor(terms: list[str]) -> bool:
    """Return false; domain vocabularies are not a prompt-injection anchor."""
    return False


def weak_intent_without_anchor(prompt: str, anchors: list[str]) -> bool:
    """Return whether an unanchored prompt is too small to justify injection."""
    if anchors:
        return False
    normalized = re.sub(r"[\s:：,，.。?？!！/\\()\[\]【】「」『』_-]+", "", prompt.lower())
    if not normalized:
        return True
    has_ascii = any(ch.isascii() and ch.isalnum() for ch in normalized)
    if has_ascii:
        return False
    return len(normalized) <= 3


__all__ = [
    "file_or_module_terms",
    "has_recall_domain_anchor",
    "recall_domain_terms",
    "weak_intent_without_anchor",
]
