from __future__ import annotations

from datetime import datetime, timezone

from agent_brain.contracts.memory_item import MemoryItem, MemoryType


def _item(
    suffix: str,
    *,
    support_count: int = 0,
    contradict_count: int = 0,
    gain_score: float = 0.0,
) -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260612-020000-{suffix}",
        type=MemoryType.episode,
        created_at=datetime.now(timezone.utc),
        title=f"Feedback {suffix}",
        summary=f"Feedback summary {suffix}",
        support_count=support_count,
        contradict_count=contradict_count,
        gain_score=gain_score,
    )


def test_index_round_trips_feedback_value_fields(tmp_path) -> None:
    from agent_brain.platform.indexing.index import HubIndex

    index = HubIndex(tmp_path / "index.db", embedding_dim=4)
    item = _item(
        "roundtrip",
        support_count=3,
        contradict_count=1,
        gain_score=0.4,
    )
    index.upsert(item, "Useful browser fix", embedding=[0.1, 0.2, 0.3, 0.4])

    assert index.get_feedback_data([item.id])[item.id] == (3, 1, 0.4)


def test_update_feedback_stats_updates_existing_index_row(tmp_path) -> None:
    from agent_brain.platform.indexing.index import HubIndex

    index = HubIndex(tmp_path / "index.db", embedding_dim=4)
    item = _item("update")
    index.upsert(item, "Useful browser fix", embedding=[0.1, 0.2, 0.3, 0.4])

    index.update_feedback_stats(
        item.id,
        support_count=5,
        contradict_count=2,
        gain_score=-0.3,
    )

    assert index.get_feedback_data([item.id])[item.id] == (5, 2, -0.3)


def test_feedback_value_weight_promotes_supported_items(tmp_path) -> None:
    from agent_brain.platform.indexing.index import HubIndex
    from agent_brain.memory.recall.retrieval_types import RetrievedItem
    from agent_brain.memory.recall.retrieval_value import apply_feedback_value_weight

    index = HubIndex(tmp_path / "index.db", embedding_dim=4)
    supported = _item("supported", support_count=5, gain_score=0.6)
    rejected = _item("rejected", contradict_count=3, gain_score=-0.6)
    for item in (supported, rejected):
        index.upsert(item, item.summary, embedding=[0.1, 0.2, 0.3, 0.4])

    result = apply_feedback_value_weight(
        index,
        [
            RetrievedItem(id=rejected.id, score=1.0, bm25_rank=None, vector_rank=None),
            RetrievedItem(id=supported.id, score=1.0, bm25_rank=None, vector_rank=None),
        ],
    )

    assert [item.id for item in result] == [supported.id, rejected.id]
    assert result[-1].score > 0


def test_retriever_applies_feedback_value_weight(tmp_path, monkeypatch) -> None:
    from agent_brain.platform.embedding import HashingEmbedder
    from agent_brain.platform.indexing.index import HubIndex
    from agent_brain.memory.recall.retrieval import Retriever
    from agent_brain.memory.recall.retrieval_types import RetrievedItem

    index = HubIndex(tmp_path / "index.db", embedding_dim=4)
    supported = _item("pipe-supported", support_count=5, gain_score=0.6)
    rejected = _item("pipe-rejected", contradict_count=3, gain_score=-0.6)
    for item in (supported, rejected):
        index.upsert(item, item.summary, embedding=[0.1, 0.2, 0.3, 0.4])

    retriever = Retriever(
        index=index,
        embedder=HashingEmbedder(dim=4),
        apply_decay=False,
        record_access=False,
    )

    def _same_score_candidates(query, allowed_ids=None):
        return [
            RetrievedItem(id=rejected.id, score=1.0, bm25_rank=None, vector_rank=None),
            RetrievedItem(id=supported.id, score=1.0, bm25_rank=None, vector_rank=None),
        ]

    monkeypatch.setattr(retriever, "_rrf_fusion", _same_score_candidates)

    result = retriever.search("browser", top_k=2)

    assert [item.id for item in result] == [supported.id, rejected.id]
