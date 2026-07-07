"""Implicit causal candidate discovery by temporal window and score."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from agent_brain.memory.governance.reasoning.causal_types import CAUSAL_TYPES, CausalCandidate
from agent_brain.contracts.memory_item import MemoryItem


ItemsById = dict[str, tuple[MemoryItem, str]]


def find_implicit_causes(
    *,
    item_id: str,
    visited: set[str],
    items: ItemsById,
    scorer: Any,
    temporal_window_days: int,
    min_score: float = 0.3,
) -> list[CausalCandidate]:
    """Find potential causes for an item from prior items in the time window."""
    if item_id not in items:
        return []
    target_item, target_body = items[item_id]
    candidates: list[CausalCandidate] = []
    window_start = target_item.created_at - timedelta(days=temporal_window_days)

    for other_id, (other_item, other_body) in items.items():
        if other_id in visited or other_id == item_id:
            continue
        if other_item.created_at >= target_item.created_at:
            continue
        if other_item.created_at < window_start:
            continue
        if other_item.type not in CAUSAL_TYPES:
            continue

        score, reasons = scorer.compute(
            cause=(other_item, other_body),
            effect=(target_item, target_body),
        )
        if score > min_score:
            candidates.append(CausalCandidate(item=other_item, score=score, reasons=reasons))

    return candidates


def find_implicit_effects(
    *,
    item_id: str,
    visited: set[str],
    items: ItemsById,
    scorer: Any,
    temporal_window_days: int,
    min_score: float = 0.3,
) -> list[CausalCandidate]:
    """Find potential effects for an item from later items in the time window."""
    if item_id not in items:
        return []
    source_item, source_body = items[item_id]
    candidates: list[CausalCandidate] = []
    window_end = source_item.created_at + timedelta(days=temporal_window_days)

    for other_id, (other_item, other_body) in items.items():
        if other_id in visited or other_id == item_id:
            continue
        if other_item.created_at <= source_item.created_at:
            continue
        if other_item.created_at > window_end:
            continue

        score, reasons = scorer.compute(
            cause=(source_item, source_body),
            effect=(other_item, other_body),
        )
        if score > min_score:
            candidates.append(CausalCandidate(item=other_item, score=score, reasons=reasons))

    return candidates


__all__ = ["find_implicit_causes", "find_implicit_effects"]
