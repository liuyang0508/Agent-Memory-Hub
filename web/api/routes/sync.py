"""Opaque storage routes for end-to-end encrypted device synchronization."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from web._base import _audit, _state_store
from web.auth import CurrentUser, get_current_user
from web.organization_access import require_org_role


router = APIRouter()
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_DEVICE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
_MAX_PAYLOAD = 2 * 1024 * 1024


class SyncObjectInput(BaseModel):
    object_key: str
    payload: str = Field(max_length=_MAX_PAYLOAD)
    device_id: str


class SyncObjectsRequest(BaseModel):
    objects: list[SyncObjectInput] = Field(max_length=1000)


class SyncHeartbeatRequest(BaseModel):
    device_id: str
    device_name: str = Field(default="", max_length=120)
    client_version: str = Field(default="", max_length=40)
    object_count: int = Field(default=0, ge=0)


@router.post("/api/sync/objects")
async def upload_sync_objects(
    request: SyncObjectsRequest,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, int]:
    require_org_role(_state_store(), user, "member")
    rows: list[dict[str, str]] = []
    for row in request.objects:
        if not _HEX64.fullmatch(row.object_key) or not _DEVICE.fullmatch(row.device_id):
            raise HTTPException(status_code=400, detail="invalid encrypted sync object")
        rows.append(row.model_dump())
    accepted, existing = await run_in_threadpool(
        _state_store().add_sync_objects,
        user.tenant_id,
        rows,
    )
    _audit(user.username, "sync_push", f"accepted={accepted} existing={existing}")
    return {"accepted": accepted, "existing": existing}


@router.get("/api/sync/objects")
async def download_sync_objects(
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, object]:
    require_org_role(_state_store(), user, "viewer")
    rows = await run_in_threadpool(
        _state_store().list_sync_objects,
        user.tenant_id,
    )
    return {"objects": rows, "count": len(rows)}


@router.post("/api/sync/heartbeat")
async def sync_heartbeat(
    request: SyncHeartbeatRequest,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, bool]:
    store = _state_store()
    require_org_role(store, user, "member")
    if not _DEVICE.fullmatch(request.device_id):
        raise HTTPException(status_code=400, detail="invalid sync device")
    await run_in_threadpool(
        store.touch_sync_device,
        user.tenant_id,
        device_id=request.device_id,
        device_name=request.device_name.strip(),
        client_version=request.client_version.strip(),
        object_count=request.object_count,
    )
    return {"accepted": True}


__all__ = ["router"]
