"""Agent Memory Hub Web Admin — governance routes.

Moved verbatim from app.py (decorators rewritten @app.→@router.); request models for
this group travel with their handlers in original order so FastAPI's decoration-time
binding is unchanged. Infra (helpers/state) comes from web._base.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

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
