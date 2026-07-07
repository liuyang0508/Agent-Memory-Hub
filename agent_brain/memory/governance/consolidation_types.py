"""Shared value objects for L0 to L1 consolidation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from agent_brain.contracts.memory_item import MemoryItem


ItemBody = tuple[MemoryItem, str]


@dataclass
class ConsolidationGroup:
    """A set of L0 facts sharing (project, tag) that should merge into one L1 fact."""

    project: str
    tag: str
    sources: list[ItemBody]

    @property
    def source_ids(self) -> list[str]:
        return [item.id for item, _ in self.sources]


@dataclass
class ConsolidationReport:
    scanned: int
    groups: list[ConsolidationGroup] = field(default_factory=list)
    created: list[MemoryItem] = field(default_factory=list)
    applied: bool = False


Summarizer = Callable[[ConsolidationGroup], str]


__all__ = ["ConsolidationGroup", "ConsolidationReport", "ItemBody", "Summarizer"]
