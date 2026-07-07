from __future__ import annotations

from collections.abc import Callable

from agent_brain.memory.governance.evolve.engine import EvolveAction, EvolveProposal
from agent_brain.memory.governance.evolve.proposal_previews import generate_promote_preview
from agent_brain.contracts.memory_item import MemoryItem, MemoryType

PROMOTION_KEYWORDS = ("always", "never", "pattern", "rule", "lesson")
PromotePreviewFactory = Callable[[MemoryItem, str, list[str]], str]


def find_promotion_candidates(
    items: list[tuple[MemoryItem, str]],
    *,
    promote_preview: PromotePreviewFactory = generate_promote_preview,
) -> list[EvolveProposal]:
    """Find episodes that contain generalizable learnings."""
    proposals: list[EvolveProposal] = []

    for item, body in items:
        if item.type != MemoryType.episode:
            continue
        body_lower = body.lower()
        found_keywords = [kw for kw in PROMOTION_KEYWORDS if kw in body_lower]
        if not found_keywords:
            continue
        proposals.append(EvolveProposal(
            action=EvolveAction.PROMOTE,
            item_ids=[item.id],
            title=f"Promote episode '{item.title}' to decision/fact",
            description="Elevate this episode to a higher-level knowledge item",
            rationale=(
                "Episode contains generalizable learning patterns: "
                f"{', '.join(found_keywords)}. This knowledge could benefit broader contexts."
            ),
            confidence=0.7 + len(found_keywords) * 0.05,
            output_preview=promote_preview(item, body, found_keywords),
        ))

    return proposals


__all__ = ["PROMOTION_KEYWORDS", "find_promotion_candidates"]
