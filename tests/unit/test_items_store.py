from datetime import datetime
import logging
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from agent_brain.contracts.memory_item import MemoryItem, MemoryType


def test_load_sample_fixture(fixtures_dir: Path) -> None:
    from agent_brain.memory.store.items_store import ItemsStore

    store = ItemsStore(items_dir=fixtures_dir / "sample_items")
    items = list(store.iter_all())
    # v1.1 B6: fixture set expanded to 10 items covering 6 types + schema
    # edge cases (CJK id, `+` in id, refs.tags forward-compat, YAML quirks).
    # See tests/conformance/test_v05_compat.py for the exhaustive contract.
    assert len(items) >= 10
    by_id = {item.id: (item, body) for item, body in items}
    item, body = by_id["mem-20260101-120000-sample-fact"]
    assert item.type == "fact"
    assert "**事实**" in body


def test_write_then_read(tmp_brain_dir: Path) -> None:
    from agent_brain.memory.store.items_store import ItemsStore

    store = ItemsStore(items_dir=tmp_brain_dir / "items")
    item = MemoryItem(
        id="mem-20260519-103000-roundtrip",
        type=MemoryType.fact,
        created_at=datetime.fromisoformat("2026-05-19T10:30:00+08:00"),
        title="round trip",
        summary="write then read back",
    )
    body = "**事实**: round-trip 测试"
    path = store.write(item, body)
    assert path.exists()
    loaded_items = list(store.iter_all())
    assert len(loaded_items) == 1
    loaded_item, loaded_body = loaded_items[0]
    assert loaded_item.id == item.id
    assert loaded_body.strip() == body


def test_item_markdown_codec_roundtrips_historical_frontmatter_quirks() -> None:
    from agent_brain.memory.store.item_markdown import parse_item_markdown, render_item_markdown

    text = (
        "\ufeff---\r\n"
        "id: mem-20260519-103000-codec\r\n"
        "type: fact\r\n"
        "created_at: 2026-05-19T10:30:00+08:00\r\n"
        "title: codec\r\n"
        "summary: parse v0.5 quirks\r\n"
        "tags:[]\r\n"
        "---\r\n"
        "\r\n"
        "body text\r\n"
    )

    item, body = parse_item_markdown(text)
    rendered = render_item_markdown(item, body)
    reloaded_item, reloaded_body = parse_item_markdown(rendered)

    assert reloaded_item.id == item.id
    assert reloaded_body.strip() == "body text"


def test_item_markdown_render_omits_unproven_null_fields() -> None:
    from agent_brain.memory.store.item_markdown import render_item_markdown

    item = MemoryItem(
        id="mem-20260519-103001-no-null-noise",
        type=MemoryType.fact,
        created_at=datetime.fromisoformat("2026-05-19T10:30:00+08:00"),
        title="no null noise",
        summary="omit optional null frontmatter fields",
    )

    rendered = render_item_markdown(item, "body text")

    assert "transcript_id: null" not in rendered
    assert "span_hash: null" not in rendered
    assert "extractor: null" not in rendered
    assert "observed_at: null" not in rendered
    assert "last_accessed: null" not in rendered


def test_iter_all_records_bad_items_without_warning_noise(tmp_brain_dir: Path, caplog) -> None:
    from agent_brain.memory.store.items_store import ItemsStore

    store = ItemsStore(items_dir=tmp_brain_dir / "items")
    (store.items_dir / "bad.md").write_text("missing frontmatter\n", encoding="utf-8")

    caplog.set_level(logging.WARNING, logger="agent_brain.memory.store.items_store")

    assert list(store.iter_all()) == []
    assert store.last_scan.skipped_count == 1
    assert store.last_scan.skipped[0].path.name == "bad.md"
    assert not [
        record for record in caplog.records
        if record.name == "agent_brain.memory.store.items_store" and record.levelno >= logging.WARNING
    ]


def test_iter_all_skips_fifo_without_blocking_or_writing_it(tmp_path: Path) -> None:
    from agent_brain.memory.store.items_store import ItemsStore

    store = ItemsStore(tmp_path / "items")
    fifo = store.items_dir / "mem-20260720-120000-fifo.md"
    os.mkfifo(fifo)
    script = (
        "from pathlib import Path; "
        "from agent_brain.memory.store.items_store import ItemsStore; "
        f"store=ItemsStore(Path({str(store.items_dir)!r})); "
        "print(len(list(store.iter_all())), store.last_scan.skipped_count)"
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        text=True,
        capture_output=True,
        timeout=2,
        check=True,
    )

    assert completed.stdout.strip() == "0 1"
    assert stat.S_ISFIFO(fifo.stat().st_mode)


def test_items_store_canonicalizes_trusted_symlink_ancestor_once(
    tmp_path: Path,
) -> None:
    from agent_brain.memory.store.items_store import ItemsStore

    real_root = tmp_path / "real-root"
    real_root.mkdir()
    alias = tmp_path / "trusted-alias"
    alias.symlink_to(real_root, target_is_directory=True)
    store = ItemsStore(alias / "items")
    item = MemoryItem(
        id="mem-20260720-120001-symlink-ancestor",
        type=MemoryType.fact,
        created_at=datetime.fromisoformat("2026-07-20T12:00:01+00:00"),
        title="canonical ancestor",
        summary="trusted configured ancestor is resolved once",
    )
    store.write(item, "body")

    alias.unlink()

    assert store.items_dir == (real_root / "items").resolve()
    assert [loaded.id for loaded, _body in store.iter_all()] == [item.id]


def test_iter_all_unsupported_descriptor_fallback_reads_only_regular_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_brain.memory.store import items_store as items_store_module
    from agent_brain.memory.store.items_store import ItemsStore

    store = ItemsStore(tmp_path / "items")
    item = MemoryItem(
        id="mem-20260720-120002-fallback-regular",
        type=MemoryType.fact,
        created_at=datetime.fromisoformat("2026-07-20T12:00:02+00:00"),
        title="fallback regular",
        summary="ordinary item remains readable without descriptor traversal",
    )
    store.write(item, "body")
    outside = tmp_path / "outside.md"
    outside.write_bytes((store.items_dir / f"{item.id}.md").read_bytes())
    symlink_id = "mem-20260720-120003-fallback-symlink"
    symlink = store.items_dir / f"{symlink_id}.md"
    symlink.symlink_to(outside)
    fifo_id = "mem-20260720-120004-fallback-fifo"
    fifo = store.items_dir / f"{fifo_id}.md"
    os.mkfifo(fifo)
    monkeypatch.setattr(
        items_store_module,
        "secure_dir_fd_io_supported",
        lambda: False,
    )

    loaded = list(store.iter_all())

    assert [loaded_item.id for loaded_item, _body in loaded] == [item.id]
    assert {row.path.name for row in store.last_scan.skipped} == {
        symlink.name,
        fifo.name,
    }
    assert store.get_nofollow(item.id)[0].id == item.id
    with pytest.raises(OSError):
        store.get_nofollow(symlink_id)
    with pytest.raises(OSError):
        store.get_nofollow(fifo_id)
    assert stat.S_ISLNK(symlink.lstat().st_mode)
    assert stat.S_ISFIFO(fifo.lstat().st_mode)


def test_unsupported_descriptor_fallback_enforces_bounded_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_brain.memory.store import items_store as items_store_module
    from agent_brain.memory.store.items_store import ItemsStore

    store = ItemsStore(tmp_path / "items")
    item = MemoryItem(
        id="mem-20260720-120005-fallback-bounded",
        type=MemoryType.fact,
        created_at=datetime.fromisoformat("2026-07-20T12:00:05+00:00"),
        title="fallback bounded",
        summary="fallback rejects oversized ordinary files",
    )
    store.write(item, "body")
    monkeypatch.setattr(
        items_store_module,
        "secure_dir_fd_io_supported",
        lambda: False,
    )
    monkeypatch.setattr(items_store_module, "_MAX_FALLBACK_ITEM_BYTES", 64)

    assert list(store.iter_all()) == []
    assert store.last_scan.skipped_count == 1
    assert "exceeds size limit" in store.last_scan.skipped[0].reason
    with pytest.raises(OSError, match="exceeds size limit"):
        store.get_nofollow(item.id)


def test_atomic_updates_and_rollback_preserve_existing_posix_mode(
    tmp_brain_dir: Path,
) -> None:
    from agent_brain.memory.store.items_store import ItemsStore

    store = ItemsStore(items_dir=tmp_brain_dir / "items")
    item = MemoryItem(
        id="mem-20260719-120000-mode-preserved",
        type=MemoryType.fact,
        created_at=datetime.fromisoformat("2026-07-19T12:00:00+00:00"),
        title="mode",
        summary="before",
    )
    path = store.write(item, "body")
    path.chmod(0o640)
    original = path.read_bytes()

    store.update_frontmatter(item.id, summary="after")
    assert stat.S_IMODE(path.stat().st_mode) == 0o640

    store.restore_raw(item.id, original)
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert path.read_bytes() == original


@pytest.mark.parametrize("kind", ["symlink", "fifo"])
def test_atomic_write_rejects_symlink_and_nonregular_targets(
    tmp_path: Path, kind: str
) -> None:
    from agent_brain.memory.store.items_store import _atomic_write_bytes

    target = tmp_path / "target.md"
    sentinel = tmp_path / "outside.md"
    sentinel.write_bytes(b"outside")
    if kind == "symlink":
        target.symlink_to(sentinel)
    else:
        os.mkfifo(target)

    with pytest.raises(OSError, match="UNSAFE_ATOMIC_WRITE_TARGET"):
        _atomic_write_bytes(target, b"replacement")

    assert sentinel.read_bytes() == b"outside"
    if kind == "symlink":
        assert stat.S_ISLNK(target.lstat().st_mode)
    else:
        assert stat.S_ISFIFO(target.lstat().st_mode)


def test_atomic_replace_fsyncs_parent_directory_after_replace(
    tmp_path: Path, monkeypatch
) -> None:
    from agent_brain.memory.store.items_store import _atomic_write_bytes

    target = tmp_path / "target.md"
    target.write_bytes(b"before")
    events: list[str] = []
    real_replace = os.replace
    real_fsync = os.fsync

    def tracking_replace(source, destination, **kwargs):
        real_replace(source, destination, **kwargs)
        events.append("replace")

    def tracking_fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            events.append("directory-fsync")
        return real_fsync(fd)

    monkeypatch.setattr(os, "replace", tracking_replace)
    monkeypatch.setattr(os, "fsync", tracking_fsync)

    _atomic_write_bytes(target, b"after")

    assert target.read_bytes() == b"after"
    assert events[-2:] == ["replace", "directory-fsync"]


@pytest.mark.parametrize("unsupported", ["missing-nofollow", "windows"])
def test_atomic_directory_preflight_failure_has_zero_side_effects(
    tmp_brain_dir: Path, monkeypatch, unsupported: str
) -> None:
    from agent_brain.memory.store.items_store import ItemsStore, _atomic_write_bytes

    store = ItemsStore(tmp_brain_dir / "items")
    item = MemoryItem(
        id="mem-20260719-130000-preflight",
        type=MemoryType.fact,
        created_at=datetime.fromisoformat("2026-07-19T13:00:00+00:00"),
        title="preflight",
        summary="before",
    )
    path = store.write(item, "body")
    path.chmod(0o640)
    before = (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
    directory_before = sorted(entry.name for entry in store.items_dir.iterdir())
    if unsupported == "missing-nofollow":
        monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    else:
        monkeypatch.setattr(os, "name", "nt")

    with pytest.raises(OSError, match="DIRECTORY_FSYNC_UNSUPPORTED"):
        _atomic_write_bytes(path, b"after", require_durable=True)

    assert (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns) == before
    assert sorted(entry.name for entry in store.items_dir.iterdir()) == directory_before


def test_nondurable_platform_keeps_ordinary_memory_update_available(
    tmp_brain_dir: Path, monkeypatch
) -> None:
    from agent_brain.memory.store import items_store as items_store_module
    from agent_brain.memory.store.items_store import ItemsStore

    store = ItemsStore(tmp_brain_dir / "items")
    item = MemoryItem(
        id="mem-20260719-140000-windows-compatible",
        type=MemoryType.fact,
        created_at=datetime.fromisoformat("2026-07-19T14:00:00+00:00"),
        title="ordinary",
        summary="before",
    )
    store.write(item, "body")
    monkeypatch.setattr(
        items_store_module, "lifecycle_mutation_capability", lambda: False
    )

    updated = store.update_frontmatter(item.id, summary="after")

    assert updated.summary == "after"


def _fallback_create_item(item_id: str) -> MemoryItem:
    return MemoryItem(
        id=item_id,
        type=MemoryType.fact,
        created_at=datetime.fromisoformat("2026-07-20T12:30:00+00:00"),
        title="fallback atomic create",
        summary="fallback durability boundary",
    )


def test_windows_fallback_dir_fsync_unavailable_does_not_report_committed_write_failed(
    tmp_brain_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from agent_brain.memory.store import items_store as items_store_module
    from agent_brain.memory.store.items_store import ItemsStore

    store = ItemsStore(tmp_brain_dir / "items")
    item = _fallback_create_item("mem-20260720-123000-windows-fallback")
    real_open = os.open

    def deny_directory_open(path, flags, *args, **kwargs):
        if os.fspath(path) == os.fspath(store.items_dir):
            raise PermissionError("simulated Windows directory handle denial")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(
        items_store_module, "lifecycle_mutation_capability", lambda: False
    )
    monkeypatch.setattr(items_store_module, "_is_windows", lambda: True, raising=False)
    monkeypatch.setattr(items_store_module.os, "open", deny_directory_open)
    caplog.set_level(logging.WARNING, logger="agent_brain.memory.store.items_store")

    target = store.write(item, "written once")

    assert target.read_text(encoding="utf-8").endswith("written once\n")
    assert [path.name for path in store.items_dir.glob("*.md")] == [target.name]
    with pytest.raises(FileExistsError):
        store.write(item, "must not overwrite")
    assert target.read_text(encoding="utf-8").endswith("written once\n")
    assert "ITEM_DIRECTORY_FSYNC_UNAVAILABLE" in caplog.text


def test_posix_fallback_dir_fsync_failure_remains_explicit_after_publish(
    tmp_brain_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_brain.memory.store import items_store as items_store_module
    from agent_brain.memory.store.items_store import ItemsStore

    store = ItemsStore(tmp_brain_dir / "items")
    item = _fallback_create_item("mem-20260720-123001-posix-fallback")
    real_open = os.open

    def deny_directory_open(path, flags, *args, **kwargs):
        if os.fspath(path) == os.fspath(store.items_dir):
            raise PermissionError("simulated POSIX directory fsync precondition failure")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(
        items_store_module, "lifecycle_mutation_capability", lambda: False
    )
    monkeypatch.setattr(items_store_module, "_is_windows", lambda: False, raising=False)
    monkeypatch.setattr(items_store_module.os, "open", deny_directory_open)

    with pytest.raises(PermissionError, match="POSIX directory fsync"):
        store.write(item, "published but durability unconfirmed")

    target = store.items_dir / f"{item.id}.md"
    assert target.is_file()
    assert target.read_text(encoding="utf-8").endswith(
        "published but durability unconfirmed\n"
    )


def test_fallback_temp_cleanup_failure_after_publish_is_diagnostic_only(
    tmp_brain_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from agent_brain.memory.store import items_store as items_store_module
    from agent_brain.memory.store.items_store import ItemsStore

    store = ItemsStore(tmp_brain_dir / "items")
    item = _fallback_create_item("mem-20260720-123002-cleanup-fallback")
    real_unlink = Path.unlink

    def fail_temp_cleanup(path: Path, *args, **kwargs):
        if path.parent == store.items_dir and path.name.startswith(f".{item.id}."):
            raise PermissionError("simulated committed temp cleanup failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(
        items_store_module, "lifecycle_mutation_capability", lambda: False
    )
    monkeypatch.setattr(items_store_module.Path, "unlink", fail_temp_cleanup)
    caplog.set_level(logging.WARNING, logger="agent_brain.memory.store.items_store")

    target = store.write(item, "committed despite cleanup failure")

    assert target.is_file()
    assert target.read_text(encoding="utf-8").endswith(
        "committed despite cleanup failure\n"
    )
    assert "ITEM_TEMP_CLEANUP_FAILED" in caplog.text


def test_item_locks_live_under_runtime_not_observable_items(tmp_brain_dir: Path) -> None:
    from agent_brain.memory.store.items_store import ItemsStore

    store = ItemsStore(tmp_brain_dir / "items")
    item = _fallback_create_item("mem-20260720-123003-runtime-lock")

    store.write(item, "body")

    assert not (store.items_dir / ".amh-item-locks").exists()
    lock = tmp_brain_dir / "runtime" / "locks" / "items" / f"{item.id}.lock"
    assert lock.is_file()
    assert stat.S_IMODE(lock.stat().st_mode) == 0o600


def test_catalog_lock_is_reentrant_across_store_instances(tmp_brain_dir: Path) -> None:
    from agent_brain.memory.store.items_store import ItemsStore

    first = ItemsStore(tmp_brain_dir / "items")
    second = ItemsStore(tmp_brain_dir / "items")
    item = _fallback_create_item("mem-20260720-123004-catalog-reentrant")

    with first.locked_catalog():
        second.write(item, "nested write")

    assert first.get(item.id)[1].strip() == "nested write"
    lock = tmp_brain_dir / "runtime" / "locks" / "catalog" / "write.lock"
    assert lock.is_file()
    assert stat.S_IMODE(lock.stat().st_mode) == 0o600


def test_catalog_lock_serializes_all_store_writes(tmp_brain_dir: Path) -> None:
    from agent_brain.memory.store.items_store import ItemsStore

    first = ItemsStore(tmp_brain_dir / "items")
    second = ItemsStore(tmp_brain_dir / "items")
    item = _fallback_create_item("mem-20260720-123005-catalog-serialized")
    started = threading.Event()
    finished = threading.Event()

    def write_second() -> None:
        started.set()
        second.write(item, "serialized")
        finished.set()

    with first.locked_catalog():
        thread = threading.Thread(target=write_second, daemon=True)
        thread.start()
        assert started.wait(timeout=5)
        assert not finished.wait(timeout=0.2)
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert finished.is_set()


def test_windows_catalog_lock_uses_one_byte_msvcrt_contract(
    tmp_brain_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_brain.memory.store import items_store as items_store_module
    from agent_brain.memory.store.items_store import ItemsStore

    store = ItemsStore(tmp_brain_dir / "items")
    events: list[tuple[int, int]] = []
    fake_msvcrt = SimpleNamespace(
        LK_LOCK=10,
        LK_UNLCK=11,
        locking=lambda _fd, operation, count: events.append((operation, count)),
    )
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    monkeypatch.setattr(items_store_module, "lifecycle_mutation_capability", lambda: False)
    monkeypatch.setattr(items_store_module.os, "name", "nt")

    with store.locked_catalog():
        lock = tmp_brain_dir / "runtime" / "locks" / "catalog" / "write.lock"
        assert lock.stat().st_size == 1

    assert events == [(10, 1), (11, 1)]
