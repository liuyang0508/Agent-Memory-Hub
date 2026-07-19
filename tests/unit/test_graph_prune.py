from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.interfaces.cli.commands.index_maintenance import reindex_store
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex

_DIM = 8


def _item(suffix: str) -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260720-130000-{suffix}",
        type=MemoryType.fact,
        created_at=datetime(2026, 7, 20, 13, 0, tzinfo=timezone.utc),
        title=f"Graph prune {suffix}",
        summary=f"Graph prune summary {suffix}",
    )


def _write_and_index(
    store: ItemsStore,
    index: HubIndex,
    item: MemoryItem,
    *,
    write_markdown: bool,
) -> None:
    if write_markdown:
        store.write(item, item.summary)
    index.upsert(item, item.summary, embedding=None)


def test_reindex_prune_removes_dangling_graph_and_keeps_valid_custom_edges(
    tmp_brain_dir: Path,
):
    store = ItemsStore(tmp_brain_dir / "items")
    index = HubIndex(tmp_brain_dir / "index.db", embedding_dim=_DIM)
    active_a = _item("active-a")
    active_b = _item("active-b")
    ghost = _item("ghost")
    for item in (active_a, active_b):
        _write_and_index(store, index, item, write_markdown=True)
    _write_and_index(store, index, ghost, write_markdown=False)
    missing_source = "mem-20260720-130000-missing-source"
    missing_target = "mem-20260720-130000-missing-target"
    index.add_ref(active_a.id, active_b.id, "refines")
    index.add_ref(active_a.id, missing_target, "custom-target")
    index.add_ref(missing_source, active_b.id, "custom-source")
    index.add_ref(ghost.id, active_a.id, "custom-ghost")
    embedder = HashingEmbedder(dim=_DIM)

    first = reindex_store(store, index, embedder, prune=True)
    second = reindex_store(store, index, embedder, prune=True)

    assert first.pruned == 1
    assert second.pruned == 0
    rows = index.connection.execute(
        "SELECT source_id, target_id, relation FROM refs_graph ORDER BY relation"
    ).fetchall()
    assert rows == [(active_a.id, active_b.id, "refines")]


def test_prune_rolls_back_items_and_graph_on_failure(
    tmp_brain_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    store = ItemsStore(tmp_brain_dir / "items")
    index = HubIndex(tmp_brain_dir / "index.db", embedding_dim=_DIM)
    active = _item("tx-active")
    ghost_a = _item("tx-ghost-a")
    ghost_b = _item("tx-ghost-b")
    _write_and_index(store, index, active, write_markdown=True)
    _write_and_index(store, index, ghost_a, write_markdown=False)
    _write_and_index(store, index, ghost_b, write_markdown=False)
    index.add_ref(ghost_a.id, active.id, "custom-a")
    index.add_ref(ghost_b.id, active.id, "custom-b")
    original_delete = index.writer.vector.delete
    calls = 0

    def fail_second_vector_delete(item_id: str):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected prune failure")
        original_delete(item_id)

    monkeypatch.setattr(index.writer.vector, "delete", fail_second_vector_delete)

    with pytest.raises(OSError, match="injected prune failure"):
        index.prune({active.id})

    assert index.all_ids() == {active.id, ghost_a.id, ghost_b.id}
    rows = index.connection.execute(
        "SELECT source_id, target_id, relation FROM refs_graph ORDER BY relation"
    ).fetchall()
    assert rows == [
        (ghost_a.id, active.id, "custom-a"),
        (ghost_b.id, active.id, "custom-b"),
    ]
