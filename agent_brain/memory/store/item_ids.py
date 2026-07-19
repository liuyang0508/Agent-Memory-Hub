"""Bounded discovery of observable MemoryItem metadata."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO

import yaml
from pydantic import ValidationError

from agent_brain.contracts.memory_item import MemoryItem, is_valid_memory_item_id
from agent_brain.memory.store.item_markdown import parse_item_markdown
from agent_brain.platform.secure_io import (
    close_descriptor,
    open_child_directory,
    open_directory_path_without_symlinks,
    open_regular_file_at,
    secure_dir_fd_io_supported,
)


MAX_OBSERVABILITY_FRONTMATTER_BYTES = 64 * 1024
MAX_OBSERVABILITY_STORE_ENTRIES = 20_000
MAX_OBSERVABILITY_TOTAL_BYTES = 64 * 1024 * 1024
MAX_OBSERVABILITY_DIRECTORY_DEPTH = 32

_OBSERVABLE_SENSITIVITIES = frozenset({"public", "internal"})
_OBSERVABLE_SCAN_GATE = threading.Lock()
_UTF8_BOM = b"\xef\xbb\xbf"


class _FrontmatterBudgetExceeded(Exception):
    """The current file exceeded its frontmatter byte budget."""


class _TotalReadBudgetExceeded(Exception):
    """The whole scan exceeded its frontmatter byte budget."""


def observable_memory_items(items_dir: Path) -> dict[str, MemoryItem]:
    """Return authorized active and archived item metadata without reading bodies.

    The scan is deliberately fail closed: only regular, non-symlink ``.md``
    files whose bounded frontmatter validates as a ``MemoryItem`` can authorize
    an ID. Ambiguous duplicate IDs are omitted, while a global resource-budget
    breach invalidates the entire result.
    """

    # Returning no authorization is the only safe downgrade on platforms that
    # cannot anchor both directory traversal and file opens to descriptors.
    if not secure_dir_fd_io_supported():
        return {}
    if not _OBSERVABLE_SCAN_GATE.acquire(blocking=False):
        return {}
    try:
        return _observable_memory_items(Path(items_dir))
    finally:
        _OBSERVABLE_SCAN_GATE.release()


def _observable_memory_items(items_dir: Path) -> dict[str, MemoryItem]:
    """Scan one store while the process-wide nonblocking gate is held."""

    observable: dict[str, MemoryItem] = {}
    seen_ids: set[str] = set()
    duplicate_ids: set[str] = set()
    scan_stack: list[tuple[int, Any, int]] = []
    entry_count = 0
    total_bytes = 0

    root_descriptor: int | None = None
    try:
        root_descriptor = open_directory_path_without_symlinks(Path(items_dir))
        root_entries = os.scandir(root_descriptor)
        scan_stack.append((root_descriptor, root_entries, 0))
        root_descriptor = None
    except OSError:
        return {}
    finally:
        if root_descriptor is not None:
            close_descriptor(root_descriptor)

    try:
        while scan_stack:
            directory_descriptor, entries, depth = scan_stack[-1]
            try:
                entry = next(entries)
            except StopIteration:
                _close_scan_frame(scan_stack.pop())
                continue
            except OSError:
                # A directory that cannot be scanned is not a trustworthy
                # authorization source, so invalidate the whole observation.
                return {}

            if depth == 0 and entry.name == ".amh-item-locks":
                continue
            if entry.name == "runtime" and _is_item_lock_runtime_tree(
                directory_descriptor, entry.name
            ):
                continue
            entry_count += 1
            if entry_count > MAX_OBSERVABILITY_STORE_ENTRIES:
                return {}
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if depth >= MAX_OBSERVABILITY_DIRECTORY_DEPTH:
                        return {}
                    child_descriptor = open_child_directory(
                        directory_descriptor,
                        entry.name,
                    )
                    try:
                        child_entries = os.scandir(child_descriptor)
                    except OSError:
                        close_descriptor(child_descriptor)
                        return {}
                    except BaseException:
                        close_descriptor(child_descriptor)
                        raise
                    scan_stack.append((child_descriptor, child_entries, depth + 1))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
            except OSError:
                continue

            filename = entry.name
            path = Path(filename)
            if path.suffix != ".md" or not is_valid_memory_item_id(path.stem):
                continue

            item_id = path.stem
            if item_id in seen_ids:
                duplicate_ids.add(item_id)
                observable.pop(item_id, None)
            else:
                seen_ids.add(item_id)

            item, bytes_read, total_exceeded = _read_observable_item(
                directory_descriptor,
                filename,
                item_id=item_id,
                total_bytes_remaining=(
                    MAX_OBSERVABILITY_TOTAL_BYTES - total_bytes
                ),
            )
            total_bytes += bytes_read
            if total_exceeded:
                return {}
            if item is None or item_id in duplicate_ids:
                continue
            observable[item_id] = item
    finally:
        while scan_stack:
            _close_scan_frame(scan_stack.pop())

    return observable


def _is_item_lock_runtime_tree(directory_descriptor: int, name: str) -> bool:
    """Recognize the reserved ``runtime/locks/items`` tree without traversing it."""

    opened: list[int] = []
    try:
        current = open_child_directory(directory_descriptor, name)
        opened.append(current)
        for component in ("locks", "items"):
            current = open_child_directory(current, component)
            opened.append(current)
        return True
    except OSError:
        return False
    finally:
        for descriptor in reversed(opened):
            close_descriptor(descriptor)


def known_memory_item_ids(items_dir: Path) -> frozenset[str]:
    """Return IDs authorized by bounded, sensitivity-aware frontmatter."""

    return frozenset(observable_memory_items(items_dir))


def _read_observable_item(
    directory_descriptor: int,
    filename: str,
    *,
    item_id: str,
    total_bytes_remaining: int,
) -> tuple[MemoryItem | None, int, bool]:
    """Read and validate one frontmatter block, returning scan budget usage."""

    bytes_read = 0
    frontmatter_lines: list[bytes] = []

    def read_line(handle: BinaryIO) -> bytes:
        nonlocal bytes_read
        file_remaining = MAX_OBSERVABILITY_FRONTMATTER_BYTES - bytes_read
        total_remaining = total_bytes_remaining - bytes_read
        read_limit = max(1, min(file_remaining, total_remaining) + 1)
        line = handle.readline(read_limit)
        bytes_read += len(line)
        if bytes_read > total_bytes_remaining:
            raise _TotalReadBudgetExceeded
        if bytes_read > MAX_OBSERVABILITY_FRONTMATTER_BYTES:
            raise _FrontmatterBudgetExceeded
        return line

    try:
        with _open_regular_binary(directory_descriptor, filename) as handle:
            opening_line = read_line(handle)
            if not _is_frontmatter_delimiter(opening_line, allow_bom=True):
                return None, bytes_read, False

            while True:
                line = read_line(handle)
                if not line:
                    return None, bytes_read, False
                if _is_frontmatter_delimiter(line):
                    break
                frontmatter_lines.append(line)

        frontmatter = b"---\n" + b"".join(frontmatter_lines) + b"---\n"
        item, _body = parse_item_markdown(frontmatter.decode("utf-8"))
    except _TotalReadBudgetExceeded:
        return None, bytes_read, True
    except (
        _FrontmatterBudgetExceeded,
        OSError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
        OverflowError,
        ValidationError,
        yaml.YAMLError,
    ):
        return None, bytes_read, False

    sensitivity = getattr(item.sensitivity, "value", item.sensitivity)
    if item.id != item_id or sensitivity not in _OBSERVABLE_SENSITIVITIES:
        return None, bytes_read, False
    return item, bytes_read, False


def _is_frontmatter_delimiter(line: bytes, *, allow_bom: bool = False) -> bool:
    value = line.rstrip(b"\r\n")
    if allow_bom and value.startswith(_UTF8_BOM):
        value = value[len(_UTF8_BOM) :]
    return value == b"---"


@contextmanager
def _open_regular_binary(
    directory_descriptor: int,
    filename: str,
) -> Iterator[BinaryIO]:
    """Open a regular item relative to an anchored directory descriptor."""

    descriptor: int | None = open_regular_file_at(directory_descriptor, filename)
    try:
        handle = os.fdopen(descriptor, "rb", buffering=0)
        descriptor = None
        with handle:
            yield handle
    finally:
        if descriptor is not None:
            close_descriptor(descriptor)


def _close_scan_frame(frame: tuple[int, Any, int]) -> None:
    descriptor, entries, _depth = frame
    try:
        entries.close()
    except OSError:
        pass
    finally:
        close_descriptor(descriptor)


__all__ = ["known_memory_item_ids", "observable_memory_items"]
