"""Tests for MCP + Hermes v1.3 alignment (confidence in write/search/confirm).

MCP tests use the underlying store/index directly because importing
mcp_server.py triggers @mcp.tool() decorators that fail with the
test environment's pydantic version. The MCP functions are thin
wrappers, so we test the same code paths.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.recall.retrieval import Retriever, SearchFilter
from agent_brain.contracts.memory_item import MemoryItem, MemoryType

_DIM = 8


def _seed_brain(brain_dir: Path, n: int = 2) -> None:
    store = ItemsStore(items_dir=brain_dir / "items")
    embedder = HashingEmbedder(dim=_DIM)
    idx = HubIndex(db_path=brain_dir / "index.db", embedding_dim=_DIM)
    for i in range(n):
        item = MemoryItem(
            id=f"mem-20260528-{i:06d}-v13test{i}",
            type=MemoryType.fact,
            created_at=datetime.now(timezone.utc),
            title=f"V13 item {i}",
            summary=f"Summary {i}",
            project="v13proj",
            tags=["v13"],
            confidence=0.5 + i * 0.2,
        )
        body = f"Body content for v13 item {i} with keyword searchable"
        store.write(item, body)
        idx.upsert(item, body, embedding=embedder.embed(f"{item.title} {body}"))
    idx.close()


def _patch_hermes(brain_dir: Path):
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(patch("agent_brain.agent_integrations.hermes.provider._brain_dir", return_value=brain_dir))
    stack.enter_context(patch(
        "agent_brain.agent_integrations.hermes.provider.get_default_embedder",
        return_value=HashingEmbedder(dim=_DIM),
    ))
    return stack


# ── MCP-equivalent: write with confidence (tested via store + index) ──


class TestWriteWithConfidence:
    def test_default_confidence_persists(self, tmp_brain_dir: Path):
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        idx = HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=_DIM)
        emb = HashingEmbedder(dim=_DIM)
        item = MemoryItem(
            id="mem-20260528-100000-defconf",
            type=MemoryType.fact,
            created_at=datetime.now(timezone.utc),
            title="default conf",
            summary="s",
        )
        store.write(item, "body")
        idx.upsert(item, "body", embedding=emb.embed("default conf body"))
        data = idx.get_confidence_data(["mem-20260528-100000-defconf"])
        assert data["mem-20260528-100000-defconf"][0] == 0.7

    def test_explicit_confidence_persists(self, tmp_brain_dir: Path):
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        idx = HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=_DIM)
        emb = HashingEmbedder(dim=_DIM)
        item = MemoryItem(
            id="mem-20260528-100000-hiconf",
            type=MemoryType.fact,
            created_at=datetime.now(timezone.utc),
            title="high conf",
            summary="s",
            confidence=0.95,
        )
        store.write(item, "body")
        idx.upsert(item, "body", embedding=emb.embed("high conf body"))
        data = idx.get_confidence_data(["mem-20260528-100000-hiconf"])
        assert data["mem-20260528-100000-hiconf"][0] == 0.95

        reloaded = list(store.iter_all())
        found = [it for it, _ in reloaded if it.id == "mem-20260528-100000-hiconf"]
        assert found[0].confidence == 0.95


# ── MCP-equivalent: confirm_memory (tested via store + index) ──


class TestConfirmMemory:
    def test_confirm_updates_md_and_index(self, tmp_brain_dir: Path):
        _seed_brain(tmp_brain_dir)
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        idx = HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=_DIM)
        item_id = "mem-20260528-000000-v13test0"

        updated = store.update_frontmatter(item_id, confidence=0.9)
        idx.update_confidence(item_id, 0.9)

        assert updated.confidence == 0.9
        data = idx.get_confidence_data([item_id])
        assert data[item_id][0] == 0.9

    def test_confirm_custom_value(self, tmp_brain_dir: Path):
        _seed_brain(tmp_brain_dir)
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        idx = HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=_DIM)
        item_id = "mem-20260528-000000-v13test0"

        store.update_frontmatter(item_id, confidence=0.6)
        idx.update_confidence(item_id, 0.6)

        data = idx.get_confidence_data([item_id])
        assert data[item_id][0] == 0.6

    def test_confirm_nonexistent_raises(self, tmp_brain_dir: Path):
        import pytest
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        with pytest.raises(FileNotFoundError):
            store.update_frontmatter("mem-20260528-999999-nope", confidence=0.5)


# ── MCP-equivalent: search results include confidence ──


class TestSearchResultsConfidence:
    def test_confidence_available_from_index(self, tmp_brain_dir: Path):
        _seed_brain(tmp_brain_dir)
        idx = HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=_DIM)
        r = Retriever(index=idx, embedder=HashingEmbedder(dim=_DIM), apply_decay=False, record_access=False)
        hits = r.search("searchable", top_k=2)
        assert len(hits) > 0
        items_by_id = {it.id: it for it, _ in ItemsStore(items_dir=tmp_brain_dir / "items").iter_all()}
        for h in hits:
            item = items_by_id.get(h.id)
            assert item is not None
            assert isinstance(item.confidence, float)


# ── Hermes hub_remember confidence ──


class TestHermesRememberConfidence:
    def test_default_confidence(self, tmp_brain_dir: Path):
        from agent_brain.agent_integrations.hermes.provider import hub_remember
        with _patch_hermes(tmp_brain_dir):
            result = hub_remember(content="test content", title="hermes default")
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        for item, _ in store.iter_all():
            if item.id == result["id"]:
                assert item.confidence == 0.7
                break

    def test_explicit_confidence(self, tmp_brain_dir: Path):
        from agent_brain.agent_integrations.hermes.provider import hub_remember
        with _patch_hermes(tmp_brain_dir):
            result = hub_remember(content="test content", title="hermes high", confidence=0.95)
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        for item, _ in store.iter_all():
            if item.id == result["id"]:
                assert item.confidence == 0.95
                break


# ── Hermes hub_search confidence in results ──


class TestHermesSearchConfidence:
    def test_search_includes_confidence(self, tmp_brain_dir: Path):
        _seed_brain(tmp_brain_dir)
        from agent_brain.agent_integrations.hermes.provider import hub_search
        with _patch_hermes(tmp_brain_dir):
            results = hub_search("searchable", top_k=2)
        assert len(results) > 0
        for r in results:
            assert "confidence" in r
            assert isinstance(r["confidence"], float)


# ── Hermes hub_context confidence in items ──


class TestHermesContextConfidence:
    def test_context_includes_confidence(self, tmp_brain_dir: Path):
        _seed_brain(tmp_brain_dir)
        from agent_brain.agent_integrations.hermes.provider import hub_context
        with _patch_hermes(tmp_brain_dir):
            ctx = hub_context()
        assert ctx["count"] > 0
        for item in ctx["items"]:
            assert "confidence" in item
            assert isinstance(item["confidence"], float)


# ── MCP-equivalent: graph_memory (tested via index directly) ──


def _seed_with_refs(brain_dir: Path) -> None:
    """Seed two items where B refs A via refs.mems."""
    from agent_brain.contracts.memory_item import Refs
    store = ItemsStore(items_dir=brain_dir / "items")
    embedder = HashingEmbedder(dim=_DIM)
    idx = HubIndex(db_path=brain_dir / "index.db", embedding_dim=_DIM)
    a = MemoryItem(
        id="mem-20260528-000000-graphA",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="Graph item A",
        summary="A node",
        project="graphproj",
        tags=["graph"],
    )
    b = MemoryItem(
        id="mem-20260528-000001-graphB",
        type=MemoryType.decision,
        created_at=datetime.now(timezone.utc),
        title="Graph item B",
        summary="B refs A",
        project="graphproj",
        tags=["graph"],
        refs=Refs(mems=["mem-20260528-000000-graphA"]),
    )
    store.write(a, "body A searchable graph")
    store.write(b, "body B searchable graph")
    idx.upsert(a, "body A searchable graph", embedding=embedder.embed("Graph item A body A"))
    idx.upsert(b, "body B searchable graph", embedding=embedder.embed("Graph item B body B"))
    idx.close()


class TestMCPGraphMemory:
    def test_graph_returns_edges(self, tmp_brain_dir: Path):
        _seed_with_refs(tmp_brain_dir)
        idx = HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=_DIM)
        edges = idx.get_refs("mem-20260528-000001-graphB")
        assert len(edges) == 1
        assert edges[0][1] == "mem-20260528-000000-graphA"

    def test_graph_returns_neighbors(self, tmp_brain_dir: Path):
        _seed_with_refs(tmp_brain_dir)
        idx = HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=_DIM)
        neighbors = idx.graph_neighbors("mem-20260528-000000-graphA", depth=1)
        assert "mem-20260528-000001-graphB" in neighbors

    def test_no_refs_empty_graph(self, tmp_brain_dir: Path):
        _seed_brain(tmp_brain_dir)
        idx = HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=_DIM)
        edges = idx.get_refs("mem-20260528-000000-v13test0")
        assert edges == []


# ── MCP-equivalent: search_memory with graph_expand ──


class TestSearchWithGraphExpand:
    def test_graph_expand_includes_neighbor(self, tmp_brain_dir: Path):
        _seed_with_refs(tmp_brain_dir)
        idx = HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=_DIM)
        r = Retriever(
            index=idx, embedder=HashingEmbedder(dim=_DIM),
            apply_decay=False, record_access=False,
            graph_expand=True, graph_depth=1,
        )
        hits = r.search("searchable graph", top_k=10)
        hit_ids = {h.id for h in hits}
        assert "mem-20260528-000000-graphA" in hit_ids
        assert "mem-20260528-000001-graphB" in hit_ids

    def test_graph_expand_false_no_extra(self, tmp_brain_dir: Path):
        _seed_with_refs(tmp_brain_dir)
        idx = HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=_DIM)
        r = Retriever(
            index=idx, embedder=HashingEmbedder(dim=_DIM),
            apply_decay=False, record_access=False,
            graph_expand=False,
        )
        hits = r.search("searchable graph", top_k=10)
        assert len(hits) >= 1


# ── Hermes hub_graph ──


class TestHermesGraph:
    def test_hub_graph_returns_edges(self, tmp_brain_dir: Path):
        _seed_with_refs(tmp_brain_dir)
        from agent_brain.agent_integrations.hermes.provider import hub_graph
        with _patch_hermes(tmp_brain_dir):
            result = hub_graph("mem-20260528-000001-graphB")
        assert result["item_id"] == "mem-20260528-000001-graphB"
        assert len(result["edges"]) == 1
        assert result["edges"][0]["target"] == "mem-20260528-000000-graphA"

    def test_hub_graph_returns_neighbors(self, tmp_brain_dir: Path):
        _seed_with_refs(tmp_brain_dir)
        from agent_brain.agent_integrations.hermes.provider import hub_graph
        with _patch_hermes(tmp_brain_dir):
            result = hub_graph("mem-20260528-000000-graphA", depth=1)
        assert len(result["neighbors"]) >= 1
        neighbor_ids = {n["id"] for n in result["neighbors"]}
        assert "mem-20260528-000001-graphB" in neighbor_ids

    def test_hub_graph_no_connections(self, tmp_brain_dir: Path):
        _seed_brain(tmp_brain_dir)
        from agent_brain.agent_integrations.hermes.provider import hub_graph
        with _patch_hermes(tmp_brain_dir):
            result = hub_graph("mem-20260528-000000-v13test0")
        assert result["edges"] == []
        assert result["neighbors"] == []


# ── Hermes hub_search with graph_expand ──


class TestHermesSearchGraphExpand:
    def test_search_with_graph_expand(self, tmp_brain_dir: Path):
        _seed_with_refs(tmp_brain_dir)
        from agent_brain.agent_integrations.hermes.provider import hub_search
        with _patch_hermes(tmp_brain_dir):
            results = hub_search("searchable graph", top_k=10, graph_expand=True)
        result_ids = {r["id"] for r in results}
        assert "mem-20260528-000000-graphA" in result_ids
        assert "mem-20260528-000001-graphB" in result_ids

    def test_search_without_graph_expand(self, tmp_brain_dir: Path):
        _seed_with_refs(tmp_brain_dir)
        from agent_brain.agent_integrations.hermes.provider import hub_search
        with _patch_hermes(tmp_brain_dir):
            results = hub_search("searchable graph", top_k=10, graph_expand=False)
        assert len(results) >= 1
