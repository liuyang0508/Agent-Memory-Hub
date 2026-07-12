"""Bounded, fail-closed JSONL reads for runtime sidecar ledgers."""

from __future__ import annotations

import json
import logging
import math
import os
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

MAX_JSONL_LINE_BYTES = 256 * 1024
MAX_JSONL_TOTAL_BYTES = 64 * 1024 * 1024
MAX_JSONL_TOTAL_ROWS = 20_000
MAX_JSON_NESTING = 64
MAX_SAFE_INTEGER = 2**53 - 1
_log = logging.getLogger(__name__)


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


def iter_bounded_jsonl(
    path: Path,
    *,
    max_line_bytes: int = MAX_JSONL_LINE_BYTES,
) -> Iterator[dict[str, Any]]:
    """Yield mapping rows without decoding unbounded or malformed lines."""

    source = Path(path)
    if type(max_line_bytes) is not int or max_line_bytes <= 0:
        return
    raw_file = _read_bounded_regular_file(source)
    if raw_file is None:
        return
    row_count = raw_file.count(b"\n")
    if raw_file and not raw_file.endswith(b"\n"):
        row_count += 1
    if row_count > MAX_JSONL_TOTAL_ROWS:
        _log.warning("reject JSONL file over total row budget in %s", source)
        return

    for raw in _iter_raw_lines(raw_file):
        if len(raw) > max_line_bytes:
            _log.warning("skip oversized JSONL row in %s", source)
            continue
        stripped = raw.strip()
        if not stripped:
            continue
        try:
            decoded = stripped.decode("utf-8")
            data = json.loads(
                decoded,
                parse_int=_parse_int,
                parse_float=_parse_float,
                parse_constant=_reject_constant,
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValueError,
            OverflowError,
            RecursionError,
        ) as exc:
            _log.warning(
                "skip invalid JSONL row in %s: %s",
                source,
                type(exc).__name__,
            )
            continue
        if not _within_nesting_limit(data):
            _log.warning("skip invalid JSONL row in %s: JSONNestingLimit", source)
            continue
        if isinstance(data, dict):
            yield data


def _read_bounded_regular_file(source: Path) -> bytes | None:
    descriptor: int | None = None
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        path_stat = source.lstat()
        if stat.S_ISLNK(path_stat.st_mode):
            _log.warning("reject symlinked JSONL file in %s", source)
            return None
        descriptor = os.open(source, flags)
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode):
            _log.warning("reject non-regular JSONL file in %s", source)
            return None
        current_path_stat = source.lstat()
        if (
            stat.S_ISLNK(current_path_stat.st_mode)
            or not os.path.samestat(path_stat, opened_stat)
            or not os.path.samestat(opened_stat, current_path_stat)
        ):
            _log.warning("reject replaced JSONL file in %s", source)
            return None
        if opened_stat.st_size > MAX_JSONL_TOTAL_BYTES:
            _log.warning("reject JSONL file over total byte budget in %s", source)
            return None
        with os.fdopen(descriptor, "rb", buffering=0) as handle:
            descriptor = None
            raw_file = handle.read(MAX_JSONL_TOTAL_BYTES + 1)
        if len(raw_file) > MAX_JSONL_TOTAL_BYTES:
            _log.warning("reject JSONL file over total byte budget in %s", source)
            return None
        return raw_file
    except FileNotFoundError:
        return None
    except OSError as exc:
        _log.warning("reject unreadable JSONL file in %s: %s", source, type(exc).__name__)
        return None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _iter_raw_lines(raw_file: bytes) -> Iterator[bytes]:
    start = 0
    while start < len(raw_file):
        newline = raw_file.find(b"\n", start)
        end = len(raw_file) if newline < 0 else newline + 1
        yield raw_file[start:end]
        start = end


__all__ = [
    "MAX_JSONL_LINE_BYTES",
    "MAX_JSONL_TOTAL_BYTES",
    "MAX_JSONL_TOTAL_ROWS",
    "MAX_JSON_NESTING",
    "MAX_SAFE_INTEGER",
    "iter_bounded_jsonl",
]
