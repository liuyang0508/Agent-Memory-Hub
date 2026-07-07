"""P0-1: created_at must always be tz-aware UTC.

Root cause behind 6 separate crash paths (drift staleness, governance TTL,
cli gc/list-recent, mcp list_recent/gc/stats, hermes sort) — they all compare
item.created_at against a tz-aware `datetime.now(timezone.utc)`. A single
naive created_at (hand-authored md, Obsidian import, date-only string) raises
`TypeError: can't compare offset-naive and offset-aware datetimes`.

The fix: normalize created_at to tz-aware UTC at the schema boundary.
"""
from datetime import datetime, timezone

import pytest


def _item(created_at):
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType

    return MemoryItem(
        id="mem-20260519-100000-tz",
        type=MemoryType.fact,
        created_at=created_at,
        title="tz",
        summary="tz",
    )


def test_naive_string_coerced_to_aware():
    item = _item("2024-01-01")
    assert item.created_at.tzinfo is not None


def test_naive_datetime_coerced_to_aware():
    item = _item(datetime(2024, 1, 1, 12, 0, 0))
    assert item.created_at.tzinfo is not None


def test_aware_datetime_preserved():
    # Already tz-aware: leave the offset untouched (minimal, non-rewriting fix).
    item = _item(datetime.fromisoformat("2026-05-19T12:00:00+08:00"))
    assert item.created_at.tzinfo is not None
    assert item.created_at.utcoffset().total_seconds() == 8 * 3600


def test_naive_created_at_comparable_with_aware_now():
    """The actual downstream crash: drift/gc compare against aware now()."""
    item = _item("2024-01-01")
    now = datetime.now(timezone.utc)
    # Must not raise TypeError (offset-naive vs offset-aware).
    assert item.created_at < now
