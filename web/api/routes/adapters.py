"""Agent adapter capability routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from agent_brain.agent_integrations.capabilities import capabilities_for_all
from agent_brain.product.adapter_onboarding import (
    build_onboarding_summary,
    doctor_adapter,
    install_verify_adapter,
    install_adapter,
    uninstall_adapter,
    verify_adapter,
)
from web._base import _brain_dir
from web.auth import CurrentUser, get_current_user


router = APIRouter()


def _require_admin(user: CurrentUser) -> None:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")


def _adapter_capabilities_payload() -> list[dict[str, object]]:
    return [cap.to_dict() for cap in capabilities_for_all(_brain_dir())]


@router.get("/api/adapters/capabilities")
async def adapter_capabilities(user: CurrentUser = Depends(get_current_user)):
    """Return adapter truth-contract records for the web dashboard."""
    return await run_in_threadpool(_adapter_capabilities_payload)


@router.get("/api/adapters/onboarding")
async def adapter_onboarding(user: CurrentUser = Depends(get_current_user)):
    """Return adapter onboarding and verification next actions."""
    return await run_in_threadpool(build_onboarding_summary, _brain_dir())


@router.get("/api/adapters/{name}/doctor")
async def adapter_doctor(name: str, user: CurrentUser = Depends(get_current_user)):
    """Run adapter preflight diagnostics without mutating verification state."""
    return doctor_adapter(_brain_dir(), name)


@router.post("/api/adapters/{name}/install")
async def adapter_install(name: str, user: CurrentUser = Depends(get_current_user)):
    """Install supported adapter configuration for the current brain dir."""
    _require_admin(user)
    result = install_adapter(_brain_dir(), name)
    if result.get("status") in {"failed", "unsupported"}:
        raise HTTPException(status_code=422, detail=result)
    return result


@router.post("/api/adapters/{name}/install-verify")
async def adapter_install_verify(
    name: str,
    uninstall_check: bool = Query(False),
    user: CurrentUser = Depends(get_current_user),
):
    """Run one-click install + doctor/runtime verification."""
    _require_admin(user)
    return install_verify_adapter(
        _brain_dir(),
        name,
        verifier=f"web:{user.username}",
        uninstall_check=uninstall_check,
    )


@router.post("/api/adapters/{name}/uninstall")
async def adapter_uninstall(name: str, user: CurrentUser = Depends(get_current_user)):
    """Remove hub-owned adapter config for a supported adapter."""
    _require_admin(user)
    result = uninstall_adapter(_brain_dir(), name)
    if result.get("status") in {"failed", "unsupported"}:
        raise HTTPException(status_code=422, detail=result)
    return result


@router.post("/api/adapters/{name}/verify")
async def adapter_verify(name: str, user: CurrentUser = Depends(get_current_user)):
    """Promote an adapter only through doctor-backed verification."""
    _require_admin(user)
    return verify_adapter(_brain_dir(), name, verifier=f"web:{user.username}")
