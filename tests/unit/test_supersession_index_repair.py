from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.interfaces.mcp.tools._shared import _components, _components_cache
from agent_brain.interfaces.mcp.tools.graph import link_memories
from agent_brain.memory.store.durable_fs import lifecycle_mutation_capability
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex


def _item(suffix: str) -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260720-110000-{suffix}",
        type=MemoryType.fact,
        created_at=datetime(2026, 7, 20, 11, 0, tzinfo=timezone.utc),
        title=f"Index repair {suffix}",
        summary=f"Index repair summary {suffix}",
        project="index-repair",
    )


def _seed(brain_dir: Path) -> tuple[MemoryItem, MemoryItem]:
    old = _item("old")
    new = _item("new")
    store = ItemsStore(brain_dir / "items")
    index = HubIndex(brain_dir / "index.db")
    embedder = HashingEmbedder()
    for item in (old, new):
        store.write(item, item.summary)
        index.upsert(item, item.summary, embedder.embed(item.summary))
    index.close()
    return old, new


@pytest.fixture(autouse=True)
def _close_components():
    for _store, index, _retriever in _components_cache.values():
        index.close()
    _components_cache.clear()
    yield
    for _store, index, _retriever in _components_cache.values():
        index.close()
    _components_cache.clear()


@pytest.mark.skipif(
    not lifecycle_mutation_capability(), reason="requires lifecycle mutation support"
)
def test_already_applied_repairs_index_without_new_snapshot_or_ledger(
    tmp_brain_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    old, new = _seed(tmp_brain_dir)
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")
    _store, index, _retriever = _components()
    original_upsert = index.upsert
    fail_sync = True

    def controlled_upsert(*args, **kwargs):
        if fail_sync:
            raise OSError("index unavailable")
        return original_upsert(*args, **kwargs)

    monkeypatch.setattr(index, "upsert", controlled_upsert)

    first = link_memories(new.id, old.id, relation="supersedes", apply=True)
    ledger_path = tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl"
    ledger_after_first = ledger_path.read_bytes()
    snapshots_after_first = {
        path.relative_to(tmp_brain_dir): path.read_bytes()
        for path in tmp_brain_dir.rglob("*")
        if path.is_file() and "lifecycle-snapshots" in path.parts
    }
    fail_sync = False

    second = link_memories(new.id, old.id, relation="supersedes", apply=True)

    assert first["status"] == "applied"
    assert first["index_repair_required"] is True
    assert second["linked"] is True
    assert second["status"] == "already_applied"
    assert second["reason"] == "ALREADY_APPLIED"
    assert second["index_repair_required"] is False
    assert ledger_path.read_bytes() == ledger_after_first
    assert {
        path.relative_to(tmp_brain_dir): path.read_bytes()
        for path in tmp_brain_dir.rglob("*")
        if path.is_file() and "lifecycle-snapshots" in path.parts
    } == snapshots_after_first
    assert index.get_refs(new.id) == [(new.id, old.id, "supersedes")]


@pytest.mark.skipif(
    not lifecycle_mutation_capability(), reason="requires lifecycle mutation support"
)
def test_already_applied_keeps_repair_required_when_index_still_fails(
    tmp_brain_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    old, new = _seed(tmp_brain_dir)
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")
    _store, index, _retriever = _components()

    def fail_upsert(*_args, **_kwargs):
        raise OSError("index still unavailable")

    monkeypatch.setattr(index, "upsert", fail_upsert)

    first = link_memories(new.id, old.id, relation="supersedes", apply=True)
    ledger_path = tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl"
    ledger_after_first = ledger_path.read_bytes()
    second = link_memories(new.id, old.id, relation="supersedes", apply=True)

    assert first["status"] == "applied"
    assert first["index_repair_required"] is True
    assert second["status"] == "already_applied"
    assert second["index_repair_required"] is True
    assert ledger_path.read_bytes() == ledger_after_first
