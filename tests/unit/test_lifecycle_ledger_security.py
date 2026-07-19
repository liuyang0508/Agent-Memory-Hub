import json
import os
import stat
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from agent_brain.memory.governance import lifecycle_ledger as ledger_module
from agent_brain.memory.governance.lifecycle_ledger import (
    LifecycleLedgerRecord,
    LifecycleLedgerRollbackError,
    append_lifecycle_record,
    latest_applied_supersession_record,
    lifecycle_transaction_lock,
)
from agent_brain.memory.store.durable_fs import SecureDirectory


OLD_ID = "mem-20260719-100000-ledger-old"
NEW_ID = "mem-20260719-110000-ledger-new"


def _record(**updates) -> LifecycleLedgerRecord:
    record = LifecycleLedgerRecord(
        action="supersede",
        timestamp="2026-07-19T12:00:00+00:00",
        status="applied",
        reason="OK",
        obsolete_id=OLD_ID,
        replacement_id=NEW_ID,
        snapshot="a" * 40,
        replacement_ref_preexisted=False,
    )
    return replace(record, **updates)


def test_append_rejects_ledger_symlink_without_touching_external_file(
    tmp_brain_dir: Path,
) -> None:
    runtime = tmp_brain_dir / "runtime"
    runtime.mkdir()
    external = tmp_brain_dir / "external-ledger"
    external.write_bytes(b"sentinel")
    external.chmod(0o640)
    (runtime / "lifecycle-actions.jsonl").symlink_to(external)

    with pytest.raises(OSError):
        append_lifecycle_record(tmp_brain_dir, _record())

    assert external.read_bytes() == b"sentinel"
    assert stat.S_IMODE(external.stat().st_mode) == 0o640


def test_transaction_lock_rejects_symlink_without_touching_external_file(
    tmp_brain_dir: Path,
) -> None:
    runtime = tmp_brain_dir / "runtime"
    runtime.mkdir()
    external = tmp_brain_dir / "external-transaction-lock"
    external.write_bytes(b"sentinel")
    external.chmod(0o640)
    (runtime / ".lifecycle-transaction.lock").symlink_to(external)

    with pytest.raises(OSError):
        with lifecycle_transaction_lock(tmp_brain_dir):
            pass

    assert external.read_bytes() == b"sentinel"
    assert stat.S_IMODE(external.stat().st_mode) == 0o640


def test_reader_rejects_ledger_lock_symlink_without_reading_external(
    tmp_brain_dir: Path,
) -> None:
    append_lifecycle_record(tmp_brain_dir, _record())
    runtime = tmp_brain_dir / "runtime"
    lock = runtime / ".lifecycle-ledger.lock"
    lock.unlink()
    external = tmp_brain_dir / "external-ledger-lock"
    external.write_bytes(b"sentinel")
    external.chmod(0o640)
    lock.symlink_to(external)

    assert latest_applied_supersession_record(tmp_brain_dir, NEW_ID, OLD_ID) is None
    assert external.read_bytes() == b"sentinel"
    assert stat.S_IMODE(external.stat().st_mode) == 0o640


def test_reader_rejects_ledger_symlink_even_when_external_contains_valid_json(
    tmp_brain_dir: Path,
) -> None:
    runtime = tmp_brain_dir / "runtime"
    runtime.mkdir()
    external = tmp_brain_dir / "external-valid-ledger"
    external.write_text(json.dumps(asdict(_record())) + "\n", encoding="utf-8")
    (runtime / "lifecycle-actions.jsonl").symlink_to(external)

    assert latest_applied_supersession_record(tmp_brain_dir, NEW_ID, OLD_ID) is None


def test_ledger_append_stays_on_runtime_inode_after_parent_swap(
    tmp_brain_dir: Path, monkeypatch
) -> None:
    runtime_path = tmp_brain_dir / "runtime"
    moved = tmp_brain_dir / "moved-runtime"
    victim = tmp_brain_dir / "victim-runtime"
    real_open = SecureDirectory.open_or_create_file
    swapped = False

    def swap_then_open(self, name, flags, mode=0o600):
        nonlocal swapped
        if name == "lifecycle-actions.jsonl" and not swapped:
            runtime_path.rename(moved)
            victim.mkdir()
            (victim / name).write_bytes(b"victim\n")
            (victim / name).chmod(0o640)
            runtime_path.symlink_to(victim, target_is_directory=True)
            swapped = True
        return real_open(self, name, flags, mode)

    monkeypatch.setattr(SecureDirectory, "open_or_create_file", swap_then_open)

    append_lifecycle_record(tmp_brain_dir, _record())

    assert (victim / "lifecycle-actions.jsonl").read_bytes() == b"victim\n"
    assert stat.S_IMODE((victim / "lifecycle-actions.jsonl").stat().st_mode) == 0o640
    assert b'"status":"applied"' in (moved / "lifecycle-actions.jsonl").read_bytes()


def test_transaction_lock_stays_on_runtime_inode_after_parent_swap(
    tmp_brain_dir: Path, monkeypatch
) -> None:
    runtime_path = tmp_brain_dir / "runtime"
    moved = tmp_brain_dir / "moved-runtime"
    victim = tmp_brain_dir / "victim-runtime"
    real_open = SecureDirectory.open_or_create_file
    swapped = False

    def swap_then_open(self, name, flags, mode=0o600):
        nonlocal swapped
        if name == ".lifecycle-transaction.lock" and not swapped:
            runtime_path.rename(moved)
            victim.mkdir()
            (victim / name).write_bytes(b"victim")
            (victim / name).chmod(0o640)
            runtime_path.symlink_to(victim, target_is_directory=True)
            swapped = True
        return real_open(self, name, flags, mode)

    monkeypatch.setattr(SecureDirectory, "open_or_create_file", swap_then_open)

    with lifecycle_transaction_lock(tmp_brain_dir):
        pass

    assert (victim / ".lifecycle-transaction.lock").read_bytes() == b"victim"
    assert (
        stat.S_IMODE((victim / ".lifecycle-transaction.lock").stat().st_mode) == 0o640
    )
    assert (moved / ".lifecycle-transaction.lock").exists()


@pytest.mark.parametrize(
    "record",
    [
        _record(action="arbitrary"),
        _record(status="arbitrary"),
        _record(reason="arbitrary"),
        _record(obsolete_id="not-canonical"),
        _record(replacement_id=None),
        _record(timestamp="2026-07-19T12:00:00"),
        _record(timestamp="2026-07-19T12:00:00+08:00"),
        _record(snapshot="/tmp/private-body"),
        _record(snapshot="A" * 40),
        _record(obsolete_id=OLD_ID + "\x01"),
        _record(obsolete_id=OLD_ID + "x" * 300),
    ],
)
def test_invalid_records_fail_before_ledger_creation(
    tmp_brain_dir: Path, record: LifecycleLedgerRecord
) -> None:
    with pytest.raises((TypeError, ValueError), match="INVALID_LIFECYCLE_LEDGER_RECORD"):
        append_lifecycle_record(tmp_brain_dir, record)

    assert not (tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl").exists()


def test_reader_treats_semantically_invalid_record_as_malformed_barrier(
    tmp_brain_dir: Path,
) -> None:
    append_lifecycle_record(tmp_brain_dir, _record())
    ledger = tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl"
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(_record(action="arbitrary"))) + "\n")

    assert latest_applied_supersession_record(tmp_brain_dir, NEW_ID, OLD_ID) is None


@pytest.mark.parametrize("control_error", [KeyboardInterrupt("control"), SystemExit("control")])
def test_append_preserves_control_exception_when_byte_rollback_fails(
    tmp_brain_dir: Path, monkeypatch, control_error: BaseException
) -> None:
    def interrupt_write(_descriptor, _payload):
        raise control_error

    def fail_truncate(_descriptor, _length):
        raise OSError("sensitive rollback detail")

    monkeypatch.setattr(ledger_module, "_write_all", interrupt_write)
    monkeypatch.setattr(os, "ftruncate", fail_truncate)

    with pytest.raises(type(control_error), match="control") as caught:
        append_lifecycle_record(tmp_brain_dir, _record())

    assert "LEDGER_ROLLBACK_FAILED" in getattr(caught.value, "__notes__", [])


def test_append_preserves_control_exception_when_rollback_fsync_fails(
    tmp_brain_dir: Path, monkeypatch
) -> None:
    runtime = tmp_brain_dir / "runtime"
    runtime.mkdir()
    (runtime / ".lifecycle-ledger.lock").write_bytes(b"\0")

    def interrupt_write(_descriptor, _payload):
        raise KeyboardInterrupt("control")

    monkeypatch.setattr(ledger_module, "_write_all", interrupt_write)
    monkeypatch.setattr(
        os,
        "fsync",
        lambda _descriptor: (_ for _ in ()).throw(OSError("rollback fsync detail")),
    )

    with pytest.raises(KeyboardInterrupt, match="control") as caught:
        append_lifecycle_record(tmp_brain_dir, _record())

    assert "LEDGER_ROLLBACK_FAILED" in getattr(caught.value, "__notes__", [])


def test_append_uses_stable_error_for_ordinary_failure_when_byte_rollback_fails(
    tmp_brain_dir: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        ledger_module,
        "_write_all",
        lambda _descriptor, _payload: (_ for _ in ()).throw(OSError("write secret")),
    )
    monkeypatch.setattr(
        os,
        "ftruncate",
        lambda _descriptor, _length: (_ for _ in ()).throw(OSError("rollback secret")),
    )

    with pytest.raises(LifecycleLedgerRollbackError, match="LEDGER_ROLLBACK_FAILED"):
        append_lifecycle_record(tmp_brain_dir, _record())
