"""Conservative post-retrieval validation of explicit query constraints."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence
from dataclasses import replace
from typing import Literal, Protocol

from agent_brain.memory.context.query_constraints import QueryConstraint
from agent_brain.memory.recall.retrieval_types import RetrievedItem

ConstraintAlignment = Literal["aligned", "conflict", "unknown"]

_CJK_CHAR_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U0002fa1f]")
_CJK_NEGATIVE_MARKERS = (
    "不需要",
    "不建议",
    "尚未",
    "还未",
    "并未",
    "从未",
    "未能",
    "没有",
    "无需",
    "不必",
    "不要",
    "不应",
    "不能",
    "不可",
    "禁止",
    "避免",
    "绝不",
    "不用",
    "勿",
    "没",
    "未",
)
_CJK_POSITIVE_MARKERS = (
    "必须",
    "需要",
    "应该",
    "应当",
    "务必",
    "只能",
    "要求",
    "将会",
)
_EN_ARTICLES = frozenset({"a", "an", "the"})
_EN_NEGATIVE_PREFIXES = (
    ("do", "not"),
    ("does", "not"),
    ("did", "not"),
    ("must", "not"),
    ("should", "not"),
    ("cannot",),
    ("can't",),
    ("never",),
    ("without",),
    ("avoid",),
    ("avoids",),
    ("avoided",),
)
_EN_POSITIVE_PREFIXES = (
    ("must",),
    ("should",),
    ("need", "to"),
    ("needs", "to"),
    ("required", "to"),
    ("requires",),
    ("will",),
)


class _TextIndex(Protocol):
    def get_texts(self, item_ids: list[str]) -> dict[str, str]: ...


def apply_constraint_alignment(
    index: _TextIndex,
    candidates: list[RetrievedItem],
    constraints: Sequence[QueryConstraint],
    *,
    aligned_multiplier: float = 1.08,
) -> list[RetrievedItem]:
    """Filter only explicit contradictions and modestly reward alignment.

    Unknown mentions are deliberately retained.  For example, a candidate
    containing only ``停机`` is not rejected for the query ``不要停机``; it must
    contain an explicit opposite assertion such as ``需要停机``.
    """

    if not candidates or not constraints:
        return candidates
    try:
        texts = index.get_texts([candidate.id for candidate in candidates])
    except (AttributeError, OSError, RuntimeError, sqlite3.Error):
        return candidates
    if not texts:
        return candidates

    aligned: list[RetrievedItem] = []
    for candidate in candidates:
        text = texts.get(candidate.id)
        if not text:
            aligned.append(candidate)
            continue
        result = assess_constraint_alignment(text, constraints)
        if result == "conflict":
            continue
        if result == "aligned":
            candidate = replace(candidate, score=candidate.score * aligned_multiplier)
        aligned.append(candidate)
    return sorted(aligned, key=lambda candidate: (-candidate.score, candidate.id))


def assess_constraint_alignment(
    text: str,
    constraints: Sequence[QueryConstraint],
) -> ConstraintAlignment:
    """Classify a candidate using only explicit, local assertion patterns."""

    saw_alignment = False
    for constraint in constraints:
        polarities = _asserted_polarities(text, constraint.predicate)
        if not polarities:
            continue
        if len(polarities) > 1:
            # A memory that discusses both alternatives needs semantic
            # disambiguation; deterministic rules must not hard-filter it.
            continue
        asserted = next(iter(polarities))
        if asserted != constraint.polarity:
            return "conflict"
        saw_alignment = True
    return "aligned" if saw_alignment else "unknown"


def _asserted_polarities(text: str, predicate: str) -> set[bool]:
    if _contains_cjk(predicate):
        return _cjk_asserted_polarities(text, predicate)
    return _english_asserted_polarities(text, predicate)


def _cjk_asserted_polarities(text: str, predicate: str) -> set[bool]:
    compact_text = re.sub(r"\s+", "", text)
    compact_predicate = re.sub(r"\s+", "", predicate)
    if not compact_predicate or compact_predicate not in compact_text:
        return set()

    gap = r"[\u3400-\u4dbf\u4e00-\u9fff]{0,8}"
    target = re.escape(compact_predicate)
    negative = any(
        re.search(re.escape(marker) + gap + target, compact_text)
        for marker in _CJK_NEGATIVE_MARKERS
    )
    positive = any(
        re.search(re.escape(marker) + gap + target, compact_text)
        for marker in _CJK_POSITIVE_MARKERS
    )
    polarities: set[bool] = set()
    if negative:
        polarities.add(False)
    if positive:
        polarities.add(True)
    return polarities


def _english_asserted_polarities(text: str, predicate: str) -> set[bool]:
    text_words = _english_words(text)
    predicate_words = _english_words(predicate)
    if not predicate_words:
        return set()
    positions = _subsequence_positions(text_words, predicate_words)
    polarities: set[bool] = set()
    for position in positions:
        prefix = tuple(text_words[max(0, position - 4):position])
        if any(_endswith(prefix, marker) for marker in _EN_NEGATIVE_PREFIXES):
            polarities.add(False)
        if any(_endswith(prefix, marker) for marker in _EN_POSITIVE_PREFIXES):
            polarities.add(True)
    return polarities


def _english_words(value: str) -> list[str]:
    return [
        word
        for word in re.findall(r"[a-z0-9_+./'-]+", value.casefold())
        if word not in _EN_ARTICLES
    ]


def _subsequence_positions(words: list[str], target: list[str]) -> list[int]:
    width = len(target)
    return [
        index
        for index in range(len(words) - width + 1)
        if words[index:index + width] == target
    ]


def _endswith(values: tuple[str, ...], suffix: tuple[str, ...]) -> bool:
    return len(values) >= len(suffix) and values[-len(suffix):] == suffix


def _contains_cjk(value: str) -> bool:
    return _CJK_CHAR_RE.search(value) is not None


__all__ = [
    "ConstraintAlignment",
    "apply_constraint_alignment",
    "assess_constraint_alignment",
]
