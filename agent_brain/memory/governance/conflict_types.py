"""Shared value objects for conflict auto-resolution."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from agent_brain.memory.governance.drift import DriftFinding


class ResolutionStrategy(str, Enum):
    KEEP_NEWER = "keep_newer"
    KEEP_HIGHER_CONFIDENCE = "keep_higher_confidence"
    MARK_CONTESTED = "mark_contested"
    MERGE_RESOLUTION = "merge_resolution"


@dataclass
class Resolution:
    finding: DriftFinding
    strategy: ResolutionStrategy
    winner_id: str | None = None
    loser_id: str | None = None
    resolution_item_id: str | None = None
    applied: bool = False
    reason: str = ""


@dataclass
class ConflictReport:
    contradictions_found: int = 0
    resolutions: list[Resolution] = field(default_factory=list)

    @property
    def resolved_count(self) -> int:
        return sum(1 for resolution in self.resolutions if resolution.applied)

    @property
    def contested_count(self) -> int:
        return sum(
            1
            for resolution in self.resolutions
            if resolution.strategy == ResolutionStrategy.MARK_CONTESTED
        )


__all__ = ["ConflictReport", "Resolution", "ResolutionStrategy"]
