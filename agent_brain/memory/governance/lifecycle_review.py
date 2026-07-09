"""Lifecycle review queue planning and explicit apply helpers."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from agent_brain.memory.governance.auto_governance import AutoGovernanceCycle
from agent_brain.memory.governance.maintenance_plan import (
    MaintenancePlan,
    build_maintenance_plan,
)
from agent_brain.memory.store.items_store import ItemsStore


def build_lifecycle_review_plan(
    *,
    brain_dir: Path,
    items_store: ItemsStore,
    limit_per_lane: int = 20,
) -> MaintenancePlan:
    """Build a read-only lifecycle review plan for Web/Admin and CLI callers."""
    report = AutoGovernanceCycle(
        brain_dir=brain_dir,
        items_store=items_store,
        include_index=False,
        include_evolve=False,
        include_conversations=False,
    ).run(apply=False)
    return build_maintenance_plan(
        report,
        limit_per_lane=limit_per_lane,
        category_filter="lifecycle",
    )


def apply_lifecycle_review_items(
    *,
    brain_dir: Path,
    items_store: ItemsStore,
    item_ids: list[str],
    apply: bool = False,
    index_repair: bool = True,
) -> dict[str, Any]:
    """Preview or archive explicitly selected lifecycle review queue items.

    Only IDs present in the current lifecycle review queue are eligible. This
    helper intentionally does not supersede memory items; supersession needs a
    replacement item and remains a separate manual action.
    """
    requested = list(dict.fromkeys(item_ids))
    plan = build_lifecycle_review_plan(
        brain_dir=brain_dir,
        items_store=items_store,
        limit_per_lane=max(len(list(items_store.iter_all())), len(requested), 1),
    )
    queue_by_id = {row.item_id: row for row in plan.review_queue}
    candidates = [
        queue_by_id[item_id].to_dict()
        for item_id in requested
        if item_id in queue_by_id
    ]
    skipped = [
        {"id": item_id, "reason": "not_in_lifecycle_review_queue"}
        for item_id in requested
        if item_id not in queue_by_id
    ]
    archived: list[str] = []
    failed: list[dict[str, str]] = []

    idx = None
    if apply and index_repair:
        db_path = brain_dir / "index.db"
        if db_path.exists():
            from agent_brain.platform.indexing.index import HubIndex

            idx = HubIndex(db_path=db_path)
    try:
        if apply:
            archive_dir = items_store.items_dir / "archived"
            archive_dir.mkdir(exist_ok=True)
            for item_id in requested:
                if item_id not in queue_by_id:
                    continue
                src = items_store.items_dir / f"{item_id}.md"
                if not src.exists():
                    failed.append({"id": item_id, "reason": "source_missing"})
                    continue
                try:
                    shutil.move(str(src), str(archive_dir / f"{item_id}.md"))
                    if idx is not None:
                        try:
                            idx.delete(item_id)
                        except Exception as exc:  # noqa: BLE001 - md archive is source-of-truth.
                            failed.append({"id": item_id, "reason": f"index_delete_failed:{exc}"})
                    archived.append(item_id)
                except Exception as exc:  # noqa: BLE001 - report per-item failure.
                    failed.append({"id": item_id, "reason": str(exc)})
    finally:
        if idx is not None:
            idx.close()

    return {
        "dry_run": not apply,
        "requested": requested,
        "candidates": candidates,
        "archived": archived,
        "skipped": skipped,
        "failed": failed,
        "boundary": "only current lifecycle review_queue items are eligible; supersede remains manual",
    }


__all__ = [
    "apply_lifecycle_review_items",
    "build_lifecycle_review_plan",
]
