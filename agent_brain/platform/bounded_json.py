"""Bounded JSON-object reads for untrusted derived sidecars."""

from __future__ import annotations

import json
import math
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from agent_brain.platform.bounded_jsonl import MAX_JSON_NESTING, MAX_SAFE_INTEGER
from agent_brain.platform.secure_io import (
    close_descriptor,
    open_directory_path_without_symlinks,
    open_regular_file_at,
    secure_dir_fd_io_supported,
)


MAX_JSON_OBJECT_BYTES = 256 * 1024
MAX_JSON_DIRECTORY_ENTRIES = 20_000
MAX_JSON_TOTAL_BYTES = 64 * 1024 * 1024


class BoundedJsonDirectory:
    """Read bounded JSON objects relative to one anchored directory."""

    def __init__(self, descriptor: int):
        self._descriptor: int | None = descriptor
        self._remaining_bytes = MAX_JSON_TOTAL_BYTES
        self._budget_exhausted = False

    @property
    def budget_exhausted(self) -> bool:
        """Return whether a read was rejected for exceeding the total budget."""

        return self._budget_exhausted

    def read_object(
        self,
        filename: str,
        *,
        max_bytes: int = MAX_JSON_OBJECT_BYTES,
    ) -> dict[str, Any] | None:
        """Return one safe JSON mapping, or ``None`` for any invalid file."""

        if (
            self._descriptor is None
            or self._budget_exhausted
            or type(max_bytes) is not int
            or max_bytes <= 0
        ):
            return None
        descriptor: int | None = None
        try:
            descriptor = open_regular_file_at(self._descriptor, filename)
            file_size = os.fstat(descriptor).st_size
            if file_size > max_bytes:
                return None
            if file_size > self._remaining_bytes:
                self._budget_exhausted = True
                return None
            read_limit = min(max_bytes, self._remaining_bytes)
            with os.fdopen(descriptor, "rb", buffering=0) as handle:
                descriptor = None
                raw = handle.read(read_limit + 1)
            if len(raw) > self._remaining_bytes:
                self._remaining_bytes = 0
                self._budget_exhausted = True
                return None
            self._remaining_bytes -= len(raw)
            if len(raw) > max_bytes:
                return None
            return _decode_json_object(raw)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
            OverflowError,
            RecursionError,
        ):
            return None
        finally:
            if descriptor is not None:
                close_descriptor(descriptor)

    def entry_names(self, *, max_entries: int | None = None) -> frozenset[str] | None:
        """Return bounded directory names, or ``None`` on error/overflow."""

        if self._descriptor is None:
            return None
        limit = MAX_JSON_DIRECTORY_ENTRIES if max_entries is None else max_entries
        if type(limit) is not int or limit < 0:
            return None
        names: set[str] = set()
        try:
            with os.scandir(self._descriptor) as entries:
                for count, entry in enumerate(entries, start=1):
                    if count > limit:
                        return None
                    if isinstance(entry.name, str):
                        names.add(entry.name)
        except OSError:
            return None
        return frozenset(names)

    def close(self) -> None:
        if self._descriptor is not None:
            close_descriptor(self._descriptor)
            self._descriptor = None


@contextmanager
def open_bounded_json_directory(
    path: Path,
) -> Iterator[BoundedJsonDirectory | None]:
    """Yield an anchored reader, failing closed when secure IO is unavailable."""

    if not secure_dir_fd_io_supported():
        yield None
        return
    try:
        descriptor = open_directory_path_without_symlinks(Path(path))
    except OSError:
        yield None
        return
    reader = BoundedJsonDirectory(descriptor)
    try:
        yield reader
    finally:
        reader.close()


def _decode_json_object(raw: bytes) -> dict[str, Any] | None:
    stripped = raw.strip()
    if not stripped:
        return None
    decoded = stripped.decode("utf-8")
    data = json.loads(
        decoded,
        parse_int=_parse_int,
        parse_float=_parse_float,
        parse_constant=_reject_constant,
    )
    if not isinstance(data, dict) or not _within_nesting_limit(data):
        return None
    return data


def _parse_int(value: str) -> int:
    parsed = int(value)
    if abs(parsed) > MAX_SAFE_INTEGER:
        raise ValueError("integer exceeds the JSON safe-number boundary")
    return parsed


def _parse_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or abs(parsed) > MAX_SAFE_INTEGER:
        raise ValueError("float exceeds the JSON safe-number boundary")
    return parsed


def _reject_constant(value: str) -> float:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _within_nesting_limit(value: object) -> bool:
    stack: list[tuple[object, int]] = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        if not isinstance(current, (dict, list)):
            continue
        if depth > MAX_JSON_NESTING:
            return False
        children = current.values() if isinstance(current, dict) else current
        stack.extend(
            (child, depth + 1)
            for child in children
            if isinstance(child, (dict, list))
        )
    return True


__all__ = [
    "BoundedJsonDirectory",
    "MAX_JSON_DIRECTORY_ENTRIES",
    "MAX_JSON_OBJECT_BYTES",
    "MAX_JSON_TOTAL_BYTES",
    "open_bounded_json_directory",
]
