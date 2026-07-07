"""P2-5: unlink must be durable — strip target from source md refs.mems.

Removing only the sqlite refs_graph edge let it resurrect on the next
upsert/reindex (HubIndex.upsert repopulates refs_graph from refs.mems). The
md is the source of truth, so unlink_mem edits the frontmatter too.
"""
from datetime import datetime, timezone
from pathlib import Path

from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Refs


def _store(tmp: Path) -> ItemsStore:
    return ItemsStore(items_dir=tmp / "items")


def _item(suffix: str, mems: list[str]) -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260519-100000-{suffix}",
        type=MemoryType.decision,
        created_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        title=suffix,
        summary=suffix,
        refs=Refs(mems=mems),
    )


def test_unlink_mem_strips_ref_and_persists(tmp_path: Path):
    store = _store(tmp_path)
    target = "mem-20260519-100000-b"
    store.write(_item("a", [target]), "body a")

    assert store.unlink_mem("mem-20260519-100000-a", target) is True

    # Re-read from disk: the edge must be gone in the source of truth.
    reread, _ = store.get("mem-20260519-100000-a")
    assert target not in reread.refs.mems


def test_unlink_mem_noop_when_not_linked(tmp_path: Path):
    store = _store(tmp_path)
    store.write(_item("a", []), "body a")
    assert store.unlink_mem("mem-20260519-100000-a", "mem-20260519-100000-x") is False


def test_unlink_mem_noop_when_source_missing(tmp_path: Path):
    store = _store(tmp_path)
    assert store.unlink_mem("mem-20260519-100000-ghost", "mem-20260519-100000-x") is False
