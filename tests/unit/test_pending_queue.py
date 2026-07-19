from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from agent_brain.contracts.memory_item import MemoryItem, Source
from agent_brain.memory.store import pending as pending_module
from agent_brain.memory.store.item_markdown import render_item_markdown
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.store.pending import (
    PendingEnqueueError,
    PendingQueue,
    enqueue_write_record,
)


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


class _StatProxy:
    def __init__(self, original: os.stat_result, **overrides: object) -> None:
        self._original = original
        self._overrides = overrides

    def __getattr__(self, name: str) -> object:
        if name in self._overrides:
            return self._overrides[name]
        return getattr(self._original, name)


class _DirEntryProxy:
    def __init__(
        self,
        original: os.DirEntry[str],
        *,
        zero_identity: bool = False,
    ) -> None:
        self._original = original
        self.name = original.name
        self._zero_identity = zero_identity

    def stat(self, *, follow_symlinks: bool = True) -> os.stat_result:
        opened = self._original.stat(follow_symlinks=follow_symlinks)
        if self._zero_identity:
            return _StatProxy(opened, st_dev=0, st_ino=0)  # type: ignore[return-value]
        return opened

    def is_symlink(self) -> bool:
        return self._original.is_symlink()

    def is_file(self, *, follow_symlinks: bool = True) -> bool:
        return self._original.is_file(follow_symlinks=follow_symlinks)


class _ScandirProxy:
    def __init__(
        self,
        original: os.ScandirIterator[str],
        *,
        zero_identity: bool = False,
    ) -> None:
        self._original = original
        self._zero_identity = zero_identity

    def __iter__(self) -> _ScandirProxy:
        return self

    def __next__(self) -> _DirEntryProxy:
        entry = next(self._original)
        return _DirEntryProxy(
            entry,
            zero_identity=self._zero_identity,
        )

    def __enter__(self) -> _ScandirProxy:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self._original.close()


def _v2_record(
    *,
    record_id: str = "pending-test-fact-0001",
    type_: str = "fact",
    original_created_at: str = "2026-07-01T10:00:00+00:00",
    project: str | None = "amh",
    tenant_id: str | None = "tenant-a",
) -> dict[str, object]:
    item: dict[str, object] = {
        "type": type_,
        "title": "queued fact",
        "summary": "queued fact summary",
        "body": "queued fact body",
        "tags": ["pending"],
        "sensitivity": "internal",
        "confidence": 0.7,
    }
    if project is not None:
        item["project"] = project
    if tenant_id is not None:
        item["tenant_id"] = tenant_id
    return {
        "v": 2,
        "op": "write",
        "origin": "hook",
        "record_id": record_id,
        "enqueued_at": "2026-07-01T11:00:00+00:00",
        "original_created_at": original_created_at,
        "item": item,
    }


def _legacy_feedback_record() -> dict[str, object]:
    return {
        "v": 1,
        "op": "write",
        "origin": "hook",
        "ts": "2026-07-01T11:00:00+00:00",
        "item": {
            "type": "feedback",
            "title": "legacy feedback",
            "summary": "legacy feedback summary",
            "body": "legacy feedback body",
            "tags": ["pending"],
            "sensitivity": "internal",
        },
    }


def _payload_sha256(record: dict[str, object]) -> str:
    payload = json.dumps(record["item"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stable_item_id(record: dict[str, object]) -> str:
    item = record["item"]
    assert isinstance(item, dict)
    created_at = datetime.fromisoformat(str(record["original_created_at"])).astimezone(timezone.utc)
    title = str(item["title"])
    slug = re.sub(r"[/\\]+", "-", "-".join(title.lower().split()))[:30].strip("-")
    stable = hashlib.sha256(str(record["record_id"]).encode("utf-8")).hexdigest()[:24]
    return f"mem-{created_at:%Y%m%d-%H%M%S}-{slug or 'pending'}-{stable}"


def _freeze_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent_brain.memory.store.pending._utc_now", lambda: NOW, raising=False)


def _write_existing_item(
    tmp_brain: Path,
    record: dict[str, object],
    *,
    item_id: str,
    span_hash: str | None,
    project: str | None = "amh",
    tenant_id: str | None = "tenant-a",
    corrupt_body: bool = False,
) -> None:
    queued = record["item"]
    assert isinstance(queued, dict)
    item = MemoryItem(
        id=item_id,
        type=str(queued["type"]),
        created_at=datetime.fromisoformat(str(record["original_created_at"])),
        title=str(queued["title"]),
        summary=str(queued["summary"]),
        tags=list(queued.get("tags") or []),
        sensitivity=str(queued.get("sensitivity") or "internal"),
        project=project,
        tenant_id=tenant_id,
        source=Source(kind="pending-replay", span_hash=span_hash),
    )
    path = ItemsStore(tmp_brain / "items").write(item, "existing body must not be read")
    if corrupt_body:
        frontmatter = render_item_markdown(item, "").encode("utf-8")
        path.write_bytes(frontmatter + b"\xff\xfe\xfd")


def test_enqueue_then_replay_writes_item(tmp_brain: Path) -> None:
    rec = {
        "v": 1,
        "op": "write",
        "origin": "test",
        "item": {
            "type": "fact",
            "title": "queued fact",
            "summary": "s",
            "body": "b",
            "tags": [],
            "sensitivity": "internal",
            "confidence": 0.7,
            "allow_unsafe": True,
        },
    }
    path = enqueue_write_record(rec)
    assert path.exists()
    q = PendingQueue()
    stats = q.replay()
    assert stats.written == 1
    assert not path.exists()
    assert q.depth() == 0


def test_replay_is_idempotent_on_empty(tmp_brain: Path) -> None:
    assert PendingQueue().replay().written == 0


def test_default_enqueue_writes_v2_envelope(tmp_brain: Path) -> None:
    path = enqueue_write_record(
        {
            "op": "write",
            "origin": "hook",
            "item": {
                "type": "fact",
                "title": "queued",
                "summary": "summary",
                "body": "body",
            },
        }
    )

    record = json.loads(path.read_text(encoding="utf-8"))

    assert record["v"] == 2
    assert record["op"] == "write"
    assert record["origin"] == "hook"
    assert record["record_id"]
    assert datetime.fromisoformat(record["enqueued_at"]).tzinfo is not None
    assert record["original_created_at"] == record["enqueued_at"]
    assert record["payload_sha256"] == _payload_sha256(record)


def test_enqueue_is_durable_and_private_under_umask_zero(tmp_brain: Path) -> None:
    previous = os.umask(0)
    try:
        path = enqueue_write_record(_v2_record())
    finally:
        os.umask(previous)

    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(path.parent.glob("*.tmp"))


def test_concurrent_same_enqueue_is_idempotent_without_overwrite(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    fixed = UUID("11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(pending_module.uuid, "uuid4", lambda: fixed)
    paths: list[Path] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def enqueue() -> None:
        try:
            barrier.wait()
            paths.append(enqueue_write_record(_v2_record(record_id=str(fixed))))
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=enqueue) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert errors == []
    assert len(paths) == 8
    assert len(set(paths)) == 1
    assert len(list((tmp_brain / "pending").glob("*.jsonl"))) == 1


def test_fixed_filename_collision_with_different_bytes_fails_closed(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    fixed = UUID("22222222-2222-4222-8222-222222222222")
    monkeypatch.setattr(pending_module.uuid, "uuid4", lambda: fixed)
    enqueue_write_record(_v2_record(record_id=str(fixed)))
    changed = _v2_record(record_id=str(fixed))
    changed_item = changed["item"]
    assert isinstance(changed_item, dict)
    changed_item["body"] = "different body"

    with pytest.raises(PendingEnqueueError, match="PENDING_RECORD_FILENAME_CONFLICT"):
        enqueue_write_record(changed)

    files = list((tmp_brain / "pending").glob("*.jsonl"))
    assert len(files) == 1
    assert "different body" not in files[0].read_text(encoding="utf-8")


def test_enqueue_publish_failure_cleans_temp_and_leaves_no_record(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        pending_module.os,
        "link",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("publish failed")),
    )

    with pytest.raises(OSError, match="publish failed"):
        enqueue_write_record(_v2_record())

    pending = tmp_brain / "pending"
    assert list(pending.glob("*.tmp")) == []
    assert list(pending.glob("*.jsonl")) == []


def test_enqueue_write_all_handles_partial_os_writes(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_write = os.write

    def partial_write(descriptor: int, data: object) -> int:
        view = memoryview(data)  # type: ignore[arg-type]
        return original_write(descriptor, view[:7])

    monkeypatch.setattr(pending_module.os, "write", partial_write)

    path = enqueue_write_record(_v2_record())

    assert json.loads(path.read_text(encoding="utf-8"))["v"] == 2


def test_enqueue_fsync_failure_cleans_unpublished_temp(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_fsync = os.fsync

    def fail_regular_file_fsync(descriptor: int) -> None:
        if stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("simulated file fsync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(pending_module.os, "fsync", fail_regular_file_fsync)

    with pytest.raises(OSError, match="simulated file fsync failure"):
        enqueue_write_record(_v2_record())

    pending = tmp_brain / "pending"
    assert list(pending.glob("*.tmp")) == []
    assert list(pending.glob("*.jsonl")) == []


def test_first_brain_creation_fsyncs_parent_before_publishing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    brain = tmp_path / "new" / "deep" / "brain"
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    original_fsync = os.fsync
    original_mkdir = os.mkdir
    directory_fsyncs = 0
    creation_events: list[str] = []

    def track_mkdir(path: object, *args: object, **kwargs: object) -> None:
        original_mkdir(path, *args, **kwargs)  # type: ignore[arg-type]
        creation_events.append("mkdir")

    def fail_first_directory_fsync(descriptor: int) -> None:
        nonlocal directory_fsyncs
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_fsyncs += 1
            creation_events.append("fsync")
            if directory_fsyncs == 1:
                raise OSError("simulated parent fsync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(pending_module.os, "mkdir", track_mkdir)
    monkeypatch.setattr(pending_module.os, "fsync", fail_first_directory_fsync)

    with pytest.raises(OSError, match="simulated parent fsync failure"):
        enqueue_write_record(_v2_record())

    assert directory_fsyncs == 1
    assert creation_events[:2] == ["mkdir", "fsync"]
    assert list(brain.glob("pending/*.jsonl")) == []


def test_committed_record_survives_temp_cleanup_failure(
    tmp_brain: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    original_unlink = os.unlink

    def fail_temp_cleanup(path: object, *args: object, **kwargs: object) -> None:
        if str(path).startswith(".amh-pending-"):
            raise OSError("simulated cleanup failure")
        original_unlink(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pending_module.os, "unlink", fail_temp_cleanup)

    path = enqueue_write_record(_v2_record())

    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8"))["v"] == 2
    assert list(path.parent.glob(".amh-pending-*.tmp"))
    assert "PENDING_TEMP_CLEANUP_FAILED" in caplog.text


def test_fallback_capability_enqueue_and_preview_are_functional(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    monkeypatch.setattr(pending_module, "secure_dir_fd_io_supported", lambda: False)

    path = enqueue_write_record(_v2_record())
    preview = PendingQueue().preview(limit=1)

    assert path.is_file()
    assert preview.scan_unavailable is False
    assert preview.records[0].classification == "ready"


def test_fallback_existing_item_scan_remains_trusted(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record()
    path = enqueue_write_record(record)
    _write_existing_item(
        tmp_brain,
        record,
        item_id=_stable_item_id(record),
        span_hash=_payload_sha256(record),
    )
    monkeypatch.setattr(pending_module, "secure_dir_fd_io_supported", lambda: False)

    preview = PendingQueue().preview(limit=1)

    assert path.is_file()
    assert preview.records[0].classification == "already_written"


def test_fallback_nested_archived_item_ignores_zero_direntry_identity(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record()
    enqueue_write_record(record)
    item_id = _stable_item_id(record)
    _write_existing_item(
        tmp_brain,
        record,
        item_id=item_id,
        span_hash=_payload_sha256(record),
    )
    nested = tmp_brain / "items" / "archived" / "nested"
    nested.mkdir(parents=True)
    (tmp_brain / "items" / f"{item_id}.md").rename(nested / f"{item_id}.md")
    original_scandir = os.scandir
    items_root = tmp_brain / "items"

    def zero_identity_scandir(path: object) -> object:
        opened = original_scandir(path)  # type: ignore[arg-type]
        candidate = Path(os.fspath(path))
        return _ScandirProxy(
            opened,  # type: ignore[arg-type]
            zero_identity=items_root == candidate or items_root in candidate.parents,
        )

    monkeypatch.setattr(pending_module, "secure_dir_fd_io_supported", lambda: False)
    monkeypatch.setattr(pending_module.os, "scandir", zero_identity_scandir)

    preview = PendingQueue().preview(limit=1)

    assert preview.records[0].classification == "already_written"


def test_zero_explicit_file_identities_never_match_even_with_identical_metadata(
    tmp_path: Path,
) -> None:
    path = tmp_path / "identity"
    path.write_text("same", encoding="utf-8")
    opened = os.lstat(path)
    first = _StatProxy(opened, st_dev=0, st_ino=0)
    second = _StatProxy(opened, st_dev=0, st_ino=0)

    assert pending_module._same_file_identity(first, second) is False


def test_zero_explicit_nested_directory_identity_blocks_fallback_scan(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    enqueue_write_record(_v2_record())
    nested = tmp_brain / "items" / "nested"
    nested.mkdir()
    original_lstat = os.lstat

    def zero_lstat(path: object, *args: object, **kwargs: object) -> os.stat_result:
        opened = original_lstat(path, *args, **kwargs)  # type: ignore[arg-type]
        if Path(os.fspath(path)) == nested:
            return _StatProxy(opened, st_dev=0, st_ino=0)  # type: ignore[return-value]
        return opened

    monkeypatch.setattr(pending_module, "secure_dir_fd_io_supported", lambda: False)
    monkeypatch.setattr(pending_module.os, "lstat", zero_lstat)

    preview = PendingQueue().preview(limit=1)

    assert preview.records[0].classification == "audit_blocked"
    assert preview.records[0].reason == "EXISTING_ITEM_SCAN_UNAVAILABLE"


def test_fallback_rejects_windows_reparse_nested_items_directory(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    enqueue_write_record(_v2_record())
    nested = tmp_brain / "items" / "junction"
    nested.mkdir()
    original_lstat = os.lstat
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

    def reparse_lstat(path: object, *args: object, **kwargs: object) -> os.stat_result:
        opened = original_lstat(path, *args, **kwargs)  # type: ignore[arg-type]
        if Path(os.fspath(path)) == nested:
            return _StatProxy(  # type: ignore[return-value]
                opened, st_file_attributes=reparse_flag
            )
        return opened

    monkeypatch.setattr(pending_module, "secure_dir_fd_io_supported", lambda: False)
    monkeypatch.setattr(pending_module.os, "lstat", reparse_lstat)

    preview = PendingQueue().preview(limit=1)

    assert preview.records[0].classification == "audit_blocked"
    assert preview.records[0].reason == "EXISTING_ITEM_SCAN_UNAVAILABLE"


def test_fallback_rejects_pending_directory_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    brain = tmp_path / "brain"
    outside = tmp_path / "outside"
    brain.mkdir()
    outside.mkdir()
    (brain / "pending").symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    monkeypatch.setattr(pending_module, "secure_dir_fd_io_supported", lambda: False)

    with pytest.raises(PendingEnqueueError):
        enqueue_write_record(_v2_record())

    preview = PendingQueue().preview(limit=1)
    assert preview.scan_unavailable is True
    assert list(outside.iterdir()) == []


def test_fallback_rejects_windows_reparse_pending_directory(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pending = tmp_brain / "pending"
    pending.mkdir()
    original_lstat = os.lstat
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

    def reparse_lstat(path: object, *args: object, **kwargs: object) -> os.stat_result:
        opened = original_lstat(path, *args, **kwargs)  # type: ignore[arg-type]
        if Path(os.fspath(path)) == pending:
            return _StatProxy(  # type: ignore[return-value]
                opened, st_file_attributes=reparse_flag
            )
        return opened

    monkeypatch.setattr(pending_module, "secure_dir_fd_io_supported", lambda: False)
    monkeypatch.setattr(pending_module.os, "lstat", reparse_lstat)

    with pytest.raises(PendingEnqueueError, match="UNSAFE_PENDING_DIRECTORY"):
        enqueue_write_record(_v2_record())


def test_fallback_reparse_pending_file_marks_scan_unavailable(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = enqueue_write_record(_v2_record())
    original_lstat = os.lstat
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

    def reparse_lstat(candidate: object, *args: object, **kwargs: object) -> os.stat_result:
        opened = original_lstat(candidate, *args, **kwargs)  # type: ignore[arg-type]
        if Path(os.fspath(candidate)) == path:
            return _StatProxy(  # type: ignore[return-value]
                opened, st_file_attributes=reparse_flag
            )
        return opened

    monkeypatch.setattr(pending_module, "secure_dir_fd_io_supported", lambda: False)
    monkeypatch.setattr(pending_module.os, "lstat", reparse_lstat)

    preview = PendingQueue().preview(limit=1)

    assert preview.scan_unavailable is True
    assert preview.reason == "PENDING_SCAN_UNAVAILABLE"


def test_fallback_rejects_fifo_pending_record(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO creation unavailable")
    monkeypatch.setattr(pending_module, "secure_dir_fd_io_supported", lambda: False)
    fifo = tmp_brain / "pending" / "fifo.jsonl"
    fifo.parent.mkdir()
    os.mkfifo(fifo)

    with pytest.raises(pending_module._PendingReadError, match="NOT_REGULAR"):
        pending_module._read_pending_record(fifo)


def test_fallback_concurrent_same_enqueue_is_idempotent(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    fixed = UUID("33333333-3333-4333-8333-333333333333")
    monkeypatch.setattr(pending_module.uuid, "uuid4", lambda: fixed)
    monkeypatch.setattr(pending_module, "secure_dir_fd_io_supported", lambda: False)
    paths: list[Path] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def enqueue() -> None:
        try:
            barrier.wait()
            paths.append(enqueue_write_record(_v2_record(record_id=str(fixed))))
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=enqueue) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert errors == []
    assert len(paths) == 8
    assert len(set(paths)) == 1
    assert len(list((tmp_brain / "pending").glob("*.jsonl"))) == 1


def test_posix_fallback_fsyncs_each_new_parent_before_continuing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    brain = tmp_path / "fallback" / "deep" / "brain"
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    monkeypatch.setattr(pending_module, "secure_dir_fd_io_supported", lambda: False)
    original_mkdir = os.mkdir
    original_fsync = os.fsync
    events: list[str] = []

    def track_mkdir(path: object, *args: object, **kwargs: object) -> None:
        original_mkdir(path, *args, **kwargs)  # type: ignore[arg-type]
        events.append("mkdir")

    def fail_first_directory_fsync(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            events.append("fsync")
            raise OSError("simulated fallback parent fsync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(pending_module.os, "mkdir", track_mkdir)
    monkeypatch.setattr(pending_module.os, "fsync", fail_first_directory_fsync)

    with pytest.raises(OSError, match="simulated fallback parent fsync failure"):
        enqueue_write_record(_v2_record())

    assert events[:2] == ["mkdir", "fsync"]
    assert list(brain.glob("pending/*.jsonl")) == []


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_payload_is_rejected_without_creating_files(tmp_brain: Path, bad: float) -> None:
    record = _v2_record()
    item = record["item"]
    assert isinstance(item, dict)
    item["confidence"] = bad

    with pytest.raises(PendingEnqueueError, match="NON_FINITE_PENDING_PAYLOAD"):
        enqueue_write_record(record)

    assert not (tmp_brain / "pending").exists()


def test_oversized_enqueue_is_rejected_before_file_creation(tmp_brain: Path) -> None:
    record = _v2_record()
    item = record["item"]
    assert isinstance(item, dict)
    item["body"] = "x" * (1024 * 1024)

    with pytest.raises(PendingEnqueueError, match="PENDING_RECORD_TOO_LARGE"):
        enqueue_write_record(record)

    assert not (tmp_brain / "pending").exists()


def test_explicit_v1_keeps_legacy_bytes_and_preview_is_read_only(
    tmp_brain: Path,
) -> None:
    record = _legacy_feedback_record()
    path = enqueue_write_record(record)
    before_bytes = path.read_bytes()
    before_mtime = path.stat().st_mtime_ns
    before_names = sorted(entry.name for entry in path.parent.iterdir())

    first = PendingQueue().preview(limit=10)
    second = PendingQueue().preview(limit=10)

    persisted = json.loads(before_bytes)
    assert persisted["v"] == 1
    assert "record_id" not in persisted
    assert "payload_sha256" not in persisted
    assert first.records[0].record_id == second.records[0].record_id
    assert path.read_bytes() == before_bytes
    assert path.stat().st_mtime_ns == before_mtime
    assert sorted(entry.name for entry in path.parent.iterdir()) == before_names


def test_string_v1_stays_on_legacy_envelope(tmp_brain: Path) -> None:
    record = _legacy_feedback_record()
    record["v"] = "1"

    path = enqueue_write_record(record)
    persisted = json.loads(path.read_text(encoding="utf-8"))

    assert persisted["v"] == "1"
    assert "record_id" not in persisted
    assert PendingQueue().preview(limit=1).records[0].classification == "unsupported_type"


def test_legacy_identity_ignores_retry_bookkeeping(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _legacy_feedback_record()
    item = record["item"]
    assert isinstance(item, dict)
    item["type"] = "fact"
    path = enqueue_write_record(record)
    first = PendingQueue().preview(limit=1).records[0]
    persisted = json.loads(path.read_text(encoding="utf-8"))
    persisted.update(
        {
            "attempt": 4,
            "last_error_code": "TEMPORARY_FAILURE",
            "last_attempt_at": "2026-07-20T11:00:00+00:00",
            "status": "retrying",
        }
    )
    path.write_text(json.dumps(persisted, ensure_ascii=False) + "\n", encoding="utf-8")

    second = PendingQueue().preview(limit=1).records[0]

    assert second.record_id == first.record_id
    assert second.payload_sha256 == first.payload_sha256


def test_v2_preview_preserves_original_time_and_stable_identity(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record()
    path = enqueue_write_record(record)

    first = PendingQueue().preview(limit=10).records[0]
    second = PendingQueue().preview(limit=10).records[0]

    assert path.exists()
    assert first.record_id == second.record_id == "pending-test-fact-0001"
    assert first.payload_sha256 == second.payload_sha256 == _payload_sha256(record)
    assert first.original_created_at == "2026-07-01T10:00:00+00:00"
    assert first.enqueued_at == "2026-07-01T11:00:00+00:00"
    assert first.age_seconds == int(
        (NOW - datetime(2026, 7, 1, 10, tzinfo=timezone.utc)).total_seconds()
    )
    assert first.classification == "ready"
    assert first.reason == "READY"


def test_stable_item_id_uses_utc_instant_and_96_bit_record_hash() -> None:
    first = pending_module._pending_item_id(
        "same title",
        datetime.fromisoformat("2026-07-01T18:00:00+08:00"),
        "same-record",
    )
    second = pending_module._pending_item_id(
        "same title",
        datetime.fromisoformat("2026-07-01T10:00:00+00:00"),
        "same-record",
    )

    assert first == second
    assert first.startswith("mem-20260701-100000-")
    assert re.search(r"-[0-9a-f]{24}$", first)


def test_stable_item_id_distinguishes_dst_fold_instants() -> None:
    zone = ZoneInfo("America/New_York")
    first_fold = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0)
    second_fold = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1)

    first = pending_module._pending_item_id("fold", first_fold, "same-record")
    second = pending_module._pending_item_id("fold", second_fold, "same-record")

    assert first != second
    assert "-053000-" in first
    assert "-063000-" in second


def test_legacy_feedback_is_unsupported_not_malformed(tmp_brain: Path) -> None:
    enqueue_write_record(_legacy_feedback_record())

    record = PendingQueue().preview(limit=10).records[0]

    assert record.malformed is False
    assert record.classification == "unsupported_type"
    assert record.reason == "UNSUPPORTED_MEMORY_TYPE"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("confidence", 1.1),
        ("sensitivity", "top-secret"),
        ("refs", ["not-a-mapping"]),
        ("validity", {"ttl_hours": "not-an-int"}),
        ("tags", "not-a-list"),
    ],
)
def test_invalid_item_schema_fails_closed_without_leaking_body(
    tmp_brain: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record()
    item = record["item"]
    assert isinstance(item, dict)
    item[field] = value
    item["body"] = "schema failure private body"
    enqueue_write_record(record)

    preview = PendingQueue().preview(limit=10).records[0]

    assert preview.classification == "malformed"
    assert preview.reason == "INVALID_ITEM_SCHEMA"
    assert "schema failure private body" not in json.dumps(preview.to_dict())


def test_unsupported_type_precedes_other_schema_failures(tmp_brain: Path) -> None:
    record = _legacy_feedback_record()
    item = record["item"]
    assert isinstance(item, dict)
    item["confidence"] = 2.0
    enqueue_write_record(record)

    preview = PendingQueue().preview(limit=1).records[0]

    assert preview.classification == "unsupported_type"
    assert preview.reason == "UNSUPPORTED_MEMORY_TYPE"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("refs", {"files": [], "filez": ["typo"]}),
        ("validity", {"ttl_hours": 24, "repoo": "typo"}),
        ("source", {"kind": "hook", "extractorr": "typo"}),
        ("source", {"kind": ["not-a-string"]}),
    ],
)
def test_nested_unknown_schema_fields_fail_closed(
    tmp_brain: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record()
    item = record["item"]
    assert isinstance(item, dict)
    item[field] = value
    enqueue_write_record(record)

    preview = PendingQueue().preview(limit=1).records[0]

    assert preview.classification == "malformed"
    assert preview.reason == "INVALID_ITEM_SCHEMA"


def test_v2_item_created_at_must_match_original_instant(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record(original_created_at="2026-07-01T10:00:00+00:00")
    item = record["item"]
    assert isinstance(item, dict)
    item["created_at"] = "2026-07-01T10:00:01+00:00"
    enqueue_write_record(record)

    preview = PendingQueue().preview(limit=1).records[0]

    assert preview.classification == "malformed"
    assert preview.reason == "ITEM_CREATED_AT_MISMATCH"


def test_v2_item_created_at_accepts_same_instant_with_different_offset(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record(original_created_at="2026-07-01T10:00:00+00:00")
    item = record["item"]
    assert isinstance(item, dict)
    item["created_at"] = "2026-07-01T18:00:00+08:00"
    enqueue_write_record(record)

    preview = PendingQueue().preview(limit=1).records[0]

    assert preview.classification == "ready"
    assert preview.original_created_at == "2026-07-01T10:00:00+00:00"


@pytest.mark.parametrize("type_", ["signal", "handoff"])
def test_old_signal_and_handoff_require_review(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch, type_: str
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record(type_=type_, original_created_at="2026-06-20T12:00:00+00:00")
    enqueue_write_record(record)

    preview = PendingQueue().preview(limit=10).records[0]

    assert preview.age_seconds == 30 * 24 * 60 * 60
    assert preview.classification == "stale_requires_review"
    assert preview.reason == "STALE_EPHEMERAL_MEMORY"


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("original_created_at", "2026-07-01T10:00:00", "NAIVE_ORIGINAL_CREATED_AT"),
        ("original_created_at", "2026-07-21T10:00:00+00:00", "FUTURE_ORIGINAL_CREATED_AT"),
        ("original_created_at", "0001-01-01T00:00:00+14:00", "INVALID_ORIGINAL_CREATED_AT"),
        ("enqueued_at", "not-a-time", "INVALID_ENQUEUED_AT"),
    ],
)
def test_invalid_pending_times_fail_closed(
    tmp_brain: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
    reason: str,
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record()
    record[field] = value
    enqueue_write_record(record)

    preview = PendingQueue().preview(limit=10).records[0]

    assert preview.malformed is True
    assert preview.classification == "malformed"
    assert preview.reason == reason


def test_v2_declared_hash_tamper_is_conflict(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record()
    record["payload_sha256"] = "0" * 64
    enqueue_write_record(record)

    preview = PendingQueue().preview(limit=10).records[0]

    assert preview.payload_sha256 == _payload_sha256(record)
    assert preview.classification == "conflict"
    assert preview.reason == "PAYLOAD_HASH_MISMATCH"


def test_audit_blocked_payload_has_closed_classification(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record()
    item = record["item"]
    assert isinstance(item, dict)
    marker = "-----BEGIN " + "RSA PRIVATE KEY-----"
    item["body"] = marker
    enqueue_write_record(record)

    preview = PendingQueue().preview(limit=10).records[0]

    assert preview.classification == "audit_blocked"
    assert preview.reason == "AUDIT_BLOCKED"
    assert marker not in json.dumps(preview.to_dict())


def test_malformed_record_does_not_block_other_records(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    first = enqueue_write_record(_v2_record(record_id="pending-valid-0001"))
    malformed = first.parent / "0000-malformed.jsonl"
    malformed.write_bytes(b"{not json\n")

    preview = PendingQueue().preview(limit=10)

    assert preview.total == 2
    assert [record.classification for record in preview.records] == ["malformed", "ready"]
    assert preview.records[0].reason == "MALFORMED_JSON"


def test_same_record_id_and_payload_marks_later_queue_record_duplicate(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    enqueue_write_record(_v2_record(record_id="shared-record"))
    enqueue_write_record(_v2_record(record_id="shared-record"))

    preview = PendingQueue().preview(limit=10)

    assert [record.classification for record in preview.records] == [
        "ready",
        "duplicate_candidate",
    ]
    assert preview.records[1].reason == "PENDING_RECORD_DUPLICATE"


def test_same_record_id_with_different_payload_marks_all_records_conflict(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    enqueue_write_record(_v2_record(record_id="shared-record"))
    changed = _v2_record(record_id="shared-record")
    item = changed["item"]
    assert isinstance(item, dict)
    item["summary"] = "different semantics"
    enqueue_write_record(changed)

    preview = PendingQueue().preview(limit=10)

    assert [record.classification for record in preview.records] == [
        "conflict",
        "conflict",
    ]
    assert {record.reason for record in preview.records} == {"PENDING_RECORD_ID_CONFLICT"}


def test_oversized_pending_record_fails_closed_without_reading_it(
    tmp_brain: Path,
) -> None:
    pending = tmp_brain / "pending"
    pending.mkdir()
    path = pending / "oversized.jsonl"
    path.write_bytes(b"{" + b" " * (1024 * 1024) + b"}\n")

    result = PendingQueue().preview(limit=10)
    preview = result.records[0]

    assert result.scan_unavailable is True
    assert preview.classification == "audit_blocked"
    assert preview.reason == "PENDING_SCAN_UNAVAILABLE"


def test_preview_ignores_symlinks_and_only_reads_regular_files(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    path = enqueue_write_record(_v2_record())
    symlink = path.parent / "0000-symlink.jsonl"
    try:
        symlink.symlink_to(path)
    except (NotImplementedError, OSError):
        pytest.skip("symlink creation unavailable")

    preview = PendingQueue().preview(limit=10)

    assert preview.total == 1
    assert [record.record_id for record in preview.records] == ["pending-test-fact-0001"]


def test_brain_dir_symlink_alias_converges_writer_and_scanner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "real-brain"
    (target / "items").mkdir(parents=True)
    alias = tmp_path / "brain-alias"
    try:
        alias.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("directory symlink unavailable")
    monkeypatch.setenv("BRAIN_DIR", str(alias))
    _freeze_now(monkeypatch)

    path = enqueue_write_record(_v2_record())
    preview = PendingQueue().preview(limit=1)

    assert path.parent == target.resolve() / "pending"
    assert preview.total == 1
    assert preview.records[0].classification == "ready"


def test_pending_directory_symlink_is_rejected_and_scan_truth_is_explicit(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_brain.parent / "outside-pending"
    outside.mkdir()
    try:
        (tmp_brain / "pending").symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("directory symlink unavailable")

    with pytest.raises((OSError, PendingEnqueueError)):
        enqueue_write_record(_v2_record())
    monkeypatch.setattr(pending_module, "secure_dir_fd_io_supported", lambda: False)
    preview = PendingQueue().preview(limit=1)

    assert preview.scan_unavailable is True
    assert preview.reason == "PENDING_SCAN_UNAVAILABLE"
    with pytest.raises(PendingEnqueueError, match="PENDING_SCAN_UNAVAILABLE"):
        PendingQueue().depth()
    assert list(outside.iterdir()) == []


def test_existing_stable_item_with_same_hash_is_already_written_without_body_read(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record()
    enqueue_write_record(record)
    _write_existing_item(
        tmp_brain,
        record,
        item_id=_stable_item_id(record),
        span_hash=_payload_sha256(record),
        corrupt_body=True,
    )

    preview = PendingQueue().preview(limit=10).records[0]

    assert preview.classification == "already_written"
    assert preview.reason == "STABLE_ITEM_ALREADY_WRITTEN"


def test_existing_private_stable_item_is_detected_but_content_stays_redacted(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record()
    queued = record["item"]
    assert isinstance(queued, dict)
    queued["sensitivity"] = "private"
    enqueue_write_record(record)
    _write_existing_item(
        tmp_brain,
        record,
        item_id=_stable_item_id(record),
        span_hash=_payload_sha256(record),
        corrupt_body=True,
    )

    preview = PendingQueue().preview(limit=10).records[0]

    assert preview.classification == "already_written"
    assert preview.title is None
    assert preview.summary is None


def test_untrusted_existing_item_scan_blocks_ready_classification(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    enqueue_write_record(_v2_record())
    monkeypatch.setattr(
        pending_module,
        "_scan_existing_item_metadata",
        lambda *_args, **_kwargs: pending_module._ItemMetadataSnapshot(items={}, trusted=False),
    )

    preview = PendingQueue().preview(limit=10).records[0]

    assert preview.classification == "audit_blocked"
    assert preview.reason == "EXISTING_ITEM_SCAN_UNAVAILABLE"


def test_existing_stable_item_with_different_hash_is_conflict(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record()
    enqueue_write_record(record)
    _write_existing_item(
        tmp_brain,
        record,
        item_id=_stable_item_id(record),
        span_hash="f" * 64,
    )

    preview = PendingQueue().preview(limit=10).records[0]

    assert preview.classification == "conflict"
    assert preview.reason == "STABLE_ITEM_PAYLOAD_CONFLICT"


@pytest.mark.parametrize(
    ("queued_project", "queued_tenant", "existing_project", "existing_tenant"),
    [
        ("amh", "tenant-a", "other-project", "tenant-a"),
        ("amh", "tenant-a", "amh", "tenant-b"),
    ],
)
def test_existing_stable_item_cross_scope_is_conflict_even_with_same_hash(
    tmp_brain: Path,
    monkeypatch: pytest.MonkeyPatch,
    queued_project: str,
    queued_tenant: str,
    existing_project: str,
    existing_tenant: str,
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record(project=queued_project, tenant_id=queued_tenant)
    enqueue_write_record(record)
    _write_existing_item(
        tmp_brain,
        record,
        item_id=_stable_item_id(record),
        span_hash=_payload_sha256(record),
        project=existing_project,
        tenant_id=existing_tenant,
    )

    preview = PendingQueue().preview(limit=10).records[0]

    assert preview.classification == "conflict"
    assert preview.reason == "STABLE_ITEM_SCOPE_CONFLICT"


def test_none_and_empty_scope_are_canonically_equivalent(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record(project=None, tenant_id=None)
    enqueue_write_record(record)
    _write_existing_item(
        tmp_brain,
        record,
        item_id=_stable_item_id(record),
        span_hash=_payload_sha256(record),
        project="",
        tenant_id="",
    )

    preview = PendingQueue().preview(limit=10).records[0]

    assert preview.classification == "already_written"
    assert preview.reason == "STABLE_ITEM_ALREADY_WRITTEN"


def test_same_scope_existing_payload_is_duplicate_candidate(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record()
    enqueue_write_record(record)
    _write_existing_item(
        tmp_brain,
        record,
        item_id="mem-20260701-100000-existing-duplicate",
        span_hash=_payload_sha256(record),
    )

    preview = PendingQueue().preview(limit=10).records[0]

    assert preview.classification == "duplicate_candidate"
    assert preview.reason == "SAME_SCOPE_PAYLOAD_DUPLICATE"


def test_same_scope_title_summary_identity_is_duplicate_candidate(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record()
    enqueue_write_record(record)
    _write_existing_item(
        tmp_brain,
        record,
        item_id="mem-20260701-100000-existing-metadata-duplicate",
        span_hash=None,
    )

    preview = PendingQueue().preview(limit=10).records[0]

    assert preview.classification == "duplicate_candidate"
    assert preview.reason == "SAME_SCOPE_METADATA_DUPLICATE"


def test_different_scope_payload_is_not_a_duplicate_candidate(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record()
    enqueue_write_record(record)
    _write_existing_item(
        tmp_brain,
        record,
        item_id="mem-20260701-100000-other-scope",
        span_hash=_payload_sha256(record),
        project="other-project",
    )

    preview = PendingQueue().preview(limit=10).records[0]

    assert preview.classification == "ready"


def test_private_preview_redacts_content_fields(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record()
    item = record["item"]
    assert isinstance(item, dict)
    item.update(
        {
            "sensitivity": "private",
            "title": "private title",
            "summary": "private summary",
            "body": "private body",
            "project": "private project",
            "agent": "private agent",
            "session": "private session",
        }
    )
    enqueue_write_record(record)

    preview = PendingQueue().preview(limit=10).records[0]
    payload = preview.to_dict()

    assert preview.classification == "ready"
    assert payload["title"] is None
    assert payload["summary"] is None
    assert payload["project"] is None
    assert payload["agent"] is None
    assert payload["session"] is None
    assert "private body" not in json.dumps(payload)


@pytest.mark.parametrize("sensitivity", ["unknown-tier", 7, ["private"]])
def test_invalid_sensitivity_redacts_content_before_schema_validation(
    tmp_brain: Path,
    monkeypatch: pytest.MonkeyPatch,
    sensitivity: object,
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record()
    item = record["item"]
    assert isinstance(item, dict)
    item.update(
        {
            "sensitivity": sensitivity,
            "title": "must redact title",
            "summary": "must redact summary",
            "body": "must redact body",
        }
    )
    enqueue_write_record(record)

    preview = PendingQueue().preview(limit=1).records[0]
    serialized = json.dumps(preview.to_dict())

    assert preview.classification == "malformed"
    assert preview.title is None
    assert preview.summary is None
    assert "must redact" not in serialized


def test_pending_preview_summarizes_records_without_replay(tmp_brain: Path) -> None:
    rec = {
        "v": 1,
        "op": "write",
        "origin": "hook",
        "attempt": 2,
        "ts": "2026-07-01T11:00:00+00:00",
        "item": {
            "type": "decision",
            "title": "queued decision",
            "summary": "queued summary",
            "body": "body",
            "tags": ["ops"],
            "sensitivity": "internal",
            "confidence": 0.7,
        },
    }
    path = enqueue_write_record(rec)

    preview = PendingQueue().preview(limit=10)

    assert path.exists()
    assert preview.total == 1
    assert preview.records[0].path == str(path)
    assert preview.records[0].title == "queued decision"
    assert preview.records[0].type == "decision"
    assert preview.records[0].attempt == 2
    assert PendingQueue().depth() == 1


def test_preview_limit_and_sort_are_deterministic(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    first = enqueue_write_record(_v2_record(record_id="pending-z"))
    second = enqueue_write_record(_v2_record(record_id="pending-a"))
    second.rename(first.parent / "0000-first.jsonl")
    first.rename(first.parent / "9999-last.jsonl")

    preview = PendingQueue().preview(limit=1)

    assert preview.total == 2
    assert preview.returned == 1
    assert preview.truncated is True
    assert preview.records[0].record_id == "pending-a"


def test_preview_limit_does_not_hide_record_id_conflict(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    first_record = _v2_record(record_id="shared-record-id")
    second_record = _v2_record(record_id="shared-record-id")
    second_item = second_record["item"]
    assert isinstance(second_item, dict)
    second_item["body"] = "conflicting body"
    first = enqueue_write_record(first_record)
    second = enqueue_write_record(second_record)
    first.rename(first.parent / "0000-first.jsonl")
    second.rename(second.parent / "9999-hidden.jsonl")

    preview = PendingQueue().preview(limit=1)

    assert preview.returned == 1
    assert preview.records[0].classification == "conflict"
    assert preview.records[0].reason == "PENDING_RECORD_ID_CONFLICT"


def test_preview_limit_does_not_hide_stable_id_conflict(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    monkeypatch.setattr(
        pending_module,
        "_pending_item_id",
        lambda *_args, **_kwargs: "mem-20260701-100000-forced-stable-id",
    )
    first_record = _v2_record(record_id="first-record-id")
    second_record = _v2_record(record_id="second-record-id")
    second_item = second_record["item"]
    assert isinstance(second_item, dict)
    second_item["body"] = "conflicting body"
    first = enqueue_write_record(first_record)
    second = enqueue_write_record(second_record)
    first.rename(first.parent / "0000-first.jsonl")
    second.rename(second.parent / "9999-hidden.jsonl")

    preview = PendingQueue().preview(limit=1)

    assert preview.returned == 1
    assert preview.records[0].classification == "conflict"
    assert preview.records[0].reason == "PENDING_STABLE_ID_CONFLICT"


def test_pending_entry_stat_failure_blocks_all_selected_records(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    first = enqueue_write_record(_v2_record(record_id="visible-record"))
    second = enqueue_write_record(_v2_record(record_id="unknown-record"))
    first.rename(first.parent / "0000-visible.jsonl")
    second.rename(second.parent / "9999-unknown.jsonl")
    hidden = tmp_brain / "pending" / "9999-unknown.jsonl"
    original_lstat = os.lstat

    def failing_lstat(path: object, *args: object, **kwargs: object) -> os.stat_result:
        if Path(os.fspath(path)) == hidden:
            raise OSError("simulated entry stat failure")
        return original_lstat(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pending_module, "secure_dir_fd_io_supported", lambda: False)
    monkeypatch.setattr(pending_module.os, "lstat", failing_lstat)

    preview = PendingQueue().preview(limit=1)

    assert preview.scan_unavailable is True
    assert preview.reason == "PENDING_SCAN_UNAVAILABLE"
    assert preview.records[0].classification == "audit_blocked"
    assert preview.records[0].reason == "PENDING_SCAN_UNAVAILABLE"


def test_pending_record_read_failure_blocks_all_selected_records(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    first = enqueue_write_record(_v2_record(record_id="readable-record"))
    second = enqueue_write_record(_v2_record(record_id="unreadable-record"))
    first.rename(first.parent / "0000-readable.jsonl")
    second.rename(second.parent / "9999-unreadable.jsonl")
    original_read = pending_module._read_pending_record

    def fail_one_read(path: Path) -> bytes:
        if path.name == "9999-unreadable.jsonl":
            raise pending_module._PendingReadError("PENDING_RECORD_READ_FAILED")
        return original_read(path)

    monkeypatch.setattr(pending_module, "_read_pending_record", fail_one_read)

    preview = PendingQueue().preview(limit=1)

    assert preview.scan_unavailable is True
    assert preview.reason == "PENDING_SCAN_UNAVAILABLE"
    assert preview.records[0].classification == "audit_blocked"
    assert preview.records[0].reason == "PENDING_SCAN_UNAVAILABLE"


def test_pending_scan_cap_keeps_cap_plus_one_and_reports_truncation(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    monkeypatch.setattr(pending_module, "MAX_PENDING_QUEUE_ENTRIES", 1)
    first = enqueue_write_record(_v2_record(record_id="pending-z"))
    second = enqueue_write_record(_v2_record(record_id="pending-a"))
    first.rename(first.parent / "z-last.jsonl")
    second.rename(second.parent / "a-first.jsonl")

    preview = PendingQueue().preview(limit=10)

    assert preview.total == 2
    assert preview.returned == 1
    assert preview.truncated is True
    assert preview.records[0].record_id == "pending-a"
    assert preview.records[0].classification == "audit_blocked"
    assert preview.records[0].reason == "PENDING_QUEUE_TRUNCATED"


def test_existing_item_scan_overflow_blocks_ready_classification(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    monkeypatch.setattr(pending_module, "MAX_ITEM_METADATA_ENTRIES", 1)
    record = _v2_record()
    enqueue_write_record(record)
    _write_existing_item(
        tmp_brain,
        record,
        item_id="mem-20260701-100000-first-existing",
        span_hash=None,
        project="other-one",
    )
    _write_existing_item(
        tmp_brain,
        record,
        item_id="mem-20260701-100001-second-existing",
        span_hash=None,
        project="other-two",
    )

    preview = PendingQueue().preview(limit=10).records[0]

    assert preview.classification == "audit_blocked"
    assert preview.reason == "EXISTING_ITEM_SCAN_UNAVAILABLE"


def test_malformed_existing_yaml_blocks_selected_preview_without_raising(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    enqueue_write_record(_v2_record())
    (tmp_brain / "items" / "mem-20260701-100000-bad-yaml.md").write_text(
        "---\nrefs: [unterminated\n---\nbody\n",
        encoding="utf-8",
    )

    preview = PendingQueue().preview(limit=1)

    assert preview.records[0].classification == "audit_blocked"
    assert preview.records[0].reason == "EXISTING_ITEM_SCAN_UNAVAILABLE"


def test_limit_zero_does_not_scan_existing_items(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    enqueue_write_record(_v2_record())
    monkeypatch.setattr(
        pending_module,
        "_scan_existing_item_metadata",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not scan")),
    )

    preview = PendingQueue().preview(limit=0)

    assert preview.total == 1
    assert preview.returned == 0
    assert preview.truncated is True
