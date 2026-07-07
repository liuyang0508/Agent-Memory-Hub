from __future__ import annotations

from datetime import datetime, timezone

from agent_brain.contracts.memory_item import MemoryItem, MemoryType


def test_embedding_text_uses_locator_and_overview_but_not_detail_body() -> None:
    from agent_brain.memory.recall.embedding_text import embedding_text_for_item

    item = MemoryItem(
        id="mem-20260618-130000-embedding-text",
        type=MemoryType.fact,
        created_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
        title="Embedding text",
        summary="fallback summary",
        context_views={
            "locator": "short locator signal",
            "overview": "middle overview navigation",
            "detail_uri": "memory://items/mem-20260618-130000-embedding-text/body",
        },
    )

    text = embedding_text_for_item(item)

    assert "short locator signal" in text
    assert "middle overview navigation" in text
    assert "fallback summary" not in text
    assert "detail-only body" not in text


def test_embedding_text_falls_back_to_summary_when_locator_missing() -> None:
    from agent_brain.memory.recall.embedding_text import embedding_text_for_item

    item = MemoryItem(
        id="mem-20260618-130001-embedding-text",
        type=MemoryType.fact,
        created_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
        title="Embedding text fallback",
        summary="fallback summary",
        context_views={
            "locator": "",
            "overview": "",
            "detail_uri": "memory://items/mem-20260618-130001-embedding-text/body",
        },
    )

    assert embedding_text_for_item(item) == "fallback summary"


def test_write_service_indexes_locator_plus_overview_not_body(tmp_path) -> None:
    from agent_brain.memory.store.items_store import ItemsStore
    from agent_brain.memory.store.write_service import WriteService

    class RecordingEmbedder:
        text = ""

        def embed(self, text: str) -> list[float]:
            self.text = text
            return [1.0, 0.0]

    class RecordingIndex:
        embedding: list[float] | None = None

        def upsert(self, item, body, embedding):
            self.embedding = embedding

    item = MemoryItem(
        id="mem-20260618-130002-embedding-text",
        type=MemoryType.fact,
        created_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
        title="Embedding write",
        summary="fallback summary",
        context_views={
            "locator": "write locator signal",
            "overview": "write overview navigation",
            "detail_uri": "memory://items/mem-20260618-130002-embedding-text/body",
        },
    )
    embedder = RecordingEmbedder()
    index = RecordingIndex()

    result = WriteService(
        ItemsStore(tmp_path / "items"),
        index=index,
        embedder=embedder,
        brain_dir=tmp_path,
    ).write(item=item, body="detail-only body should not be embedded", allow_unsafe=True)

    assert result.indexed is True
    assert embedder.text == "write locator signal\nwrite overview navigation"
    assert "detail-only body" not in embedder.text
    assert index.embedding == [1.0, 0.0]


def test_reindex_store_uses_same_embedding_feature_text() -> None:
    from agent_brain.interfaces.cli.commands.index_maintenance import reindex_store

    class RecordingEmbedder:
        texts: list[str]

        def __init__(self) -> None:
            self.texts = []

        def embed(self, text: str) -> list[float]:
            self.texts.append(text)
            return [1.0, 0.0]

    class Store:
        def iter_all(self):
            yield item, "detail-only body should not be embedded"

    class Index:
        def upsert(self, item, body, embedding):
            pass

    item = MemoryItem(
        id="mem-20260618-130003-embedding-text",
        type=MemoryType.fact,
        created_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
        title="Embedding reindex",
        summary="fallback summary",
        context_views={
            "locator": "reindex locator signal",
            "overview": "reindex overview navigation",
        },
    )
    embedder = RecordingEmbedder()

    result = reindex_store(Store(), Index(), embedder)

    assert result.indexed == 1
    assert embedder.texts == ["reindex locator signal\nreindex overview navigation"]
