from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.platform.embedding import HashingEmbedder


def test_continuous_hopfield_recall_prefers_associated_cluster() -> None:
    from agent_brain.memory.recall.hopfield_memory import ContinuousHopfieldMemory

    memory = ContinuousHopfieldMemory(
        {
            "redis-cache": [1.0, 0.0, 0.0],
            "redis-ttl": [0.92, 0.08, 0.0],
            "vector-db": [0.0, 1.0, 0.0],
        },
        beta=8.0,
    )

    recalled = memory.recall([1.0, 0.0, 0.0], top_k=2)

    assert recalled.associations[0].id == "redis-cache"
    assert {assoc.id for assoc in recalled.associations} == {"redis-cache", "redis-ttl"}
    assert recalled.attractor[0] > recalled.attractor[1]


def test_retriever_hopfield_expansion_adds_associative_vector_neighbor(
    tmp_brain_dir: Path,
) -> None:
    from agent_brain.platform.indexing.index import HubIndex
    from agent_brain.memory.recall.retrieval import Retriever

    idx = HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=3)
    now = datetime(2026, 6, 18, tzinfo=timezone.utc)
    seed = MemoryItem(
        id="mem-20260618-120000-redis-seed",
        type=MemoryType.fact,
        created_at=now,
        title="Redis incident anchor",
        summary="redis incident anchor",
    )
    neighbor = MemoryItem(
        id="mem-20260618-120001-cache-neighbor",
        type=MemoryType.fact,
        created_at=now,
        title="Cache ttl policy",
        summary="ttl policy that is vector-associated with redis incidents",
    )
    far = MemoryItem(
        id="mem-20260618-120002-far-memory",
        type=MemoryType.fact,
        created_at=now,
        title="Postgres migration",
        summary="schema migration rollback",
    )
    idx.upsert(seed, "redis incident anchor", embedding=[1.0, 0.0, 0.0])
    idx.upsert(neighbor, "cache ttl policy", embedding=[0.95, 0.05, 0.0])
    idx.upsert(far, "postgres migration", embedding=[0.0, 1.0, 0.0])

    retriever = Retriever(
        index=idx,
        embedder=HashingEmbedder(dim=3),
        bm25_top=1,
        vector_weight=0.0,
        apply_decay=False,
        record_access=False,
        hopfield_expand=True,
        hopfield_top=3,
    )

    hits = retriever.search("redis", top_k=2)

    assert hits[0].id == seed.id
    assert neighbor.id in {hit.id for hit in hits}
