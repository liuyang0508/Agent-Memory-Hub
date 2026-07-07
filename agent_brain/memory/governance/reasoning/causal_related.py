"""Related-decision discovery for causal reasoning."""

from __future__ import annotations

from typing import Any

from agent_brain.memory.governance.reasoning.causal_inference import ItemsById
from agent_brain.memory.governance.reasoning.causal_types import CausalCandidate
from agent_brain.contracts.memory_item import MemoryType


def find_related_decisions(
    *,
    item_id: str,
    items: ItemsById,
    scorer: Any,
    max_results: int = 5,
) -> list[CausalCandidate]:
    """Find decisions that may be causally related to the given item."""
    if item_id not in items:
        return []

    target_item, target_body = items[item_id]
    candidates: list[CausalCandidate] = []

    for other_id, (other_item, other_body) in items.items():
        if other_id == item_id:
            continue
        if other_item.type != MemoryType.decision:
            continue
        if other_item.superseded_by is not None:
            continue

        score, reasons = scorer.compute(
            cause=(other_item, other_body),
            effect=(target_item, target_body),
        )
        if score > 0.2:
            candidates.append(CausalCandidate(item=other_item, score=score, reasons=reasons))

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:max_results]


__all__ = ["find_related_decisions"]
