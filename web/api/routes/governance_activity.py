"""Activity timeline governance route for the Web Admin API."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query

from web._base import _components, _visible
from web.auth import CurrentUser, get_current_user

router = APIRouter()


@router.get("/api/activity")
async def activity_timeline(
    days: int = Query(30, le=365),
    user: CurrentUser = Depends(get_current_user),
):
    """Return item creation activity grouped by day for timeline visualization."""
    store, _, _, _ = _components()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    daily: dict[str, dict[str, int]] = {}
    type_totals: dict[str, int] = {}
    recent_items: list[dict] = []

    for item, _body in store.iter_all():
        if not _visible(item, user):
            continue
        created = item.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created < cutoff:
            continue
        day_key = created.strftime("%Y-%m-%d")
        item_type = str(item.type)
        if day_key not in daily:
            daily[day_key] = {}
        daily[day_key][item_type] = daily[day_key].get(item_type, 0) + 1
        type_totals[item_type] = type_totals.get(item_type, 0) + 1
        recent_items.append({
            "id": item.id,
            "type": item_type,
            "title": item.title,
            "project": item.project,
            "created_at": created.isoformat(),
            "agent": item.agent,
        })

    recent_items.sort(key=lambda x: x["created_at"], reverse=True)

    timeline = []
    for day_key in sorted(daily.keys()):
        timeline.append({
            "date": day_key,
            "counts": daily[day_key],
            "total": sum(daily[day_key].values()),
        })

    return {
        "timeline": timeline,
        "type_totals": type_totals,
        "recent": recent_items[:50],
        "days": days,
    }


__all__ = ["activity_timeline", "router"]
