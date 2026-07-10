"""Request-chain log diagnostic routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from agent_brain.product.chain_log import build_chain_log_detail, build_chain_log_report
from web._base import _brain_dir
from web.auth import CurrentUser, get_current_user, require_admin


router = APIRouter()


@router.get("/api/chain-logs")
def chain_logs(
    hours: int = Query(72, ge=1, le=72),
    limit: int = Query(100, ge=1, le=500),
    adapter: str | None = Query(None),
    session_id: str | None = Query(None),
    cwd: str | None = Query(None),
    status: str | None = Query(None, pattern="^(injected|blocked|partial|not_observed)$"),
    user: CurrentUser = Depends(get_current_user),
):
    """List sanitized request-chain log summaries."""

    require_admin(user)
    return build_chain_log_report(
        _brain_dir(),
        hours=hours,
        limit=limit,
        adapter=adapter,
        session_id=session_id,
        cwd=cwd,
        status=status,
    ).to_dict()


@router.get("/api/chain-logs/{chain_id}")
def chain_log_detail(
    chain_id: str,
    hours: int = Query(72, ge=1, le=72),
    user: CurrentUser = Depends(get_current_user),
):
    """Return one sanitized request-chain log detail."""

    require_admin(user)
    try:
        return build_chain_log_detail(_brain_dir(), chain_id, hours=hours).to_dict()
    except KeyError as exc:
        if exc.args != (chain_id,):
            raise
        raise HTTPException(status_code=404, detail="chain log not found") from exc
