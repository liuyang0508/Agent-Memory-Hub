"""Agent adapter capability routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from agent_brain.agent_integrations.capabilities import capabilities_for_all
from agent_brain.product.adapter_onboarding import (
    AdapterLifecycleResult,
    build_onboarding_summary,
    doctor_adapter,
    execute_adapter_action,
    install_verify_adapter,
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
    result = execute_adapter_action(
        _brain_dir(),
        name,
        "install",
        verifier=f"web:{user.username}",
    )
    return _lifecycle_http_result(result)


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
    result = execute_adapter_action(
        _brain_dir(),
        name,
        "uninstall",
        verifier=f"web:{user.username}",
    )
    return _lifecycle_http_result(result)


@router.post("/api/adapters/{name}/verify")
async def adapter_verify(name: str, user: CurrentUser = Depends(get_current_user)):
    """Promote an adapter only through doctor-backed verification."""
    _require_admin(user)
    return verify_adapter(_brain_dir(), name, verifier=f"web:{user.username}")


def _lifecycle_http_result(result: AdapterLifecycleResult) -> dict[str, object]:
    payload = result.to_dict()
    if result.status == "blocked":
        raise HTTPException(status_code=409, detail=payload)
    if result.status == "failed":
        raise HTTPException(status_code=422, detail=payload)
    return payload


@router.post("/api/adapters/{name}/repair")
async def adapter_repair(
    name: str,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, object]:
    """Repair only AMH-owned drift through the shared lifecycle executor."""
    _require_admin(user)
    result = execute_adapter_action(
        _brain_dir(),
        name,
        "repair",
        verifier=f"web:{user.username}",
    )
    return _lifecycle_http_result(result)


@router.post("/api/adapters/{name}/upgrade")
async def adapter_upgrade(
    name: str,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, object]:
    """Upgrade an adapter with a private rollback snapshot."""
    _require_admin(user)
    result = execute_adapter_action(
        _brain_dir(),
        name,
        "upgrade",
        verifier=f"web:{user.username}",
    )
    return _lifecycle_http_result(result)


@router.post("/api/adapters/{name}/release")
async def adapter_release(
    name: str,
    stage: str = Query(...),
    cohort_percent: int | None = Query(None, ge=1, le=100),
    reason: str = Query(""),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, object]:
    """Promote or disable one adapter without changing core CLI/MCP state."""
    from typing import cast

    from agent_brain.agent_integrations.release_controls import ReleaseStage, set_adapter_release

    _require_admin(user)
    if stage not in {"shadow", "canary", "default", "disabled"}:
        raise HTTPException(status_code=422, detail="invalid adapter release stage")
    try:
        result = set_adapter_release(
            _brain_dir(),
            name,
            cast(ReleaseStage, stage),
            cohort_percent=cohort_percent,
            reason=reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    payload = result.to_dict()
    if result.status != "passed":
        raise HTTPException(status_code=409, detail=payload)
    return payload
