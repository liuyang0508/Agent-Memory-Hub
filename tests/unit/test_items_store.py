from datetime import datetime
import logging
import os
from pathlib import Path
import stat
import subprocess
import sys

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
