"""Request models and payload helpers for item CRUD routes."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Refs, Sensitivity
from web.auth import CurrentUser


class UpdateItemRequest(BaseModel):
    title: str | None = None
    summary: str | None = None
    tags: list[str] | None = None
    confidence: float | None = None
    project: str | None = None


class CreateItemRequest(BaseModel):
    type: str
    title: str
    summary: str
    body: str = ""
    tags: list[str] = []
    refs: dict[str, list[str]] = {}
    project: str | None = None
    confidence: float = 0.7
    sensitivity: str = "internal"


def pinned_item_summary(item: MemoryItem) -> dict[str, object]:
    return {
        "id": item.id,
        "type": str(item.type),
        "title": item.title,
        "summary": item.summary,
        "project": item.project,
        "confidence": item.confidence,
        "created_at": item.created_at.isoformat(),
    }


def update_fields_from_request(req: UpdateItemRequest) -> dict[str, object]:
    updates: dict[str, object] = {}
    if req.title is not None:
        updates["title"] = req.title
    if req.summary is not None:
        updates["summary"] = req.summary
    if req.tags is not None:
        updates["tags"] = req.tags
    if req.confidence is not None:
        updates["confidence"] = req.confidence
    if req.project is not None:
        updates["project"] = req.project
    return updates


def create_item_record(
    req: CreateItemRequest,
    *,
    item_id: str,
    created_at: datetime,
    user: CurrentUser,
) -> MemoryItem:
    return MemoryItem(
        id=item_id,
        type=MemoryType(req.type),
        created_at=created_at,
        agent="web-admin",
        project=req.project,
        tenant_id=user.tenant_id if not user.is_admin else None,
        tags=req.tags,
        refs=Refs.model_validate(req.refs or {}),
        sensitivity=Sensitivity(req.sensitivity),
        title=req.title,
        summary=req.summary,
        confidence=req.confidence,
    )


def clone_item_record(
    item: MemoryItem,
    *,
    clone_id: str,
    created_at: datetime,
) -> MemoryItem:
    clone_data = item.model_dump(mode="json", exclude_none=False)
    clone_data["id"] = clone_id
    clone_data["created_at"] = created_at.isoformat()
    clone_data["tags"] = sorted(set(item.tags) | {"cloned"})
    return MemoryItem.model_validate(clone_data)


__all__ = [
    "CreateItemRequest",
    "UpdateItemRequest",
    "clone_item_record",
    "create_item_record",
    "pinned_item_summary",
    "update_fields_from_request",
]
