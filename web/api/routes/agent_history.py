"""Local Agent history scan, sync, and draft review routes."""

from __future__ import annotations

import os
import threading
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from agent_brain.product.history_sync import HistorySyncRequest, run_history_sync
from agent_brain.product.local_history_sources import scan_local_history_sources
from agent_brain.product.memory_drafts import DraftStore
from web._base import _audit, _brain_dir
from web.auth import CurrentUser, get_current_user, require_admin


router = APIRouter()
_LOCAL_HISTORY_CACHE_TTL_SECONDS = 15.0
_local_history_cache: dict[str, tuple[float, dict[str, object]]] = {}
_local_history_cache_lock = threading.Lock()


class SyncRequest(BaseModel):
    source_paths: list[str] = Field(default_factory=list)
    use_llm: bool = False
    draft_limit: int = Field(default=50, ge=1, le=200)


class DraftUpdateRequest(BaseModel):
    title: str | None = None
    summary: str | None = None
    body: str | None = None
    type: str | None = None
    tags: list[str] | None = None
    risk_flags: list[str] | None = None


def _local_history_cache_ttl() -> float:
    try:
        return float(os.environ.get("MEMORY_HUB_LOCAL_HISTORY_CACHE_TTL", _LOCAL_HISTORY_CACHE_TTL_SECONDS))
    except (TypeError, ValueError):
        return _LOCAL_HISTORY_CACHE_TTL_SECONDS


def _clear_local_history_cache() -> None:
    with _local_history_cache_lock:
        _local_history_cache.clear()


def _cached_local_history_report(*, refresh: bool = False) -> dict[str, object]:
    brain = _brain_dir()
    key = str(brain)
    ttl = _local_history_cache_ttl()
    now = time.monotonic()
    with _local_history_cache_lock:
        cached = _local_history_cache.get(key)
        if not refresh and ttl > 0 and cached and now - cached[0] < ttl:
            return cached[1]
        report = scan_local_history_sources(brain_dir=brain)
        _local_history_cache[key] = (time.monotonic(), report)
        return report


@router.get("/api/agents/local-history")
async def local_history(user: CurrentUser = Depends(get_current_user)):
    require_admin(user)
    return await run_in_threadpool(_cached_local_history_report)


@router.post("/api/agents/local-history/scan")
async def local_history_scan(user: CurrentUser = Depends(get_current_user)):
    require_admin(user)
    return await run_in_threadpool(_cached_local_history_report, refresh=True)


@router.get("/api/agents/local-history/drafts")
async def local_history_drafts(status: str | None = None, user: CurrentUser = Depends(get_current_user)):
    require_admin(user)
    drafts = DraftStore(_brain_dir()).list(status=status)
    return {"drafts": [draft.to_dict() for draft in drafts]}


@router.patch("/api/agents/local-history/drafts/{draft_id}")
async def update_local_history_draft(
    draft_id: str,
    req: DraftUpdateRequest,
    user: CurrentUser = Depends(get_current_user),
):
    require_admin(user)
    try:
        draft = DraftStore(_brain_dir()).update(draft_id, **req.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _audit(user.username, "history_draft_update", draft_id)
    return draft.to_dict()


@router.post("/api/agents/local-history/drafts/{draft_id}/apply")
async def apply_local_history_draft(draft_id: str, user: CurrentUser = Depends(get_current_user)):
    require_admin(user)
    try:
        draft = DraftStore(_brain_dir()).apply(draft_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _audit(user.username, "history_draft_apply", draft_id)
    return draft.to_dict()


@router.post("/api/agents/local-history/drafts/{draft_id}/skip")
async def skip_local_history_draft(draft_id: str, user: CurrentUser = Depends(get_current_user)):
    require_admin(user)
    try:
        draft = DraftStore(_brain_dir()).skip(draft_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _audit(user.username, "history_draft_skip", draft_id)
    return draft.to_dict()


@router.get("/api/agents/{agent}/local-history/sources")
async def local_history_sources(agent: str, user: CurrentUser = Depends(get_current_user)):
    require_admin(user)
    report = await run_in_threadpool(_cached_local_history_report)
    for row in report["agents"]:
        if row["agent"] == agent:
            return row
    raise HTTPException(status_code=404, detail="agent not found")


@router.post("/api/agents/{agent}/local-history/sync")
async def local_history_sync(agent: str, req: SyncRequest, user: CurrentUser = Depends(get_current_user)):
    require_admin(user)
    result = run_history_sync(
        _brain_dir(),
        HistorySyncRequest(
            agent=agent,
            source_paths=req.source_paths,
            use_llm=req.use_llm,
            draft_limit=req.draft_limit,
        ),
    )
    _audit(user.username, "history_sync", f"{agent}: {result['drafts_created']} drafts")
    return result
