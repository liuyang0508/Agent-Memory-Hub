"""Tests for agent_brain.observability — stats collection and health scoring."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent_brain.observability import BrainStats, HealthScore, collect_stats
from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Sensitivity


def _make_item(
    *,
    idx: int = 0,
    type: str = "fact",
    project: str | None = "test-project",
    agent: str | None = "claude-code",
    tags: list[str] | None = None,
    days_ago: int = 0,
) -> tuple[MemoryItem, str]:
    now = datetime.now(timezone.utc) - timedelta(days=days_ago)
    item_id = f"mem-{now.strftime('%Y%m%d-%H%M%S')}-test-item-{idx}"
    item = MemoryItem(
        id=item_id,
        type=MemoryType(type),
        created_at=now,
        agent=agent,
        project=project,
        tags=tags or ["test"],
        sensitivity=Sensitivity.internal,
        title=f"Test item {idx}",
        summary=f"Summary for test item {idx}",
    )
    body = f"Body content for item {idx}. " * 5
    return item, body


class TestCollectStats:
    def test_empty_pool(self) -> None:
        stats = collect_stats([])
        assert stats.total_items == 0
        assert stats.type_counts == {}
        assert stats.oldest is None

    def test_basic_counts(self) -> None:
        items = [
            _make_item(idx=0, type="fact"),
            _make_item(idx=1, type="fact"),
            _make_item(idx=2, type="decision"),
            _make_item(idx=3, type="episode"),
        ]
        stats = collect_stats(items)
        assert stats.total_items == 4
        assert stats.type_counts["fact"] == 2
        assert stats.type_counts["decision"] == 1
        assert stats.type_counts["episode"] == 1

    def test_project_filter(self) -> None:
        items = [
            _make_item(idx=0, project="alpha"),
            _make_item(idx=1, project="beta"),
            _make_item(idx=2, project="alpha"),
        ]
        stats = collect_stats(items, project_filter="alpha")
        assert stats.total_items == 2

    def test_agent_counts(self) -> None:
        items = [
            _make_item(idx=0, agent="claude-code"),
            _make_item(idx=1, agent="codex"),
            _make_item(idx=2, agent="claude-code"),
        ]
        stats = collect_stats(items)
        assert stats.agent_counts["claude-code"] == 2
        assert stats.agent_counts["codex"] == 1

    def test_date_range(self) -> None:
        items = [
            _make_item(idx=0, days_ago=30),
            _make_item(idx=1, days_ago=10),
            _make_item(idx=2, days_ago=0),
        ]
        stats = collect_stats(items)
        assert stats.oldest is not None
        assert stats.newest is not None
        assert stats.oldest < stats.newest

    def test_avg_body_length(self) -> None:
        items = [_make_item(idx=0), _make_item(idx=1)]
        stats = collect_stats(items)
        assert stats.avg_body_length > 0

    def test_tag_counts(self) -> None:
        items = [
            _make_item(idx=0, tags=["arch", "decision"]),
            _make_item(idx=1, tags=["arch", "python"]),
        ]
        stats = collect_stats(items)
        assert stats.tag_counts["arch"] == 2
        assert stats.tag_counts["decision"] == 1

    def test_weekly_trend(self) -> None:
        items = [
            _make_item(idx=0, days_ago=0),
            _make_item(idx=1, days_ago=1),
            _make_item(idx=2, days_ago=8),
        ]
        stats = collect_stats(items)
        assert len(stats.weekly_trend) > 0

    def test_skipped_count_passthrough(self) -> None:
        stats = collect_stats([_make_item(idx=0)], skipped_count=3)
        assert stats.skipped_count == 3


class TestHealthScore:
    def test_grade_a_when_clean(self) -> None:
        score = HealthScore(total_items=100)
        assert score.grade == "A"
        assert score.healthy

    def test_grade_degrades_with_issues(self) -> None:
        score = HealthScore(total_items=100, items_with_issues=20)
        assert score.grade == "C"
        assert not score.healthy

    def test_grade_d_when_very_bad(self) -> None:
        score = HealthScore(total_items=100, items_with_issues=30)
        assert score.grade == "D"
        assert not score.healthy

    def test_drift_affects_grade(self) -> None:
        score = HealthScore(total_items=100, governance_issues=0, drift_findings=10)
        assert score.grade not in ("A", "A-")

    def test_issue_rate(self) -> None:
        score = HealthScore(total_items=200, items_with_issues=10)
        assert score.issue_rate == 0.05

    def test_issue_rate_zero_items(self) -> None:
        score = HealthScore(total_items=0, governance_issues=0)
        assert score.issue_rate == 0.0
