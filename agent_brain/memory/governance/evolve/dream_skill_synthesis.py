"""Skill synthesis helpers for dreaming cycles."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from typing import Any

from agent_brain.memory.governance.evolve.crystallizer import SKILL_MATURITY_THRESHOLD, synthesize_skill
from agent_brain.contracts.memory_item import MemoryItem, MemoryType

ItemWithBody = tuple[MemoryItem, str]
SynthesizeFn = Callable[..., Any]


def find_existing_skill(items: Sequence[ItemWithBody], project: str) -> ItemWithBody | None:
    """Return the active skill for a project bucket, if one exists."""
    expected_project = project if project != "__general__" else None
    for item, body in items:
        if (
            item.type == MemoryType.skill
            and item.project == expected_project
            and item.superseded_by is None
        ):
            return item, body
    return None


def synthesize_mature_policy_skills(
    items: Sequence[ItemWithBody],
    store: Any,
    *,
    synthesize: SynthesizeFn = synthesize_skill,
) -> tuple[int, list[str]]:
    """Synthesize skills from mature policy groups and collect per-group errors."""
    policies = [
        (item, body)
        for item, body in items
        if item.type == MemoryType.policy
        and item.support_count >= SKILL_MATURITY_THRESHOLD
        and item.superseded_by is None
    ]
    if len(policies) < 2:
        return 0, []

    by_project: dict[str, list[ItemWithBody]] = defaultdict(list)
    for item, body in policies:
        by_project[item.project or "__general__"].append((item, body))

    synthesized = 0
    errors: list[str] = []
    for project, group in by_project.items():
        if len(group) < 2:
            continue
        try:
            synthesize(group, store, existing_skill=find_existing_skill(items, project))
            synthesized += 1
        except Exception as exc:
            errors.append(f"synthesize: {exc}")
    return synthesized, errors
