"""Tests for lightweight knowledge graph (refs_graph table, graph expansion)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.recall.retrieval import Retriever
from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Refs

_DIM = 8


def _item(suffix: str, refs_mems: list[str] | None = None, **kw) -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260528-100000-{suffix}",
        type=kw.pop("type", MemoryType.fact),
        created_at=datetime.now(timezone.utc),
        title=kw.pop("title", f"Item {suffix}"),
        summary=kw.pop("summary", f"Summary {suffix}"),
        project=kw.pop("project", "kg"),
        tags=kw.pop("tags", []),
        tenant_id=kw.pop("tenant_id", None),
        refs=Refs(mems=refs_mems or []),
    )


def _seed(brain_dir: Path, items: list[tuple[MemoryItem, str]]) -> HubIndex:
    store = ItemsStore(items_dir=brain_dir / "items")
    idx = HubIndex(db_path=brain_dir / "index.db", embedding_dim=_DIM)
    emb = HashingEmbedder(dim=_DIM)
    for item, body in items:
        store.write(item, body)
        idx.upsert(item, body, embedding=emb.embed(f"{item.title} {body}"))
    return idx


class TestRefsGraphTable:
    def test_refs_mems_populated_on_upsert(self, tmp_brain_dir: Path):
        a = _item("a")
        b = _item("b", refs_mems=["mem-20260528-100000-a"])
        idx = _seed(tmp_brain_dir, [(a, "body a"), (b, "body b")])
        edges = idx.get_refs("mem-20260528-100000-b")
        assert len(edges) == 1
        assert edges[0] == ("mem-20260528-100000-b", "mem-20260528-100000-a", "refs")

    def test_empty_refs_no_edges(self, tmp_brain_dir: Path):
        a = _item("lonely")
        idx = _seed(tmp_brain_dir, [(a, "body")])
        assert idx.get_refs("mem-20260528-100000-lonely") == []

    def test_upsert_replaces_old_edges(self, tmp_brain_dir: Path):
        a = _item("a")
        b = _item("b", refs_mems=["mem-20260528-100000-a"])
        idx = _seed(tmp_brain_dir, [(a, "body a"), (b, "body b")])
        assert len(idx.get_refs("mem-20260528-100000-b")) == 1

        b_updated = _item("b", refs_mems=[])
        emb = HashingEmbedder(dim=_DIM)
        idx.upsert(b_updated, "body b updated", embedding=emb.embed("body"))
        assert idx.get_refs("mem-20260528-100000-b") == []

    def test_delete_cleans_edges(self, tmp_brain_dir: Path):
        a = _item("a")
        b = _item("b", refs_mems=["mem-20260528-100000-a"])
        idx = _seed(tmp_brain_dir, [(a, "body a"), (b, "body b")])
        idx.delete("mem-20260528-100000-b")
        assert idx.get_refs("mem-20260528-100000-a") == []


class TestGraphNeighbors:
    def test_depth_1(self, tmp_brain_dir: Path):
        a = _item("a")
        b = _item("b", refs_mems=["mem-20260528-100000-a"])
        c = _item("c", refs_mems=["mem-20260528-100000-b"])
        idx = _seed(tmp_brain_dir, [(a, "a"), (b, "b"), (c, "c")])

        neighbors = idx.graph_neighbors("mem-20260528-100000-b", depth=1)
        assert "mem-20260528-100000-a" in neighbors
        assert "mem-20260528-100000-c" in neighbors
        assert "mem-20260528-100000-b" not in neighbors

    def test_depth_2(self, tmp_brain_dir: Path):
        a = _item("a")
        b = _item("b", refs_mems=["mem-20260528-100000-a"])
        c = _item("c", refs_mems=["mem-20260528-100000-b"])
        idx = _seed(tmp_brain_dir, [(a, "a"), (b, "b"), (c, "c")])

        neighbors = idx.graph_neighbors("mem-20260528-100000-a", depth=2)
        assert "mem-20260528-100000-b" in neighbors
        assert "mem-20260528-100000-c" in neighbors

    def test_depth_1_only_direct(self, tmp_brain_dir: Path):
        a = _item("a")
        b = _item("b", refs_mems=["mem-20260528-100000-a"])
        c = _item("c", refs_mems=["mem-20260528-100000-b"])
        idx = _seed(tmp_brain_dir, [(a, "a"), (b, "b"), (c, "c")])

        neighbors = idx.graph_neighbors("mem-20260528-100000-a", depth=1)
        assert "mem-20260528-100000-b" in neighbors
        assert "mem-20260528-100000-c" not in neighbors

    def test_no_neighbors(self, tmp_brain_dir: Path):
        a = _item("solo")
        idx = _seed(tmp_brain_dir, [(a, "solo")])
        assert idx.graph_neighbors("mem-20260528-100000-solo") == set()

    def test_bidirectional(self, tmp_brain_dir: Path):
        a = _item("a")
        b = _item("b", refs_mems=["mem-20260528-100000-a"])
        idx = _seed(tmp_brain_dir, [(a, "a"), (b, "b")])
        n_from_a = idx.graph_neighbors("mem-20260528-100000-a", depth=1)
        n_from_b = idx.graph_neighbors("mem-20260528-100000-b", depth=1)
        assert "mem-20260528-100000-b" in n_from_a
        assert "mem-20260528-100000-a" in n_from_b


class TestAddRef:
    def test_add_ref_creates_edge(self, tmp_brain_dir: Path):
        a = _item("a")
        b = _item("b")
        idx = _seed(tmp_brain_dir, [(a, "a"), (b, "b")])
        idx.add_ref("mem-20260528-100000-a", "mem-20260528-100000-b", "supersedes")
        edges = idx.get_refs("mem-20260528-100000-a")
        assert len(edges) == 1
        assert edges[0][2] == "supersedes"

    def test_add_ref_idempotent(self, tmp_brain_dir: Path):
        a = _item("a")
        b = _item("b")
        idx = _seed(tmp_brain_dir, [(a, "a"), (b, "b")])
        idx.add_ref("mem-20260528-100000-a", "mem-20260528-100000-b")
        idx.add_ref("mem-20260528-100000-a", "mem-20260528-100000-b")
        edges = idx.get_refs("mem-20260528-100000-a")
        assert len(edges) == 1


class TestGraphExpansionInRetriever:
    def test_graph_expansion_helper_is_split(self, tmp_brain_dir: Path):
        from agent_brain.memory.recall.retrieval_graph import expand_via_graph
        from agent_brain.memory.recall.retrieval_types import RetrievedItem

        a = _item("split-a", title="split graph alpha")
        b = _item("split-b", refs_mems=["mem-20260528-100000-split-a"])
        idx = _seed(tmp_brain_dir, [(a, "split graph alpha"), (b, "neighbor")])
        candidates = [
            RetrievedItem(
                id="mem-20260528-100000-split-a",
                score=1.0,
                bm25_rank=1,
                vector_rank=None,
            )
        ]

        expanded = expand_via_graph(idx, candidates, top_k=1, graph_depth=1)

        assert [item.id for item in expanded] == [
            "mem-20260528-100000-split-a",
            "mem-20260528-100000-split-b",
        ]

    def test_graph_expand_pulls_neighbors(self, tmp_brain_dir: Path):
        a = _item("ga", title="searchable topic alpha")
        b = _item("gb", title="unrelated title beta", refs_mems=["mem-20260528-100000-ga"])
        idx = _seed(tmp_brain_dir, [(a, "searchable topic alpha content"), (b, "unrelated beta body")])
        emb = HashingEmbedder(dim=_DIM)
        r = Retriever(
            index=idx, embedder=emb,
            apply_decay=False, record_access=False,
            graph_expand=True, graph_depth=1,
        )
        hits = r.search("searchable topic alpha", top_k=10)
        hit_ids = {h.id for h in hits}
        assert "mem-20260528-100000-ga" in hit_ids
        assert "mem-20260528-100000-gb" in hit_ids

    def test_graph_expand_off_by_default(self, tmp_brain_dir: Path):
        a = _item("goff-a", title="unique keyword zeta")
        b = _item("goff-b", title="something else", refs_mems=["mem-20260528-100000-goff-a"])
        idx = _seed(tmp_brain_dir, [
            (a, "unique keyword zeta content"),
            (b, "completely different body"),
        ])
        emb = HashingEmbedder(dim=_DIM)
        r = Retriever(index=idx, embedder=emb, apply_decay=False, record_access=False)
        hits = r.search("unique keyword zeta", top_k=10)
        hit_ids = {h.id for h in hits}
        assert "mem-20260528-100000-goff-a" in hit_ids

    def test_graph_expand_respects_tenant_filter(self, tmp_brain_dir: Path):
        from agent_brain.memory.recall.retrieval import SearchFilter

        tenant_a = _item(
            "tenant-a",
            title="tenant isolated alpha",
            tenant_id="tenant-a",
        )
        tenant_b = _item(
            "tenant-b",
            title="cross tenant neighbor",
            refs_mems=["mem-20260528-100000-tenant-a"],
            tenant_id="tenant-b",
        )
        idx = _seed(tmp_brain_dir, [
            (tenant_a, "tenant isolated alpha body"),
            (tenant_b, "cross tenant neighbor body"),
        ])
        emb = HashingEmbedder(dim=_DIM)
        r = Retriever(
            index=idx, embedder=emb,
            apply_decay=False, record_access=False,
            graph_expand=True, graph_depth=1,
        )

        hits = r.search(
            "tenant isolated alpha",
            top_k=10,
            filters=SearchFilter(tenant_id="tenant-a"),
        )

        hit_ids = {h.id for h in hits}
        assert "mem-20260528-100000-tenant-a" in hit_ids
        assert "mem-20260528-100000-tenant-b" not in hit_ids
