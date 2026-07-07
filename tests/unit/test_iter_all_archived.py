"""P1-2: iter_all must not re-surface archived items.

batch_archive moves items to ``items/archived/``. But iter_all used a plain
recursive rglob, so a subsequent reindex/governance sweep picked archived
items back up — silently undoing the archive and inflating counts.
"""
from datetime import datetime, timezone
from pathlib import Path

from agent_brain.contracts.memory_item import MemoryItem, MemoryType


def _item(suffix: str) -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260519-100000-{suffix}",
        type=MemoryType.fact,
        created_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        title=suffix,
        summary=suffix,
    )


def test_archived_excluded_by_default(tmp_path: Path) -> None:
    from agent_brain.memory.store.items_store import ItemsStore

    store = ItemsStore(items_dir=tmp_path / "items")
    store.write(_item("live"), "live body")
    # Simulate batch_archive: a file under items/archived/.
    store.write(_item("gone"), "archived body")
    (store.items_dir / "archived").mkdir(exist_ok=True)
    (store.items_dir / "mem-20260519-100000-gone.md").rename(
        store.items_dir / "archived" / "mem-20260519-100000-gone.md"
    )

    ids = [item.id for item, _ in store.iter_all()]
    assert "mem-20260519-100000-live" in ids
    assert "mem-20260519-100000-gone" not in ids


def test_archived_included_when_requested(tmp_path: Path) -> None:
    from agent_brain.memory.store.items_store import ItemsStore

    store = ItemsStore(items_dir=tmp_path / "items")
    store.write(_item("live"), "live body")
    (store.items_dir / "archived").mkdir(exist_ok=True)
    store.write(_item("kept"), "body")
    (store.items_dir / "mem-20260519-100000-kept.md").rename(
        store.items_dir / "archived" / "mem-20260519-100000-kept.md"
    )

    ids = [item.id for item, _ in store.iter_all(include_archived=True)]
    assert "mem-20260519-100000-kept" in ids
