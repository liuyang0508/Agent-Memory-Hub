"""Tests for link/unlink knowledge-graph management."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Refs
from agent_brain.interfaces.cli.commands.index_maintenance import reindex_store
from agent_brain.interfaces.mcp.tools._shared import _components, _components_cache
from agent_brain.interfaces.mcp.tools.graph import link_memories, unlink_memories
from agent_brain.memory.governance.lifecycle_snapshot import LifecycleSnapshotError
from agent_brain.memory.governance.supersession import SupersessionService
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex

_DIM = 8


def _item(suffix: str, **kw) -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260528-600000-{suffix}",
        type=kw.pop("type", MemoryType.fact),
        created_at=kw.pop("created_at", datetime.now(timezone.utc)),
        title=kw.pop("title", f"Item {suffix}"),
        summary=kw.pop("summary", f"Summary for {suffix}"),
        project=kw.pop("project", "linkproj"),
        tags=kw.pop("tags", ["test"]),
    )


def _seed(brain_dir: Path, items: list[tuple[MemoryItem, str]]) -> HubIndex:
    store = ItemsStore(items_dir=brain_dir / "items")
    idx = HubIndex(db_path=brain_dir / "index.db", embedding_dim=_DIM)
    emb = HashingEmbedder(dim=_DIM)
    for item, body in items:
        store.write(item, body)
        idx.upsert(item, body, embedding=emb.embed(f"{item.title} {body}"))
    return idx


def _close_mcp_components() -> None:
    for _store, index, _retriever in _components_cache.values():
        index.close()
    _components_cache.clear()


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.fixture(autouse=True)
def isolate_mcp_components():
    _close_mcp_components()
    yield
    _close_mcp_components()


def _superseded_pair(old_suffix: str, new_suffix: str) -> tuple[MemoryItem, MemoryItem]:
    old = _item(old_suffix)
    new = _item(new_suffix)
    return (
        old.model_copy(update={"superseded_by": new.id}),
        new.model_copy(update={"refs": Refs(mems=[old.id])}),
    )


def _patch_hermes(brain_dir: Path):
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(patch("agent_brain.agent_integrations.hermes.provider._brain_dir", return_value=brain_dir))
    stack.enter_context(patch(
        "agent_brain.agent_integrations.hermes.provider.get_default_embedder",
        return_value=HashingEmbedder(dim=_DIM),
    ))
    return stack


class TestIndexLinkUnlink:
    def test_add_ref(self, tmp_brain_dir: Path):
        a = _item("link-a")
        b = _item("link-b")
        idx = _seed(tmp_brain_dir, [(a, "a"), (b, "b")])
        idx.add_ref(a.id, b.id, "related_to")
        refs = idx.get_refs(a.id)
        assert len(refs) == 1
        assert refs[0] == (a.id, b.id, "related_to")

    def test_remove_ref(self, tmp_brain_dir: Path):
        a = _item("unlink-a")
        b = _item("unlink-b")
        idx = _seed(tmp_brain_dir, [(a, "a"), (b, "b")])
        idx.add_ref(a.id, b.id, "refs")
        assert len(idx.get_refs(a.id)) == 1
        removed = idx.remove_ref(a.id, b.id)
        assert removed == 1
        assert len(idx.get_refs(a.id)) == 0

    def test_remove_nonexistent_ref(self, tmp_brain_dir: Path):
        a = _item("nope-a")
        idx = _seed(tmp_brain_dir, [(a, "a")])
        removed = idx.remove_ref(a.id, "mem-20260528-999999-nope")
        assert removed == 0

    def test_bidirectional_get_refs(self, tmp_brain_dir: Path):
        a = _item("bi-a")
        b = _item("bi-b")
        idx = _seed(tmp_brain_dir, [(a, "a"), (b, "b")])
        idx.add_ref(a.id, b.id, "supersedes")
        refs_a = idx.get_refs(a.id)
        refs_b = idx.get_refs(b.id)
        assert len(refs_a) == 1
        assert len(refs_b) == 1

    def test_remove_ref_can_target_one_relation(self, tmp_brain_dir: Path):
        old, new = _superseded_pair("relation-old", "relation-new")
        idx = _seed(tmp_brain_dir, [(old, "old"), (new, "new")])
        idx.add_ref(new.id, old.id, "refs")
        idx.add_ref(new.id, old.id, "supersedes")

        removed = idx.remove_ref(new.id, old.id, relation="refs")

        assert removed == 1
        assert idx.get_refs(new.id) == [(new.id, old.id, "supersedes")]

    def test_remove_ref_without_relation_keeps_legacy_all_relation_behavior(
        self, tmp_brain_dir: Path
    ):
        old, new = _superseded_pair("legacy-remove-old", "legacy-remove-new")
        idx = _seed(tmp_brain_dir, [(old, "old"), (new, "new")])
        idx.add_ref(new.id, old.id, "refs")
        idx.add_ref(new.id, old.id, "supersedes")

        removed = idx.remove_ref(new.id, old.id)

        assert removed == 2
        assert idx.get_refs(new.id) == []

    @pytest.mark.parametrize("order", ["obsolete-first", "replacement-first"])
    def test_upsert_derives_supersedes_independent_of_item_order(
        self, tmp_brain_dir: Path, order: str
    ):
        old, new = _superseded_pair(f"{order}-old", f"{order}-new")
        items = [(old, "old"), (new, "new")]
        if order == "replacement-first":
            items.reverse()

        idx = _seed(tmp_brain_dir, items)

        assert idx.get_refs(new.id) == [(new.id, old.id, "supersedes")]

    @pytest.mark.parametrize("order", ["obsolete-first", "replacement-first"])
    def test_upsert_derives_supersedes_from_obsolete_frontmatter_only(
        self, tmp_brain_dir: Path, order: str
    ):
        old = _item(f"single-authority-{order}-old")
        new = _item(f"single-authority-{order}-new")
        old = old.model_copy(update={"superseded_by": new.id})
        assert new.refs.mems == []
        store = ItemsStore(tmp_brain_dir / "items")
        store.write(old, "old")
        store.write(new, "new")
        idx = HubIndex(tmp_brain_dir / "index.db", embedding_dim=_DIM)
        embedder = HashingEmbedder(dim=_DIM)
        items = [(old, "old"), (new, "new")]
        if order == "replacement-first":
            items.reverse()

        for _ in range(2):
            for item, body in items:
                idx.upsert(item, body, embedder.embed(body))

        assert idx.get_refs(new.id) == [(new.id, old.id, "supersedes")]

    def test_single_authority_reindex_is_idempotent(self, tmp_brain_dir: Path):
        old = _item("single-authority-reindex-old")
        new = _item("single-authority-reindex-new")
        old = old.model_copy(update={"superseded_by": new.id})
        store = ItemsStore(tmp_brain_dir / "items")
        store.write(new, "new")
        store.write(old, "old")
        idx = HubIndex(tmp_brain_dir / "index.db", embedding_dim=_DIM)
        embedder = HashingEmbedder(dim=_DIM)

        for _ in range(2):
            reindex_store(store, idx, embedder, prune=True)

        assert idx.get_refs(new.id) == [(new.id, old.id, "supersedes")]

    @pytest.mark.parametrize("order", ["obsolete-first", "replacement-first"])
    def test_single_authority_revert_is_order_independent(
        self, tmp_brain_dir: Path, order: str
    ):
        old = _item(f"single-revert-{order}-old")
        new = _item(f"single-revert-{order}-new")
        superseded = old.model_copy(update={"superseded_by": new.id})
        idx = _seed(tmp_brain_dir, [(superseded, "old"), (new, "new")])
        assert idx.get_refs(new.id) == [(new.id, old.id, "supersedes")]
        store = ItemsStore(tmp_brain_dir / "items")
        store.update_frontmatter(old.id, superseded_by=None)
        reverted, _body = store.get(old.id)
        items = [(reverted, "old"), (new, "new")]
        if order == "replacement-first":
            items.reverse()

        for item, body in items:
            idx.upsert(item, body, embedding=None)

        assert not any(
            relation == "supersedes"
            for *_pair, relation in idx.get_refs(new.id)
        )

    def test_supersession_reconciliation_preserves_custom_and_removes_generic_relation(
        self, tmp_brain_dir: Path
    ):
        old = _item("relation-authority-old")
        new = _item("relation-authority-new")
        idx = _seed(tmp_brain_dir, [(old, "old"), (new, "new")])
        idx.add_ref(new.id, old.id, "refs")
        idx.add_ref(new.id, old.id, "refines")
        superseded = old.model_copy(update={"superseded_by": new.id})

        idx.upsert(superseded, "old", embedding=None)
        idx.upsert(new, "new", embedding=None)

        assert sorted(idx.get_refs(new.id)) == sorted(
            [
                (new.id, old.id, "refines"),
                (new.id, old.id, "supersedes"),
            ]
        )

    def test_reindex_rebuilds_only_supersedes_relation_from_frontmatter(
        self, tmp_brain_dir: Path
    ):
        old, new = _superseded_pair("reindex-old", "reindex-new")
        idx = _seed(tmp_brain_dir, [(new, "new"), (old, "old")])
        store = ItemsStore(tmp_brain_dir / "items")
        embedder = HashingEmbedder(dim=_DIM)

        reindex_store(store, idx, embedder, prune=True)
        reindex_store(store, idx, embedder, prune=True)

        assert idx.get_refs(new.id) == [(new.id, old.id, "supersedes")]

    @pytest.mark.parametrize("ref_preexisted", [False, True])
    def test_governed_revert_restores_the_markdown_derived_relation(
        self, tmp_brain_dir: Path, ref_preexisted: bool
    ):
        old = _item(f"revert-old-{ref_preexisted}")
        new = _item(f"revert-new-{ref_preexisted}")
        if ref_preexisted:
            new = new.model_copy(update={"refs": Refs(mems=[old.id])})
        idx = _seed(tmp_brain_dir, [(old, "old"), (new, "new")])
        store = ItemsStore(tmp_brain_dir / "items")
        service = SupersessionService(tmp_brain_dir, store, idx)

        assert service.apply(new.id, old.id, apply=True).status == "applied"
        assert idx.get_refs(new.id) == [(new.id, old.id, "supersedes")]

        assert service.revert(new.id, old.id, apply=True).status == "reverted"
        expected = [(new.id, old.id, "refs")] if ref_preexisted else []
        assert idx.get_refs(new.id) == expected


class TestMcpLinkUnlink:
    def test_generic_relation_keeps_immediate_link_behavior_when_apply_is_false(
        self, tmp_brain_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        source = _item("mcp-generic-source")
        target = _item("mcp-generic-target")
        idx = _seed(tmp_brain_dir, [(source, "source"), (target, "target")])
        idx.close()
        monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
        monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")

        result = link_memories(source.id, target.id, relation="refines", apply=False)

        assert result["linked"] is True
        assert result["status"] == "linked"
        assert result["dry_run"] is False
        store = ItemsStore(tmp_brain_dir / "items")
        assert store.get(source.id)[0].refs.mems == [target.id]

    def test_supersedes_defaults_to_preview_without_mutation(
        self, tmp_brain_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        old = _item("mcp-preview-old")
        new = _item("mcp-preview-new")
        idx = _seed(tmp_brain_dir, [(old, "old"), (new, "new")])
        idx.close()
        monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
        monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")
        _components()
        before = _tree_bytes(tmp_brain_dir)

        result = link_memories(new.id, old.id, relation="supersedes")

        assert result["dry_run"] is True
        assert result["linked"] is False
        assert result["status"] == "ready"
        assert _tree_bytes(tmp_brain_dir) == before
        store = ItemsStore(tmp_brain_dir / "items")
        assert store.get(old.id)[0].superseded_by is None
        assert store.get(new.id)[0].refs.mems == []

    def test_supersedes_updates_obsolete_frontmatter(
        self, tmp_brain_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        old = _item("mcp-old")
        new = _item("mcp-new")
        idx = _seed(tmp_brain_dir, [(old, "old"), (new, "new")])
        idx.close()
        monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
        monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")

        result = link_memories(new.id, old.id, relation="supersedes", apply=True)

        store = ItemsStore(tmp_brain_dir / "items")
        assert result["linked"] is True
        assert result["dry_run"] is False
        assert result["status"] == "applied"
        assert result["reason"] == "OK"
        assert result["index_repair_required"] is False
        assert store.get(old.id)[0].superseded_by == new.id
        assert store.get(new.id)[0].refs.mems == [old.id]
        _store, mcp_index, _retriever = next(iter(_components_cache.values()))
        assert mcp_index.get_refs(new.id) == [(new.id, old.id, "supersedes")]

    def test_repeated_supersedes_is_idempotent(
        self, tmp_brain_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        old = _item("mcp-repeat-old")
        new = _item("mcp-repeat-new")
        idx = _seed(tmp_brain_dir, [(old, "old"), (new, "new")])
        idx.close()
        monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
        monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")

        first = link_memories(new.id, old.id, relation="supersedes", apply=True)
        ledger = tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl"
        ledger_after_first = ledger.read_bytes()
        second = link_memories(new.id, old.id, relation="supersedes", apply=True)

        assert first["status"] == "applied"
        assert second["linked"] is True
        assert second["status"] == "already_applied"
        assert second["reason"] == "ALREADY_APPLIED"
        assert isinstance(first["index_repair_required"], bool)
        assert first["index_repair_required"] is False
        assert second["index_repair_required"] is False
        assert ledger.read_bytes() == ledger_after_first
        _store, mcp_index, _retriever = next(iter(_components_cache.values()))
        assert mcp_index.get_refs(new.id) == [(new.id, old.id, "supersedes")]

    def test_supersedes_platform_failure_does_not_write_graph_or_markdown(
        self, tmp_brain_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        old = _item("mcp-unsupported-old")
        new = _item("mcp-unsupported-new")
        idx = _seed(tmp_brain_dir, [(old, "old"), (new, "new")])
        idx.close()
        monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
        monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")
        monkeypatch.setattr(
            "agent_brain.memory.governance.supersession.lifecycle_mutation_capability",
            lambda: False,
        )

        result = link_memories(new.id, old.id, relation="supersedes", apply=True)

        assert result["linked"] is False
        assert result["status"] == "blocked"
        assert result["reason"] == "PLATFORM_UNSUPPORTED"
        assert result["index_repair_required"] is False
        store = ItemsStore(tmp_brain_dir / "items")
        assert store.get(old.id)[0].superseded_by is None
        assert store.get(new.id)[0].refs.mems == []
        _store, mcp_index, _retriever = next(iter(_components_cache.values()))
        assert mcp_index.get_refs(new.id) == []

    def test_supersedes_validation_failure_does_not_write_graph_or_markdown(
        self, tmp_brain_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        old = _item("mcp-invalid-old", project="old-project")
        new = _item("mcp-invalid-new", project="new-project")
        idx = _seed(tmp_brain_dir, [(old, "old"), (new, "new")])
        idx.close()
        monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
        monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")

        result = link_memories(new.id, old.id, relation="supersedes", apply=True)

        assert result["linked"] is False
        assert result["status"] == "blocked"
        assert result["reason"] == "PROJECT_MISMATCH"
        assert result["index_repair_required"] is False
        store = ItemsStore(tmp_brain_dir / "items")
        assert store.get(old.id)[0].superseded_by is None
        assert store.get(new.id)[0].refs.mems == []
        _store, mcp_index, _retriever = next(iter(_components_cache.values()))
        assert mcp_index.get_refs(new.id) == []

    def test_supersedes_snapshot_failure_does_not_write_graph_or_markdown(
        self, tmp_brain_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        old = _item("mcp-snapshot-old")
        new = _item("mcp-snapshot-new")
        idx = _seed(tmp_brain_dir, [(old, "old"), (new, "new")])
        idx.close()
        monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
        monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")

        def fail_snapshot(*_args, **_kwargs):
            raise LifecycleSnapshotError("raw detail must not escape")

        monkeypatch.setattr(
            "agent_brain.memory.governance.lifecycle_snapshot."
            "LifecycleSnapshotStore.snapshot_pair",
            fail_snapshot,
        )

        result = link_memories(new.id, old.id, relation="supersedes", apply=True)

        assert result["linked"] is False
        assert result["status"] == "blocked"
        assert result["reason"] == "SNAPSHOT_FAILED"
        assert result["index_repair_required"] is False
        assert "raw detail" not in str(result)
        store = ItemsStore(tmp_brain_dir / "items")
        assert store.get(old.id)[0].superseded_by is None
        assert store.get(new.id)[0].refs.mems == []
        _store, mcp_index, _retriever = next(iter(_components_cache.values()))
        assert mcp_index.get_refs(new.id) == []

    def test_supersedes_reports_committed_markdown_when_index_sync_fails(
        self, tmp_brain_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        old = _item("mcp-index-repair-old")
        new = _item("mcp-index-repair-new")
        idx = _seed(tmp_brain_dir, [(old, "old"), (new, "new")])
        idx.close()
        monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
        monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")
        _store, mcp_index, _retriever = _components()

        def fail_index_sync(*_args, **_kwargs):
            raise OSError("raw index failure detail")

        monkeypatch.setattr(mcp_index, "upsert", fail_index_sync)

        result = link_memories(new.id, old.id, relation="supersedes", apply=True)

        assert result["linked"] is True
        assert result["status"] == "applied"
        assert result["reason"] == "OK"
        assert result["index_repair_required"] is True
        assert "raw index failure" not in str(result)
        store = ItemsStore(tmp_brain_dir / "items")
        assert store.get(old.id)[0].superseded_by == new.id
        assert store.get(new.id)[0].refs.mems == [old.id]

    def test_unlink_refuses_to_bypass_supersession_revert(
        self, tmp_brain_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        old, new = _superseded_pair("mcp-unlink-old", "mcp-unlink-new")
        idx = _seed(tmp_brain_dir, [(old, "old"), (new, "new")])
        idx.close()
        monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
        monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")

        result = unlink_memories(new.id, old.id)

        assert result["removed"] is False
        assert result["status"] == "blocked"
        assert result["reason"] == "SUPERSESSION_REVERT_REQUIRED"
        store = ItemsStore(tmp_brain_dir / "items")
        assert store.get(old.id)[0].superseded_by == new.id
        assert store.get(new.id)[0].refs.mems == [old.id]
        _store, mcp_index, _retriever = next(iter(_components_cache.values()))
        assert mcp_index.get_refs(new.id) == [(new.id, old.id, "supersedes")]

    def test_unlink_refuses_supersession_when_graph_needs_repair(
        self, tmp_brain_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        old, new = _superseded_pair("mcp-stale-graph-old", "mcp-stale-graph-new")
        idx = _seed(tmp_brain_dir, [(old, "old"), (new, "new")])
        idx.remove_ref(new.id, old.id, relation="supersedes")
        idx.close()
        monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
        monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")

        result = unlink_memories(new.id, old.id)

        assert result["removed"] is False
        assert result["status"] == "blocked"
        assert result["reason"] == "SUPERSESSION_REVERT_REQUIRED"
        store = ItemsStore(tmp_brain_dir / "items")
        assert store.get(old.id)[0].superseded_by == new.id
        assert store.get(new.id)[0].refs.mems == [old.id]

    def test_unlink_removes_only_generic_ref(
        self, tmp_brain_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        source = _item("mcp-ref-source").model_copy(
            update={"refs": Refs(mems=[_item("mcp-ref-target").id])}
        )
        target = _item("mcp-ref-target")
        idx = _seed(tmp_brain_dir, [(source, "source"), (target, "target")])
        idx.close()
        monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
        monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")

        result = unlink_memories(source.id, target.id)

        assert result["removed"] is True
        assert result["status"] == "removed"
        assert result["reason"] == "OK"
        store = ItemsStore(tmp_brain_dir / "items")
        assert store.get(source.id)[0].refs.mems == []
        _store, mcp_index, _retriever = next(iter(_components_cache.values()))
        assert mcp_index.get_refs(source.id) == []

    def test_unlink_keeps_existing_custom_relation_removal_behavior(
        self, tmp_brain_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        target = _item("mcp-custom-target")
        source = _item("mcp-custom-source").model_copy(
            update={"refs": Refs(mems=[target.id])}
        )
        idx = _seed(tmp_brain_dir, [(source, "source"), (target, "target")])
        idx.add_ref(source.id, target.id, "refines")
        idx.close()
        monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
        monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")

        result = unlink_memories(source.id, target.id)

        assert result["removed"] is True
        store = ItemsStore(tmp_brain_dir / "items")
        assert store.get(source.id)[0].refs.mems == []
        _store, mcp_index, _retriever = next(iter(_components_cache.values()))
        assert mcp_index.get_refs(source.id) == []


class TestHermesLinkUnlink:
    def test_hub_link(self, tmp_brain_dir: Path):
        a = _item("hl-a")
        b = _item("hl-b")
        _seed(tmp_brain_dir, [(a, "a"), (b, "b")])
        from agent_brain.agent_integrations.hermes.provider import hub_link
        with _patch_hermes(tmp_brain_dir):
            result = hub_link(a.id, b.id, relation="depends_on")
        assert result["linked"] is True
        assert result["relation"] == "depends_on"

    def test_hub_unlink(self, tmp_brain_dir: Path):
        a = _item("hu-a")
        b = _item("hu-b")
        idx = _seed(tmp_brain_dir, [(a, "a"), (b, "b")])
        idx.add_ref(a.id, b.id, "refs")
        from agent_brain.agent_integrations.hermes.provider import hub_unlink
        with _patch_hermes(tmp_brain_dir):
            result = hub_unlink(a.id, b.id)
        assert result["removed"] is True

    def test_hub_link_then_graph(self, tmp_brain_dir: Path):
        a = _item("lg-a")
        b = _item("lg-b")
        _seed(tmp_brain_dir, [(a, "a"), (b, "b")])
        from agent_brain.agent_integrations.hermes.provider import hub_link, hub_graph
        with _patch_hermes(tmp_brain_dir):
            hub_link(a.id, b.id)
            graph = hub_graph(a.id)
        assert len(graph["edges"]) == 1
        assert b.id in [n["id"] for n in graph["neighbors"]]
