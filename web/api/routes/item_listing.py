from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any, Callable


def item_list_row(item: Any, body: str) -> dict[str, Any]:
    return {
        "id": item.id,
        "type": str(item.type),
        "title": item.title,
        "summary": item.summary,
        "project": item.project,
        "tags": item.tags,
        "confidence": item.confidence,
        "created_at": item.created_at.isoformat(),
        "body_preview": body[:200],
    }


def list_visible_items(
    *,
    items_with_bodies: Iterable[tuple[Any, str]],
    user: Any,
    is_visible: Callable[[Any, Any], bool],
    item_type: str | None = None,
    project: str | None = None,
    tag: str | None = None,
    q: str | None = None,
    conf_min: float | None = None,
    conf_max: float | None = None,
    since: str | None = None,
    until: str | None = None,
    sort: str = "created_at",
    order: str = "desc",
    offset: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    all_items = []
    q_lower = q.lower() if q else None
    since_dt = datetime.fromisoformat(since).replace(tzinfo=timezone.utc) if since else None
    until_dt = datetime.fromisoformat(until).replace(tzinfo=timezone.utc) if until else None

    for item, body in items_with_bodies:
        if not is_visible(item, user):
            continue
        if item_type and str(item.type) != item_type:
            continue
        if project and item.project != project:
            continue
        if tag and tag not in item.tags:
            continue
        if q_lower and q_lower not in item.title.lower() and q_lower not in item.summary.lower():
            continue
        if conf_min is not None and (item.confidence or 0) < conf_min:
            continue
        if conf_max is not None and (item.confidence or 0) > conf_max:
            continue
        if since_dt and item.created_at < since_dt:
            continue
        if until_dt and item.created_at > until_dt:
            continue
        all_items.append(item_list_row(item, body))

    reverse = order == "desc"
    all_items.sort(key=lambda row: row.get(sort, ""), reverse=reverse)
    page = all_items[offset:offset + limit]
    return {"items": page, "total": len(all_items), "offset": offset, "limit": limit}


__all__ = ["item_list_row", "list_visible_items"]
