"""Fail-closed identity and timestamp helpers for public telemetry."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any


_OBSERVATION_ID_PATTERNS = {
    prefix: re.compile(
        rf"^{prefix}-\d{{8}}T\d{{6}}(?:[+-]\d{{4}})?-[0-9a-f]{{8}}$"
    )
    for prefix in ("gap", "out")
}
_OPAQUE_TASK_ID_PATTERN = re.compile(r"^task-observed-[0-9a-f]{16}$")


def parse_utc_timestamp(value: object) -> datetime | None:
    """Parse an ISO timestamp without allowing UTC conversion overflow."""

    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def sanitize_observation_id(value: object, *, prefix: str) -> str:
    """Keep canonical runtime IDs and hash every legacy or malformed value."""

    pattern = _OBSERVATION_ID_PATTERNS.get(prefix)
    if pattern is None:
        raise ValueError(f"unsupported observation id prefix: {prefix}")
    if isinstance(value, str) and pattern.fullmatch(value):
        return value
    return f"{prefix}-invalid-{_stable_digest(value)}"


def sanitize_task_id(value: object) -> str:
    """Expose task correlation as an opaque stable identifier only."""

    if isinstance(value, str) and _OPAQUE_TASK_ID_PATTERN.fullmatch(value):
        return value
    return f"task-observed-{_stable_digest(value)}"


def sanitize_session_id(value: object) -> str | None:
    """Preserve bounded textual session IDs; reject non-string identities."""

    return sanitize_optional_text(value, max_length=256, surrogate_prefix="session")


def sanitize_cwd(value: object) -> str | None:
    """Preserve bounded textual working directories without coercing objects."""

    return sanitize_optional_text(value, max_length=4096, surrogate_prefix="cwd")


def sanitize_optional_text(
    value: object,
    *,
    max_length: int,
    surrogate_prefix: str,
) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        return None
    if len(value) <= max_length and not any(ord(character) < 32 for character in value):
        return value
    return f"{surrogate_prefix}-invalid-{_stable_digest(value)}"


def _stable_digest(value: Any) -> str:
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, OverflowError, RecursionError):
        serialized = f"<{type(value).__module__}.{type(value).__qualname__}>"
    return hashlib.sha256(serialized.encode("utf-8", errors="replace")).hexdigest()[:16]


__all__ = [
    "parse_utc_timestamp",
    "sanitize_cwd",
    "sanitize_observation_id",
    "sanitize_optional_text",
    "sanitize_session_id",
    "sanitize_task_id",
]
