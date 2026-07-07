"""Tests for evolve/active_recall.py — proactive memory retrieval."""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from agent_brain.memory.governance.evolve.active_recall import ActiveRecall, RecallResult
from agent_brain.contracts.memory_item import MemoryItem, MemoryType


def _make_policy(suffix, gain=0.5, support=3, superseded=None):
    return MemoryItem(
        id=f"mem-20260601-100000-{suffix}",
        type=MemoryType.policy,
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        title=f"Policy {suffix}",
        summary=f"Summary for {suffix}",
        project="proj",
        tags=["test"],
        confidence=0.8,
        gain_score=gain,
        support_count=support,
        superseded_by=superseded,
    )


def _make_skill(suffix, gain=1.0, version=1):
    return MemoryItem(
        id=f"mem-20260601-100000-{suffix}",
        type=MemoryType.skill,
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        title=f"Skill {suffix}",
        summary=f"Skill summary {suffix}",
        project="proj",
        tags=["test"],
        confidence=0.9,
        gain_score=gain,
        support_count=10,
        version=version,
    )


def test_before_task_returns_sorted_by_active_score():
    mock_retriever = MagicMock()
    p1 = _make_policy("low", gain=0.1, support=2)
    p2 = _make_policy("high", gain=1.0, support=5)
    s1 = _make_skill("sk", gain=2.0)

    mock_retriever.search.return_value = [
        (p1, 0.8),
        (p2, 0.7),
        (s1, 0.6),
    ]

    recall = ActiveRecall(mock_retriever, top_k=3)
    result = recall.before_task("build an API endpoint")

    assert len(result.items) == 3
    # skill has highest active_score: 0.6 * (1+2.0) * 0.9 = 1.62
    # p2: 0.7 * (1+1.0) * 0.8 = 1.12
    # p1: 0.8 * (1+0.1) * 0.8 = 0.704
    assert result.items[0].id == s1.id
    assert result.items[1].id == p2.id
    assert result.items[2].id == p1.id


def test_superseded_items_excluded():
    mock_retriever = MagicMock()
    p_old = _make_policy("old", gain=5.0, superseded="mem-20260601-100000-new")
    p_new = _make_policy("new", gain=0.5)

    mock_retriever.search.return_value = [
        (p_old, 0.9),
        (p_new, 0.7),
    ]

    recall = ActiveRecall(mock_retriever, top_k=5)
    result = recall.before_task("test query")

    assert p_old not in result.items
    assert p_new in result.items


def test_low_gain_items_filtered():
    mock_retriever = MagicMock()
    p_bad = _make_policy("bad", gain=-1.0)
    p_good = _make_policy("good", gain=0.5)

    mock_retriever.search.return_value = [
        (p_bad, 0.9),
        (p_good, 0.7),
    ]

    recall = ActiveRecall(mock_retriever, top_k=5, min_gain=-0.5)
    result = recall.before_task("anything")

    assert p_bad not in result.items
    assert p_good in result.items


def test_format_context_empty():
    mock_retriever = MagicMock()
    recall = ActiveRecall(mock_retriever)
    result = RecallResult()
    assert recall.format_context(result) == ""


def test_format_context_renders_items():
    mock_retriever = MagicMock()
    recall = ActiveRecall(mock_retriever)
    p = _make_policy("x", gain=0.5, support=4)
    result = RecallResult(items=[p], scores=[1.0])
    text = recall.format_context(result)
    assert "POLICY" in text
    assert "Policy x" in text
    assert "support=4" in text
