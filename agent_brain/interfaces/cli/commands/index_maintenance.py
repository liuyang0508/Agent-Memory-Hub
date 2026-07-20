"""Index maintenance helpers for CLI storage commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_brain.memory.recall.embedding_text import embedding_text_for_item
from agent_brain.memory.store.pending import (
    clear_dirty_index_marker,
    read_dirty_index_marker,
)


@dataclass(frozen=True)
class ReindexResult:
    indexed: int
    pruned: int = 0


@dataclass(frozen=True)
class IndexDrift:
    md_ids: set[str]
    index_ids: set[str]
    missing_in_index: set[str]
    orphan_in_index: set[str]


def reindex_store(store: Any, idx: Any, embedder: Any, *, prune: bool = False) -> ReindexResult:
    items_dir = getattr(store, "items_dir", None)
    dirty_marker_before = (
        read_dirty_index_marker(items_dir.parent)
        if items_dir is not None
        else None
    )
    md_ids: set[str] = set()
    indexed = 0
    for item, body in store.iter_all():
        idx.upsert(item, body, embedding=embedder.embed(embedding_text_for_item(item)))
        md_ids.add(item.id)
        indexed += 1
    scan_complete = _store_scan_complete(store)

    pruned = 0
    if prune:
        pruned = idx.prune(md_ids)
    if items_dir is not None and scan_complete:
        if not clear_dirty_index_marker(
            items_dir.parent,
            repaired_ids=(
                dirty_marker_before.item_ids
                if dirty_marker_before is not None
                else frozenset()
            ),
            expected_entries=(
                dirty_marker_before.entries
                if dirty_marker_before is not None
                else ()
            ),
        ):
            raise OSError("INDEX_DIRTY_MARKER_CLEAR_FAILED")
    return ReindexResult(indexed=indexed, pruned=pruned)


def inspect_index_drift(store: Any, idx: Any) -> IndexDrift:
    md_ids = {item.id for item, _ in store.iter_all()}
    index_ids = idx.all_ids()
    return IndexDrift(
        md_ids=md_ids,
        index_ids=index_ids,
        missing_in_index=md_ids - index_ids,
        orphan_in_index=index_ids - md_ids,
    )


def repair_index_drift(store: Any, idx: Any, embedder: Any, drift: IndexDrift) -> ReindexResult:
    items_dir = getattr(store, "items_dir", None)
    dirty_marker_before = (
        read_dirty_index_marker(items_dir.parent)
        if items_dir is not None
        else None
    )
    repaired = 0
    for item, body in store.iter_all():
        idx.upsert(item, body, embedding=embedder.embed(embedding_text_for_item(item)))
        repaired += 1
    repair_scan_complete = _store_scan_complete(store)

    pruned = 0
    for ghost_id in drift.orphan_in_index:
        idx.delete(ghost_id)
        pruned += 1
    if items_dir is not None and repair_scan_complete:
        remaining = inspect_index_drift(store, idx)
        verification_scan_complete = _store_scan_complete(store)
        if verification_scan_complete:
            cleared = clear_dirty_index_marker(
                items_dir.parent,
                repaired_ids=(
                    dirty_marker_before.item_ids
                    if not remaining.missing_in_index and not remaining.orphan_in_index
                    and dirty_marker_before is not None
                    else frozenset()
                ),
                expected_entries=(
                    dirty_marker_before.entries
                    if dirty_marker_before is not None
                    else ()
                ),
            )
            if not cleared:
                raise OSError("INDEX_DIRTY_MARKER_CLEAR_FAILED")
    return ReindexResult(indexed=repaired, pruned=pruned)


def _store_scan_complete(store: Any) -> bool:
    stats = getattr(store, "last_scan", None)
    return stats is None or (
        int(getattr(stats, "skipped_count", 0)) == 0
        and not bool(getattr(stats, "errors", ()))
        and not bool(getattr(stats, "truncated", False))
    )


__all__ = [
    "IndexDrift",
    "ReindexResult",
    "inspect_index_drift",
    "reindex_store",
    "repair_index_drift",
]
