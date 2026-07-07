"""Export routes for item data."""

from __future__ import annotations

import csv
import io
import zipfile
from typing import Any

import yaml
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from web._base import _components, _visible
from web.auth import CurrentUser, get_current_user


router = APIRouter()


@router.get("/api/export")
async def export_items(
    type: str | None = None,
    project: str | None = None,
    user: CurrentUser = Depends(get_current_user),
):
    """Export visible items as JSON array."""
    store, _, _, _ = _components()
    records = []
    for item, body in store.iter_all():
        if not _visible(item, user):
            continue
        if type and str(item.type) != type:
            continue
        if project and item.project != project:
            continue
        records.append({"frontmatter": item.model_dump(mode="json"), "body": body})
    return {"items": records, "count": len(records)}


@router.get("/api/export/csv")
async def export_csv(
    type: str | None = None,
    project: str | None = None,
    user: CurrentUser = Depends(get_current_user),
):
    """Export visible items as CSV."""
    store, _, _, _ = _components()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["id", "type", "title", "summary", "project", "confidence", "tags", "created_at"]
    )
    count = 0
    for item, _ in store.iter_all():
        if not _visible(item, user):
            continue
        if type and str(item.type) != type:
            continue
        if project and item.project != project:
            continue
        writer.writerow(
            [
                _csv_safe(item.id),
                _csv_safe(str(item.type)),
                _csv_safe(item.title),
                _csv_safe(item.summary or ""),
                _csv_safe(item.project or ""),
                item.confidence,
                _csv_safe(",".join(item.tags)),
                item.created_at.isoformat(),
            ]
        )
        count += 1
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=memory-export-{count}.csv"
        },
    )


@router.get("/api/export/markdown")
async def export_markdown(
    type: str = Query("", description="Filter by type"),
    project: str = Query("", description="Filter by project"),
    user: CurrentUser = Depends(get_current_user),
):
    """Export all items as a ZIP of Markdown files."""
    store, _, _, _ = _components()
    buf = io.BytesIO()
    count = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for item, body in store.iter_all():
            if not _visible(item, user):
                continue
            if type and str(item.type) != type:
                continue
            if project and item.project != project:
                continue
            fm: dict[str, Any] = {
                "id": item.id,
                "type": str(item.type),
                "title": item.title,
            }
            if item.summary:
                fm["summary"] = item.summary
            if item.project:
                fm["project"] = item.project
            fm["confidence"] = item.confidence
            if item.tags:
                fm["tags"] = list(item.tags)
            fm["created_at"] = item.created_at.isoformat()
            yaml_text = yaml.safe_dump(
                fm,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            )
            md_content = f"---\n{yaml_text}---\n\n{body}"
            zf.writestr(f"{item.id}.md", md_content)
            count += 1
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=memory-export-{count}.zip"},
    )


def _csv_safe(value: object) -> str:
    # Guard against CSV/spreadsheet formula injection (CWE-1236): cells
    # starting with =, +, -, @, tab or CR are prefixed with a single quote.
    s = "" if value is None else str(value)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s
