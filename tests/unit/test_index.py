from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from agent_brain.contracts.memory_item import MemoryItem, MemoryType


def _make_item(suffix: str, title: str, body_keywords: list[str]) -> tuple[MemoryItem, str]:
    item = MemoryItem(
        id=f"mem-20260519-100000-{suffix}",
        type=MemoryType.fact,
        created_at=datetime.fromisoformat("2026-05-19T10:00:00+08:00"),
        title=title,
        summary=" ".join(body_keywords),
    )
    body = " ".join(body_keywords)
    return item, body


def test_index_schema_helpers_are_split_and_reexported():
    from agent_brain.platform.indexing import index
    from agent_brain.platform.indexing.index_schema import init_index_schema, segment_cjk

    assert index._segment_cjk is segment_cjk
    assert callable(init_index_schema)
    assert segment_cjk("中文abc") == " 中  文 abc"


def test_index_creates_tables(tmp_brain_dir: Path):
    from agent_brain.platform.indexing.index import HubIndex

    idx = HubIndex(db_path=tmp_brain_dir / "index.db")
    tables = idx.connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    names = {row[0] for row in tables}
    assert "items_meta" in names
    assert "items_fts" in names


def test_index_delegates_metadata_operations(tmp_brain_dir: Path):
    from agent_brain.platform.indexing.index import HubIndex
    from agent_brain.platform.indexing.metadata_index import MetadataIndex

    idx = HubIndex(db_path=tmp_brain_dir / "index.db")
    assert isinstance(idx.metadata, MetadataIndex)
    assert idx.filter_ids() is None


def test_index_delegates_vector_operations(tmp_brain_dir: Path):
    from agent_brain.platform.indexing.index import HubIndex
    from agent_brain.platform.indexing.vector_index import VectorIndex

    idx = HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=4)
    assert isinstance(idx.vector, VectorIndex)


def test_index_delegates_write_operations(tmp_brain_dir: Path):
    from agent_brain.platform.indexing.index import HubIndex
    from agent_brain.platform.indexing.index_writer import IndexWriter

    idx = HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=4)
    assert isinstance(idx.writer, IndexWriter)


def test_upsert_and_fts_search(tmp_brain_dir: Path):
    from agent_brain.platform.indexing.index import HubIndex

    idx = HubIndex(db_path=tmp_brain_dir / "index.db")
    item, body = _make_item("alpha", "Python type hints", "pyright mypy type-checker".split())
    idx.upsert(item, body, embedding=None)
    hits = idx.bm25_search("mypy", top_k=10)
    assert len(hits) == 1
    assert hits[0].id == item.id
    assert hits[0].score > 0


def test_vector_search(tmp_brain_dir: Path):
    from agent_brain.platform.indexing.index import HubIndex

    idx = HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=4)
    item_a, body_a = _make_item("a", "A", ["foo"])
    item_b, body_b = _make_item("b", "B", ["bar"])
    idx.upsert(item_a, body_a, embedding=[1.0, 0.0, 0.0, 0.0])
    idx.upsert(item_b, body_b, embedding=[0.0, 1.0, 0.0, 0.0])
    hits = idx.vector_search([1.0, 0.0, 0.0, 0.0], top_k=10)
    assert hits[0].id == item_a.id


def test_index_connection_can_be_used_from_worker_thread(tmp_brain_dir: Path):
    from agent_brain.platform.indexing.index import HubIndex

    idx = HubIndex(db_path=tmp_brain_dir / "index.db")
    item, body = _make_item("thread", "Thread-safe web cache", ["web", "cache"])

    def write_and_search():
        idx.upsert(item, body, embedding=None)
        return [hit.id for hit in idx.bm25_search("cache", top_k=5)]

    with ThreadPoolExecutor(max_workers=1) as pool:
        ids = pool.submit(write_and_search).result()

    assert item.id in ids
