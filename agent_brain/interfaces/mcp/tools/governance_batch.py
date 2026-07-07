"""MCP governance batch mutation helpers."""

from __future__ import annotations

import shutil
from typing import Any

from agent_brain.interfaces.mcp.tools._shared import _components, _resolve_item_path


def batch_confirm(
    item_ids: list[str],
    confidence: float = 0.9,
) -> dict[str, Any]:
    """Confirm multiple memory items at once.

    Sets confidence for each item in both the md file and sqlite index.
    Returns results per item (success or error).

    WHEN TO USE
    -----------
    After bulk verification, e.g. a `drift_check` run produced a list of
    "stale" candidates that you manually verified are still valid — batch
    confirm them in one call to refresh confidence and reset the staleness
    clock.
    """
    store, idx, _ = _components()
    # Match confirm_memory / HubIndex clamping so an out-of-range confidence is
    # clamped rather than rejected by the md schema for some items only.
    confidence = min(max(confidence, 0.0), 1.0)
    results: list[dict[str, Any]] = []
    for item_id in item_ids:
        try:
            store.update_frontmatter(item_id, confidence=confidence)
            idx.update_confidence(item_id, confidence)
            results.append({"id": item_id, "status": "ok", "confidence": confidence})
        except FileNotFoundError:
            results.append({"id": item_id, "status": "not_found"})
        except Exception as e:
            results.append({"id": item_id, "status": "error", "detail": str(e)})
    return {
        "total": len(item_ids),
        "confirmed": sum(1 for r in results if r["status"] == "ok"),
        "results": results,
    }


def batch_archive(
    item_ids: list[str],
) -> dict[str, Any]:
    """Archive multiple memory items at once.

    Moves each item to items/archived/ and removes from the sqlite index.

    WHEN TO USE
    -----------
    After `evolve_memory` or `govern` proposed archival of decayed items AND
    the user approved. Archive is reversible (md files are moved, not
    deleted) but invisible to search until restored.
    """
    store, idx, _ = _components()
    archive_dir = store.items_dir / "archived"
    archive_dir.mkdir(exist_ok=True)
    results: list[dict[str, Any]] = []
    for item_id in item_ids:
        try:
            src = _resolve_item_path(store, item_id)
        except ValueError as e:
            results.append({"id": item_id, "status": "error", "detail": str(e)})
            continue
        if not src.exists():
            results.append({"id": item_id, "status": "not_found"})
            continue
        try:
            dst = archive_dir / f"{item_id}.md"
            shutil.move(str(src), str(dst))
            try:
                idx.delete(item_id)
            except Exception:
                pass
            results.append({"id": item_id, "status": "archived"})
        except Exception as e:
            results.append({"id": item_id, "status": "error", "detail": str(e)})
    return {
        "total": len(item_ids),
        "archived": sum(1 for r in results if r["status"] == "archived"),
        "results": results,
    }


__all__ = ["batch_archive", "batch_confirm"]
