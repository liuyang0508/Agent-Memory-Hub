"""Deterministic, metadata-only supersession candidate ranking."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from agent_brain.contracts.memory_enums import MemoryType, memory_enum_value
from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.memory.context.context_firewall_rules import REVIEW_REQUIRED_TAGS

_SOURCE_REF_FIELDS = ("commits", "files", "resources")
_LOCATOR_SCAN_LIMIT = 512
_ASCII_TERM_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{2,}")
_CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]{2,}")
_CLOSURE_RE = re.compile(
    r"(?:\b(?:closed|fixed|obsolete|replaced|replaces|resolved|superseded|supersedes)\b"
    r"|不再适用|修复完成|问题已解决|已修复|已关闭|已取代|已替代|取代了|替代了)"
)
_TOPIC_STOPWORDS = {
    "after",
    "before",
    "closed",
    "current",
    "decision",
    "fact",
    "fixed",
    "handoff",
    "issue",
    "latest",
    "memory",
    "new",
    "newer",
    "obsolete",
    "old",
    "older",
    "pending",
    "replaced",
    "resolved",
    "signal",
    "state",
    "status",
    "superseded",
    "the",
    "this",
    "updated",
    "with",
    "之前",
    "关闭",
    "当前",
    "已修复",
    "已关闭",
    "已替代",
    "已解决",
    "已取代",
    "更新",
    "状态",
    "修复",
    "替代",
    "解决",
    "取代",
}
_CJK_TOPIC_STOPWORDS = (
    "已修复",
    "已关闭",
    "已替代",
    "已解决",
    "已取代",
    "关闭",
    "当前",
    "更新",
    "状态",
    "修复",
    "替代",
    "解决",
    "取代",
    "之前",
)


@dataclass(frozen=True)
class SupersessionCandidate:
    replacement_id: str
    score: float
    evidence_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "replacement_id": self.replacement_id,
            "score": self.score,
            "evidence_codes": list(self.evidence_codes),
        }


@dataclass(frozen=True)
class _RankedCandidate:
    result: SupersessionCandidate
    created_at: datetime


def rank_supersession_candidates(
    *,
    obsolete: MemoryItem,
    items: Iterable[MemoryItem],
    supersedes_edges: set[tuple[str, str]],
) -> list[SupersessionCandidate]:
    """Return at most three same-scope replacement suggestions.

    The function reads validated MemoryItem metadata only. It never reads item
    bodies, resolves detail URIs, mutates lifecycle state, or performs remote or
    model-assisted scoring.
    """
    if not isinstance(obsolete, MemoryItem):
        raise TypeError("obsolete must be a MemoryItem")
    ranked: list[_RankedCandidate] = []
    for candidate in items:
        if not isinstance(candidate, MemoryItem):
            raise TypeError("items must contain only MemoryItem instances")
        if not _is_valid_candidate(candidate, obsolete):
            continue
        result = _score_candidate(candidate, obsolete, supersedes_edges)
        if result.score <= 0:
            continue
        ranked.append(_RankedCandidate(result=result, created_at=candidate.created_at))

    ranked.sort(
        key=lambda candidate: (
            -candidate.result.score,
            -_utc_timestamp(candidate.created_at),
            candidate.result.replacement_id,
        )
    )
    return [candidate.result for candidate in ranked[:3]]


def _is_valid_candidate(candidate: MemoryItem, obsolete: MemoryItem) -> bool:
    return (
        candidate.id != obsolete.id
        and candidate.created_at > obsolete.created_at
        and candidate.tenant_id == obsolete.tenant_id
        and candidate.project == obsolete.project
        and memory_enum_value(candidate.type) == memory_enum_value(obsolete.type)
        and candidate.superseded_by is None
        and not (
            REVIEW_REQUIRED_TAGS
            & {tag.casefold() for tag in candidate.tags}
        )
    )


def _score_candidate(
    candidate: MemoryItem,
    obsolete: MemoryItem,
    supersedes_edges: set[tuple[str, str]],
) -> SupersessionCandidate:
    evidence: list[str] = []
    score = 0.0

    if (candidate.id, obsolete.id) in supersedes_edges:
        evidence.append("EXPLICIT_SUPERSEDES_EDGE")
        score = 1.0
    if obsolete.id in candidate.refs.mems:
        evidence.append("EXPLICIT_MEMORY_REF")
        score += 0.45
    if _has_shared_source_evidence(candidate, obsolete):
        evidence.append("SHARED_SOURCE_EVIDENCE")
        score += 0.25
    if _topic_tokens(candidate) & _topic_tokens(obsolete):
        evidence.append("TOPIC_OVERLAP")
        score += 0.20
    if _has_closure_language(candidate):
        evidence.append("CLOSURE_LANGUAGE")
        score += 0.10
    evidence.append("NEWER_ITEM")
    score += 0.05

    return SupersessionCandidate(
        replacement_id=candidate.id,
        score=round(min(score, 1.0), 2),
        evidence_codes=tuple(evidence),
    )


def _has_shared_source_evidence(candidate: MemoryItem, obsolete: MemoryItem) -> bool:
    return any(
        bool(set(getattr(candidate.refs, field)) & set(getattr(obsolete.refs, field)))
        for field in _SOURCE_REF_FIELDS
    )


def _topic_tokens(item: MemoryItem) -> set[str]:
    text = unicodedata.normalize("NFKC", " ".join([item.title, *item.tags])).casefold()
    tokens = {
        token
        for token in _ASCII_TERM_RE.findall(text)
        if token not in _TOPIC_STOPWORDS
    }
    cjk_text = text
    for stopword in _CJK_TOPIC_STOPWORDS:
        cjk_text = cjk_text.replace(stopword, " ")
    for run in _CJK_RUN_RE.findall(cjk_text):
        if run not in _TOPIC_STOPWORDS:
            tokens.add(run)
        tokens.update(
            pair
            for pair in (run[index:index + 2] for index in range(len(run) - 1))
            if pair not in _TOPIC_STOPWORDS
        )
    return tokens


def _has_closure_language(candidate: MemoryItem) -> bool:
    if memory_enum_value(candidate.type) != MemoryType.signal.value:
        return False
    locator = candidate.context_views.locator[:_LOCATOR_SCAN_LIMIT]
    metadata = unicodedata.normalize("NFKC", f"{candidate.summary}\n{locator}").casefold()
    return _CLOSURE_RE.search(metadata) is not None


def _utc_timestamp(value: datetime) -> float:
    return value.astimezone(timezone.utc).timestamp()


__all__ = [
    "SupersessionCandidate",
    "rank_supersession_candidates",
]
