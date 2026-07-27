"""Organization-scoped authorization backed by live server state."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, status

from web.auth import CurrentUser
from web.state_store import WebStateStore


ORG_ROLES = ("viewer", "member", "admin", "owner")
_ROLE_RANK = {role: rank for rank, role in enumerate(ORG_ROLES)}


@dataclass(frozen=True)
class OrganizationPrincipal:
    tenant_id: str
    username: str
    role: str


def ensure_organization_principal(
    store: WebStateStore,
    user: CurrentUser,
) -> OrganizationPrincipal:
    """Return a live org role, bootstrapping legacy users once.

    JWT/API-key roles are intentionally not used after bootstrap. This makes a
    membership downgrade effective immediately even while an old token remains
    otherwise valid.
    """

    store.ensure_organization(user.tenant_id, created_by=user.username)
    member = store.get_org_member(user.tenant_id, user.username)
    if member is None:
        legacy_role = "owner" if user.is_admin else "member"
        member = store.set_org_member(user.tenant_id, user.username, legacy_role)
    return OrganizationPrincipal(
        tenant_id=user.tenant_id,
        username=user.username,
        role=member["role"],
    )


def require_org_role(
    store: WebStateStore,
    user: CurrentUser,
    minimum: str,
) -> OrganizationPrincipal:
    if minimum not in _ROLE_RANK:
        raise ValueError(f"unknown organization role: {minimum}")
    principal = ensure_organization_principal(store, user)
    if _ROLE_RANK.get(principal.role, -1) < _ROLE_RANK[minimum]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"organization role {minimum} required",
        )
    return principal


def can_manage_role(actor_role: str, target_role: str) -> bool:
    """Owners manage every role; admins manage members and viewers."""

    if actor_role == "owner":
        return target_role in ORG_ROLES
    return actor_role == "admin" and target_role in {"member", "viewer"}


__all__ = [
    "ORG_ROLES",
    "OrganizationPrincipal",
    "can_manage_role",
    "ensure_organization_principal",
    "require_org_role",
]
