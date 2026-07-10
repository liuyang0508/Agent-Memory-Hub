"""Bounded, fail-closed JSONL reads for runtime sidecar ledgers."""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Iterator
from pathlib import Path
from typing import Any

MAX_JSONL_LINE_BYTES = 256 * 1024
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
    if not source.exists():
        return
    with source.open("rb") as fh:
        while True:
            raw = fh.readline(max_line_bytes + 1)
            if not raw:
                return
            if len(raw) > max_line_bytes:
                while raw and not raw.endswith(b"\n"):
                    raw = fh.readline(max_line_bytes + 1)
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


__all__ = [
    "MAX_JSONL_LINE_BYTES",
    "MAX_JSON_NESTING",
    "MAX_SAFE_INTEGER",
    "iter_bounded_jsonl",
]
