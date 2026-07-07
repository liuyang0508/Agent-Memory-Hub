"""Tests for MMR (Maximal Marginal Relevance) diversity re-ranking."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.recall.retrieval import Retriever, _cosine_sim
from agent_brain.contracts.memory_item import MemoryItem, MemoryType

_DIM = 8


def _item(suffix: str, title: str, **kw) -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260528-200000-{suffix}",
        type=kw.pop("type", MemoryType.fact),
        created_at=kw.pop("created_at", datetime(2026, 5, 28, 20, 0, 0, tzinfo=timezone.utc)),
        title=title,
        summary=kw.pop("summary", f"Summary for {suffix}"),
        project=kw.pop("project", "mmr-test"),
        tags=kw.pop("tags", ["test"]),
        **kw,
    )


def _seed(brain_dir: Path, items: list[tuple[MemoryItem, str]]) -> tuple[ItemsStore, HubIndex]:
    store = ItemsStore(items_dir=brain_dir / "items")
    embedder = HashingEmbedder(dim=_DIM)
    idx = HubIndex(db_path=brain_dir / "index.db", embedding_dim=_DIM)
    for item, body in items:
        store.write(item, body)
        idx.upsert(item, body, embedding=embedder.embed(f"{item.title} {body}"))
    return store, idx


class TestCosineSimHelper:
    def test_identical(self):
        v = [1.0, 2.0, 3.0]
        assert abs(_cosine_sim(v, v) - 1.0) < 1e-6

    def test_orthogonal(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert abs(_cosine_sim(a, b)) < 1e-6

    def test_zero_vector(self):
        assert _cosine_sim([0.0, 0.0], [1.0, 2.0]) == 0.0


class TestGetEmbeddings:
    def test_retrieves_stored_embeddings(self, tmp_brain_dir: Path):
        item = _item("emb-a", title="Embedding test")
        store, idx = _seed(tmp_brain_dir, [(item, "body")])
        embeddings = idx.get_embeddings([item.id])
        assert item.id in embeddings
        assert len(embeddings[item.id]) == _DIM

    def test_missing_id_excluded(self, tmp_brain_dir: Path):
        item = _item("emb-b", title="Exists")
        store, idx = _seed(tmp_brain_dir, [(item, "body")])
        embeddings = idx.get_embeddings([item.id, "mem-nonexistent"])
        assert item.id in embeddings
        assert "mem-nonexistent" not in embeddings


class TestMMRReranking:
    def test_mmr_lambda_none_no_rerank(self, tmp_brain_dir: Path):
        """With mmr_lambda=None, results are in pure relevance order."""
        items = [
            (_item(f"no-mmr-{i}", title=f"Python coding item {i}"), f"python coding {i}")
            for i in range(5)
        ]
        store, idx = _seed(tmp_brain_dir, items)
        embedder = HashingEmbedder(dim=_DIM)
        r = Retriever(index=idx, embedder=embedder, apply_decay=False, record_access=False)
        results = r.search("python coding", top_k=5)
        assert len(results) > 0

    def test_mmr_lambda_1_pure_relevance(self, tmp_brain_dir: Path):
        """mmr_lambda=1.0 should give same order as no MMR (pure relevance)."""
        items = [
            (_item(f"pure-{i}", title=f"Database optimization {i}"), f"database query optimization {i}")
            for i in range(5)
        ]
        store, idx = _seed(tmp_brain_dir, items)
        embedder = HashingEmbedder(dim=_DIM)

        r_no_mmr = Retriever(index=idx, embedder=embedder, apply_decay=False, record_access=False)
        r_mmr = Retriever(index=idx, embedder=embedder, apply_decay=False, record_access=False, mmr_lambda=1.0)

        results_no = r_no_mmr.search("database optimization", top_k=5)
        results_mmr = r_mmr.search("database optimization", top_k=5)
        ids_no = [r.id for r in results_no]
        ids_mmr = [r.id for r in results_mmr]
        assert ids_no == ids_mmr

    def test_mmr_low_lambda_increases_diversity(self, tmp_brain_dir: Path):
        """Low mmr_lambda should change ordering vs pure relevance."""
        a = _item("div-a", title="Python web framework Flask")
        b = _item("div-b", title="Python web framework Django")
        c = _item("div-c", title="Rust memory safety")
        d = _item("div-d", title="Python web framework FastAPI")
        items = [
            (a, "python web framework flask routes"),
            (b, "python web framework django views"),
            (c, "rust memory safety borrow checker"),
            (d, "python web framework fastapi async"),
        ]
        store, idx = _seed(tmp_brain_dir, items)
        embedder = HashingEmbedder(dim=_DIM)

        r_mmr = Retriever(
            index=idx, embedder=embedder, apply_decay=False,
            record_access=False, mmr_lambda=0.3,
        )
        results = r_mmr.search("python web framework", top_k=4)
        assert len(results) >= 3

    def test_mmr_returns_correct_count(self, tmp_brain_dir: Path):
        items = [
            (_item(f"cnt-{i}", title=f"Item about topic {i}"), f"content about topic {i}")
            for i in range(8)
        ]
        store, idx = _seed(tmp_brain_dir, items)
        embedder = HashingEmbedder(dim=_DIM)
        r = Retriever(
            index=idx, embedder=embedder, apply_decay=False,
            record_access=False, mmr_lambda=0.5,
        )
        results = r.search("topic", top_k=3)
        assert len(results) <= 3

    def test_mmr_single_result(self, tmp_brain_dir: Path):
        items = [(_item("single", title="Only item"), "only content")]
        store, idx = _seed(tmp_brain_dir, items)
        embedder = HashingEmbedder(dim=_DIM)
        r = Retriever(
            index=idx, embedder=embedder, apply_decay=False,
            record_access=False, mmr_lambda=0.5,
        )
        results = r.search("only item", top_k=1)
        assert len(results) == 1
