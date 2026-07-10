"""Memory-lineage explainability routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from agent_brain.product.memory_lineage import build_memory_lineage_report
from web._base import _brain_dir
from web.auth import CurrentUser, get_current_user, require_admin


router = APIRouter()


@router.get("/api/memory-lineage")
def memory_lineage(
    hours: int = Query(72, ge=1, le=72),
    limit: int = Query(200, ge=1, le=500),
    agent: str | None = Query(None),
    mode: str | None = Query(None, pattern="^(maintain|recall|evolve)$"),
    item_id: str | None = Query(None),
    user: CurrentUser = Depends(get_current_user),
):
    """Explain how agents write, retrieve, score, load, and inject memory."""

    require_admin(user)
    return build_memory_lineage_report(
        _brain_dir(),
        hours=hours,
        agent=agent,
        mode=mode,
        item_id=item_id,
        limit=limit,
    ).to_dict()
