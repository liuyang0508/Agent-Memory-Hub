"""Descriptor-relative archive transaction with an independent target inode."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agent_brain.contracts.memory_item import MemoryItem, is_valid_memory_item_id
from agent_brain.memory.governance.lifecycle_ledger import lifecycle_transaction_lock
from agent_brain.memory.store import durable_fs
from agent_brain.memory.store.durable_fs import SecureDirectory
from agent_brain.memory.store.item_markdown import parse_item_markdown
from agent_brain.memory.store.items_store import ItemsStore

_MAX_ARCHIVE_ITEM_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class ArchiveTransactionResult:
    status: str
    reason: str
    index_repair_attempted: bool = False
    index_repair_required: bool = False


@dataclass(frozen=True)
class _FileSnapshot:
    device: int
    inode: int
    mode: int
    size: int
    modified_ns: int
    changed_ns: int
    digest: bytes

    @property
    def identity(self) -> tuple[int, int]:
        return self.device, self.inode


class _ConcurrentModification(OSError):
    pass


class _TargetPublishError(OSError):
    def __init__(self, *, cleanup_failed: bool) -> None:
        super().__init__("ARCHIVE_TARGET_PUBLISH_FAILED")
        self.cleanup_failed = cleanup_failed


def archive_reviewed_item(
    *,
    brain_dir: Path,
    items_store: ItemsStore,
    item_id: str,
    eligible: Callable[[MemoryItem], bool],
    index: Any = None,
) -> ArchiveTransactionResult:
    """Copy verified bytes to an exclusive independent target, then unlink source."""
    if not is_valid_memory_item_id(item_id):
        return ArchiveTransactionResult("blocked", "INVALID_ITEM_ID")
    if not durable_fs.lifecycle_mutation_capability():
        return ArchiveTransactionResult("blocked", "PLATFORM_UNSUPPORTED")

    source = f"{item_id}.md"
    try:
        with (
            lifecycle_transaction_lock(brain_dir),
            items_store.locked_items([item_id]),
            SecureDirectory.open(items_store.items_dir) as items,
            items.child("archived", create=True) as archive,
        ):
            try:
                expected, payload = _read_snapshot(items, source)
            except FileNotFoundError:
                return _already_archived_without_source(
                    items=items,
                    archive=archive,
                    source=source,
                    item_id=item_id,
                    index=index,
                )
            except _ConcurrentModification:
                return ArchiveTransactionResult("blocked", "CONCURRENT_MODIFICATION")
            except (OSError, ValueError):
                return ArchiveTransactionResult("blocked", "ITEM_INVALID")

            try:
                item, _body = _parse_payload(payload)
            except (UnicodeError, ValueError):
                return ArchiveTransactionResult("blocked", "ITEM_INVALID")
            if item.id != item_id:
                return ArchiveTransactionResult("blocked", "ITEM_INVALID")
            if not eligible(item):
                return ArchiveTransactionResult(
                    "blocked", "NOT_IN_LIFECYCLE_REVIEW_QUEUE"
                )

            try:
                current = _current_snapshot(items, source)
            except (OSError, ValueError):
                return ArchiveTransactionResult("blocked", "CONCURRENT_MODIFICATION")
            if current is None or not _same_source(expected, current):
                return ArchiveTransactionResult("blocked", "CONCURRENT_MODIFICATION")

            try:
                target = _read_snapshot(archive, source)[0]
            except FileNotFoundError:
                target = None
            except (OSError, ValueError, _ConcurrentModification):
                return ArchiveTransactionResult("blocked", "ARCHIVE_TARGET_EXISTS")

            if target is not None:
                return _resume_existing_target(
                    items=items,
                    archive=archive,
                    source=source,
                    expected=expected,
                    payload=payload,
                    target=target,
                    index=index,
                )

            try:
                target = _publish_independent_target(
                    archive=archive,
                    name=source,
                    payload=payload,
                    mode=expected.mode,
                )
            except FileExistsError:
                try:
                    target = _read_snapshot(archive, source)[0]
                except (OSError, ValueError, _ConcurrentModification):
                    return ArchiveTransactionResult(
                        "blocked", "ARCHIVE_TARGET_EXISTS"
                    )
                return _resume_existing_target(
                    items=items,
                    archive=archive,
                    source=source,
                    expected=expected,
                    payload=payload,
                    target=target,
                    index=index,
                )
            except _TargetPublishError as error:
                return ArchiveTransactionResult(
                    "partial" if error.cleanup_failed else "blocked",
                    "ARCHIVE_TARGET_CLEANUP_FAILED"
                    if error.cleanup_failed
                    else "ARCHIVE_FAILED",
                )

            return _finish_independent_archive(
                items=items,
                archive=archive,
                source=source,
                expected=expected,
                payload=payload,
                target=target,
                target_created=True,
                index=index,
            )
    except FileNotFoundError:
        return ArchiveTransactionResult("blocked", "SOURCE_MISSING")
    except (OSError, UnicodeError, ValueError):
        return ArchiveTransactionResult("blocked", "ARCHIVE_FAILED")


def _resume_existing_target(
    *,
    items: SecureDirectory,
    archive: SecureDirectory,
    source: str,
    expected: _FileSnapshot,
    payload: bytes,
    target: _FileSnapshot,
    index: Any,
) -> ArchiveTransactionResult:
    if target.identity == expected.identity:
        return ArchiveTransactionResult("partial", "ARCHIVE_TARGET_SHARED_INODE")
    if not _same_payload(expected, target):
        return ArchiveTransactionResult("blocked", "ARCHIVE_TARGET_EXISTS")
    return _finish_independent_archive(
        items=items,
        archive=archive,
        source=source,
        expected=expected,
        payload=payload,
        target=target,
        target_created=False,
        index=index,
    )


def _finish_independent_archive(
    *,
    items: SecureDirectory,
    archive: SecureDirectory,
    source: str,
    expected: _FileSnapshot,
    payload: bytes,
    target: _FileSnapshot,
    target_created: bool,
    index: Any,
) -> ArchiveTransactionResult:
    try:
        _fsync_regular_file(archive, source, target.identity)
        archive.fsync()
    except (OSError, ValueError):
        cleaned = target_created and _remove_target_if_identity(
            archive, source, target.identity
        )
        return ArchiveTransactionResult(
            "blocked" if cleaned else "partial",
            "ARCHIVE_FAILED" if cleaned else "ARCHIVE_TARGET_DURABILITY_FAILED",
        )

    try:
        target_now = _current_snapshot(archive, source)
    except (OSError, ValueError):
        return ArchiveTransactionResult("partial", "ARCHIVE_TARGET_REPLACED")
    try:
        source_now = _current_snapshot(items, source)
    except (OSError, ValueError):
        return ArchiveTransactionResult("partial", "ARCHIVE_SOURCE_REPLACED")
    if target_now is None or target_now.identity != target.identity:
        return ArchiveTransactionResult("partial", "ARCHIVE_TARGET_REPLACED")
    if not _same_payload(expected, target_now):
        return ArchiveTransactionResult("partial", "ARCHIVE_CONTENT_CHANGED")
    if source_now is None:
        return _applied_with_index(index, source.removesuffix(".md"))
    if not _same_source(expected, source_now):
        return ArchiveTransactionResult("partial", "ARCHIVE_SOURCE_REPLACED")

    # Compare exact prepared bytes on both names immediately before unlink.
    try:
        target_check, target_bytes = _read_snapshot(archive, source)
        source_check, source_bytes = _read_snapshot(items, source)
    except FileNotFoundError:
        try:
            current_source = _current_snapshot(items, source)
        except (OSError, ValueError):
            return ArchiveTransactionResult("partial", "ARCHIVE_SOURCE_REPLACED")
        if current_source is None:
            return _applied_with_index(index, source.removesuffix(".md"))
        return ArchiveTransactionResult("partial", "ARCHIVE_CONTENT_CHANGED")
    except (OSError, ValueError, _ConcurrentModification):
        return ArchiveTransactionResult("partial", "ARCHIVE_CONTENT_CHANGED")
    if (
        target_check.identity != target.identity
        or target_bytes != payload
        or not _same_source(expected, source_check)
        or source_bytes != payload
    ):
        return ArchiveTransactionResult("partial", "ARCHIVE_SOURCE_REPLACED")

    try:
        items.unlink(source)
        items.fsync()
    except FileNotFoundError:
        pass
    except OSError:
        return ArchiveTransactionResult("partial", "ARCHIVE_SOURCE_UNLINK_FAILED")

    try:
        remaining = _current_snapshot(items, source)
    except (OSError, ValueError):
        return ArchiveTransactionResult("partial", "ARCHIVE_SOURCE_REPLACED")
    if remaining is not None:
        return ArchiveTransactionResult("partial", "ARCHIVE_SOURCE_REPLACED")
    return _applied_with_index(index, source.removesuffix(".md"))


def _publish_independent_target(
    *,
    archive: SecureDirectory,
    name: str,
    payload: bytes,
    mode: int,
) -> _FileSnapshot:
    descriptor = -1
    identity: tuple[int, int] | None = None
    try:
        descriptor, _ = archive.open_file(
            name,
            os.O_WRONLY | os.O_NONBLOCK,
            mode,
            exclusive=True,
        )
        opened = os.fstat(descriptor)
        identity = opened.st_dev, opened.st_ino
        os.fchmod(descriptor, mode)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        archive.fsync()
        target, written = _read_snapshot(archive, name)
        if target.identity != identity or written != payload:
            raise OSError("ARCHIVE_TARGET_VERIFY_FAILED")
        return target
    except FileExistsError:
        raise
    except BaseException as error:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        cleaned = identity is not None and _remove_target_if_identity(
            archive, name, identity
        )
        if not isinstance(error, Exception):
            if not cleaned:
                error.add_note("ARCHIVE_TARGET_CLEANUP_FAILED")
            raise
        raise _TargetPublishError(cleanup_failed=not cleaned) from error


def _already_archived_without_source(
    *,
    items: SecureDirectory,
    archive: SecureDirectory,
    source: str,
    item_id: str,
    index: Any,
) -> ArchiveTransactionResult:
    try:
        target, payload = _read_snapshot(archive, source)
        item, _body = _parse_payload(payload)
        if item.id != item_id:
            return ArchiveTransactionResult("blocked", "ARCHIVE_TARGET_INVALID")
        _fsync_regular_file(archive, source, target.identity)
        archive.fsync()
        items.fsync()
    except FileNotFoundError:
        return ArchiveTransactionResult("blocked", "SOURCE_MISSING")
    except (OSError, UnicodeError, ValueError, _ConcurrentModification):
        return ArchiveTransactionResult("blocked", "ARCHIVE_TARGET_INVALID")
    indexed = _applied_with_index(index, item_id)
    return ArchiveTransactionResult(
        "already_applied",
        "ALREADY_ARCHIVED",
        index_repair_attempted=indexed.index_repair_attempted,
        index_repair_required=indexed.index_repair_required,
    )


def _applied_with_index(index: Any, item_id: str) -> ArchiveTransactionResult:
    attempted = index is not None
    required = index is None
    if index is not None:
        try:
            index.delete(item_id)
        except Exception:  # noqa: BLE001 - archived markdown is authoritative.
            required = True
    return ArchiveTransactionResult(
        "applied",
        "OK",
        index_repair_attempted=attempted,
        index_repair_required=required,
    )


def _remove_target_if_identity(
    archive: SecureDirectory,
    name: str,
    identity: tuple[int, int],
) -> bool:
    try:
        target = archive.stat(name)
        if (target.st_dev, target.st_ino) != identity:
            return False
        archive.unlink(name)
        archive.fsync()
        return True
    except (FileNotFoundError, OSError, ValueError):
        return False


def _fsync_regular_file(
    directory: SecureDirectory,
    name: str,
    identity: tuple[int, int],
) -> None:
    descriptor, _ = directory.open_file(name, os.O_RDONLY | os.O_NONBLOCK)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError("ITEM_INVALID")
        if (opened.st_dev, opened.st_ino) != identity:
            raise _ConcurrentModification("ARCHIVE_TARGET_REPLACED")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _current_snapshot(
    directory: SecureDirectory,
    name: str,
) -> _FileSnapshot | None:
    try:
        return _read_snapshot(directory, name)[0]
    except FileNotFoundError:
        return None


def _read_snapshot(
    directory: SecureDirectory,
    name: str,
) -> tuple[_FileSnapshot, bytes]:
    descriptor, _ = directory.open_file(name, os.O_RDONLY | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("ITEM_INVALID")
        if before.st_size > _MAX_ARCHIVE_ITEM_BYTES:
            raise ValueError("ITEM_TOO_LARGE")
        payload = _read_bounded(descriptor)
        after = os.fstat(descriptor)
        if _read_stat_signature(before) != _read_stat_signature(after):
            raise _ConcurrentModification("ITEM_CHANGED_DURING_READ")
        if len(payload) != after.st_size:
            raise _ConcurrentModification("ITEM_SIZE_CHANGED_DURING_READ")
        return _snapshot(after, payload), payload
    finally:
        os.close(descriptor)


def _read_bounded(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    remaining = _MAX_ARCHIVE_ITEM_BYTES + 1
    while remaining > 0:
        chunk = os.read(descriptor, min(65536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(chunks)
    if len(payload) > _MAX_ARCHIVE_ITEM_BYTES:
        raise ValueError("ITEM_TOO_LARGE")
    return payload


def _write_all(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("ARCHIVE_TARGET_WRITE_FAILED")
        remaining = remaining[written:]


def _snapshot(value: os.stat_result, payload: bytes) -> _FileSnapshot:
    return _FileSnapshot(
        device=value.st_dev,
        inode=value.st_ino,
        mode=stat.S_IMODE(value.st_mode),
        size=value.st_size,
        modified_ns=value.st_mtime_ns,
        changed_ns=value.st_ctime_ns,
        digest=hashlib.sha256(payload).digest(),
    )


def _read_stat_signature(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        stat.S_IMODE(value.st_mode),
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _same_source(left: _FileSnapshot, right: _FileSnapshot) -> bool:
    return (
        left.identity == right.identity
        and left.mode == right.mode
        and left.size == right.size
        and left.modified_ns == right.modified_ns
        and left.digest == right.digest
    )


def _same_payload(left: _FileSnapshot, right: _FileSnapshot) -> bool:
    return left.size == right.size and left.digest == right.digest


def _parse_payload(payload: bytes) -> tuple[MemoryItem, str]:
    text = payload.decode("utf-8-sig")
    return parse_item_markdown(text.replace("\r\n", "\n").replace("\r", "\n"))


__all__ = ["ArchiveTransactionResult", "archive_reviewed_item"]
