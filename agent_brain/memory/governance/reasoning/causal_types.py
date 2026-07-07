"""Shared causal reasoning value objects and constants."""
from __future__ import annotations

from dataclasses import dataclass, field

from agent_brain.contracts.memory_item import MemoryItem, MemoryType


CAUSAL_RELATIONS = ("caused_by", "led_to", "refs", "evolved_from")
CAUSAL_TYPES = (MemoryType.decision, MemoryType.episode, MemoryType.signal)
TEMPORAL_WINDOW_DAYS = 30


@dataclass
class CausalLink:
    """A single link in a causal chain."""
    source_id: str
    target_id: str
    relation: str
    confidence: float
    reason: str


@dataclass
class CausalTrace:
    """Full causal trace from effect back to root cause(s)."""
    origin_id: str
    chain: list[CausalLink] = field(default_factory=list)
    root_causes: list[str] = field(default_factory=list)

    @property
    def depth(self) -> int:
        return len(self.chain)

    @property
    def item_ids_in_order(self) -> list[str]:
        """From root cause to effect (chronological)."""
        ids = []
        for link in reversed(self.chain):
            if link.target_id not in ids:
                ids.append(link.target_id)
        if self.origin_id not in ids:
            ids.append(self.origin_id)
        return ids


@dataclass
class CausalCandidate:
    """A potential cause item with scoring."""
    item: MemoryItem
    score: float
    reasons: list[str] = field(default_factory=list)


__all__ = [
    "CAUSAL_RELATIONS",
    "CAUSAL_TYPES",
    "TEMPORAL_WINDOW_DAYS",
    "CausalCandidate",
    "CausalLink",
    "CausalTrace",
]
