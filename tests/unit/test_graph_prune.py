from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
import threading

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.interfaces.cli.commands.index_maintenance import reindex_store
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.store.pending import dirty_index_path
from agent_brain.product.governance_readiness import build_memory_lifecycle_readiness
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


def test_successful_full_reindex_clears_dirty_marker_and_readiness_warning(
    tmp_brain_dir: Path,
):
    store = ItemsStore(tmp_brain_dir / "items")
    index = HubIndex(tmp_brain_dir / "index.db", embedding_dim=_DIM)
    item = _item("dirty-close")
    store.write(item, item.summary)
    marker = dirty_index_path(tmp_brain_dir)
    marker.write_text(
        f"{item.id}\nmem-20260720-120004-deleted-dirty-item\n",
        encoding="utf-8",
    )

    result = reindex_store(store, index, HashingEmbedder(dim=_DIM), prune=True)
    index.close()

    assert result.indexed == 1
    assert not marker.exists() or marker.read_text(encoding="utf-8") == ""
    lane = build_memory_lifecycle_readiness(tmp_brain_dir)
    assert lane.metrics["index_dirty_status"] == "clean"


def test_failed_reindex_preserves_dirty_marker(tmp_brain_dir: Path):
    store = ItemsStore(tmp_brain_dir / "items")
    index = HubIndex(tmp_brain_dir / "index.db", embedding_dim=_DIM)
    item = _item("dirty-failure")
    store.write(item, item.summary)
    marker = dirty_index_path(tmp_brain_dir)
    marker.write_text(f"{item.id}\n", encoding="utf-8")

    class FailingIndex:
        def upsert(self, *_args, **_kwargs):
            raise OSError("injected reindex failure")

    with pytest.raises(OSError, match="injected reindex failure"):
        reindex_store(store, FailingIndex(), HashingEmbedder(dim=_DIM))

    index.close()
    assert marker.read_text(encoding="utf-8") == f"{item.id}\n"


def test_clear_dirty_marker_preserves_append_between_read_and_lock(
    tmp_brain_dir: Path,
    monkeypatch,
):
    import agent_brain.memory.store.pending as pending_module

    item_a = _item("dirty-race-a")
    item_b = _item("dirty-race-b")
    item_c = _item("dirty-race-c")
    marker = dirty_index_path(tmp_brain_dir)
    marker.write_text(f"{item_a.id}\n{item_b.id}\n", encoding="utf-8")
    read_complete = threading.Event()
    allow_clear = threading.Event()
    real_read = pending_module.read_dirty_index_marker

    def paused_read(brain):
        result = real_read(brain)
        read_complete.set()
        assert allow_clear.wait(timeout=2)
        return result

    monkeypatch.setattr(pending_module, "read_dirty_index_marker", paused_read)
    outcome: list[bool] = []
    worker = threading.Thread(
        target=lambda: outcome.append(
            pending_module.clear_dirty_index_marker(
                tmp_brain_dir,
                repaired_ids={item_a.id},
            )
        )
    )
    worker.start()
    assert read_complete.wait(timeout=2)
    assert pending_module.append_dirty_index_marker(tmp_brain_dir, item_c.id) is True
    allow_clear.set()
    worker.join(timeout=2)

    assert outcome == [True]
    assert marker.read_text(encoding="utf-8").splitlines() == [item_b.id, item_c.id]


def test_full_reindex_does_not_clear_marker_after_incomplete_item_scan(
    tmp_brain_dir: Path,
):
    store = ItemsStore(tmp_brain_dir / "items")
    index = HubIndex(tmp_brain_dir / "index.db", embedding_dim=_DIM)
    item = _item("dirty-incomplete-scan")
    store.write(item, item.summary)
    (store.items_dir / "malformed.md").write_text("not frontmatter", encoding="utf-8")
    marker = dirty_index_path(tmp_brain_dir)
    marker.write_text(f"{item.id}\n", encoding="utf-8")

    result = reindex_store(store, index, HashingEmbedder(dim=_DIM), prune=True)
    index.close()

    assert result.indexed == 1
    assert store.last_scan.skipped_count == 1
    assert marker.read_text(encoding="utf-8") == f"{item.id}\n"


def test_replace_supersedes_is_transactional_and_preserves_other_relations(
    tmp_brain_dir: Path,
) -> None:
    index = HubIndex(tmp_brain_dir / "index.db", embedding_dim=_DIM)
    source = _item("replace-source")
    target = _item("replace-target")
    stale = _item("replace-stale")
    for item in (source, target, stale):
        index.upsert(item, item.summary, embedding=None)
    index.add_ref(source.id, target.id, "refines")
    index.add_ref(stale.id, source.id, "supersedes")

    result = index.reconcile_supersedes({(source.id, target.id)})

    assert result.deleted == 1
    assert result.inserted == 1
    rows = index.connection.execute(
        "SELECT source_id, target_id, relation FROM refs_graph ORDER BY relation"
    ).fetchall()
    assert rows == [
        (source.id, target.id, "refines"),
        (source.id, target.id, "supersedes"),
    ]


def test_replace_supersedes_rolls_back_delete_when_insert_fails(
    tmp_brain_dir: Path,
) -> None:
    index = HubIndex(tmp_brain_dir / "index.db", embedding_dim=_DIM)
    source = _item("rollback-source")
    old_target = _item("rollback-old-target")
    rejected_target = _item("rollback-rejected-target")
    for item in (source, old_target, rejected_target):
        index.upsert(item, item.summary, embedding=None)
    index.add_ref(source.id, old_target.id, "supersedes")
    index.connection.execute(
        "CREATE TRIGGER reject_supersedes BEFORE INSERT ON refs_graph "
        "WHEN NEW.relation = 'supersedes' AND NEW.target_id = '"
        + rejected_target.id
        + "' BEGIN SELECT RAISE(ABORT, 'injected'); END"
    )
    index.connection.commit()

    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        index.reconcile_supersedes({(source.id, rejected_target.id)})

    assert index.get_refs(source.id) == [
        (source.id, old_target.id, "supersedes")
    ]
