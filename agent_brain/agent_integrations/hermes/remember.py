"""Hermes remember/write-path tool implementation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from agent_brain.agent_integrations.hermes.related import suggest_related_memories
from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Refs, Sensitivity
from agent_brain.memory.store.write_service import WriteService


ComponentsFactory = Callable[[], tuple[Any, Any, Any]]
EmbedderFactory = Callable[[], Any]
ItemIdFactory = Callable[..., str]
StoreFactory = Callable[[], Any]


def hub_remember_impl(
    components: ComponentsFactory,
    embedder_factory: EmbedderFactory,
    item_id_factory: ItemIdFactory,
    content: str,
    title: str,
    type: str = "fact",
    tags: list[str] | None = None,
    refs: dict | None = None,
    project: str | None = None,
    agent: str | None = None,
    confidence: float = 0.7,
    tenant_id: str | None = None,
    allow_unsafe: bool = False,
    store_factory: StoreFactory | None = None,
) -> dict[str, Any]:
    """Store a new memory in the brain pool."""
    now = datetime.now(timezone.utc).astimezone()
    item = MemoryItem(
        id=item_id_factory(title, when=now),
        type=MemoryType(type),
        created_at=now,
        agent=agent,
        project=project,
        tenant_id=tenant_id,
        tags=tags or [],
        refs=Refs.model_validate(refs or {}),
        sensitivity=Sensitivity.internal,
        title=title,
        summary=content[:200],
        confidence=confidence,
    )
    store = store_factory() if store_factory is not None else components()[0]
    write_result = WriteService(
        store,
        lambda: components()[1],
        embedder_factory,
        brain_dir=store.items_dir.parent,
    ).write(item=item, body=content, allow_unsafe=allow_unsafe)
    if write_result.status == "blocked":
        return {
            "stored": False,
            "status": "blocked",
            "reason": "skill audit found critical/high issues; pass allow_unsafe=true to override",
            "findings": write_result.findings or [],
        }

    related: list[dict[str, Any]] = []
    try:
        current_store, _, retriever = components()
    except Exception:
        current_store = store
        retriever = None
    if retriever is not None:
        related = suggest_related_memories(
            retriever=retriever,
            store=current_store,
            item_id=item.id,
            query=f"{title} {content[:100]}",
        )

    result: dict[str, Any] = {
        "id": item.id,
        "stored": True,
        "path": write_result.path,
        "indexed": write_result.indexed,
        "warnings": write_result.warnings,
    }
    if related:
        result["related"] = related[:3]
    return result


__all__ = ["hub_remember_impl"]
