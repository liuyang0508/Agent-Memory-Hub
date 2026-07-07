"""Proactive memory review routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from agent_brain.product.proactive_memory import (
    approve_candidate,
    generate_candidates,
    generate_semantic_candidates,
    list_candidates,
    reject_candidate,
)
from web._base import _brain_dir
from web.auth import CurrentUser, get_current_user


router = APIRouter()


def _require_admin(user: CurrentUser) -> None:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")


@router.get("/api/memory-candidates")
async def memory_candidates(user: CurrentUser = Depends(get_current_user)):
    """Return proactive memory candidates awaiting review."""
    return list_candidates(_brain_dir())


@router.post("/api/memory-candidates/generate")
async def memory_candidates_generate(user: CurrentUser = Depends(get_current_user)):
    """Generate deterministic memory candidates from existing items."""
    _require_admin(user)
    return generate_candidates(_brain_dir())


@router.post("/api/memory-candidates/generate-semantic")
async def memory_candidates_generate_semantic(user: CurrentUser = Depends(get_current_user)):
    """Generate semantic proactive memory candidates from existing items."""
    _require_admin(user)
    return generate_semantic_candidates(_brain_dir())


@router.post("/api/memory-candidates/{candidate_id}/approve")
async def memory_candidate_approve(
    candidate_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """Approve a candidate and write the resulting item through WriteService."""
    _require_admin(user)
    try:
        return approve_candidate(_brain_dir(), candidate_id, reviewer=f"web:{user.username}")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="candidate not found") from None


@router.post("/api/memory-candidates/{candidate_id}/reject")
async def memory_candidate_reject(
    candidate_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """Reject a candidate without writing a memory item."""
    _require_admin(user)
    try:
        return reject_candidate(_brain_dir(), candidate_id, reviewer=f"web:{user.username}")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="candidate not found") from None
