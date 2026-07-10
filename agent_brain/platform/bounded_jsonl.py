"""Bounded, fail-closed JSONL reads for runtime sidecar ledgers."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

MAX_JSONL_LINE_BYTES = 256 * 1024
_log = logging.getLogger(__name__)


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
                data = json.loads(decoded)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, OverflowError) as exc:
                _log.warning(
                    "skip invalid JSONL row in %s: %s",
                    source,
                    type(exc).__name__,
                )
                continue
            if isinstance(data, dict):
                yield data


__all__ = ["MAX_JSONL_LINE_BYTES", "iter_bounded_jsonl"]
