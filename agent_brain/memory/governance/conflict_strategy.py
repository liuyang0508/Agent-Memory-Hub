"""Strategy selection for conflict auto-resolution."""
from __future__ import annotations

from agent_brain.memory.governance.conflict_types import ResolutionStrategy
from agent_brain.memory.governance.drift import DriftFinding
from agent_brain.contracts.memory_item import MemoryItem


def select_strategy(
    finding: DriftFinding,
    item_a: MemoryItem,
    item_b: MemoryItem,
) -> tuple[ResolutionStrategy, str]:
    """Choose resolution strategy based on finding confidence and item attributes."""
    conf_diff = abs(item_a.confidence - item_b.confidence)

    if conf_diff >= 0.3:
        return ResolutionStrategy.KEEP_HIGHER_CONFIDENCE, (
            f"confidence gap {conf_diff:.2f} (A={item_a.confidence:.2f}, B={item_b.confidence:.2f})"
        )

    if finding.confidence >= 0.8:
        return ResolutionStrategy.KEEP_NEWER, (
            f"high-confidence contradiction ({finding.confidence:.2f}), temporal resolution"
        )

    return ResolutionStrategy.MARK_CONTESTED, (
        f"moderate confidence ({finding.confidence:.2f}), flagging for review"
    )


__all__ = ["select_strategy"]
