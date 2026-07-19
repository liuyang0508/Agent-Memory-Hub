"""Descriptor-relative archive transaction for one reviewed memory item."""

from __future__ import annotations

import os
import stat
import uuid
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


@dataclass(frozen=True)
class ArchiveTransactionResult:
    status: str
    reason: str
    index_repair_attempted: bool = False
    index_repair_required: bool = False


def archive_reviewed_item(
    *,
    brain_dir: Path,
    items_store: ItemsStore,
    item_id: str,
    eligible: Callable[[MemoryItem], bool],
    index: Any = None,
) -> ArchiveTransactionResult:
    """Archive exactly the nofollow source that passed ``eligible``.

    The active source first moves to private staging, is checked against the
    already-open descriptor identity, then is published with an exclusive hard
    link so an existing archive target is never overwritten.
    """
    if not is_valid_memory_item_id(item_id):
        return ArchiveTransactionResult("blocked", "INVALID_ITEM_ID")
    if (
        not durable_fs.lifecycle_mutation_capability()
        or not _HAS_DIR_FD_LINK
    ):
        return ArchiveTransactionResult("blocked", "PLATFORM_UNSUPPORTED")

    source = f"{item_id}.md"
    stage_name = f"{uuid.uuid4().hex}.md"
    try:
        with (
            lifecycle_transaction_lock(brain_dir),
            items_store.locked_items([item_id]),
            SecureDirectory.open(items_store.items_dir) as items,
            items.child("archived", create=True) as archive,
            items.child(".amh-lifecycle-stage", create=True) as stage,
        ):
            descriptor, _ = items.open_file(source, os.O_RDONLY)
            try:
                opened = os.fstat(descriptor)
                if not stat.S_ISREG(opened.st_mode):
                    return ArchiveTransactionResult("blocked", "ITEM_INVALID")
                item, _body = _parse_descriptor(descriptor)
                if item.id != item_id:
                    return ArchiveTransactionResult("blocked", "ITEM_INVALID")
                if not eligible(item):
                    return ArchiveTransactionResult(
                        "blocked", "NOT_IN_LIFECYCLE_REVIEW_QUEUE"
                    )
                identity = (opened.st_dev, opened.st_ino)
                os.rename(
                    source,
                    stage_name,
                    src_dir_fd=items.fd,
                    dst_dir_fd=stage.fd,
                )
                staged = stage.stat(stage_name)
                staged_identity = (staged.st_dev, staged.st_ino)
                if staged_identity != identity:
                    restored = _restore_payload(
                        items=items,
                        archive=archive,
                        stage=stage,
                        source=source,
                        target=source,
                        stage_name=stage_name,
                        payload_identity=staged_identity,
                        target_linked=False,
                    )
                    return ArchiveTransactionResult(
                        "blocked",
                        "CONCURRENT_MODIFICATION"
                        if restored
                        else "ARCHIVE_ROLLBACK_FAILED",
                    )

                target_linked = False
                try:
                    os.link(
                        stage_name,
                        source,
                        src_dir_fd=stage.fd,
                        dst_dir_fd=archive.fd,
                        follow_symlinks=False,
                    )
                    target_linked = True
                    archived = archive.stat(source)
                    if (archived.st_dev, archived.st_ino) != identity:
                        raise OSError("ARCHIVE_IDENTITY_MISMATCH")
                    archive.fsync()
                    stage.unlink(stage_name)
                    stage.fsync()
                    items.fsync()
                except FileExistsError:
                    restored = _restore_payload(
                        items=items,
                        archive=archive,
                        stage=stage,
                        source=source,
                        target=source,
                        stage_name=stage_name,
                        payload_identity=identity,
                        target_linked=False,
                    )
                    return ArchiveTransactionResult(
                        "blocked",
                        "ARCHIVE_TARGET_EXISTS"
                        if restored
                        else "ARCHIVE_ROLLBACK_FAILED",
                    )
                except BaseException as archive_error:
                    restored = _restore_payload(
                        items=items,
                        archive=archive,
                        stage=stage,
                        source=source,
                        target=source,
                        stage_name=stage_name,
                        payload_identity=identity,
                        target_linked=target_linked,
                    )
                    if not isinstance(archive_error, Exception):
                        if not restored:
                            archive_error.add_note("ARCHIVE_ROLLBACK_FAILED")
                        raise
                    return ArchiveTransactionResult(
                        "blocked",
                        "ARCHIVE_FAILED" if restored else "ARCHIVE_ROLLBACK_FAILED",
                    )
            finally:
                os.close(descriptor)

            index_attempted = index is not None
            index_required = index is None
            if index is not None:
                try:
                    index.delete(item_id)
                except Exception:  # noqa: BLE001 - archived markdown is authoritative.
                    index_required = True
            return ArchiveTransactionResult(
                "applied",
                "OK",
                index_repair_attempted=index_attempted,
                index_repair_required=index_required,
            )
    except FileNotFoundError:
        return ArchiveTransactionResult("blocked", "SOURCE_MISSING")
    except (OSError, UnicodeError, ValueError):
        return ArchiveTransactionResult("blocked", "ARCHIVE_FAILED")


def _parse_descriptor(descriptor: int) -> tuple[MemoryItem, str]:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 65536)
        if not chunk:
            break
        chunks.append(chunk)
    text = b"".join(chunks).decode("utf-8-sig")
    return cast(
        tuple[MemoryItem, str],
        parse_item_markdown(text.replace("\r\n", "\n").replace("\r", "\n")),
    )


def _restore_payload(
    *,
    items: SecureDirectory,
    archive: SecureDirectory,
    stage: SecureDirectory,
    source: str,
    target: str,
    stage_name: str,
    payload_identity: tuple[int, int],
    target_linked: bool,
) -> bool:
    payload_directory = archive if target_linked else stage
    payload_name = target if target_linked else stage_name
    try:
        payload = payload_directory.stat(payload_name)
        if (payload.st_dev, payload.st_ino) != payload_identity:
            return False
        try:
            active = items.stat(source)
        except FileNotFoundError:
            active = None
        if active is None:
            os.link(
                payload_name,
                source,
                src_dir_fd=payload_directory.fd,
                dst_dir_fd=items.fd,
                follow_symlinks=False,
            )
            items.fsync()
        elif (active.st_dev, active.st_ino) != payload_identity:
            return False

        if target_linked:
            archive.unlink(target)
            archive.fsync()
        try:
            staged = stage.stat(stage_name)
        except FileNotFoundError:
            pass
        else:
            if (staged.st_dev, staged.st_ino) != payload_identity:
                return False
            stage.unlink(stage_name)
            stage.fsync()
        return True
    except (FileExistsError, FileNotFoundError, OSError):
        return False


__all__ = ["ArchiveTransactionResult", "archive_reviewed_item"]
