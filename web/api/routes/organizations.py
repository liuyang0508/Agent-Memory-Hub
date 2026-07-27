"""Organization directory, live RBAC, and cloud operations summary."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from web._base import _audit, _components, _state_store
from web.auth import CurrentUser, _load_users, get_current_user
from web.organization_access import (
    ORG_ROLES,
    can_manage_role,
    ensure_organization_principal,
    require_org_role,
)


router = APIRouter()
_USERNAME = re.compile(r"^[A-Za-z0-9._@-]{1,80}$")


class OrganizationUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class OrganizationMemberInput(BaseModel):
    username: str
    role: str = "member"


class OrganizationRoleUpdate(BaseModel):
    role: str


def _valid_role(role: str) -> str:
    normalized = role.strip().lower()
    if normalized not in ORG_ROLES:
        raise HTTPException(status_code=400, detail="invalid organization role")
    return normalized


def _known_tenant_user(username: str, tenant_id: str) -> bool:
    return any(
        row.get("username") == username
        and row.get("tenant_id", "default") == tenant_id
        for row in _load_users()
    )


def _organization_payload(user: CurrentUser) -> dict[str, Any]:
    store = _state_store()
    principal = ensure_organization_principal(store, user)
    organization = store.ensure_organization(
        user.tenant_id,
        created_by=user.username,
    )
    members = store.list_org_members(user.tenant_id)
    return {
        "organization": {
            "id": organization["tenant_id"],
            "name": organization["name"],
            "created_at": organization["created_at"],
        },
        "current_role": principal.role,
        "member_count": len(members),
    }


@router.get("/api/organization")
async def get_organization(
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    require_org_role(_state_store(), user, "viewer")
    return await run_in_threadpool(_organization_payload, user)


@router.patch("/api/organization")
async def update_organization(
    request: OrganizationUpdate,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    store = _state_store()
    require_org_role(store, user, "admin")
    organization = await run_in_threadpool(
        store.rename_organization,
        user.tenant_id,
        request.name.strip(),
    )
    _audit(user.username, "organization_rename", user.tenant_id)
    return {"organization": {"id": user.tenant_id, "name": organization["name"]}}


@router.get("/api/organization/members")
async def list_organization_members(
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    store = _state_store()
    principal = require_org_role(store, user, "viewer")
    members = await run_in_threadpool(store.list_org_members, user.tenant_id)
    return {"members": members, "current_role": principal.role}


@router.post("/api/organization/members")
async def add_organization_member(
    request: OrganizationMemberInput,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, str]:
    store = _state_store()
    principal = require_org_role(store, user, "admin")
    username = request.username.strip()
    role = _valid_role(request.role)
    if not _USERNAME.fullmatch(username):
        raise HTTPException(status_code=400, detail="invalid username")
    if not can_manage_role(principal.role, role):
        raise HTTPException(status_code=403, detail="owner role required")
    if not _known_tenant_user(username, user.tenant_id):
        raise HTTPException(
            status_code=404,
            detail="account must already exist in the current tenant",
        )
    member = await run_in_threadpool(
        store.set_org_member,
        user.tenant_id,
        username,
        role,
    )
    _audit(user.username, "organization_member_add", f"{username}:{role}")
    return member


@router.patch("/api/organization/members/{username}")
async def change_organization_member_role(
    username: str,
    request: OrganizationRoleUpdate,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, str]:
    store = _state_store()
    principal = require_org_role(store, user, "owner")
    role = _valid_role(request.role)
    current = store.get_org_member(user.tenant_id, username)
    if current is None:
        raise HTTPException(status_code=404, detail="organization member not found")
    if (
        current["role"] == "owner"
        and role != "owner"
        and store.count_org_owners(user.tenant_id) <= 1
    ):
        raise HTTPException(status_code=409, detail="organization requires at least one owner")
    if not can_manage_role(principal.role, role):
        raise HTTPException(status_code=403, detail="owner role required")
    member = await run_in_threadpool(
        store.set_org_member,
        user.tenant_id,
        username,
        role,
    )
    _audit(user.username, "organization_member_role", f"{username}:{role}")
    return member


@router.delete("/api/organization/members/{username}")
async def remove_organization_member(
    username: str,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    store = _state_store()
    principal = require_org_role(store, user, "admin")
    current = store.get_org_member(user.tenant_id, username)
    if current is None:
        raise HTTPException(status_code=404, detail="organization member not found")
    if not can_manage_role(principal.role, current["role"]):
        raise HTTPException(status_code=403, detail="owner role required")
    if current["role"] == "owner" and store.count_org_owners(user.tenant_id) <= 1:
        raise HTTPException(status_code=409, detail="organization requires at least one owner")
    removed = await run_in_threadpool(
        store.remove_org_member,
        user.tenant_id,
        username,
    )
    _audit(user.username, "organization_member_remove", username)
    return {"removed": removed, "username": username}


def _operations_summary(user: CurrentUser) -> dict[str, Any]:
    state = _state_store()
    principal = require_org_role(state, user, "viewer")
    organization = state.ensure_organization(
        user.tenant_id,
        created_by=user.username,
    )
    members = state.list_org_members(user.tenant_id)
    member_roles = Counter(row["role"] for row in members)
    memory_items = [
        item
        for item, _body in _components()[0].iter_all()
        if item.tenant_id == user.tenant_id
    ]
    return {
        "organization": {
            "id": user.tenant_id,
            "name": organization["name"],
            "current_role": principal.role,
        },
        "members": {
            "total": len(members),
            "by_role": {role: member_roles.get(role, 0) for role in ORG_ROLES},
        },
        "memory": {
            "items": len(memory_items),
            "needs_review": sum("needs-review" in item.tags for item in memory_items),
            "contested": sum("contested" in item.tags for item in memory_items),
        },
        "sync": state.sync_stats(user.tenant_id),
        "security": {
            "encryption": "AES-256-GCM",
            "server_plaintext": False,
            "tenant_isolation": True,
        },
    }


@router.get("/api/operations/summary")
async def operations_summary(
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    return await run_in_threadpool(_operations_summary, user)


@router.get("/api/operations/devices")
async def operations_devices(
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    store = _state_store()
    require_org_role(store, user, "viewer")
    devices = await run_in_threadpool(store.list_sync_devices, user.tenant_id)
    return {"devices": devices, "count": len(devices)}


__all__ = ["router"]
