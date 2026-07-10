"""Tests for lightweight canonical MemoryItem filename discovery."""

from __future__ import annotations

from pathlib import Path


def test_known_memory_item_ids_scans_filenames_only_and_includes_archived(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_brain.memory.store.item_ids import known_memory_item_ids
    from agent_brain.memory.store.items_store import ItemsStore

    active_id = "mem-20260711-020304-active"
    archived_id = "mem-20260711-020305-archived"
    items_dir = tmp_path / "items"
    archived_dir = items_dir / "archived"
    archived_dir.mkdir(parents=True)
    (items_dir / f"{active_id}.md").write_text("malformed active body", encoding="utf-8")
    (archived_dir / f"{archived_id}.md").write_text(
        "malformed archived body", encoding="utf-8"
    )
    (items_dir / "not-a-memory-id.md").write_text("ignored", encoding="utf-8")
    (items_dir / "mem-20260711-020306-wrong.txt").write_text("ignored", encoding="utf-8")

    def fail_body_scan(*args, **kwargs):
        raise AssertionError("known ID discovery must not parse item bodies/frontmatter")

    monkeypatch.setattr(ItemsStore, "iter_all", fail_body_scan)

    assert known_memory_item_ids(items_dir) == frozenset({active_id, archived_id})
