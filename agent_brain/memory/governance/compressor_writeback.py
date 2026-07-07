"""Writeback helpers for semantic memory compression."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from agent_brain.memory.store.items_store import make_item_id
from agent_brain.memory.governance.compressor_types import CompressionCandidate
from agent_brain.contracts.memory_item import AbstractionLayer, MemoryItem, MemoryType, Refs


_log = logging.getLogger(__name__)


def build_compressed_item(
    candidate: CompressionCandidate,
    *,
    title: str,
    summary: str,
    tags: list[str],
    now: datetime | None = None,
) -> MemoryItem:
    """Build the L2 memory item that represents one compressed candidate."""
    created_at = now or datetime.now(timezone.utc)
    safe_title = title if len(title) <= 200 else title[:197] + "..."
    return MemoryItem(
        id=make_item_id(title, when=created_at),
        type=MemoryType.fact,
        created_at=created_at,
        project=candidate.items[0][0].project,
        tags=tags[:8],
        title=safe_title,
        summary=summary,
        refs=Refs(mems=candidate.item_ids),
        confidence=0.8,
        abstraction=AbstractionLayer.L2,
    )


def mark_sources_superseded(
    store: Any,
    candidate: CompressionCandidate,
    *,
    compressed_item_id: str,
) -> None:
    """Mark source items as superseded, logging failures instead of hiding them."""
    for item, _ in candidate.items:
        try:
            store.update_frontmatter(item.id, superseded_by=compressed_item_id)
        except Exception as exc:
            _log.warning(
                "failed to mark source item superseded: item_id=%s compressed_id=%s error=%s",
                item.id,
                compressed_item_id,
                exc,
            )


__all__ = ["build_compressed_item", "mark_sources_superseded"]
