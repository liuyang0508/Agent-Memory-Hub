"""Tests for link/unlink knowledge-graph management."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.contracts.memory_item import MemoryItem, MemoryType

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
