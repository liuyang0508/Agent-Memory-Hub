"""Deterministic, metadata-only supersession candidate ranking."""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_left
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
_DENSE_POSTING_MIN_COUNT = 32
_PY_INT_BASE_BYTES = 24
_PY_INT_DIGIT_BITS = 30
_PY_INT_DIGIT_BYTES = 4
_PY_TUPLE_BASE_BYTES = 40
_PY_POINTER_BYTES = 8
_PY_INT_OBJECT_BYTES = 28

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
class _ScopeBitmapIndex:
    items: tuple[_ItemFeatures, ...]
    negative_created_keys: tuple[int, ...]
    closure_mask: int


@dataclass(frozen=True)
class _SparsePosting:
    positions: tuple[int, ...]


@dataclass(frozen=True)
class _DensePosting:
    mask: int


_Posting = _SparsePosting | _DensePosting


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
        self._features_by_identity: dict[int, tuple[MemoryItem, _ItemFeatures]] = {}
        for item in items:
            if not isinstance(item, MemoryItem):
                raise TypeError("items must contain only MemoryItem instances")
            existing = self._features_by_id.get(item.id)
            if existing is not None:
                self._features_by_identity[id(item)] = (item, existing)
                continue
            features = _build_item_features(item)
            self._features_by_id[features.item_id] = features
            self._features_by_identity[id(item)] = (item, features)

        valid_by_scope: dict[_ScopeKey, list[_ItemFeatures]] = {}
        for features in self._features_by_id.values():
            if not features.superseded and not features.requires_review:
                valid_by_scope.setdefault(features.scope_key, []).append(features)
        self._scope_indices = _build_scope_bitmap_indices(valid_by_scope)
        positions = {
            (scope_key, features.item_id): position
            for scope_key, scope_index in self._scope_indices.items()
            for position, features in enumerate(scope_index.items)
        }

        explicit: dict[tuple[_ScopeKey, str], list[int]] = {}
        memory_refs: dict[tuple[_ScopeKey, str], list[int]] = {}
        source_refs: dict[tuple[_ScopeKey, int, str], list[int]] = {}
        topic_tokens: dict[tuple[_ScopeKey, str], list[int]] = {}
        for features in self._features_by_id.values():
            position = positions.get((features.scope_key, features.item_id))
            if position is None:
                continue
            for target_id in features.mem_refs:
                _add_to_posting_builder(
                    memory_refs,
                    (features.scope_key, target_id),
                    position,
                )
            for field_index, values in enumerate(features.source_refs):
                for value in values:
                    _add_to_posting_builder(
                        source_refs,
                        (features.scope_key, field_index, value),
                        position,
                    )
            for token in features.topic_tokens:
                _add_to_posting_builder(
                    topic_tokens,
                    (features.scope_key, token),
                    position,
                )
        for source_id, target_id in self._supersedes_edges:
            source = self._features_by_id.get(source_id)
            if source is not None:
                position = positions.get((source.scope_key, source.item_id))
                if position is not None:
                    _add_to_posting_builder(
                        explicit,
                        (source.scope_key, target_id),
                        position,
                    )

        self._explicit_index = _freeze_posting_index(explicit)
        self._memory_ref_index = _freeze_posting_index(memory_refs)
        self._source_ref_index = _freeze_posting_index(source_refs)
        self._topic_index = _freeze_posting_index(topic_tokens)

    def rank(self, obsolete: MemoryItem) -> list[SupersessionCandidate]:
        """Return a bounded Top-3 without materializing all scored candidates."""
        if not isinstance(obsolete, MemoryItem):
            raise TypeError("obsolete must be a MemoryItem")
        identity_snapshot = self._features_by_identity.get(id(obsolete))
        if identity_snapshot is not None and identity_snapshot[0] is obsolete:
            obsolete_features = identity_snapshot[1]
        else:
            obsolete_features = _build_item_features(obsolete)
        scope_index = self._scope_indices.get(obsolete_features.scope_key)
        if scope_index is None:
            return []
        newer_count = bisect_left(
            scope_index.negative_created_keys,
            -obsolete_features.created_key,
        )
        newer_mask = (1 << newer_count) - 1
        if not newer_mask:
            return []

        direct_key = (obsolete_features.scope_key, obsolete_features.item_id)
        explicit_mask = _posting_mask(self._explicit_index.get(direct_key)) & newer_mask
        memory_mask = _posting_mask(self._memory_ref_index.get(direct_key)) & newer_mask
        source_mask = 0
        for field_index, values in enumerate(obsolete_features.source_refs):
            for value in values:
                source_mask |= _posting_mask(
                    self._source_ref_index.get(
                        (obsolete_features.scope_key, field_index, value)
                    )
                )
        source_mask &= newer_mask
        topic_mask = 0
        for token in obsolete_features.topic_tokens:
            topic_mask |= _posting_mask(
                self._topic_index.get((obsolete_features.scope_key, token))
            )
        topic_mask &= newer_mask
        closure_mask = scope_index.closure_mask & newer_mask

        candidate_positions = set(_lowest_set_positions(newer_mask, limit=3))
        candidate_positions.update(_lowest_set_positions(explicit_mask, limit=3))
        evidence_masks = (memory_mask, source_mask, topic_mask, closure_mask)
        for signature in range(1, 1 << len(evidence_masks)):
            signature_mask = newer_mask
            for index, evidence_mask in enumerate(evidence_masks):
                if signature & (1 << index):
                    signature_mask &= evidence_mask
                    if not signature_mask:
                        break
            candidate_positions.update(
                _lowest_set_positions(signature_mask, limit=3)
            )

        best: list[_RankedCandidate] = []
        for position in candidate_positions:
            candidate_features = scope_index.items[position]
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


def _add_to_posting_builder(
    index: dict[_IndexKey, list[int]],
    key: _IndexKey,
    position: int,
) -> None:
    index.setdefault(key, []).append(position)


def _freeze_posting_index(
    index: dict[_IndexKey, list[int]],
) -> dict[_IndexKey, _Posting]:
    return {key: _freeze_posting(positions) for key, positions in index.items()}


def _freeze_posting(positions: list[int]) -> _Posting:
    ordered = tuple(sorted(set(positions)))
    if len(ordered) < _DENSE_POSTING_MIN_COUNT:
        return _SparsePosting(ordered)
    max_position = ordered[-1]
    dense_digits = ((max_position + 1) + _PY_INT_DIGIT_BITS - 1) // _PY_INT_DIGIT_BITS
    dense_bytes = _PY_INT_BASE_BYTES + (dense_digits * _PY_INT_DIGIT_BYTES)
    sparse_bytes = _PY_TUPLE_BASE_BYTES + len(ordered) * (
        _PY_POINTER_BYTES + _PY_INT_OBJECT_BYTES
    )
    if dense_bytes > sparse_bytes:
        return _SparsePosting(ordered)
    mask = 0
    for position in ordered:
        mask |= 1 << position
    return _DensePosting(mask)


def _posting_mask(posting: _Posting | None) -> int:
    if posting is None:
        return 0
    if isinstance(posting, _DensePosting):
        return posting.mask
    mask = 0
    for position in posting.positions:
        mask |= 1 << position
    return mask


def _build_scope_bitmap_indices(
    index: dict[_ScopeKey, list[_ItemFeatures]],
) -> dict[_ScopeKey, _ScopeBitmapIndex]:
    result: dict[_ScopeKey, _ScopeBitmapIndex] = {}
    for key, values in index.items():
        items = tuple(
            sorted(
                values,
                key=lambda features: (-features.created_key, features.item_id),
            )
        )
        closure_mask = 0
        for position, features in enumerate(items):
            if features.closure_language:
                closure_mask |= 1 << position
        result[key] = _ScopeBitmapIndex(
            items=items,
            negative_created_keys=tuple(-features.created_key for features in items),
            closure_mask=closure_mask,
        )
    return result


def _lowest_set_positions(mask: int, *, limit: int) -> tuple[int, ...]:
    positions: list[int] = []
    while mask and len(positions) < limit:
        lowest_bit = mask & -mask
        positions.append(lowest_bit.bit_length() - 1)
        mask ^= lowest_bit
    return tuple(positions)


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
