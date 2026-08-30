"""Agent Memory Hub Web Admin — governance routes.

Moved verbatim from app.py (decorators rewritten @app.→@router.); request models for
this group travel with their handlers in original order so FastAPI's decoration-time
binding is unchanged. Infra (helpers/state) comes from web._base.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from web._base import *  # noqa: F401,F403  (state, helpers, models, lifespan, middleware)
from web.api.routes import governance_activity, governance_audit, governance_webhooks
from web.auth import CurrentUser, get_current_user

router = APIRouter()
router.include_router(governance_activity.router)
router.include_router(governance_audit.router)
router.include_router(governance_webhooks.router)


@router.get("/api/stats")
async def brain_stats(user: CurrentUser = Depends(get_current_user)):
    store, _, _, _ = _components()
    type_counts: dict[str, int] = {}
    project_counts: dict[str, int] = {}
    tag_counts: dict[str, int] = {}
    agent_counts: dict[str, int] = {}
    sensitivity_counts: dict[str, int] = {}
    conf_buckets = [0] * 10
    total = 0
    oldest: str | None = None
    newest: str | None = None
    for item, _ in store.iter_all():
        if not _visible(item, user):
            continue
        total += 1
        t = str(item.type)
        type_counts[t] = type_counts.get(t, 0) + 1
        p = item.project or "(none)"
        project_counts[p] = project_counts.get(p, 0) + 1
        for tag in item.tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
        bucket = min(int((item.confidence or 0) * 10), 9)
        conf_buckets[bucket] += 1
        agent = item.agent or "(unknown)"
        agent_counts[agent] = agent_counts.get(agent, 0) + 1
        sens = str(item.sensitivity) if item.sensitivity else "internal"
        sensitivity_counts[sens] = sensitivity_counts.get(sens, 0) + 1
        ts = item.created_at.isoformat()
        if oldest is None or ts < oldest:
            oldest = ts
        if newest is None or ts > newest:
            newest = ts
    return {
        "total": total,
        "by_type": dict(sorted(type_counts.items(), key=lambda x: -x[1])),
        "by_project": dict(sorted(project_counts.items(), key=lambda x: -x[1])[:10]),
        "top_tags": dict(sorted(tag_counts.items(), key=lambda x: -x[1])[:20]),
        "confidence_distribution": conf_buckets,
        "by_agent": dict(sorted(agent_counts.items(), key=lambda x: -x[1])),
        "by_sensitivity": sensitivity_counts,
        "date_range": {"oldest": oldest, "newest": newest},
    }

class GcRequest(BaseModel):
    max_age_days: int = 7
    tags: list[str] | None = None
    dry_run: bool = True

@router.post("/api/gc")
async def garbage_collect(req: GcRequest, user: CurrentUser = Depends(get_current_user)):
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    store, _, _, _ = _components()
    cutoff = datetime.now(timezone.utc) - timedelta(days=req.max_age_days)
    target_tags = set(req.tags) if req.tags else {"session-end", "auto-captured", "needs-review"}
    candidates = []
    deleted = 0
    for item, _ in store.iter_all():
        if not set(item.tags).intersection(target_tags):
            continue
        if item.created_at >= cutoff:
            continue
        candidates.append({"id": item.id, "title": item.title, "type": str(item.type)})
        if not req.dry_run:
            md_path = store.items_dir / f"{item.id}.md"
            if md_path.exists():
                md_path.unlink()
                _evict_from_index(item.id)
                deleted += 1
    return {"deleted": deleted, "candidates": candidates, "dry_run": req.dry_run}


class LifecycleActionRequest(BaseModel):
    action: Literal[
        "supersede", "archive", "keep-active", "defer", "revert-supersession"
    ]
    item_id: str
    replacement_id: str | None = None
    defer_days: int | None = None

    @model_validator(mode="after")
    def validate_action_arguments(self) -> LifecycleActionRequest:
        from agent_brain.contracts.memory_item import is_valid_memory_item_id

        if not is_valid_memory_item_id(self.item_id):
            raise ValueError("item_id must be a canonical memory item id")
        if self.action in {"supersede", "revert-supersession"}:
            if self.replacement_id is None:
                raise ValueError("replacement_id required")
            if not is_valid_memory_item_id(self.replacement_id):
                raise ValueError("replacement_id must be a canonical memory item id")
        elif self.replacement_id is not None:
            raise ValueError("replacement_id is only valid for supersession actions")
        if self.action == "defer" and (
            type(self.defer_days) is not int or not 1 <= self.defer_days <= 365
        ):
            raise ValueError("defer_days must be between 1 and 365")
        if self.action != "defer" and self.defer_days is not None:
            raise ValueError("defer_days is only valid for defer")
        return self


class LifecycleApplyRequest(BaseModel):
    actions: list[LifecycleActionRequest] = Field(default_factory=list)
    item_ids: list[str] = Field(default_factory=list)
    apply: bool = False
    index_repair: bool = True

    @model_validator(mode="after")
    def validate_legacy_item_ids(self) -> LifecycleApplyRequest:
        from agent_brain.contracts.memory_item import is_valid_memory_item_id

        if any(not is_valid_memory_item_id(item_id) for item_id in self.item_ids):
            raise ValueError("item_ids must contain canonical memory item ids")
        return self


@router.get("/api/governance/lifecycle-review")
async def lifecycle_review(
    limit: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(get_current_user),
):
    """Return the current read-only lifecycle review queue. Admin only."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")

    from agent_brain.memory.governance.lifecycle_review import (
        build_lifecycle_review_plan,
    )
    from agent_brain.memory.store.items_store import ItemsStore

    brain = _brain_dir()
    plan = build_lifecycle_review_plan(
        brain_dir=brain,
        items_store=ItemsStore(items_dir=brain / "items"),
        limit_per_lane=limit,
    )
    return plan.to_dict()


@router.post("/api/governance/lifecycle-apply")
async def lifecycle_apply(
    req: LifecycleApplyRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """Preview or apply selected lifecycle review actions. Admin only."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    if not req.actions and not req.item_ids:
        raise HTTPException(status_code=400, detail="actions or item_ids required")

    from agent_brain.memory.governance.lifecycle_review import (
        LifecycleReviewAction,
        apply_lifecycle_review_actions,
        apply_lifecycle_review_items,
        conflicting_lifecycle_action_item,
    )
    from agent_brain.memory.store.items_store import ItemsStore

    brain = _brain_dir()
    if req.item_ids and not req.actions:
        return apply_lifecycle_review_items(
            brain_dir=brain,
            items_store=ItemsStore(items_dir=brain / "items"),
            item_ids=req.item_ids,
            apply=req.apply,
            index_repair=req.index_repair,
        )
    actions = [
        LifecycleReviewAction(
            action=action.action,
            item_id=action.item_id,
            replacement_id=action.replacement_id,
            defer_days=action.defer_days,
        )
        for action in req.actions
    ]
    actions.extend(
        LifecycleReviewAction(action="archive", item_id=item_id)
        for item_id in req.item_ids
    )
    conflict = conflicting_lifecycle_action_item(actions)
    if conflict is not None:
        raise HTTPException(
            status_code=400,
            detail={"code": "CONFLICTING_ACTIONS", "item_id": conflict},
        )
    return apply_lifecycle_review_actions(
        brain_dir=brain,
        items_store=ItemsStore(items_dir=brain / "items"),
        actions=actions,
        apply=req.apply,
        index_repair=req.index_repair,
    )


class EvolveRequest(BaseModel):
    apply: bool = False
    decay_archive_threshold: float = 0.1

@router.post("/api/evolve")
async def evolve_items(req: EvolveRequest, user: CurrentUser = Depends(get_current_user)):
    """Run self-evolve engine. Admin only."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")

    from agent_brain.memory.governance.audit.scanner import SkillScanner
    from agent_brain.memory.governance.evolve.engine import EvolveEngine
    from agent_brain.product.evolution_control import build_evolution_control_report

    store, idx, _, _ = _components()
    scanner = SkillScanner()
    engine = EvolveEngine(
        items_store=store,
        scanner=scanner,
        dry_run=not req.apply,
        index=idx,
        decay_archive_threshold=req.decay_archive_threshold,
    )
    report = engine.evolve()
    control = build_evolution_control_report(
        _brain_dir(),
        apply_requested=req.apply,
        evolve_report=report,
    )
    return {
        "scanned_items": report.scanned_items,
        "total_proposals": len(report.proposals),
        "audit_blocked": report.audit_blocked,
        "approved": len(report.approved_proposals),
        "executed": report.executed,
        "proposals": [
            {
                "action": p.action.value,
                "item_ids": p.item_ids[:5],
                "title": p.title,
                "description": p.description,
                "confidence": round(p.confidence, 2),
                "audit_passed": p.audit_passed,
            }
            for p in report.proposals[:30]
        ],
        "evolution_control": control.to_dict(),
    }
