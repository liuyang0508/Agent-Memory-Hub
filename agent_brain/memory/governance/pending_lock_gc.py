"""Bounded observation and safe collection of pending record lock files."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from agent_brain.memory.store.durable_fs import (
    SecureDirectory,
    lifecycle_mutation_capability,
)


MAX_PENDING_LOCK_GC_ENTRIES = 20_000
_LOCK_NAME = re.compile(r"[0-9a-f]{32}\.lock\Z")


@dataclass(frozen=True)
class PendingLockGcReport:
    total: int = 0
    orphan: int = 0
    deleted: int = 0
    preserved: int = 0
    unsafe: int = 0
    truncated: bool = False
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "orphan": self.orphan,
            "deleted": self.deleted,
            "preserved": self.preserved,
            "unsafe": self.unsafe,
            "truncated": self.truncated,
            "reason": self.reason,
        }


def pending_record_lock_name(record_name: str) -> str:
    """Return the stable lock basename for one pending record basename."""

    if not record_name or Path(record_name).name != record_name:
        raise ValueError("INVALID_PENDING_RECORD_NAME")
    return hashlib.sha256(record_name.encode("utf-8")).hexdigest()[:32] + ".lock"


def collect_pending_record_locks(
    pending_dir: Path,
    *,
    live_record_names: set[str],
    apply: bool,
    limit: int = 1000,
) -> PendingLockGcReport:
    """Inspect locks, deleting proven orphans only on the safe Unix path.

    Mutation callers must already hold the global pending queue lock. This
    function independently requires the platform's durable mutation capability
    and a non-blocking exclusive record lock before unlinking anything.
    """

    bounded_limit = min(max(0, limit), MAX_PENDING_LOCK_GC_ENTRIES)
    live_lock_names = {
        pending_record_lock_name(name)
        for name in live_record_names
        if name and Path(name).name == name
    }
    if lifecycle_mutation_capability() and os.name != "nt":
        return _collect_secure(
            Path(pending_dir),
            live_lock_names=live_lock_names,
            apply=apply,
            limit=bounded_limit,
        )
    return _collect_fallback(
        Path(pending_dir),
        live_lock_names=live_lock_names,
        mutation_requested=apply,
        limit=bounded_limit,
    )


def _collect_secure(
    pending_dir: Path,
    *,
    live_lock_names: set[str],
    apply: bool,
    limit: int,
) -> PendingLockGcReport:
    total = orphan = deleted = preserved = unsafe = 0
    truncated = False
    try:
        with SecureDirectory.open(pending_dir) as pending:
            with pending.child(".amh-record-locks") as locks:
                with os.scandir(locks.fd) as entries:
                    while True:
                        try:
                            entry = next(entries)
                        except StopIteration:
                            break
                        if total >= limit:
                            truncated = True
                            break
                        total += 1
                        name = entry.name
                        try:
                            opened = locks.stat(name)
                            if not _safe_lock(name, opened):
                                unsafe += 1
                                preserved += 1
                                continue
                            if name in live_lock_names:
                                preserved += 1
                                continue
                            orphan += 1
                            if not apply:
                                preserved += 1
                                continue
                            if _delete_unlocked_orphan(locks, name, opened):
                                deleted += 1
                            else:
                                preserved += 1
                        except (OSError, ValueError):
                            unsafe += 1
                            preserved += 1
    except FileNotFoundError:
        return PendingLockGcReport()
    except OSError:
        return PendingLockGcReport(
            total=total,
            orphan=orphan,
            deleted=deleted,
            preserved=preserved,
            unsafe=unsafe + 1,
            truncated=truncated,
            reason="PENDING_LOCK_GC_UNSAFE_ENTRY",
        )
    reason = (
        "PENDING_LOCK_GC_TRUNCATED"
        if truncated
        else "PENDING_LOCK_GC_UNSAFE_ENTRY"
        if unsafe
        else None
    )
    return PendingLockGcReport(
        total=total,
        orphan=orphan,
        deleted=deleted,
        preserved=preserved,
        unsafe=unsafe,
        truncated=truncated,
        reason=reason,
    )


def _delete_unlocked_orphan(
    locks: SecureDirectory,
    name: str,
    expected: os.stat_result,
) -> bool:
    descriptor = -1
    acquired = False
    try:
        descriptor, _ = locks.open_file(name, os.O_RDWR | os.O_NONBLOCK)
        opened = os.fstat(descriptor)
        if not _safe_lock(name, opened) or not _same_identity(expected, opened):
            return False
        import fcntl

        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            return False
        current = locks.stat(name)
        if not _safe_lock(name, current) or not _same_identity(opened, current):
            return False
        locks.unlink(name)
        locks.fsync()
        return True
    except FileNotFoundError:
        return False
    finally:
        if descriptor >= 0:
            try:
                if acquired:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def _collect_fallback(
    pending_dir: Path,
    *,
    live_lock_names: set[str],
    mutation_requested: bool,
    limit: int,
) -> PendingLockGcReport:
    lock_dir = pending_dir / ".amh-record-locks"
    total = orphan = preserved = unsafe = 0
    truncated = False
    try:
        opened_dir = os.lstat(lock_dir)
        if not stat.S_ISDIR(opened_dir.st_mode) or stat.S_ISLNK(opened_dir.st_mode):
            raise OSError("unsafe lock directory")
        with os.scandir(lock_dir) as entries:
            while True:
                try:
                    entry = next(entries)
                except StopIteration:
                    break
                if total >= limit:
                    truncated = True
                    break
                total += 1
                preserved += 1
                try:
                    opened = os.lstat(lock_dir / entry.name)
                    if not _safe_lock(entry.name, opened):
                        unsafe += 1
                        continue
                    if entry.name not in live_lock_names:
                        orphan += 1
                except OSError:
                    unsafe += 1
    except FileNotFoundError:
        return PendingLockGcReport(
            reason="PENDING_LOCK_GC_UNAVAILABLE" if mutation_requested else None
        )
    except OSError:
        return PendingLockGcReport(
            unsafe=1,
            reason=(
                "PENDING_LOCK_GC_UNAVAILABLE"
                if mutation_requested
                else "PENDING_LOCK_GC_UNSAFE_ENTRY"
            ),
        )
    reason = (
        "PENDING_LOCK_GC_UNAVAILABLE"
        if mutation_requested
        else "PENDING_LOCK_GC_TRUNCATED"
        if truncated
        else "PENDING_LOCK_GC_UNSAFE_ENTRY"
        if unsafe
        else None
    )
    return PendingLockGcReport(
        total=total,
        orphan=orphan,
        deleted=0,
        preserved=preserved,
        unsafe=unsafe,
        truncated=truncated,
        reason=reason,
    )


def _safe_lock(name: str, opened: os.stat_result) -> bool:
    expected_uid = getattr(os, "geteuid", lambda: opened.st_uid)()
    return (
        _LOCK_NAME.fullmatch(name) is not None
        and stat.S_ISREG(opened.st_mode)
        and not stat.S_ISLNK(opened.st_mode)
        and stat.S_IMODE(opened.st_mode) == 0o600
        and opened.st_size <= 1
        and getattr(opened, "st_uid", expected_uid) == expected_uid
    )


def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        bool(left.st_dev)
        and bool(left.st_ino)
        and left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
    )


__all__ = [
    "PendingLockGcReport",
    "collect_pending_record_locks",
    "pending_record_lock_name",
]
