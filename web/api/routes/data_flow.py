"""Data-flow observability routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from agent_brain.observability.data_flow import DataFlowLedger
from web._base import _brain_dir
from web.auth import CurrentUser, get_current_user, require_admin


router = APIRouter()


@router.get("/api/data-flow")
def data_flow(
    hours: int = Query(72, ge=1, le=72),
    limit: int = Query(200, ge=1, le=500),
    source: str | None = Query(None),
    user: CurrentUser = Depends(get_current_user),
):
    """Return the last three days of sanitized AMH data-flow events."""

    require_admin(user)
    ledger = DataFlowLedger(_brain_dir())
    events = ledger.list_events(since_hours=hours, limit=limit, source=source)
    return {
        "window_hours": hours,
        "limit": limit,
        "source": source,
        "summary": ledger.summary(events, since_hours=hours, source=source).to_dict(),
        "events": [event.to_dict() for event in events],
    }
