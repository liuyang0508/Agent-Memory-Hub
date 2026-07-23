from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_brain.memory.governance import pending_lock_gc as lock_gc
from agent_brain.memory.store.pending import PendingQueue


def _lock_file(pending: Path, record_name: str) -> Path:
    locks = pending / ".amh-record-locks"
    locks.mkdir(parents=True, exist_ok=True)
    os.chmod(locks, 0o700)
    path = locks / lock_gc.pending_record_lock_name(record_name)
    path.write_bytes(b"")
    os.chmod(path, 0o600)
    return path


def test_pending_lock_report_counts_live_and_orphan_without_mutation(tmp_path):
    pending = tmp_path / "pending"
    live = _lock_file(pending, "live.jsonl")
    orphan = _lock_file(pending, "orphan.jsonl")

    report = lock_gc.collect_pending_record_locks(
        pending,
        live_record_names={"live.jsonl"},
        apply=False,
    )

    assert report.total == 2
    assert report.orphan == 1
    assert report.deleted == 0
    assert report.preserved == 2
    assert report.unsafe == 0
    assert live.exists()
    assert orphan.exists()


def test_pending_lock_gc_deletes_only_unlocked_orphan(tmp_path):
    pending = tmp_path / "pending"
    live = _lock_file(pending, "live.jsonl")
    orphan = _lock_file(pending, "orphan.jsonl")

    report = lock_gc.collect_pending_record_locks(
        pending,
        live_record_names={"live.jsonl"},
        apply=True,
    )

    assert report.total == 2
    assert report.orphan == 1
    assert report.deleted == 1
    assert report.preserved == 1
    assert live.exists()
    assert not orphan.exists()


def test_pending_lock_gc_preserves_held_orphan(tmp_path):
    if os.name == "nt":
        pytest.skip("fcntl lock semantics are Unix-only")
    import fcntl

    pending = tmp_path / "pending"
    orphan = _lock_file(pending, "held-orphan.jsonl")
    descriptor = os.open(orphan, os.O_RDWR)
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    try:
        report = lock_gc.collect_pending_record_locks(
            pending,
            live_record_names=set(),
            apply=True,
        )
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

    assert report.deleted == 0
    assert report.preserved == 1
    assert orphan.exists()


def test_pending_lock_gc_rejects_unsafe_symlink(tmp_path):
    pending = tmp_path / "pending"
    locks = pending / ".amh-record-locks"
    locks.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.write_bytes(b"")
    name = lock_gc.pending_record_lock_name("unsafe.jsonl")
    try:
        (locks / name).symlink_to(outside)
    except (NotImplementedError, OSError):
        pytest.skip("symlink unavailable")

    report = lock_gc.collect_pending_record_locks(
        pending,
        live_record_names=set(),
        apply=True,
    )

    assert report.unsafe == 1
    assert report.deleted == 0
    assert outside.exists()


def test_pending_lock_gc_is_bounded_and_reports_truncation(tmp_path):
    pending = tmp_path / "pending"
    for index in range(3):
        _lock_file(pending, f"orphan-{index}.jsonl")

    report = lock_gc.collect_pending_record_locks(
        pending,
        live_record_names=set(),
        apply=False,
        limit=2,
    )

    assert report.total == 2
    assert report.truncated is True
    assert report.reason == "PENDING_LOCK_GC_TRUNCATED"
    assert len(list((pending / ".amh-record-locks").iterdir())) == 3


def test_pending_lock_gc_fallback_is_report_only(tmp_path, monkeypatch):
    pending = tmp_path / "pending"
    orphan = _lock_file(pending, "fallback-orphan.jsonl")
    monkeypatch.setattr(lock_gc, "lifecycle_mutation_capability", lambda: False)

    report = lock_gc.collect_pending_record_locks(
        pending,
        live_record_names=set(),
        apply=True,
    )

    assert report.orphan == 1
    assert report.deleted == 0
    assert report.reason == "PENDING_LOCK_GC_UNAVAILABLE"
    assert orphan.exists()


def test_pending_lock_gc_blocks_inode_replacement_before_unlink(
    tmp_path,
    monkeypatch,
):
    from agent_brain.memory.store.durable_fs import SecureDirectory

    pending = tmp_path / "pending"
    orphan = _lock_file(pending, "inode-swap-orphan.jsonl")
    original_stat = SecureDirectory.stat
    target_name = orphan.name
    calls = 0

    def swap_on_second_stat(self, name: str):
        nonlocal calls
        if name == target_name:
            calls += 1
            if calls == 2:
                orphan.unlink()
                orphan.write_bytes(b"")
                orphan.chmod(0o600)
        return original_stat(self, name)

    monkeypatch.setattr(SecureDirectory, "stat", swap_on_second_stat)

    report = lock_gc.collect_pending_record_locks(
        pending,
        live_record_names=set(),
        apply=True,
    )

    assert calls == 2
    assert report.deleted == 0
    assert report.preserved == 1
    assert orphan.exists()


def test_pending_queue_collect_orphan_locks_is_preview_first(
    tmp_brain: Path,
) -> None:
    lock_dir = tmp_brain / "pending" / ".amh-record-locks"
    lock_dir.mkdir(parents=True)
    orphan = lock_dir / f"{'0' * 32}.lock"
    orphan.write_bytes(b"")
    orphan.chmod(0o600)
    queue = PendingQueue(brain=tmp_brain)

    preview = queue.collect_orphan_locks(apply=False)

    assert preview.orphan == 1
    assert preview.deleted == 0
    assert orphan.exists()
    assert not (tmp_brain / "runtime").exists()

    applied = queue.collect_orphan_locks(apply=True)

    assert applied.deleted == 1
    assert not orphan.exists()
    assert (tmp_brain / "runtime" / "locks" / "pending" / "queue.lock").exists()
