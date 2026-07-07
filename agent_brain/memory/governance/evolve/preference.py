"""Preference Inference — derive user behavioral preferences from memory access patterns.

Inspired by OpenAI Dreaming's preference inference: instead of requiring explicit
preference declarations, infer them from what the user repeatedly accesses, affirms,
and rejects. Outputs a ranked list of PreferenceSignal that can be surfaced in
hub_profile or used to bias active recall.

Zero-model: pure statistical analysis of support_count, gain_score, access patterns,
and tag co-occurrence across the memory pool.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone

from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.governance.evolve.preference_format import format_preference_profile
from agent_brain.memory.governance.evolve.preference_types import PreferenceProfile, PreferenceSignal
from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.memory.scope import ScopeContext, ScopedMemory, filter_items_for_scope


def _extract_decision_patterns(items: list[ScopedMemory]) -> list[tuple[str, float]]:
    """Extract recurring decision patterns weighted by gain_score."""
    decisions = [
        scoped for scoped in items
        for it in (scoped.item,)
        if it.type in (MemoryType.decision, MemoryType.policy)
        and it.gain_score > 0
    ]
    decisions.sort(key=lambda scoped: -scoped.item.gain_score)
    return [(scoped.item.title, scoped.item.gain_score) for scoped in decisions[:20]]


def _infer_tag_preferences(items: list[ScopedMemory]) -> list[PreferenceSignal]:
    """Infer preferences from tag frequency × gain_score correlation."""
    tag_gain: dict[str, float] = defaultdict(float)
    tag_count: dict[str, int] = defaultdict(int)
    tag_support: dict[str, int] = defaultdict(int)
    tag_source_ids: dict[str, list[str]] = defaultdict(list)
    tag_scope_matches: dict[str, list[str]] = defaultdict(list)

    for scoped in items:
        it = scoped.item
        for tag in it.tags:
            tag_gain[tag] += it.gain_score
            tag_count[tag] += 1
            tag_support[tag] += it.support_count
            tag_source_ids[tag].append(it.id)
            tag_scope_matches[tag].append(scoped.scope_match)

    signals = []
    for tag, total_gain in sorted(tag_gain.items(), key=lambda x: -x[1]):
        if tag_count[tag] < 2:
            continue
        avg_gain = total_gain / tag_count[tag]
        if avg_gain > 0.1:
            signals.append(PreferenceSignal(
                dimension="topic",
                preference=f"偏好 {tag} 相关方案",
                anti_preference=None,
                confidence=min(0.95, 0.5 + avg_gain * 0.3),
                evidence_count=tag_count[tag],
                tags=[tag],
                scope_match=_dominant_scope_match(tag_scope_matches[tag]),
                source_item_ids=_dedupe(tag_source_ids[tag]),
            ))

    # Negative signals: tags with consistently negative gain
    for tag, total_gain in sorted(tag_gain.items(), key=lambda x: x[1]):
        if tag_count[tag] < 2:
            continue
        avg_gain = total_gain / tag_count[tag]
        if avg_gain < -0.1:
            signals.append(PreferenceSignal(
                dimension="avoidance",
                preference=f"倾向避免 {tag} 方案",
                anti_preference=tag,
                confidence=min(0.9, 0.4 + abs(avg_gain) * 0.3),
                evidence_count=tag_count[tag],
                tags=[tag],
                scope_match=_dominant_scope_match(tag_scope_matches[tag]),
                source_item_ids=_dedupe(tag_source_ids[tag]),
            ))

    return signals


def _infer_type_preferences(items: list[ScopedMemory]) -> list[PreferenceSignal]:
    """Infer preferences about solution types from decision/policy patterns."""
    type_gain: dict[str, float] = defaultdict(float)
    type_count: dict[str, int] = defaultdict(int)

    for scoped in items:
        it = scoped.item
        if it.type in (MemoryType.decision, MemoryType.policy) and it.gain_score != 0:
            key = str(it.type)
            type_gain[key] += it.gain_score
            type_count[key] += 1

    signals = []
    high_gain_decisions = [
        scoped for scoped in items
        for it in (scoped.item,)
        if it.type == MemoryType.decision and it.gain_score >= 0.5
    ]
    if len(high_gain_decisions) >= 3:
        signals.append(PreferenceSignal(
            dimension="style",
            preference="有明确决策记录的方案验证通过率高",
            anti_preference=None,
            confidence=0.7,
            evidence_count=len(high_gain_decisions),
            scope_match=_dominant_scope_match([scoped.scope_match for scoped in high_gain_decisions]),
            source_item_ids=[scoped.item.id for scoped in high_gain_decisions],
        ))

    return signals


def _infer_cooccurrence_preferences(items: list[ScopedMemory]) -> list[PreferenceSignal]:
    """Find tag pairs that co-occur in high-gain items → inferred approach preferences."""
    pair_gain: dict[tuple[str, str], float] = defaultdict(float)
    pair_count: dict[tuple[str, str], int] = defaultdict(int)
    pair_source_ids: dict[tuple[str, str], list[str]] = defaultdict(list)
    pair_scope_matches: dict[tuple[str, str], list[str]] = defaultdict(list)

    for scoped in items:
        it = scoped.item
        if it.gain_score <= 0 or len(it.tags) < 2:
            continue
        sorted_tags = sorted(it.tags)
        for i in range(len(sorted_tags)):
            for j in range(i + 1, len(sorted_tags)):
                pair = (sorted_tags[i], sorted_tags[j])
                pair_gain[pair] += it.gain_score
                pair_count[pair] += 1
                pair_source_ids[pair].append(it.id)
                pair_scope_matches[pair].append(scoped.scope_match)

    signals = []
    for pair, total_gain in sorted(pair_gain.items(), key=lambda x: -x[1])[:5]:
        if pair_count[pair] < 2:
            continue
        signals.append(PreferenceSignal(
            dimension="approach",
            preference=f"组合 [{pair[0]} + {pair[1]}] 的方案效果好",
            anti_preference=None,
            confidence=min(0.85, 0.5 + total_gain * 0.1),
            evidence_count=pair_count[pair],
            tags=list(pair),
            scope_match=_dominant_scope_match(pair_scope_matches[pair]),
            source_item_ids=_dedupe(pair_source_ids[pair]),
        ))

    return signals


def infer_preferences(store: ItemsStore, scope: ScopeContext | None = None) -> PreferenceProfile:
    """Run full preference inference pipeline over the memory pool."""
    items = filter_items_for_scope(store.iter_all(), scope)
    if not items:
        return PreferenceProfile(generated_at=datetime.now(timezone.utc), scope=_scope_payload(scope))

    # Top projects by item count
    proj_count = Counter(scoped.item.project for scoped in items if scoped.item.project)
    top_projects = proj_count.most_common(10)

    # Top tags by support-weighted frequency
    tag_weight: dict[str, int] = defaultdict(int)
    for scoped in items:
        it = scoped.item
        for tag in it.tags:
            tag_weight[tag] += max(1, it.support_count)
    top_tags = sorted(tag_weight.items(), key=lambda x: -x[1])[:15]

    # Decision patterns
    patterns = _extract_decision_patterns(items)
    decision_patterns = [title for title, _ in patterns[:10]]

    # Preference signals
    signals: list[PreferenceSignal] = []
    signals.extend(_infer_tag_preferences(items))
    signals.extend(_infer_type_preferences(items))
    signals.extend(_infer_cooccurrence_preferences(items))
    signals.sort(key=lambda s: -s.confidence)

    return PreferenceProfile(
        generated_at=datetime.now(timezone.utc),
        signals=signals[:20],
        top_projects=top_projects,
        top_tags=top_tags,
        decision_patterns=decision_patterns,
        scope=_scope_payload(scope),
    )


def _dominant_scope_match(matches: list[str]) -> str:
    priority = {"exact": 0, "related": 1, "global": 2}
    return min(matches or ["exact"], key=lambda value: priority.get(value, 99))


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _scope_payload(scope: ScopeContext | None) -> dict[str, object]:
    if scope is None:
        return {}
    return {
        "project": scope.project,
        "tenant_id": scope.tenant_id,
        "tags": list(scope.tags),
        "seed_item_ids": list(scope.seed_item_ids),
        "related_item_ids": list(scope.related_item_ids),
        "include_related": scope.include_related,
        "include_global": scope.include_global,
    }
