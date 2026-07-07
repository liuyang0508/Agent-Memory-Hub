"""Tests for GovernancePipeline."""

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

from agent_brain.memory.governance.pipeline import (
    GovernancePipeline,
)
from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Sensitivity, Refs


def _make_item(
    item_id: str = "mem-20260520-000001-test",
    title: str = "Test Item",
    summary: str = "This is a test summary for the item",
    item_type: MemoryType = MemoryType.fact,
    tags: list[str] | None = None,
    created_at: datetime | None = None,
    project: str | None = "test-project",
) -> MemoryItem:
    """Helper to create a MemoryItem for testing."""
    if tags is None:
        tags = ["test"]
    if created_at is None:
        created_at = datetime.now(timezone.utc)
    return MemoryItem(
        id=item_id,
        type=item_type,
        created_at=created_at,
        agent="test-agent",
        session="test-session",
        project=project,
        tags=tags,
        sensitivity=Sensitivity.internal,
        title=title,
        summary=summary,
        refs=Refs(),
    )


class TestGovernancePipeline:
    """Test suite for GovernancePipeline."""

    def test_duplicate_detector_is_split_and_delegated(self):
        from agent_brain.memory.governance.duplicates import detect_duplicates

        item1 = _make_item(
            item_id="mem-20260520-010001-test",
            title="Authentication Bug Fix",
            summary="Fixed authentication issue in login flow",
        )
        item2 = _make_item(
            item_id="mem-20260520-010002-test",
            title="Authentication Bug Fix",
            summary="Fixed authentication issue in login flow",
        )

        mock_store = MagicMock()
        mock_store.iter_all.return_value = [(item1, ""), (item2, "")]
        pipeline = GovernancePipeline(items_store=mock_store)
        items = [item for item, _body in mock_store.iter_all()]

        direct = detect_duplicates(items)
        delegated = pipeline._check_duplicates(items)

        assert [issue.item_id for issue in direct] == [item2.id]
        assert [issue.item_id for issue in delegated] == [item2.id]

    def test_detect_duplicate_items(self):
        """Test that near-duplicate items are detected."""
        # Create two items with nearly identical title+summary
        item1 = _make_item(
            item_id="mem-20260520-000001-test",
            title="Authentication Bug Fix",
            summary="Fixed authentication issue in login flow",
        )
        item2 = _make_item(
            item_id="mem-20260520-000002-test",
            title="Authentication Bug Fix",
            summary="Fixed authentication issue in login flow",
        )

        mock_store = MagicMock()
        mock_store.iter_all.return_value = [(item1, ""), (item2, "")]

        pipeline = GovernancePipeline(items_store=mock_store)
        report = pipeline.run()

        # Should detect one duplicate
        duplicates = [i for i in report.issues if i.issue_type == 'duplicate']
        assert len(duplicates) == 1
        assert duplicates[0].item_id in [item1.id, item2.id]

    def test_session_active_signals_do_not_count_as_duplicates(self):
        """Mechanical session-start signals should not dominate health duplicate counts."""
        item1 = _make_item(
            item_id="mem-20260518-120625-session-851178f1-active-2026-05-18-1206",
            title="Session 851178f1 active 2026-05-18 12:06",
            summary="session 第一次 turn 结束（已 dedupe，每 session 只写一次）；待 session 真正完成后 /remember 归档",
            item_type=MemoryType.signal,
            tags=["session-active", "needs-review", "auto-captured", "session-851178f1"],
            project=None,
        )
        item2 = _make_item(
            item_id="mem-20260518-120625-session-851178f1-active-2026-05-18-1206",
            title="Session 851178f1 active 2026-05-18 12:06",
            summary="session 第一次 turn 结束（已 dedupe，每 session 只写一次）；待 session 真正完成后 /remember 归档",
            item_type=MemoryType.signal,
            tags=["session-active", "needs-review", "auto-captured", "session-851178f1"],
            project=None,
        )

        mock_store = MagicMock()
        mock_store.iter_all.return_value = [(item1, ""), (item2, "")]

        report = GovernancePipeline(items_store=mock_store).run()

        duplicates = [i for i in report.issues if i.issue_type == "duplicate"]
        assert duplicates == []

    def test_detect_noise_empty_body(self):
        """Test that items with very short summary are flagged as noise."""
        item = _make_item(
            item_id="mem-20260520-000003-test",
            title="Short Summary Item",
            summary="Short",  # Less than 10 chars
        )

        mock_store = MagicMock()
        mock_store.iter_all.return_value = [(item, "")]

        pipeline = GovernancePipeline(items_store=mock_store)
        report = pipeline.run()

        noise_issues = [i for i in report.issues if i.issue_type == 'noise']
        assert len(noise_issues) == 1
        assert noise_issues[0].item_id == item.id

    def test_detect_expired_signal(self):
        """Test that expired signal items are detected."""
        # Create a signal item older than 30 days
        old_date = datetime.now(timezone.utc) - timedelta(days=45)
        item = _make_item(
            item_id="mem-20260520-000004-test",
            title="Old Signal",
            summary="This is an old signal item",
            item_type=MemoryType.signal,
            created_at=old_date,
        )

        mock_store = MagicMock()
        mock_store.iter_all.return_value = [(item, "")]

        pipeline = GovernancePipeline(items_store=mock_store)
        report = pipeline.run()

        expired_issues = [i for i in report.issues if i.issue_type == 'expired']
        assert len(expired_issues) == 1
        assert expired_issues[0].item_id == item.id

    def test_detect_low_quality_no_tags(self):
        """Test that items without tags are flagged as low quality."""
        item = _make_item(
            item_id="mem-20260520-000005-test",
            title="No Tags Item",
            summary="This item has no tags",
            tags=[],  # Empty tags
        )

        mock_store = MagicMock()
        mock_store.iter_all.return_value = [(item, "")]

        pipeline = GovernancePipeline(items_store=mock_store)
        report = pipeline.run()

        quality_issues = [i for i in report.issues if i.issue_type == 'low_quality']
        assert len(quality_issues) >= 1
        # At least one should be about missing tags
        no_tag_issues = [i for i in quality_issues if 'no tags' in i.description.lower()]
        assert len(no_tag_issues) >= 1

    def test_clean_items_pass(self):
        """Test that normal items pass all checks without issues."""
        item = _make_item(
            item_id="mem-20260520-000006-test",
            title="Good Quality Item",
            summary="This is a well-formed item with proper content",
            tags=["quality", "test"],
            project="test-project",
        )

        mock_store = MagicMock()
        mock_store.iter_all.return_value = [(item, "")]

        pipeline = GovernancePipeline(items_store=mock_store)
        report = pipeline.run()

        # Should have no issues for a clean item
        assert report.total_issues == 0
        assert report.healthy is True

    def test_report_summary_counts(self):
        """Test that report correctly counts issues by type."""
        # Create items that trigger different issue types
        old_signal = _make_item(
            item_id="mem-20260520-000007-test",
            title="Old Signal",
            summary="Old signal",
            item_type=MemoryType.signal,
            created_at=datetime.now(timezone.utc) - timedelta(days=45),
            tags=[],
        )

        mock_store = MagicMock()
        mock_store.iter_all.return_value = [(old_signal, "")]

        pipeline = GovernancePipeline(items_store=mock_store)
        report = pipeline.run()

        assert report.scanned_items == 1
        assert report.expired >= 1  # Old signal
        assert report.low_quality >= 1  # No tags
        assert report.total_issues == len(report.issues)
