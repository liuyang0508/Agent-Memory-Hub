"""Tests for lightweight canonical MemoryItem filename discovery."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from agent_brain.contracts.memory_item import MemoryItem, MemoryType


def _item(item_id: str, *, sensitivity: str = "internal") -> MemoryItem:
    return MemoryItem(
        id=item_id,
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title=f"title {item_id}",
        summary=f"summary {item_id}",
        sensitivity=sensitivity,
    )


def test_known_memory_item_ids_scans_bounded_frontmatter_and_includes_archived(
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
    store = ItemsStore(items_dir)
    store.write(_item(active_id), "active body")
    archived_path = store.write(_item(archived_id), "archived body")
    archived_path.replace(archived_dir / archived_path.name)
    (items_dir / "not-a-memory-id.md").write_text("ignored", encoding="utf-8")
    (items_dir / "mem-20260711-020306-wrong.txt").write_text("ignored", encoding="utf-8")

    def fail_body_scan(*args, **kwargs):
        raise AssertionError("known ID discovery must not parse item bodies/frontmatter")

    monkeypatch.setattr(ItemsStore, "iter_all", fail_body_scan)

    assert known_memory_item_ids(items_dir) == frozenset({active_id, archived_id})


def test_observable_item_ids_require_bounded_frontmatter_and_exclude_sensitive_items(
    tmp_path: Path,
) -> None:
    from agent_brain.memory.store.item_markdown import render_item_markdown
    from agent_brain.memory.store.item_ids import known_memory_item_ids
    from agent_brain.memory.store.items_store import ItemsStore

    items_dir = tmp_path / "items"
    store = ItemsStore(items_dir)
    internal = _item("mem-20260711-030401-observable-internal")
    archived = _item("mem-20260711-030402-observable-archived")
    private = _item("mem-20260711-030403-observable-private", sensitivity="private")
    secret = _item("mem-20260711-030404-observable-secret", sensitivity="secret")
    binary_body = _item("mem-20260711-030405-observable-binary-body")
    for item in (internal, archived, private, secret):
        store.write(item, "body must not be read")

    archive_dir = items_dir / "archived"
    archive_dir.mkdir(parents=True)
    (items_dir / f"{archived.id}.md").replace(archive_dir / f"{archived.id}.md")
    binary_path = items_dir / f"{binary_body.id}.md"
    binary_path.write_bytes(
        render_item_markdown(binary_body, "").encode("utf-8") + (b"\xff" * 1024 * 1024)
    )
    symlink_id = "mem-20260711-030406-observable-symlink"
    (items_dir / f"{symlink_id}.md").symlink_to(items_dir / f"{internal.id}.md")

    assert known_memory_item_ids(items_dir) == frozenset({
        internal.id,
        archived.id,
        binary_body.id,
    })


def test_observable_item_id_scan_fails_closed_when_global_entry_budget_is_exceeded(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import agent_brain.memory.store.item_ids as item_ids
    from agent_brain.memory.store.items_store import ItemsStore

    store = ItemsStore(tmp_path / "items")
    store.write(_item("mem-20260711-030407-budget-first"), "body")
    store.write(_item("mem-20260711-030408-budget-second"), "body")
    monkeypatch.setattr(item_ids, "MAX_OBSERVABILITY_STORE_ENTRIES", 1, raising=False)

    assert item_ids.known_memory_item_ids(tmp_path / "items") == frozenset()


def test_observable_item_id_scan_skips_oversized_frontmatter_and_fails_closed_on_total_bytes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import agent_brain.memory.store.item_ids as item_ids
    from agent_brain.memory.store.items_store import ItemsStore

    items_dir = tmp_path / "items"
    store = ItemsStore(items_dir)
    safe = _item("mem-20260711-030409-budget-safe")
    store.write(safe, "body")
    oversized_id = "mem-20260711-030410-budget-oversized"
    (items_dir / f"{oversized_id}.md").write_text(
        "---\n"
        f"id: {oversized_id}\n"
        "sensitivity: internal\n"
        f"padding: {'x' * 4096}\n"
        "---\nbody\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(item_ids, "MAX_OBSERVABILITY_FRONTMATTER_BYTES", 2048, raising=False)

    assert item_ids.known_memory_item_ids(items_dir) == frozenset({safe.id})

    monkeypatch.setattr(item_ids, "MAX_OBSERVABILITY_TOTAL_BYTES", 32, raising=False)
    assert item_ids.known_memory_item_ids(items_dir) == frozenset()


def test_observable_item_scan_skips_frontmatter_that_overflows_validation(
    tmp_path: Path,
) -> None:
    from agent_brain.memory.store.item_ids import known_memory_item_ids
    from agent_brain.memory.store.items_store import ItemsStore

    items_dir = tmp_path / "items"
    safe = _item("mem-20260711-030411-overflow-safe")
    ItemsStore(items_dir).write(safe, "body")
    overflow_id = "mem-20260711-030412-overflow-malformed"
    overflow_item = _item(overflow_id)
    overflow_payload = overflow_item.model_dump(mode="json", exclude_none=True)
    overflow_payload["confidence"] = int("9" * 4000)

    import yaml

    (items_dir / f"{overflow_id}.md").write_text(
        f"---\n{yaml.safe_dump(overflow_payload, sort_keys=False)}---\nbody\n",
        encoding="utf-8",
    )

    assert known_memory_item_ids(items_dir) == frozenset({safe.id})


def test_observable_item_scan_never_follows_directory_symlink_swapped_during_walk(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import agent_brain.memory.store.item_ids as item_ids
    from agent_brain.memory.store.items_store import ItemsStore

    items_dir = tmp_path / "items"
    archive_dir = items_dir / "archived"
    archive_dir.mkdir(parents=True)
    outside_dir = tmp_path / "outside"
    outside_store = ItemsStore(outside_dir)
    outside_item = _item("mem-20260711-030413-symlink-race-outside")
    outside_store.write(outside_item, "must remain outside the observable store")

    real_scandir = os.scandir
    real_open = os.open
    swapped = False

    def swap_archive_to_symlink() -> None:
        nonlocal swapped
        if swapped:
            return
        swapped = True
        archive_dir.rename(items_dir / "archived-before-swap")
        archive_dir.symlink_to(outside_dir, target_is_directory=True)

    def swapping_scandir(path="."):
        if not isinstance(path, int) and Path(path) == archive_dir:
            swap_archive_to_symlink()
        return real_scandir(path)

    def swapping_open(path, flags, mode=0o777, *, dir_fd=None):
        if path == archive_dir.name and dir_fd is not None:
            swap_archive_to_symlink()
        if dir_fd is None:
            return real_open(path, flags, mode)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(item_ids.os, "scandir", swapping_scandir)
    monkeypatch.setattr(item_ids.os, "open", swapping_open)

    assert item_ids.known_memory_item_ids(items_dir) == frozenset()


def test_observable_item_scan_fails_closed_when_root_scandir_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import agent_brain.memory.store.item_ids as item_ids

    items_dir = tmp_path / "items"
    items_dir.mkdir()
    real_scandir = os.scandir

    def failing_root_scandir(path="."):
        if isinstance(path, int):
            raise OSError("root scandir failed")
        return real_scandir(path)

    monkeypatch.setattr(item_ids.os, "scandir", failing_root_scandir)

    assert item_ids.known_memory_item_ids(items_dir) == frozenset()


def test_observable_item_scan_directory_descriptors_scale_with_depth_not_width(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import agent_brain.memory.store.item_ids as item_ids

    items_dir = tmp_path / "items"
    items_dir.mkdir()
    for index in range(64):
        (items_dir / f"sibling-{index:03d}").mkdir()

    real_open = os.open
    real_close = os.close
    open_directory_descriptors: set[int] = set()
    peak_open_directory_descriptors = 0

    def tracking_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal peak_open_directory_descriptors
        if dir_fd is None:
            descriptor = real_open(path, flags, mode)
        else:
            descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if flags & item_ids.os.O_DIRECTORY:
            open_directory_descriptors.add(descriptor)
            peak_open_directory_descriptors = max(
                peak_open_directory_descriptors,
                len(open_directory_descriptors),
            )
        return descriptor

    def tracking_close(descriptor):
        open_directory_descriptors.discard(descriptor)
        return real_close(descriptor)

    monkeypatch.setattr(item_ids.os, "open", tracking_open)
    monkeypatch.setattr(item_ids.os, "close", tracking_close)

    assert item_ids.known_memory_item_ids(items_dir) == frozenset()
    assert peak_open_directory_descriptors <= 4
    assert open_directory_descriptors == set()
