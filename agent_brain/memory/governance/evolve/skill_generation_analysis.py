from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from agent_brain.memory.governance.evolve.engine import EvolveAction, EvolveProposal
from agent_brain.memory.governance.evolve.proposal_previews import generate_skill_preview
from agent_brain.contracts.memory_item import MemoryItem, MemoryType

SkillPreviewFactory = Callable[[str, str, list[tuple[MemoryItem, str]]], str]


def find_skill_generation_candidates(
    items: list[tuple[MemoryItem, str]],
    *,
    skill_preview: SkillPreviewFactory = generate_skill_preview,
) -> list[EvolveProposal]:
    """Find repeated episode/decision patterns that could become a skill."""
    pattern_groups: dict[tuple[str, str], list[tuple[MemoryItem, str]]] = defaultdict(list)

    for item, body in items:
        if item.type in (MemoryType.episode, MemoryType.decision) and item.project:
            tag_key = ",".join(sorted(item.tags)) if item.tags else ""
            pattern_groups[(item.project, tag_key)].append((item, body))

    proposals: list[EvolveProposal] = []
    for (project, tags), group_items in pattern_groups.items():
        if len(group_items) < 3:
            continue
        item_ids = [item.id for item, _ in group_items]
        types = set(str(item.type) for item, _ in group_items)
        proposals.append(EvolveProposal(
            action=EvolveAction.GENERATE_SKILL,
            item_ids=item_ids,
            title=f"Generate skill from {len(group_items)} patterns in '{project}'",
            description="Create a reusable skill based on recurring patterns",
            rationale=(
                f"Found {len(group_items)} items with similar tags ({tags}) in project "
                f"'{project}'. Types: {', '.join(types)}. These patterns suggest a "
                "repeatable workflow worth codifying as a skill."
            ),
            confidence=min(0.95, 0.6 + len(group_items) * 0.08),
            output_preview=skill_preview(project, tags, group_items),
        ))

    return proposals


__all__ = ["find_skill_generation_candidates"]
