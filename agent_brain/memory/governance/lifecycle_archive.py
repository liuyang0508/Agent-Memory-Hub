"""Descriptor-relative, link-first archive transaction for reviewed items."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, cast

from agent_brain.contracts.memory_item import MemoryItem, is_valid_memory_item_id
from agent_brain.memory.governance.lifecycle_ledger import lifecycle_transaction_lock
from agent_brain.memory.store import durable_fs
from agent_brain.memory.store.durable_fs import SecureDirectory
from agent_brain.memory.store.item_markdown import parse_item_markdown
from agent_brain.memory.store.items_store import ItemsStore

_HAS_DIR_FD_LINK = os.link in os.supports_dir_fd
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


def archive_reviewed_item(
    *,
    brain_dir: Path,
    items_store: ItemsStore,
    item_id: str,
    eligible: Callable[[MemoryItem], bool],
    index: Any = None,
) -> ArchiveTransactionResult:
    """Exclusively link a verified source into archive, then unlink source.

    A crash before the final unlink leaves source and target as the same inode;
    the next invocation recognizes that half-transaction and safely completes it.
    """
    if not is_valid_memory_item_id(item_id):
        return ArchiveTransactionResult("blocked", "INVALID_ITEM_ID")
    if not durable_fs.lifecycle_mutation_capability() or not _HAS_DIR_FD_LINK:
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

            item, _body = _parse_payload(payload)
            if item.id != item_id:
                return ArchiveTransactionResult("blocked", "ITEM_INVALID")
            if not eligible(item):
                return ArchiveTransactionResult(
                    "blocked", "NOT_IN_LIFECYCLE_REVIEW_QUEUE"
                )

            try:
                target = _read_snapshot(archive, source)[0]
            except FileNotFoundError:
                target = None
            except (OSError, ValueError, _ConcurrentModification):
                return ArchiveTransactionResult("blocked", "ARCHIVE_TARGET_EXISTS")

            if target is not None:
                if not _same_content(expected, target):
                    return ArchiveTransactionResult("blocked", "ARCHIVE_TARGET_EXISTS")
                try:
                    current = _current_snapshot(items, source)
                except (OSError, ValueError):
                    return ArchiveTransactionResult(
                        "blocked", "ARCHIVE_TARGET_EXISTS"
                    )
                if current is None or not _same_content(expected, current):
                    return ArchiveTransactionResult("blocked", "ARCHIVE_TARGET_EXISTS")
                return _finish_linked_archive(
                    items=items,
                    archive=archive,
                    source=source,
                    expected=expected,
                    index=index,
                )

            try:
                current = _current_snapshot(items, source)
            except (OSError, ValueError):
                return ArchiveTransactionResult(
                    "blocked", "CONCURRENT_MODIFICATION"
                )
            if current is None or not _same_prelink_snapshot(expected, current):
                return ArchiveTransactionResult("blocked", "CONCURRENT_MODIFICATION")
            try:
                os.link(
                    source,
                    source,
                    src_dir_fd=items.fd,
                    dst_dir_fd=archive.fd,
                    follow_symlinks=False,
                )
            except FileExistsError:
                return _recover_link_race(
                    items=items,
                    archive=archive,
                    source=source,
                    expected=expected,
                    index=index,
                )
            except OSError:
                return ArchiveTransactionResult("blocked", "ARCHIVE_FAILED")

            return _finish_linked_archive(
                items=items,
                archive=archive,
                source=source,
                expected=expected,
                index=index,
            )
    except FileNotFoundError:
        return ArchiveTransactionResult("blocked", "SOURCE_MISSING")
    except (OSError, UnicodeError, ValueError):
        return ArchiveTransactionResult("blocked", "ARCHIVE_FAILED")


def _finish_linked_archive(
    *,
    items: SecureDirectory,
    archive: SecureDirectory,
    source: str,
    expected: _FileSnapshot,
    index: Any,
) -> ArchiveTransactionResult:
    try:
        _fsync_regular_file(archive, source, expected)
        archive.fsync()
    except _ConcurrentModification:
        restored = _remove_target_if_source_is_safe(
            items=items,
            archive=archive,
            source=source,
        )
        return ArchiveTransactionResult(
            "blocked",
            "CONCURRENT_MODIFICATION" if restored else "ARCHIVE_ROLLBACK_FAILED",
        )
    except (OSError, ValueError):
        restored = _remove_target_if_source_is_safe(
            items=items,
            archive=archive,
            source=source,
        )
        return ArchiveTransactionResult(
            "blocked",
            "ARCHIVE_FAILED" if restored else "ARCHIVE_ROLLBACK_FAILED",
        )

    try:
        target = _current_snapshot(archive, source)
    except (OSError, ValueError):
        return ArchiveTransactionResult("partial", "ARCHIVE_TARGET_REPLACED")
    try:
        current = _current_snapshot(items, source)
    except (OSError, ValueError):
        return ArchiveTransactionResult("partial", "ARCHIVE_SOURCE_REPLACED")
    if target is None:
        return ArchiveTransactionResult("blocked", "ARCHIVE_TARGET_MISSING")
    if target.identity != expected.identity:
        return ArchiveTransactionResult("partial", "ARCHIVE_TARGET_REPLACED")
    if current is None:
        if _same_content(expected, target):
            return _applied_with_index(index, source.removesuffix(".md"))
        return ArchiveTransactionResult("partial", "ARCHIVE_CONTENT_CHANGED")
    if current.identity != expected.identity:
        if _same_content(expected, target):
            return ArchiveTransactionResult("partial", "ARCHIVE_SOURCE_REPLACED")
        return ArchiveTransactionResult("partial", "ARCHIVE_CONTENT_CHANGED")
    if not (_same_content(expected, current) and _same_content(expected, target)):
        restored = _remove_target_if_source_is_safe(
            items=items,
            archive=archive,
            source=source,
        )
        return ArchiveTransactionResult(
            "blocked",
            "CONCURRENT_MODIFICATION" if restored else "ARCHIVE_ROLLBACK_FAILED",
        )

    # Re-read both names immediately before unlink. The cooperative lifecycle
    # and item locks exclude supported writers; these checks reject external
    # rename/write races observed before the irreversible name removal.
    try:
        target = _current_snapshot(archive, source)
    except (OSError, ValueError):
        return ArchiveTransactionResult("partial", "ARCHIVE_TARGET_REPLACED")
    try:
        current = _current_snapshot(items, source)
    except (OSError, ValueError):
        return ArchiveTransactionResult("partial", "ARCHIVE_SOURCE_REPLACED")
    if target is None or current is None:
        if current is None and target is not None and _same_content(expected, target):
            return _applied_with_index(index, source.removesuffix(".md"))
        return ArchiveTransactionResult("partial", "ARCHIVE_CONTENT_CHANGED")
    if current.identity != expected.identity:
        return ArchiveTransactionResult("partial", "ARCHIVE_SOURCE_REPLACED")
    if not (_same_content(expected, current) and _same_content(expected, target)):
        return ArchiveTransactionResult("partial", "ARCHIVE_CONTENT_CHANGED")

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


def _recover_link_race(
    *,
    items: SecureDirectory,
    archive: SecureDirectory,
    source: str,
    expected: _FileSnapshot,
    index: Any,
) -> ArchiveTransactionResult:
    try:
        target = _current_snapshot(archive, source)
        current = _current_snapshot(items, source)
    except (OSError, ValueError):
        return ArchiveTransactionResult("blocked", "ARCHIVE_TARGET_EXISTS")
    if (
        target is not None
        and current is not None
        and _same_content(expected, target)
        and _same_content(expected, current)
    ):
        return _finish_linked_archive(
            items=items,
            archive=archive,
            source=source,
            expected=expected,
            index=index,
        )
    return ArchiveTransactionResult("blocked", "ARCHIVE_TARGET_EXISTS")


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
        _fsync_regular_file(archive, source, target)
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


def _remove_target_if_source_is_safe(
    *,
    items: SecureDirectory,
    archive: SecureDirectory,
    source: str,
) -> bool:
    """Remove the archive link only while the same inode still has an active name."""
    try:
        target = _read_snapshot(archive, source)[0]
        current = _read_snapshot(items, source)[0]
        if target.identity != current.identity:
            return False
        archive.unlink(source)
        archive.fsync()
        return True
    except (FileNotFoundError, OSError, ValueError, _ConcurrentModification):
        return False


def _fsync_regular_file(
    directory: SecureDirectory,
    name: str,
    expected: _FileSnapshot,
) -> None:
    descriptor, _ = directory.open_file(
        name,
        os.O_RDONLY | os.O_NONBLOCK,
    )
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError("ITEM_INVALID")
        if (opened.st_dev, opened.st_ino) != expected.identity:
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
    descriptor, _ = directory.open_file(
        name,
        os.O_RDONLY | os.O_NONBLOCK,
    )
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("ITEM_INVALID")
        if before.st_size > _MAX_ARCHIVE_ITEM_BYTES:
            raise ValueError("ITEM_TOO_LARGE")
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
        after = os.fstat(descriptor)
        if _read_stat_signature(before) != _read_stat_signature(after):
            raise _ConcurrentModification("ITEM_CHANGED_DURING_READ")
        if len(payload) != after.st_size:
            raise _ConcurrentModification("ITEM_SIZE_CHANGED_DURING_READ")
        return _snapshot(after, payload), payload
    finally:
        os.close(descriptor)


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


def _same_prelink_snapshot(left: _FileSnapshot, right: _FileSnapshot) -> bool:
    return _same_content(left, right) and left.changed_ns == right.changed_ns


def _same_content(left: _FileSnapshot, right: _FileSnapshot) -> bool:
    return (
        left.identity == right.identity
        and left.mode == right.mode
        and left.size == right.size
        and left.modified_ns == right.modified_ns
        and left.digest == right.digest
    )


def _parse_payload(payload: bytes) -> tuple[MemoryItem, str]:
    text = payload.decode("utf-8-sig")
    return cast(
        tuple[MemoryItem, str],
        parse_item_markdown(text.replace("\r\n", "\n").replace("\r", "\n")),
    )


__all__ = ["ArchiveTransactionResult", "archive_reviewed_item"]
