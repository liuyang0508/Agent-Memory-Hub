"""Deterministic, metadata-only supersession candidate ranking."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, TypeVar

from agent_brain.contracts.memory_enums import MemoryType, memory_enum_value
from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.memory.context.context_firewall_rules import REVIEW_REQUIRED_TAGS

_SOURCE_REF_FIELDS = ("commits", "files", "resources")
_LOCATOR_SCAN_LIMIT = 512
_SUMMARY_SCAN_LIMIT = 1024
_TOPIC_TITLE_SCAN_LIMIT = 512
_TOPIC_TAG_LIMIT = 64
_TOPIC_TAG_SCAN_LIMIT = 128
_REF_ENTRY_LIMIT = 256
_REF_VALUE_SCAN_LIMIT = 512
_ASCII_TERM_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{2,}")
_CLOSURE_RE = re.compile(
    r"(?:\b(?:closed|fixed|obsolete|replaced|replaces|resolved|superseded|supersedes)\b"
    r"|不再适用|修复完成|问题已解决|已修复|已关闭|已取代|已替代|取代了|替代了)"
)
_TOPIC_SCRIPT_RANGES = (
    (0x1100, 0x11FF),  # Hangul Jamo
    (0x3040, 0x30FF),  # Hiragana and Katakana
    (0x3130, 0x318F),  # Hangul Compatibility Jamo
    (0x31F0, 0x31FF),  # Katakana Phonetic Extensions
    (0x3400, 0x4DBF),  # CJK Extension A
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0xA960, 0xA97F),  # Hangul Jamo Extended-A
    (0xAC00, 0xD7AF),  # Hangul Syllables
    (0xD7B0, 0xD7FF),  # Hangul Jamo Extended-B
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
    (0xFF66, 0xFF9D),  # Halfwidth Katakana (normally folded by NFKC)
    (0x20000, 0x2FA1F),  # Astral CJK extensions and compatibility supplement
    (0x30000, 0x323AF),  # Newer astral CJK extensions
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
_MICROSECONDS_PER_SECOND = 1_000_000
_SECONDS_PER_DAY = 86_400

_ScopeKey = tuple[str | None, str | None, str]
_IndexKey = TypeVar("_IndexKey")


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
class _ItemFeatures:
    item_id: str
    scope_key: _ScopeKey
    created_key: int
    superseded: bool
    topic_tokens: frozenset[str]
    mem_refs: frozenset[str]
    source_refs: tuple[frozenset[str], ...]
    closure_language: bool
    requires_review: bool


@dataclass(frozen=True)
class _RankedCandidate:
    result: SupersessionCandidate
    created_key: int


class SupersessionCandidateRanker:
    """Reusable feature/index cache for one maintenance planning pass."""

    def __init__(
        self,
        *,
        items: Iterable[MemoryItem],
        supersedes_edges: Iterable[tuple[str, str]],
    ) -> None:
        self._supersedes_edges = frozenset(supersedes_edges)
        self._features_by_id: dict[str, _ItemFeatures] = {}
        self._features_by_identity: dict[int, _ItemFeatures] = {}
        for item in items:
            if not isinstance(item, MemoryItem):
                raise TypeError("items must contain only MemoryItem instances")
            existing = self._features_by_id.get(item.id)
            if existing is not None:
                self._features_by_identity[id(item)] = existing
                continue
            features = _build_item_features(item)
            self._features_by_id[features.item_id] = features
            self._features_by_identity[id(item)] = features

        explicit: dict[tuple[_ScopeKey, str], set[str]] = {}
        memory_refs: dict[tuple[_ScopeKey, str], set[str]] = {}
        source_refs: dict[tuple[_ScopeKey, int, str], set[str]] = {}
        topic_tokens: dict[tuple[_ScopeKey, str], set[str]] = {}
        base_by_scope: dict[_ScopeKey, list[_ItemFeatures]] = {}
        closure_by_scope: dict[_ScopeKey, list[_ItemFeatures]] = {}
        for features in self._features_by_id.values():
            for target_id in features.mem_refs:
                _add_to_index(
                    memory_refs,
                    (features.scope_key, target_id),
                    features.item_id,
                )
            for field_index, values in enumerate(features.source_refs):
                for value in values:
                    _add_to_index(
                        source_refs,
                        (features.scope_key, field_index, value),
                        features.item_id,
                    )
            for token in features.topic_tokens:
                _add_to_index(
                    topic_tokens,
                    (features.scope_key, token),
                    features.item_id,
                )
            if not features.superseded and not features.requires_review:
                base_by_scope.setdefault(features.scope_key, []).append(features)
                if features.closure_language:
                    closure_by_scope.setdefault(features.scope_key, []).append(features)
        for source_id, target_id in self._supersedes_edges:
            source = self._features_by_id.get(source_id)
            if source is not None:
                _add_to_index(
                    explicit,
                    (source.scope_key, target_id),
                    source.item_id,
                )

        self._explicit_index = _freeze_set_index(explicit)
        self._memory_ref_index = _freeze_set_index(memory_refs)
        self._source_ref_index = _freeze_set_index(source_refs)
        self._topic_index = _freeze_set_index(topic_tokens)
        self._base_by_scope = _freeze_time_index(base_by_scope)
        self._closure_by_scope = _freeze_time_index(closure_by_scope)

    def rank(self, obsolete: MemoryItem) -> list[SupersessionCandidate]:
        """Return a bounded Top-3 without materializing all scored candidates."""
        if not isinstance(obsolete, MemoryItem):
            raise TypeError("obsolete must be a MemoryItem")
        obsolete_features = self._features_by_identity.get(id(obsolete))
        if obsolete_features is None:
            obsolete_features = _build_item_features(obsolete)

        candidate_ids: set[str] = set()
        direct_key = (obsolete_features.scope_key, obsolete_features.item_id)
        candidate_ids.update(self._explicit_index.get(direct_key, ()))
        candidate_ids.update(self._memory_ref_index.get(direct_key, ()))
        for field_index, values in enumerate(obsolete_features.source_refs):
            for value in values:
                candidate_ids.update(
                    self._source_ref_index.get(
                        (obsolete_features.scope_key, field_index, value),
                        (),
                    )
                )
        for token in obsolete_features.topic_tokens:
            candidate_ids.update(
                self._topic_index.get((obsolete_features.scope_key, token), ())
            )
        candidate_ids.update(
            _latest_newer_ids(
                self._base_by_scope.get(obsolete_features.scope_key, ()),
                obsolete_features,
            )
        )
        candidate_ids.update(
            _latest_newer_ids(
                self._closure_by_scope.get(obsolete_features.scope_key, ()),
                obsolete_features,
            )
        )

        best: list[_RankedCandidate] = []
        for candidate_id in candidate_ids:
            candidate_features = self._features_by_id[candidate_id]
            if not _is_valid_candidate(candidate_features, obsolete_features):
                continue
            result = _score_candidate(
                candidate_features,
                obsolete_features,
                self._supersedes_edges,
            )
            if result.score > 0:
                _insert_top_three(
                    best,
                    _RankedCandidate(
                        result=result,
                        created_key=candidate_features.created_key,
                    ),
                )
        return [candidate.result for candidate in best]


def _add_to_index(
    index: dict[_IndexKey, set[str]],
    key: _IndexKey,
    item_id: str,
) -> None:
    index.setdefault(key, set()).add(item_id)


def _freeze_set_index(
    index: dict[_IndexKey, set[str]],
) -> dict[_IndexKey, frozenset[str]]:
    return {key: frozenset(values) for key, values in index.items()}


def _freeze_time_index(
    index: dict[_ScopeKey, list[_ItemFeatures]],
) -> dict[_ScopeKey, tuple[_ItemFeatures, ...]]:
    return {
        key: tuple(
            sorted(
                values,
                key=lambda features: (-features.created_key, features.item_id),
            )
        )
        for key, values in index.items()
    }


def _latest_newer_ids(
    index: tuple[_ItemFeatures, ...],
    obsolete: _ItemFeatures,
) -> tuple[str, ...]:
    selected: list[str] = []
    for candidate in index:
        if candidate.created_key <= obsolete.created_key:
            break
        if candidate.item_id == obsolete.item_id:
            continue
        selected.append(candidate.item_id)
        if len(selected) == 3:
            break
    return tuple(selected)


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
    return SupersessionCandidateRanker(
        items=items,
        supersedes_edges=supersedes_edges,
    ).rank(obsolete)


def _build_item_features(item: MemoryItem) -> _ItemFeatures:
    return _ItemFeatures(
        item_id=item.id,
        scope_key=_scope_key(item),
        created_key=_utc_microsecond_key(item.created_at),
        superseded=bool(item.superseded_by),
        topic_tokens=frozenset(_topic_tokens(item)),
        mem_refs=frozenset(_bounded_refs(item.refs.mems)),
        source_refs=tuple(
            frozenset(_bounded_refs(getattr(item.refs, field)))
            for field in _SOURCE_REF_FIELDS
        ),
        closure_language=_has_closure_language(item),
        requires_review=bool(
            REVIEW_REQUIRED_TAGS & {tag.casefold() for tag in item.tags}
        ),
    )


def _scope_key(item: MemoryItem) -> _ScopeKey:
    return (item.tenant_id, item.project, memory_enum_value(item.type))


def _is_valid_candidate(
    candidate: _ItemFeatures,
    obsolete: _ItemFeatures,
) -> bool:
    return (
        candidate.item_id != obsolete.item_id
        and candidate.created_key > obsolete.created_key
        and candidate.scope_key == obsolete.scope_key
        and not candidate.superseded
        and not candidate.requires_review
    )


def _score_candidate(
    candidate: _ItemFeatures,
    obsolete: _ItemFeatures,
    supersedes_edges: frozenset[tuple[str, str]],
) -> SupersessionCandidate:
    evidence: list[str] = []
    score = 0.0

    if (candidate.item_id, obsolete.item_id) in supersedes_edges:
        evidence.append("EXPLICIT_SUPERSEDES_EDGE")
        score = 1.0
    if obsolete.item_id in candidate.mem_refs:
        evidence.append("EXPLICIT_MEMORY_REF")
        score += 0.45
    if any(left & right for left, right in zip(candidate.source_refs, obsolete.source_refs)):
        evidence.append("SHARED_SOURCE_EVIDENCE")
        score += 0.25
    if candidate.topic_tokens & obsolete.topic_tokens:
        evidence.append("TOPIC_OVERLAP")
        score += 0.20
    if candidate.closure_language:
        evidence.append("CLOSURE_LANGUAGE")
        score += 0.10
    evidence.append("NEWER_ITEM")
    score += 0.05

    return SupersessionCandidate(
        replacement_id=candidate.item_id,
        score=round(min(score, 1.0), 2),
        evidence_codes=tuple(evidence),
    )


def _insert_top_three(
    best: list[_RankedCandidate],
    candidate: _RankedCandidate,
) -> None:
    candidate_key = _ranking_key(candidate)
    index = 0
    while index < len(best) and _ranking_key(best[index]) <= candidate_key:
        index += 1
    best.insert(index, candidate)
    if len(best) > 3:
        best.pop()


def _ranking_key(candidate: _RankedCandidate) -> tuple[float, int, str]:
    return (
        -candidate.result.score,
        -candidate.created_key,
        candidate.result.replacement_id,
    )


def _bounded_refs(values: list[str]) -> tuple[str, ...]:
    return tuple(
        value
        for value in values[:_REF_ENTRY_LIMIT]
        if len(value) <= _REF_VALUE_SCAN_LIMIT
    )


def _topic_tokens(item: MemoryItem) -> set[str]:
    parts = [item.title[:_TOPIC_TITLE_SCAN_LIMIT]]
    parts.extend(
        tag[:_TOPIC_TAG_SCAN_LIMIT]
        for tag in item.tags[:_TOPIC_TAG_LIMIT]
    )
    text = unicodedata.normalize("NFKC", " ".join(parts)).casefold()
    tokens = {
        token
        for token in _ASCII_TERM_RE.findall(text)
        if token not in _TOPIC_STOPWORDS
    }
    script_text = text
    for stopword in _CJK_TOPIC_STOPWORDS:
        script_text = script_text.replace(stopword, " ")
    for run in _topic_script_runs(script_text):
        if run not in _TOPIC_STOPWORDS:
            tokens.add(run)
        tokens.update(
            pair
            for pair in (run[index:index + 2] for index in range(len(run) - 1))
            if pair not in _TOPIC_STOPWORDS
        )
    return tokens


def _topic_script_runs(text: str) -> Iterable[str]:
    run: list[str] = []
    for character in text:
        if _is_topic_script_character(character):
            run.append(character)
            continue
        if len(run) >= 2:
            yield "".join(run)
        run.clear()
    if len(run) >= 2:
        yield "".join(run)


def _is_topic_script_character(character: str) -> bool:
    if not unicodedata.category(character).startswith("L"):
        return False
    codepoint = ord(character)
    return any(start <= codepoint <= end for start, end in _TOPIC_SCRIPT_RANGES)


def _has_closure_language(candidate: MemoryItem) -> bool:
    if memory_enum_value(candidate.type) != MemoryType.signal.value:
        return False
    locator = candidate.context_views.locator[:_LOCATOR_SCAN_LIMIT]
    summary = candidate.summary[:_SUMMARY_SCAN_LIMIT]
    metadata = unicodedata.normalize("NFKC", f"{summary}\n{locator}").casefold()
    return _CLOSURE_RE.search(metadata) is not None


def _utc_microsecond_key(value: datetime) -> int:
    """Return an exact UTC ordering key without datetime conversion or floats."""
    offset = value.utcoffset()
    if offset is None:
        offset = timedelta(0)
    local_microseconds = (
        (
            (value.toordinal() * _SECONDS_PER_DAY)
            + (value.hour * 3600)
            + (value.minute * 60)
            + value.second
        )
        * _MICROSECONDS_PER_SECOND
        + value.microsecond
    )
    return local_microseconds - _timedelta_microseconds(offset)


def _timedelta_microseconds(value: timedelta) -> int:
    return (
        ((value.days * _SECONDS_PER_DAY) + value.seconds) * _MICROSECONDS_PER_SECOND
        + value.microseconds
    )


__all__ = [
    "SupersessionCandidate",
    "SupersessionCandidateRanker",
    "rank_supersession_candidates",
]
