"""Candidate discovery for semantic memory compression."""
from __future__ import annotations

from collections import defaultdict

from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.governance.compressor_types import CompressionCandidate
from agent_brain.contracts.memory_item import AbstractionLayer, MemoryItem, MemoryType


def find_compression_candidates(
    store: ItemsStore,
    *,
    min_group_size: int = 3,
    max_group_size: int = 50,
    project: str | None = None,
) -> list[CompressionCandidate]:
    """Find groups of items that would benefit from compression.

    Groups by project+overlapping tags, excluding already-compressed (L2) items.
    """
    items = list(store.iter_all())
    eligible = [
        (item, body) for item, body in items
        if item.abstraction != AbstractionLayer.L2
        and item.superseded_by is None
        and item.type in (MemoryType.fact, MemoryType.episode, MemoryType.decision)
    ]

    by_project_tag: dict[tuple[str, str], list[tuple[MemoryItem, str]]] = defaultdict(list)
    for item, body in eligible:
        proj = item.project or "__general__"
        if project and proj != project:
            continue
        for tag in item.tags:
            by_project_tag[(proj, tag)].append((item, body))

    seen_ids: set[str] = set()
    candidates: list[CompressionCandidate] = []
    sorted_groups = sorted(by_project_tag.items(), key=lambda x: -len(x[1]))

    for (proj, tag), group in sorted_groups:
        unique = [(item, body) for item, body in group if item.id not in seen_ids]
        if len(unique) < min_group_size:
            continue

        unique = unique[:max_group_size]
        for item, _ in unique:
            seen_ids.add(item.id)

        candidates.append(CompressionCandidate(
            items=unique,
            reason=f"project={proj}, tag={tag}, count={len(unique)}",
            estimated_reduction=0.4,
        ))

    return candidates


__all__ = ["find_compression_candidates"]
