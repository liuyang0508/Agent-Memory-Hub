from __future__ import annotations

from typing import Any

from agent_brain.contracts.memory_item import MemoryType, TYPE_TO_DECAY_CLASS


def build_update_fields(
    *,
    title: str | None = None,
    summary: str | None = None,
    tags: list[str] | None = None,
    type: str | None = None,
    confidence: float | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    if title is not None:
        updates["title"] = title
    if summary is not None:
        updates["summary"] = summary
    if tags is not None:
        updates["tags"] = tags
    if type is not None:
        try:
            mem_type = MemoryType(type)
        except ValueError:
            raise ValueError(f"invalid type {type!r}; valid: {[t.value for t in MemoryType]}")
        updates["type"] = mem_type.value
        # decay_class is auto-mapped from type only at creation. On a type
        # change the stored retention.decay_class must be re-derived so
        # retention scoring and decay-status use the new half-life.
        updates["retention.decay_class"] = TYPE_TO_DECAY_CLASS.get(mem_type.value, "fact")
    if confidence is not None:
        updates["confidence"] = confidence
    if project is not None:
        updates["project"] = project
    return updates
