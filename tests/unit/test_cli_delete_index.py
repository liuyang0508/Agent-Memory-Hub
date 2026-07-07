"""P0-4: delete/gc must evict the item from the sqlite index too.

Previously the CLI unlinked the md file but left the FTS/vec rows behind, so
the deleted item kept surfacing as a permanent ghost hit on every search.
"""
from datetime import datetime, timezone
from pathlib import Path

from agent_brain.contracts.memory_item import MemoryItem, MemoryType


def _item() -> tuple[MemoryItem, str]:
    item = MemoryItem(
        id="mem-20260519-100000-ghost",
        type=MemoryType.fact,
        created_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        title="ghost hit",
        summary="mypy pyright type checker",
    )
    return item, item.summary


def test_evict_removes_item_from_index(tmp_brain_dir: Path, monkeypatch) -> None:
    from agent_brain.platform.indexing.index import HubIndex
    from agent_brain.interfaces import cli

    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    idx = HubIndex(db_path=tmp_brain_dir / "index.db")
    item, body = _item()
    idx.upsert(item, body, embedding=None)
    assert idx.bm25_search("mypy", top_k=10)  # present before
    idx.connection.close()

    cli._evict_from_index(item.id)

    idx2 = HubIndex(db_path=tmp_brain_dir / "index.db")
    assert idx2.bm25_search("mypy", top_k=10) == []  # gone after


def test_evict_is_noop_without_index_db(tmp_brain_dir: Path, monkeypatch) -> None:
    from agent_brain.interfaces import cli

    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    # No index.db on disk — must not raise or create churn.
    cli._evict_from_index("mem-20260519-100000-absent")
    assert not (tmp_brain_dir / "index.db").exists()
