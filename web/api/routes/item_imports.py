"""Import routes for item data."""

from __future__ import annotations

from typing import Any

import yaml as _yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.memory.governance.audit.scanner import audit_memory_text
from web._base import _audit, _components, _write_service
from web.auth import CurrentUser, get_current_user


router = APIRouter()


class ImportRequest(BaseModel):
    items: list[dict[str, Any]]
    overwrite: bool = False
    strategy: str = ""


@router.post("/api/import")
async def import_items(req: ImportRequest, user: CurrentUser = Depends(get_current_user)):
    """Import items from JSON. Admin only.

    strategy: "skip" (default), "overwrite", "merge" (merge tags + append body).
    Legacy `overwrite=True` maps to strategy="overwrite".
    """
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    strat = req.strategy or ("overwrite" if req.overwrite else "skip")
    if strat not in ("skip", "overwrite", "merge"):
        raise HTTPException(status_code=400, detail="strategy must be skip, overwrite, or merge")
    store, idx, _, embedder = _components()
    write_service = _write_service()
    imported, skipped, merged, blocked, errors = 0, 0, 0, 0, 0
    for rec in req.items:
        try:
            fm = rec.get("frontmatter", rec)
            body = rec.get("body", "")
            item = MemoryItem(**fm)
            md_path = store.items_dir / f"{item.id}.md"
            if md_path.exists():
                if strat == "skip":
                    skipped += 1
                    continue
                elif strat == "merge":
                    existing_item, existing_body = store.get(item.id)
                    merged_tags = sorted(set(existing_item.tags) | set(item.tags))
                    merged_body = existing_body
                    if body and body not in existing_body:
                        merged_body = existing_body.rstrip() + "\n\n" + body
                    audit_report = audit_memory_text(
                        f"{existing_item.title}\n{existing_item.summary}\n{merged_body}"
                    )
                    if not audit_report.passed:
                        blocked += 1
                        continue
                    updated_item = store.update_frontmatter(item.id, tags=merged_tags)
                    if merged_body != existing_body:
                        fm_data = updated_item.model_dump(mode="json", exclude_none=False)
                        yaml_text = _yaml.safe_dump(
                            fm_data,
                            allow_unicode=True,
                            sort_keys=False,
                            default_flow_style=False,
                        )
                        md_path.write_text(
                            f"---\n{yaml_text}---\n\n{merged_body.rstrip()}\n",
                            encoding="utf-8",
                        )
                    final_item, final_body = store.get(item.id)
                    idx.upsert(
                        final_item,
                        final_body,
                        embedding=embedder.embed(final_item.context_views.locator),
                    )
                    merged += 1
                    continue
                else:
                    md_path.unlink()
            result = write_service.write(item=item, body=body)
            if result.status == "blocked":
                blocked += 1
                continue
            imported += 1
        except Exception:
            errors += 1
    _audit(user.username, "import", f"{imported} imported, {skipped} skipped, {merged} merged")
    return {
        "imported": imported,
        "skipped": skipped,
        "merged": merged,
        "blocked": blocked,
        "errors": errors,
    }
