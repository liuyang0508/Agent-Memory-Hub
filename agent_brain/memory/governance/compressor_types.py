"""Shared value objects for semantic memory compression."""
from __future__ import annotations

from dataclasses import dataclass, field

from agent_brain.contracts.memory_item import MemoryItem


@dataclass
class CompressionCandidate:
    """A group of items eligible for compression."""
    items: list[tuple[MemoryItem, str]]
    reason: str
    estimated_reduction: float = 0.0

    @property
    def item_ids(self) -> list[str]:
        return [item.id for item, _ in self.items]

    @property
    def total_chars(self) -> int:
        return sum(len(body) + len(item.title) + len(item.summary) for item, body in self.items)


@dataclass
class CompressionReport:
    scanned: int = 0
    candidates: list[CompressionCandidate] = field(default_factory=list)
    compressed: list[MemoryItem] = field(default_factory=list)
    chars_before: int = 0
    chars_after: int = 0

    @property
    def reduction_ratio(self) -> float:
        if self.chars_before == 0:
            return 0.0
        return 1.0 - (self.chars_after / self.chars_before)


__all__ = ["CompressionCandidate", "CompressionReport"]
