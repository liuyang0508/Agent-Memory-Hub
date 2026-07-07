from __future__ import annotations

import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.recall.retrieval_access import RetrievalAccessRecorder
from agent_brain.memory.recall.retrieval_types import RetrievedItem
from agent_brain.platform.indexing.vector_index import VectorIndex
from agent_brain.contracts.memory_item import MemoryItem, MemoryType


def _item(
    item_id: str,
    *,
    title: str = "review hardening",
    summary: str = "review hardening summary",
    tags: list[str] | None = None,
    created_at: datetime | None = None,
    tenant_id: str | None = None,
) -> MemoryItem:
    return MemoryItem(
        id=item_id,
        type=MemoryType.fact,
        created_at=created_at or datetime.now(timezone.utc),
        title=title,
        summary=summary,
        tags=tags or [],
        tenant_id=tenant_id,
    )


def test_mcp_gc_memory_deletes_index_rows_for_removed_markdown(tmp_brain: Path):
    from agent_brain.interfaces.mcp.tools._shared import _components_cache
    from agent_brain.interfaces.mcp.tools.io import gc_memory

    _components_cache.clear()
    item = _item(
        "mem-20260616-010000-stale-gc",
        tags=["needs-review"],
        created_at=datetime.now(timezone.utc) - timedelta(days=30),
    )
    store = ItemsStore(tmp_brain / "items")
    idx = HubIndex(tmp_brain / "index.db", embedding_dim=8)
    embedder = HashingEmbedder(dim=8)
    store.write(item, "stale gc body")
    idx.upsert(item, "stale gc body", embedding=embedder.embed(item.context_views.locator))
    idx.close()

    result = gc_memory(max_age_days=7, tags=["needs-review"], dry_run=False)

    assert result["deleted"] == 1
    idx = HubIndex(tmp_brain / "index.db", embedding_dim=8)
    try:
        assert item.id not in idx.all_ids()
        assert idx.bm25_search("stale", top_k=5) == []
    finally:
        idx.close()
        _components_cache.clear()


def test_cli_evict_from_index_closes_best_effort_index(monkeypatch, tmp_brain: Path):
    from agent_brain.interfaces.cli import _shared

    (tmp_brain / "index.db").touch()
    closed: list[str] = []

    class FakeHubIndex:
        def __init__(self, db_path: Path) -> None:
            self.db_path = db_path

        def delete(self, item_id: str) -> None:
            assert item_id == "mem-20260616-010001-close-index"

        def close(self) -> None:
            closed.append(str(self.db_path))

    monkeypatch.setattr(_shared, "HubIndex", FakeHubIndex)

    _shared._evict_from_index("mem-20260616-010001-close-index")

    assert closed == [str(tmp_brain / "index.db")]


def test_index_upsert_rolls_back_metadata_when_vector_write_fails(tmp_brain_dir: Path):
    idx = HubIndex(tmp_brain_dir / "index.db", embedding_dim=8)
    item = _item("mem-20260616-010002-rollback", title="rollback sentinel")

    with pytest.raises(ValueError):
        idx.upsert(item, "rollback body", embedding=[1.0, 2.0])

    row = idx.connection.execute(
        "SELECT id FROM items_meta WHERE id = ?",
        (item.id,),
    ).fetchone()
    assert row is None
    assert idx.bm25_search("rollback", top_k=5) == []


def test_index_filter_since_days_uses_utc_instant_not_local_offset(tmp_brain_dir: Path):
    idx = HubIndex(tmp_brain_dir / "index.db", embedding_dim=8)
    offset = timezone(timedelta(hours=8))
    old_instant_with_positive_offset = (
        datetime.now(timezone.utc) - timedelta(days=7, minutes=5)
    ).astimezone(offset)
    item = _item(
        "mem-20260616-010006-offset-old",
        title="offset old",
        created_at=old_instant_with_positive_offset,
    )
    embedder = HashingEmbedder(dim=8)
    idx.upsert(item, "offset old body", embedding=embedder.embed(item.context_views.locator))

    assert item.id not in idx.filter_ids(since_days=7)


class _FakeRows:
    def __init__(self, rows: list[tuple[str, bytes]]) -> None:
        self.rows = rows

    def fetchall(self) -> list[tuple[str, bytes]]:
        return self.rows


class _FakeOne:
    def __init__(self, row: tuple[bytes] | None) -> None:
        self.row = row

    def fetchone(self) -> tuple[bytes] | None:
        return self.row


class _CountingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    def execute(self, sql: str, params):
        self.calls.append((sql, list(params)))
        if "WHERE id = ?" in sql:
            item_id = params[0]
            if item_id == "missing":
                return _FakeOne(None)
            index = len(self.calls)
            return _FakeOne((struct.pack("2f", float(index), float(index + 1)),))
        rows = [
            (item_id, struct.pack("2f", float(index), float(index + 1)))
            for index, item_id in enumerate(params)
            if item_id != "missing"
        ]
        return _FakeRows(rows)


def test_vector_get_embeddings_fetches_ids_in_one_query() -> None:
    connection = _CountingConnection()
    vector = VectorIndex(connection, embedding_dim=2)  # type: ignore[arg-type]

    embeddings = vector.get_embeddings(["a", "missing", "b"])

    assert len(connection.calls) == 1
    sql, params = connection.calls[0]
    assert "WHERE id IN" in sql
    assert params == ["a", "missing", "b"]
    assert set(embeddings) == {"a", "b"}


def test_retrieval_access_recorder_batches_plain_access_updates() -> None:
    recorded: list[tuple[list[str], str]] = []

    class FakeIndex:
        def record_access_many(self, item_ids: list[str], accessed_at: str) -> None:
            recorded.append((item_ids, accessed_at))

        def record_access(self, item_id: str, accessed_at: str) -> None:
            raise AssertionError("plain access should use batch update")

    results = [
        RetrievedItem("mem-20260616-010003-access-a", score=1.0, bm25_rank=1, vector_rank=None),
        RetrievedItem("mem-20260616-010004-access-b", score=0.9, bm25_rank=2, vector_rank=None),
    ]

    RetrievalAccessRecorder(index=FakeIndex()).record(results, accessed_at="2026-06-16T01:00:00Z")

    assert recorded == [
        (
            [
                "mem-20260616-010003-access-a",
                "mem-20260616-010004-access-b",
            ],
            "2026-06-16T01:00:00Z",
        )
    ]


def test_import_records_marks_dirty_when_indexing_fails(tmp_brain: Path):
    from agent_brain.memory.evidence.import_service import import_records

    class BrokenIndex:
        def upsert(self, *args, **kwargs) -> None:
            raise RuntimeError("index offline")

    class Embedder:
        def embed(self, text: str) -> list[float]:
            return [0.0] * 8

    item = _item("mem-20260616-010005-import-dirty", title="import dirty")
    result = import_records(
        [{"frontmatter": item.model_dump(mode="json"), "body": "import dirty body"}],
        store=ItemsStore(tmp_brain / "items"),
        index=BrokenIndex(),
        embedder=Embedder(),
        overwrite=False,
        brain_dir=tmp_brain,
    )

    assert result.imported == 1
    assert result.skipped == 0
    assert result.errors == ["mem-20260616-010005-import-dirty: index failed: index offline"]
    assert (tmp_brain / "items" / f"{item.id}.md").exists()
    assert (tmp_brain / ".index-dirty").read_text(encoding="utf-8") == f"{item.id}\n"
