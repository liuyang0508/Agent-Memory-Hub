from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from agent_brain.memory.store.items_store import ItemsStore, make_item_id
from agent_brain.memory.store.write_service import WriteService
from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Refs, Sensitivity


def write_item(
    *,
    store: ItemsStore,
    index_getter: Callable,
    embedder_getter: Callable,
    type: str,
    title: str,
    summary: str,
    body: str = "",
    overview: str | None = None,
    tags: list[str] | None = None,
    refs: dict | None = None,
    project: str | None = None,
    agent: str | None = None,
    session: str | None = None,
    confidence: float = 0.7,
    sensitivity: str = "internal",
    validity: dict | None = None,
    allow_unsafe: bool = False,
) -> str:
    now = datetime.now(timezone.utc).astimezone()
    item = MemoryItem(
        id=make_item_id(title, when=now),
        type=MemoryType(type),
        created_at=now,
        agent=agent,
        session=session,
        project=project,
        tags=tags or [],
        sensitivity=Sensitivity(sensitivity),
        title=title,
        summary=summary,
        refs=Refs.model_validate(refs or {}),
        context_views={"overview": overview} if overview is not None else {},
        confidence=confidence,
        validity=validity or {},
    )

    result = WriteService(
        store,
        index_getter,
        embedder_getter,
        brain_dir=store.items_dir.parent,
    ).write(item=item, body=body, allow_unsafe=allow_unsafe)
    if result.status == "blocked":
        raise ValueError(f"write blocked by audit gate: {result.findings}")

    return item.id


__all__ = ["write_item"]
