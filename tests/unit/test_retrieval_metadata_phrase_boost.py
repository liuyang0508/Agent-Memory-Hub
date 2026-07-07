from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.memory.recall.embedding_text import embedding_text_for_item
from agent_brain.memory.recall.retrieval import Retriever, SearchFilter
from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex


NOW = datetime(2026, 6, 28, 12, 30, tzinfo=timezone.utc)


def _item(idx: int, title: str, summary: str) -> tuple[MemoryItem, str]:
    item_id = f"mem-20260628-123000-phrase-boost-{idx:03d}"
    item = MemoryItem.model_validate(
        {
            "id": item_id,
            "type": MemoryType.artifact.value,
            "created_at": NOW.isoformat(),
            "title": title,
            "summary": summary,
            "project": "agent-memory-hub",
            "tags": ["phrase-boost", "agent-memory-hub"],
            "confidence": 0.8,
            "refs": {"files": [f"docs/{idx}.md"]},
            "context_views": {
                "locator": summary,
                "overview": summary,
                "detail_uri": f"memory://items/{item_id}/body",
            },
        }
    )
    return item, f"{title}\n{summary}\nbody {idx}"


def test_exact_metadata_phrase_is_promoted_above_same_topic_distractors(tmp_path: Path) -> None:
    target, target_body = _item(
        1,
        "agent-memory-hub v0.2 完整项目 brain pool plus hooks plus slash command",
        "跨 Agent 共享大脑项目的完整代码与文档，含 brain pool hooks templates demo experiments",
    )
    distractors = [
        _item(
            idx + 10,
            f"Agent Memory Hub newer runtime hook benchmark {idx}",
            "agent memory hub brain pool hooks runtime benchmark context retrieval",
        )
        for idx in range(30)
    ]

    embedder = HashingEmbedder()
    index = HubIndex(tmp_path / "index.db", embedding_dim=embedder.dim)
    try:
        for item, body in [*distractors, (target, target_body)]:
            index.upsert(item, body, embedding=embedder.embed(embedding_text_for_item(item)))

        hits = Retriever(
            index=index,
            embedder=embedder,
            record_access=False,
            rerank=False,
            bm25_top=20,
            vector_top=20,
        ).search(
            "agent-memory-hub agent-memory agent memory hub v0.2 完整项目 brain pool plus hooks hook slash command",
            top_k=3,
            filters=SearchFilter(type="artifact"),
            explain=True,
        )

        assert hits[0].id == target.id
        assert hits[0].trace is not None
        assert "metadata_phrase:boosted" in hits[0].trace.signals
    finally:
        index.close()
