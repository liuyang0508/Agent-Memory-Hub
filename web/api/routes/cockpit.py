"""Cockpit read-model routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from agent_brain.product.cockpit import build_cockpit_summary
from web._base import _brain_dir
from web.auth import CurrentUser, get_current_user


router = APIRouter()


@router.get("/api/cockpit/summary")
async def cockpit_summary(user: CurrentUser = Depends(get_current_user)):
    """Return today's trusted handoff Cockpit summary."""

    return await run_in_threadpool(build_cockpit_summary, _brain_dir())
