"""Maintenance routes for item data."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from web._base import _audit, _components, _visible
from web.auth import CurrentUser, get_current_user


router = APIRouter()


@router.get("/api/decay-status")
async def decay_status(
    top_n: int = Query(30, le=100),
    project: str | None = None,
    user: CurrentUser = Depends(get_current_user),
):
    """Return items ranked by effective score (confidence * retention decay)."""
    from agent_brain.memory.recall.retrieval import retention_factor
    from agent_brain.contracts.memory_item import DECAY_HALF_LIFE_DAYS

    store, _, _, _ = _components()
    now = datetime.now(timezone.utc)
    rows = []
    for item, _ in store.iter_all():
        if not _visible(item, user):
            continue
        if project and item.project != project:
            continue
        last_acc = item.retention.last_accessed
        if last_acc:
            if last_acc.tzinfo is None:
                last_acc = last_acc.replace(tzinfo=timezone.utc)
            days = (now - last_acc).total_seconds() / 86400
        else:
            created = item.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            days = (now - created).total_seconds() / 86400
        dc = str(item.retention.decay_class)
        rf = retention_factor(dc, days)
        effective = item.confidence * rf
        rows.append(
            {
                "id": item.id,
                "type": str(item.type),
                "title": item.title,
                "confidence": item.confidence,
                "decay_class": dc,
                "half_life": DECAY_HALF_LIFE_DAYS.get(dc, 60),
                "days_since": round(days, 1),
                "retention": round(rf, 3),
                "effective": round(effective, 3),
                "access_count": item.retention.access_count,
            }
        )
    rows.sort(key=lambda r: r["effective"])
    return {"items": rows[:top_n], "total": len(rows)}


class ObsidianExportRequest(BaseModel):
    vault_path: str
    overwrite: bool = False


@router.post("/api/obsidian/export")
async def obsidian_export(
    req: ObsidianExportRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """Export all items to an Obsidian vault directory."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    from agent_brain.memory.evidence.integrations.obsidian import ObsidianSync

    store, _, _, _ = _components()
    vault = Path(req.vault_path).expanduser()
    if not vault.exists():
        vault.mkdir(parents=True, exist_ok=True)
    sync = ObsidianSync(items_store=store, vault_dir=vault)
    report = sync.export_all(overwrite=req.overwrite)
    _audit(user.username, "obsidian_export", f"{report.exported} exported to {vault}")
    return {"exported": report.exported, "skipped": report.skipped, "vault_path": str(vault)}


class ObsidianImportRequest(BaseModel):
    vault_path: str


@router.post("/api/obsidian/import")
async def obsidian_import(
    req: ObsidianImportRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """Import markdown files from an Obsidian vault."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    from agent_brain.memory.evidence.integrations.obsidian import ObsidianSync

    store, idx, _, embedder = _components()
    vault = Path(req.vault_path).expanduser()
    if not vault.exists():
        raise HTTPException(status_code=404, detail="vault path not found")
    sync = ObsidianSync(items_store=store, vault_dir=vault)
    report = sync.import_from_vault()
    for item, body in store.iter_all():
        idx.upsert(
            item,
            body,
            embedding=embedder.embed(item.context_views.locator),
        )
    _audit(user.username, "obsidian_import", f"{report.exported} imported from {vault}")
    return {"imported": report.exported, "skipped": report.skipped, "vault_path": str(vault)}


@router.post("/api/reindex")
async def reindex(user: CurrentUser = Depends(get_current_user)):
    """Rebuild the vector search index from all items. Admin only."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    store, idx, _, embedder = _components()
    count = 0
    for item, body in store.iter_all():
        embedding = embedder.embed(item.context_views.locator)
        idx.upsert(item, body, embedding=embedding)
        count += 1
    _audit(user.username, "reindex", f"{count} items reindexed")
    return {"reindexed": count}
