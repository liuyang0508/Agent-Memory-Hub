"""Temporal safety filtering for retrieval candidates."""

from __future__ import annotations

from datetime import datetime

from agent_brain.memory.governance.temporal_state import TemporalStateGate
from agent_brain.memory.recall.retrieval_types import RetrievedItem
from agent_brain.contracts.memory_item import MemoryItem


def filter_stale_temporal_state(
    index,
    candidates: list[RetrievedItem],
    *,
    include_stale_state: bool = False,
    now: datetime | None = None,
) -> list[RetrievedItem]:
    """Drop stale runtime-state observations before final retrieval output.

    This mirrors the before-inject context firewall, but runs earlier so old
    browser/permission/test/build state does not occupy the result set unless a
    caller explicitly asks for audit mode.
    """
    if include_stale_state or not candidates:
        return candidates

    ids = [candidate.id for candidate in candidates]
    metadata = index.get_search_metadata(ids)
    texts = index.get_texts(ids)
    gate = TemporalStateGate(now=now)
    filtered: list[RetrievedItem] = []
    for candidate in candidates:
        item = _item_from_metadata(candidate.id, metadata.get(candidate.id))
        if item is None:
            filtered.append(candidate)
            continue
        signal = gate.evaluate(item, texts.get(candidate.id, ""))
        if signal.status == "stale":
            continue
        filtered.append(candidate)
    return filtered


def _item_from_metadata(item_id: str, metadata: dict[str, object] | None) -> MemoryItem | None:
    if not metadata:
        return None
    try:
        return MemoryItem.model_validate({
            "id": item_id,
            "type": metadata["type"],
            "created_at": metadata["created_at"],
            "title": metadata.get("title") or "",
            "summary": metadata.get("summary") or "",
            "tags": metadata.get("tags") or [],
        })
    except (KeyError, TypeError, ValueError):
        return None


__all__ = ["filter_stale_temporal_state"]
