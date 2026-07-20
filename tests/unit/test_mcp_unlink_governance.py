from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

import pytest

from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Refs
from agent_brain.interfaces.mcp.tools import graph as graph_tools
from agent_brain.interfaces.mcp.tools._shared import _components, _components_cache
from agent_brain.memory.governance.supersession import SupersessionService
from agent_brain.memory.store.durable_fs import lifecycle_mutation_capability
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex

_DIM = 8


def _item(
    suffix: str,
    *,
    refs: list[str] | None = None,
    superseded_by: str | None = None,
) -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260720-100000-{suffix}",
        type=MemoryType.fact,
        created_at=datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc),
        title=f"Governed unlink {suffix}",
        summary=f"Governed unlink summary {suffix}",
        project="unlink-governance",
        refs=Refs(mems=refs or []),
        superseded_by=superseded_by,
    )


def _seed(brain_dir: Path, items: list[MemoryItem]) -> HubIndex:
    store = ItemsStore(brain_dir / "items")
    index = HubIndex(brain_dir / "index.db", embedding_dim=_DIM)
    embedder = HashingEmbedder(dim=_DIM)
    for item in items:
        store.write(item, item.summary)
        index.upsert(item, item.summary, embedder.embed(item.summary))
    return index


def _configure_mcp(
    brain_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ItemsStore, HubIndex]:
    monkeypatch.setenv("BRAIN_DIR", str(brain_dir))
    monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")
    store, index, _retriever = _components()
    return store, index


@pytest.fixture(autouse=True)
def _close_components():
    for _store, index, _retriever in _components_cache.values():
        index.close()
    _components_cache.clear()
    yield
    for _store, index, _retriever in _components_cache.values():
        index.close()
    _components_cache.clear()


@pytest.mark.parametrize("tool_name", ["link_memories", "unlink_memories"])
@pytest.mark.parametrize("invalid_position", ["source", "target"])
def test_invalid_id_is_blocked_before_components(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    invalid_position: str,
):
    valid = "mem-20260720-100000-valid-id"
    source_id = "../invalid" if invalid_position == "source" else valid
    target_id = "../invalid" if invalid_position == "target" else valid

    def components_must_not_run():
        raise AssertionError("components must not be initialized for invalid IDs")

    monkeypatch.setattr(graph_tools, "_components", components_must_not_run)
    result = getattr(graph_tools, tool_name)(source_id, target_id)

    assert result["status"] == "blocked"
    assert result["reason"] == "INVALID_ITEM_ID"
    assert result["index_repair_required"] is False
    assert result["linked" if tool_name == "link_memories" else "removed"] is False


def test_unlink_missing_item_preserves_graph(
    tmp_brain_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    source_id = "mem-20260720-100000-missing-source"
    target = _item("missing-target")
    index = _seed(tmp_brain_dir, [target])
    index.add_ref(source_id, target.id, "refs")
    index.close()
    _store, mcp_index = _configure_mcp(tmp_brain_dir, monkeypatch)

    result = graph_tools.unlink_memories(source_id, target.id)

    assert result["status"] == "blocked"
    assert result["reason"] == "ITEM_MISSING"
    assert result["index_repair_required"] is False
    assert mcp_index.get_refs(source_id) == [(source_id, target.id, "refs")]


@pytest.mark.parametrize("malformed_role", ["source", "target"])
def test_unlink_invalid_item_preserves_markdown_and_graph(
    tmp_brain_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    malformed_role: str,
):
    target = _item("invalid-target")
    source = _item("invalid-source", refs=[target.id])
    index = _seed(tmp_brain_dir, [source, target])
    index.close()
    malformed = source if malformed_role == "source" else target
    malformed_path = tmp_brain_dir / "items" / f"{malformed.id}.md"
    malformed_path.write_bytes(b"\xff\xfe malformed")
    source_path = tmp_brain_dir / "items" / f"{source.id}.md"
    source_before = source_path.read_bytes()
    _store, mcp_index = _configure_mcp(tmp_brain_dir, monkeypatch)

    result = graph_tools.unlink_memories(source.id, target.id)

    assert result["status"] == "blocked"
    assert result["reason"] == "ITEM_INVALID"
    assert result["index_repair_required"] is False
    assert source_path.read_bytes() == source_before
    assert mcp_index.get_refs(source.id) == [(source.id, target.id, "refs")]


def test_unlink_graph_check_failure_is_closed_before_markdown_mutation(
    tmp_brain_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    target = _item("graph-failure-target")
    source = _item("graph-failure-source", refs=[target.id])
    index = _seed(tmp_brain_dir, [source, target])
    index.close()
    store, mcp_index = _configure_mcp(tmp_brain_dir, monkeypatch)
    original_get_refs = mcp_index.get_refs

    def fail_graph_check(_item_id: str):
        raise OSError("raw graph read detail")

    monkeypatch.setattr(mcp_index, "get_refs", fail_graph_check)

    result = graph_tools.unlink_memories(source.id, target.id)

    assert result["status"] == "blocked"
    assert result["reason"] == "GRAPH_CHECK_FAILED"
    assert "raw graph" not in str(result)
    assert store.get(source.id)[0].refs.mems == [target.id]
    assert original_get_refs(source.id) == [(source.id, target.id, "refs")]


def test_unlink_index_failure_reports_committed_markdown_and_repair(
    tmp_brain_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    target = _item("index-failure-target")
    source = _item("index-failure-source", refs=[target.id])
    index = _seed(tmp_brain_dir, [source, target])
    index.close()
    store, mcp_index = _configure_mcp(tmp_brain_dir, monkeypatch)
    original_get_refs = mcp_index.get_refs

    def fail_index_remove(*_args, **_kwargs):
        raise OSError("raw index remove detail")

    monkeypatch.setattr(mcp_index, "remove_ref", fail_index_remove)

    result = graph_tools.unlink_memories(source.id, target.id)

    assert result["removed"] is False
    assert result["status"] == "partial"
    assert result["reason"] == "INDEX_UPDATE_FAILED"
    assert result["index_repair_required"] is True
    assert "raw index" not in str(result)
    assert store.get(source.id)[0].refs.mems == []
    assert original_get_refs(source.id) == [(source.id, target.id, "refs")]


def test_unlink_removes_each_non_supersession_relation_explicitly(
    tmp_brain_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    target = _item("relation-target")
    source = _item("relation-source", refs=[target.id])
    index = _seed(tmp_brain_dir, [source, target])
    index.add_ref(source.id, target.id, "refines")
    index.close()
    store, mcp_index = _configure_mcp(tmp_brain_dir, monkeypatch)
    original_remove_ref = mcp_index.remove_ref
    removed_relations: list[str | None] = []

    def recording_remove(source_id: str, target_id: str, relation: str | None = None):
        removed_relations.append(relation)
        return original_remove_ref(source_id, target_id, relation=relation)

    monkeypatch.setattr(mcp_index, "remove_ref", recording_remove)

    result = graph_tools.unlink_memories(source.id, target.id)

    assert result["removed"] is True
    assert result["status"] == "removed"
    assert result["index_repair_required"] is False
    assert removed_relations == ["refines", "refs"]
    assert store.get(source.id)[0].refs.mems == []
    assert mcp_index.get_refs(source.id) == []


def test_unsupported_platform_keeps_ordinary_unlink_compatibility(
    tmp_brain_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    from agent_brain.memory.store import items_store as items_store_module

    target = _item("unsupported-target")
    source = _item("unsupported-source", refs=[target.id])
    index = _seed(tmp_brain_dir, [source, target])
    index.close()
    store, mcp_index = _configure_mcp(tmp_brain_dir, monkeypatch)
    monkeypatch.setattr(
        graph_tools, "lifecycle_mutation_capability", lambda: False, raising=False
    )
    monkeypatch.setattr(
        items_store_module, "lifecycle_mutation_capability", lambda: False
    )

    def lifecycle_lock_must_not_run(_brain_dir: Path):
        raise AssertionError("unsupported ordinary unlink must not use lifecycle lock")

    monkeypatch.setattr(
        graph_tools, "lifecycle_transaction_lock", lifecycle_lock_must_not_run,
        raising=False,
    )

    result = graph_tools.unlink_memories(source.id, target.id)

    assert result["removed"] is True
    assert result["status"] == "removed"
    assert store.get(source.id)[0].refs.mems == []
    assert mcp_index.get_refs(source.id) == []


@pytest.mark.skipif(
    not lifecycle_mutation_capability(), reason="requires lifecycle lock support"
)
def test_unlink_waits_for_concurrent_apply_and_then_blocks(
    tmp_brain_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    target = _item("concurrent-target")
    source = _item("concurrent-source")
    index = _seed(tmp_brain_dir, [source, target])
    index.close()
    store, mcp_index = _configure_mcp(tmp_brain_dir, monkeypatch)
    apply_holds_lock = Event()
    release_apply = Event()
    original_snapshot = SupersessionService._snapshot

    def blocking_snapshot(self, *args, **kwargs):
        apply_holds_lock.set()
        assert release_apply.wait(timeout=5)
        return original_snapshot(self, *args, **kwargs)

    monkeypatch.setattr(SupersessionService, "_snapshot", blocking_snapshot)

    with ThreadPoolExecutor(max_workers=2) as pool:
        apply_future = pool.submit(
            graph_tools.link_memories,
            source.id,
            target.id,
            "supersedes",
            apply=True,
        )
        assert apply_holds_lock.wait(timeout=5)
        unlink_future = pool.submit(
            graph_tools.unlink_memories, source.id, target.id
        )
        try:
            early_unlink = unlink_future.result(timeout=0.2)
        except FutureTimeout:
            early_unlink = None
        finally:
            release_apply.set()
        apply_result = apply_future.result(timeout=5)
        unlink_result = (
            early_unlink
            if early_unlink is not None
            else unlink_future.result(timeout=5)
        )

    assert apply_result["status"] == "applied"
    assert unlink_result["status"] == "blocked"
    assert unlink_result["reason"] == "SUPERSESSION_REVERT_REQUIRED"
    assert store.get(target.id)[0].superseded_by == source.id
    assert store.get(source.id)[0].refs.mems == [target.id]
    assert mcp_index.get_refs(source.id) == [
        (source.id, target.id, "supersedes")
    ]


def test_unlink_docstring_requires_governed_revert():
    assert "governed revert" in (graph_tools.unlink_memories.__doc__ or "").lower()
