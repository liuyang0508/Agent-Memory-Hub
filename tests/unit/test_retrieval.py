from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.memory.recall.retrieval import expand_query, _tokenize_mixed, rerank_enabled
from agent_brain.contracts.memory_item import MemoryItem, MemoryType


def _build_index(tmp: Path, items: list[tuple[str, str, str]]):
    """items = list of (suffix, title, body)."""
    from agent_brain.platform.indexing.index import HubIndex

    idx = HubIndex(db_path=tmp / "index.db", embedding_dim=8)
    emb = HashingEmbedder(dim=8)
    for suffix, title, body in items:
        item = MemoryItem(
            id=f"mem-20260519-100000-{suffix}",
            type=MemoryType.fact,
            created_at=datetime.now(),
            title=title,
            summary=body[:60],
        )
        idx.upsert(item, body, embedding=emb.embed(f"{title} {body}"))
    return idx


def test_rrf_combines_bm25_and_vector(tmp_brain_dir: Path):
    from agent_brain.memory.recall.retrieval import Retriever

    idx = _build_index(tmp_brain_dir, [
        ("a", "Python type hints", "mypy pyright type checker"),
        ("b", "Database migration", "alembic schema rollback"),
        ("c", "Type annotations", "static analysis pyright"),
    ])
    retriever = Retriever(index=idx, embedder=HashingEmbedder(dim=8))
    hits = retriever.search("type checker pyright", top_k=3)
    ids = [h.id for h in hits]
    # Items a and c are about types, should rank above b
    assert ids[0].endswith("a") or ids[0].endswith("c")
    assert not ids[0].endswith("b")


def test_rrf_top_k_respected(tmp_brain_dir: Path):
    from agent_brain.memory.recall.retrieval import Retriever

    idx = _build_index(tmp_brain_dir, [
        (f"i{i}", f"title {i}", f"body {i} keyword")
        for i in range(5)
    ])
    retriever = Retriever(index=idx, embedder=HashingEmbedder(dim=8))
    hits = retriever.search("keyword", top_k=2)
    assert len(hits) == 2


def test_search_record_access_override_is_per_call_and_does_not_mutate_instance(
    tmp_brain_dir: Path,
):
    from agent_brain.memory.recall.retrieval import Retriever

    idx = _build_index(tmp_brain_dir, [
        ("override", "Per call access override", "record access override boundary"),
    ])
    retriever = Retriever(
        index=idx,
        embedder=HashingEmbedder(dim=8),
        record_access=True,
    )

    hits = retriever.search(
        "record access override boundary",
        top_k=1,
        record_access=False,
    )

    assert [hit.id for hit in hits] == ["mem-20260519-100000-override"]
    assert retriever.record_access is True
    assert idx.get_decay_data([hits[0].id])[hits[0].id][4] == 0

    retriever.search("record access override boundary", top_k=1)

    assert retriever.record_access is True
    assert idx.get_decay_data([hits[0].id])[hits[0].id][4] == 1


def test_bm25_weight_boost(tmp_brain_dir: Path):
    from agent_brain.memory.recall.retrieval import Retriever

    idx = _build_index(tmp_brain_dir, [
        ("a", "Redis caching layer", "fast in-memory store"),
        ("b", "Database migration", "alembic schema rollback"),
    ])
    r1 = Retriever(index=idx, embedder=HashingEmbedder(dim=8), bm25_weight=1.0, vector_weight=1.0)
    r2 = Retriever(index=idx, embedder=HashingEmbedder(dim=8), bm25_weight=2.0, vector_weight=0.5)
    h1 = r1.search("Redis", top_k=2)
    h2 = r2.search("Redis", top_k=2)
    assert h1[0].id == h2[0].id
    assert h2[0].score > h1[0].score


def test_vector_disabled_does_not_call_embedder(tmp_brain_dir: Path):
    from agent_brain.memory.recall.retrieval import Retriever

    class ExplodingEmbedder:
        dim = 8
        degraded = False

        def embed(self, text: str):
            raise AssertionError("vector embedder should not be called")

    idx = _build_index(tmp_brain_dir, [
        ("a", "Redis caching layer", "fast in-memory store"),
    ])
    retriever = Retriever(
        index=idx,
        embedder=ExplodingEmbedder(),
        vector_weight=0.0,
        vector_top=0,
        apply_decay=False,
        record_access=False,
    )

    hits = retriever.search("Redis", top_k=1)

    assert hits[0].id.endswith("a")


def test_rrf_fusion_helper_combines_ranked_hit_lists():
    from agent_brain.memory.recall.retrieval_fusion import rrf_fusion

    hits = rrf_fusion(
        bm25_hits=[SimpleNamespace(id="a"), SimpleNamespace(id="b")],
        vector_hits=[SimpleNamespace(id="b"), SimpleNamespace(id="c")],
        rrf_k=60,
        bm25_weight=1.0,
        vector_weight=1.0,
    )

    by_id = {hit.id: hit for hit in hits}
    assert [hit.id for hit in hits] == ["b", "a", "c"]
    assert by_id["b"].bm25_rank == 2
    assert by_id["b"].vector_rank == 1
    assert by_id["a"].bm25_rank == 1
    assert by_id["a"].vector_rank is None


def test_access_recorder_logs_record_access_failures(caplog):
    from agent_brain.memory.recall.retrieval_access import RetrievalAccessRecorder
    from agent_brain.memory.recall.retrieval_types import RetrievedItem

    class FailingIndex:
        def record_access(self, item_id: str, accessed_at: str) -> None:
            raise RuntimeError(f"boom {item_id}")

    recorder = RetrievalAccessRecorder(index=FailingIndex())
    results = [RetrievedItem(id="mem-1", score=1.0, bm25_rank=1, vector_rank=None)]

    returned = recorder.record(results)

    assert returned == results
    assert "Failed to record retrieval access for mem-1" in caplog.text


def test_status_handoff_strategy_helpers_are_split():
    from agent_brain.memory.recall.retrieval_status import (
        apply_status_handoff_boost,
        is_status_risk_query,
        supplement_status_handoff_candidates,
    )

    assert is_status_risk_query("stale memory outdated project status") is True
    assert is_status_risk_query("ordinary semantic search") is False
    assert callable(supplement_status_handoff_candidates)
    assert callable(apply_status_handoff_boost)


def test_status_risk_query_boosts_current_handoff_signal(tmp_brain_dir: Path):
    from agent_brain.platform.indexing.index import HubIndex
    from agent_brain.memory.recall.retrieval import Retriever

    idx = HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=8)
    emb = HashingEmbedder(dim=8)
    old_decision = MemoryItem(
        id="mem-20260519-100000-old-status",
        type=MemoryType.decision,
        created_at=datetime.now(),
        title="Outdated project status warning",
        summary="stale outdated project status risk",
        tags=["feedback", "reorient"],
        project="agent-memory-hub",
    )
    current_signal = MemoryItem(
        id="mem-20260609-100000-current-handoff",
        type=MemoryType.signal,
        created_at=datetime.now(),
        title="Current project handoff signal",
        summary="current status handoff and next-step guidance",
        tags=["handoff", "status", "next-step"],
        project="agent-memory-hub",
    )
    idx.upsert(
        old_decision,
        "stale outdated project status " * 8,
        embedding=emb.embed(f"{old_decision.title} {old_decision.summary}"),
    )
    idx.upsert(
        current_signal,
        "current handoff status next-step",
        embedding=emb.embed(f"{current_signal.title} {current_signal.summary}"),
    )

    retriever = Retriever(
        index=idx,
        embedder=emb,
        vector_weight=0,
        apply_decay=False,
        record_access=False,
    )

    hits = retriever.search("stale memory outdated project status", top_k=2)

    assert hits[0].id == current_signal.id


def test_status_risk_query_supplements_handoff_signal_beyond_bm25_pool(
    tmp_brain_dir: Path,
):
    from agent_brain.platform.indexing.index import HubIndex
    from agent_brain.memory.recall.retrieval import Retriever

    idx = HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=8)
    emb = HashingEmbedder(dim=8)
    for i in range(12):
        stale = MemoryItem(
            id=f"mem-20260519-100000-stale-{i}",
            type=MemoryType.decision,
            created_at=datetime.now(),
            title=f"Outdated project status warning {i}",
            summary="stale outdated project status risk",
            tags=["feedback", "reorient"],
            project="agent-memory-hub",
        )
        idx.upsert(
            stale,
            "stale outdated project status " * 8,
            embedding=emb.embed(f"{stale.title} {stale.summary}"),
        )
    current_signal = MemoryItem(
        id="mem-20260609-100000-current-handoff",
        type=MemoryType.signal,
        created_at=datetime.now(),
        title="Current project handoff signal",
        summary="next-step guidance",
        tags=["handoff", "status", "next-step"],
        project="agent-memory-hub",
    )
    idx.upsert(
        current_signal,
        "handoff next-step",
        embedding=emb.embed(f"{current_signal.title} {current_signal.summary}"),
    )

    retriever = Retriever(
        index=idx,
        embedder=emb,
        bm25_top=3,
        vector_weight=0,
        apply_decay=False,
        record_access=False,
    )

    hits = retriever.search("stale memory outdated project status", top_k=2)

    assert current_signal.id in {hit.id for hit in hits}


def test_adapter_runtime_query_boosts_specific_runtime_fact(tmp_brain_dir: Path):
    from agent_brain.platform.indexing.index import HubIndex
    from agent_brain.memory.recall.retrieval_runtime import apply_adapter_runtime_evidence_boost
    from agent_brain.memory.recall.retrieval_types import RetrievedItem

    idx = HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=8)
    emb = HashingEmbedder(dim=8)
    generic = MemoryItem(
        id="mem-20260609-100000-runtime-ledger",
        type=MemoryType.artifact,
        created_at=datetime.now(),
        title="Adapter runtime evidence ledger",
        summary="runtime events and verification truth contract",
        tags=["adapter", "runtime-evidence", "truth-contract"],
        project="agent-memory-hub",
    )
    codex_fact = MemoryItem(
        id="mem-20260609-100000-codex-runtime",
        type=MemoryType.fact,
        created_at=datetime.now(),
        title="Codex adapter runtime env installed in real hooks",
        summary="AGENT_MEMORY_HUB_ADAPTER=codex configured; runtime_observed still pending",
        tags=["codex", "adapter", "real-config", "runtime-evidence", "hooks"],
        project="agent-memory-hub",
    )
    idx.upsert(
        generic,
        "runtime events adapter verification",
        embedding=emb.embed(f"{generic.title} {generic.summary}"),
    )
    idx.upsert(
        codex_fact,
        "codex hooks installed runtime observation pending",
        embedding=emb.embed(f"{codex_fact.title} {codex_fact.summary}"),
    )
    candidates = [
        RetrievedItem(id=generic.id, score=0.020, bm25_rank=2, vector_rank=1),
        RetrievedItem(id=codex_fact.id, score=0.011, bm25_rank=1, vector_rank=None),
    ]

    boosted = apply_adapter_runtime_evidence_boost(
        idx,
        "Codex hooks are installed but runtime events still need real observation",
        candidates,
    )

    assert boosted[0].id == codex_fact.id


def test_query_expansion_or():
    expanded = expand_query("hello")
    assert '"hello"' in expanded
    expanded = expand_query("agent memory hub")
    assert "OR" in expanded
    assert '"agent"' in expanded
    assert '"memory"' in expanded
    assert '"hub"' in expanded


def test_query_expansion_and():
    expanded = expand_query("agent memory hub", use_or=False)
    assert "OR" not in expanded
    assert '"agent"' in expanded
    assert '"hub"' in expanded


def test_query_expansion_cjk():
    expanded = expand_query("MCP 接入 测试")
    assert "OR" in expanded
    tokens = _tokenize_mixed("MCP 接入 测试")
    assert "MCP" in tokens
    assert "接" in tokens
    assert "入" in tokens


def test_query_expansion_escapes_special_chars():
    expanded = expand_query("CLI: something AND title:value")
    assert "OR" in expanded
    assert '"CLI"' in expanded


def test_query_expansion_disabled(tmp_brain_dir: Path):
    from agent_brain.memory.recall.retrieval import Retriever

    idx = _build_index(tmp_brain_dir, [
        ("a", "alpha beta gamma", "content alpha"),
        ("b", "delta epsilon", "content delta"),
    ])
    r_on = Retriever(index=idx, embedder=HashingEmbedder(dim=8), query_expansion=True)
    r_off = Retriever(index=idx, embedder=HashingEmbedder(dim=8), query_expansion=False)
    hits_on = r_on.search("alpha delta", top_k=2)
    hits_off = r_off.search("alpha delta", top_k=2)
    assert len(hits_on) >= len(hits_off)


def test_rerank_enabled_env(monkeypatch):
    monkeypatch.delenv("RERANK_ENABLED", raising=False)
    assert rerank_enabled() is False
    monkeypatch.setenv("RERANK_ENABLED", "1")
    assert rerank_enabled() is True
    monkeypatch.setenv("RERANK_ENABLED", "true")
    assert rerank_enabled() is True
    monkeypatch.setenv("RERANK_ENABLED", "0")
    assert rerank_enabled() is False


def test_retriever_delegates_cross_encoder_rerank(tmp_brain_dir: Path):
    from agent_brain.memory.recall import retrieval
    from agent_brain.memory.recall.retrieval import Retriever
    from agent_brain.memory.recall.retrieval_rerank import CrossEncoderReranker

    idx = _build_index(tmp_brain_dir, [("a", "hello world", "some content")])
    r = Retriever(index=idx, embedder=HashingEmbedder(dim=8), rerank=True)
    assert isinstance(r.cross_encoder_reranker, CrossEncoderReranker)
    assert retrieval._sigmoid is retrieval.retrieval_rerank._sigmoid


def test_rerank_off_by_default(tmp_brain_dir: Path, monkeypatch):
    from agent_brain.memory.recall.retrieval import Retriever

    monkeypatch.delenv("RERANK_ENABLED", raising=False)
    idx = _build_index(tmp_brain_dir, [("a", "hello world", "some content")])
    r = Retriever(index=idx, embedder=HashingEmbedder(dim=8))
    assert r.rerank is False


def test_rerank_explicit_flag(tmp_brain_dir: Path, monkeypatch):
    from agent_brain.memory.recall.retrieval import Retriever

    monkeypatch.delenv("RERANK_ENABLED", raising=False)
    idx = _build_index(tmp_brain_dir, [("a", "hello world", "some content")])
    r = Retriever(index=idx, embedder=HashingEmbedder(dim=8), rerank=True)
    assert r.rerank is True


def test_rerank_reorders_candidates(tmp_brain_dir: Path):
    from agent_brain.memory.recall.retrieval import Retriever

    idx = _build_index(tmp_brain_dir, [
        ("a", "Python async programming", "asyncio await coroutine event loop"),
        ("b", "Database indexing", "B-tree hash index query optimizer"),
        ("c", "Python decorators", "function wrapper decorator pattern"),
    ])

    def fake_predict(pairs):
        scores = []
        for _, doc in pairs:
            if "decorator" in doc.lower():
                scores.append(0.9)
            elif "asyncio" in doc.lower():
                scores.append(0.5)
            else:
                scores.append(0.1)
        return scores

    r = Retriever(index=idx, embedder=HashingEmbedder(dim=8), rerank=True, apply_decay=False)
    with patch("agent_brain.memory.recall.retrieval._get_cross_encoder") as mock_ce:
        mock_ce.return_value.predict = fake_predict
        hits = r.search("Python patterns", top_k=3)

    assert hits[0].id.endswith("c")
    # Rerank scores are sigmoid-normalized to (0,1) before decay (P1-5) so that
    # negative cross-encoder logits can't invert the ranking. Ordering is the
    # real contract; the squashed score must be monotonic in the raw logit.
    from agent_brain.memory.recall.retrieval import _sigmoid

    assert hits[0].score == pytest.approx(_sigmoid(0.9))
    assert hits[0].score > hits[1].score > hits[2].score


def test_rerank_with_no_fts_match(tmp_brain_dir: Path):
    """When FTS returns nothing, vector still produces candidates; reranker should not crash."""
    from agent_brain.memory.recall.retrieval import Retriever

    idx = _build_index(tmp_brain_dir, [("a", "hello", "world")])

    def fake_predict(pairs):
        return [0.5] * len(pairs)

    r = Retriever(index=idx, embedder=HashingEmbedder(dim=8), rerank=True)
    with patch("agent_brain.memory.recall.retrieval._get_cross_encoder") as mock_ce:
        mock_ce.return_value.predict = fake_predict
        hits = r.search("zzz_no_match_zzz", top_k=3)
    assert isinstance(hits, list)


def test_rerank_top_limits_pool(tmp_brain_dir: Path):
    from agent_brain.memory.recall.retrieval import Retriever

    items = [(f"i{i}", f"item number {i}", f"content keyword {i}") for i in range(10)]
    idx = _build_index(tmp_brain_dir, items)

    predict_call_sizes = []

    def fake_predict(pairs):
        predict_call_sizes.append(len(pairs))
        return [0.5] * len(pairs)

    r = Retriever(index=idx, embedder=HashingEmbedder(dim=8), rerank=True, rerank_top=3)
    with patch("agent_brain.memory.recall.retrieval._get_cross_encoder") as mock_ce:
        mock_ce.return_value.predict = fake_predict
        r.search("keyword", top_k=10)

    assert predict_call_sizes[0] <= 3
