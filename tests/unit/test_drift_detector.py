"""Tests for DriftDetector - M3 Anti-drift."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from agent_brain.memory.governance.drift import DriftDetector, DriftType
from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Refs


def test_drift_module_reexports_shared_types():
    from agent_brain.memory.governance import drift
    from agent_brain.memory.governance.drift_types import DriftFinding, DriftReport

    assert drift.DriftType is DriftType
    assert drift.DriftFinding is DriftFinding
    assert drift.DriftReport is DriftReport


def test_drift_module_reexports_citation_helpers(monkeypatch):
    from agent_brain.memory.governance import drift
    from agent_brain.memory.governance.drift_citations import URL_PATTERN, extract_urls

    assert drift.URL_PATTERN is URL_PATTERN
    assert extract_urls("see www.example.com and https://example.com/doc") == [
        "www.example.com",
        "https://example.com/doc",
    ]

    def fake_probe(urls, *, timeout):
        return [(url, f"timeout={timeout}") for url in urls]

    monkeypatch.setattr("agent_brain.memory.governance.drift_citations.probe_urls_for_rot", fake_probe)

    detector = DriftDetector(MockItemsStore([]), url_timeout=0.25)
    assert detector._probe_urls_for_rot(["https://example.com"]) == [
        ("https://example.com", "timeout=0.25"),
    ]


def test_staleness_detector_is_split_with_injected_now():
    from agent_brain.memory.governance.drift_staleness import detect_staleness

    now = datetime.now(timezone.utc)
    old = create_memory_item(
        item_id="mem-20260519-100000-old",
        mem_type=MemoryType.fact,
        created_at=now - timedelta(days=200),
        title="Old fact",
        summary="Old",
    )
    fresh = create_memory_item(
        item_id="mem-20260519-100000-fresh",
        mem_type=MemoryType.fact,
        created_at=now - timedelta(days=5),
        title="Fresh fact",
        summary="Fresh",
    )

    findings = detect_staleness([old, fresh], staleness_days=180, now=now)

    assert [finding.item_ids for finding in findings] == [[old.id]]
    assert "200 days old" in findings[0].description


def test_drift_cluster_detection_is_split():
    from agent_brain.memory.governance.drift_clusters import detect_drift_clusters

    items = [
        create_memory_item(
            item_id=f"mem-20250101-12000{i}-cluster-{i}",
            mem_type=MemoryType.decision,
            project="cluster-project",
            title=f"Decision {i}",
            tags=["cluster-topic"],
        )
        for i in range(4)
    ]
    small_project = create_memory_item(
        item_id="mem-20250101-120010-small",
        mem_type=MemoryType.fact,
        project="small-project",
        title="Small project item",
    )

    findings = detect_drift_clusters(items + [small_project])

    assert len(findings) == 1
    assert findings[0].drift_type == DriftType.DRIFT_CLUSTER
    assert findings[0].item_ids == [item.id for item in items]
    assert "cluster-project" in findings[0].description


def test_drift_cluster_requires_shared_topic_tag():
    from agent_brain.memory.governance.drift_clusters import detect_drift_clusters

    items = [
        create_memory_item(
            item_id=f"mem-20250101-12100{i}-different-topic-{i}",
            mem_type=MemoryType.decision,
            project="large-project",
            title=f"Decision {i}",
            tags=[f"topic-{i}"],
        )
        for i in range(5)
    ]

    findings = detect_drift_clusters(items)

    assert findings == []


def test_drift_cluster_collapses_overlapping_topic_tags():
    from agent_brain.memory.governance.drift_clusters import detect_drift_clusters

    items = [
        create_memory_item(
            item_id=f"mem-20250101-12200{i}-overlap-{i}",
            mem_type=MemoryType.decision,
            project="large-project",
            title=f"Decision {i}",
            tags=["shared-topic", *([] if i == 4 else ["secondary-topic"])],
        )
        for i in range(5)
    ]

    findings = detect_drift_clusters(items)

    assert len(findings) == 1
    assert findings[0].item_ids == [item.id for item in items]
    assert "shared-topic" in findings[0].evidence
    assert "secondary-topic" in findings[0].evidence


def test_drift_cluster_ignores_session_active_signals():
    from agent_brain.memory.governance.drift_clusters import detect_drift_clusters

    items = [
        create_memory_item(
            item_id=f"mem-20260518-12000{i}-session-{i}",
            mem_type=MemoryType.signal,
            project=None,
            title=f"Session {i:08x} active 2026-05-18 12:0{i}",
            summary="session 第一次 turn 结束（已 dedupe，每 session 只写一次）；待 session 真正完成后 /remember 归档",
            tags=["session-active", "needs-review", "auto-captured", f"session-{i:08x}"],
        )
        for i in range(6)
    ]

    findings = detect_drift_clusters(items)

    assert findings == []


class MockItemsStore:
    """Mock ItemsStore for testing."""

    def __init__(self, items: list[tuple[MemoryItem, str]]):
        self._items = items

    def iter_all(self) -> Any:
        """Yield (MemoryItem, body) tuples."""
        return iter(self._items)


def create_memory_item(
    item_id: str,
    mem_type: MemoryType = MemoryType.decision,
    created_at: datetime | None = None,
    project: str | None = "test-project",
    title: str = "Test Decision",
    summary: str = "Test summary",
    tags: list[str] | None = None,
) -> MemoryItem:
    """Helper to create a MemoryItem for testing."""
    if created_at is None:
        created_at = datetime.now(timezone.utc)
    if tags is None:
        tags = []

    return MemoryItem(
        id=item_id,
        type=mem_type,
        created_at=created_at,
        project=project,
        title=title,
        summary=summary,
        tags=tags,
        refs=Refs(),
    )


class TestDetectContradiction:
    """Test contradiction detection."""

    def test_contradiction_detection_is_split_and_reexported(self):
        from agent_brain.memory.governance import drift
        from agent_brain.memory.governance.drift_contradictions import detect_contradictions

        assert drift.detect_contradictions is detect_contradictions

    def test_detector_delegates_decision_pattern_extraction(self):
        """Decision pattern heuristics live outside the drift orchestration class."""
        from agent_brain.memory.governance.drift_patterns import DecisionPatternExtractor

        detector = DriftDetector(MockItemsStore([]))

        assert isinstance(detector.pattern_extractor, DecisionPatternExtractor)

    def test_detect_contradiction_same_project(self):
        """Two decisions on same topic with different choices should be detected."""
        now = datetime.now(timezone.utc)
        
        # Decision A: suggests using React
        item_a = create_memory_item(
            item_id="mem-20250101-120000-decision-a",
            mem_type=MemoryType.decision,
            created_at=now - timedelta(days=10),
            project="web-app",
            title="Frontend Framework Choice",
            tags=["frontend", "framework"],
        )
        body_a = "We decided to use React for the frontend framework."

        # Decision B: suggests using Vue (contradiction)
        item_b = create_memory_item(
            item_id="mem-20250101-120001-decision-b",
            mem_type=MemoryType.decision,
            created_at=now - timedelta(days=5),
            project="web-app",
            title="Frontend Framework Choice Updated",
            tags=["frontend", "framework"],
        )
        body_b = "After evaluation, we chose Vue instead of React."

        store = MockItemsStore([(item_a, body_a), (item_b, body_b)])
        detector = DriftDetector(store)
        report = detector.detect()

        assert report.contradictions >= 1
        contradiction_findings = [f for f in report.findings if f.drift_type == DriftType.CONTRADICTION]
        assert len(contradiction_findings) >= 1
        
        finding = contradiction_findings[0]
        assert item_a.id in finding.item_ids
        assert item_b.id in finding.item_ids

    def test_disjoint_topics_in_same_project_are_not_contradictions(self):
        """Different decision topics in one repo should not become pairwise conflicts."""
        now = datetime.now(timezone.utc)
        frontend = create_memory_item(
            item_id="mem-20250101-130000-frontend-decision",
            mem_type=MemoryType.decision,
            created_at=now - timedelta(days=10),
            project="web-app",
            title="Frontend framework choice",
            tags=["frontend", "framework"],
        )
        frontend_body = "We decided to use React for the frontend framework."
        database = create_memory_item(
            item_id="mem-20250101-130001-database-decision",
            mem_type=MemoryType.decision,
            created_at=now - timedelta(days=5),
            project="web-app",
            title="Database engine choice",
            tags=["database", "storage"],
        )
        database_body = "After evaluation, we chose Postgres instead of MySQL."

        store = MockItemsStore([(frontend, frontend_body), (database, database_body)])
        report = DriftDetector(store).detect()

        contradiction_findings = [
            finding for finding in report.findings
            if finding.drift_type == DriftType.CONTRADICTION
        ]
        assert contradiction_findings == []


class TestDetectStaleness:
    """Test staleness detection."""

    def test_detect_staleness(self):
        """Old items should be detected as stale."""
        old_date = datetime.now(timezone.utc) - timedelta(days=200)
        
        item = create_memory_item(
            item_id="mem-20240101-120000-old-item",
            mem_type=MemoryType.fact,
            created_at=old_date,
            project="legacy-system",
            title="Old Fact",
        )
        body = "This is an old fact."

        store = MockItemsStore([(item, body)])
        detector = DriftDetector(store, staleness_days=180)
        report = detector.detect()

        assert report.stale >= 1
        stale_findings = [f for f in report.findings if f.drift_type == DriftType.STALENESS]
        assert len(stale_findings) >= 1
        assert item.id in stale_findings[0].item_ids


class TestDetectCitationRot:
    """Test citation rotation detection."""

    def test_detect_citation_rot_old_urls(self):
        """Old items with URLs should be flagged for potential citation rot."""
        old_date = datetime.now(timezone.utc) - timedelta(days=100)
        
        item = create_memory_item(
            item_id="mem-20240101-120000-doc-link",
            mem_type=MemoryType.artifact,
            created_at=old_date,
            project="documentation",
            title="API Documentation Link",
        )
        body = "See the documentation at https://example.com/api/docs for more details."

        store = MockItemsStore([(item, body)])
        detector = DriftDetector(store)
        report = detector.detect()

        assert report.citation_rot >= 1
        rot_findings = [f for f in report.findings if f.drift_type == DriftType.CITATION_ROT]
        assert len(rot_findings) >= 1
        assert item.id in rot_findings[0].item_ids
        assert "https://example.com/api/docs" in rot_findings[0].evidence


class TestDetectDriftCluster:
    """Test drift cluster detection."""

    def test_detect_drift_cluster(self):
        """Projects with >3 items should be flagged as potential clusters."""
        now = datetime.now(timezone.utc)
        
        items = []
        for i in range(5):
            item = create_memory_item(
                item_id=f"mem-20250101-12000{i}-cluster-{i}",
                mem_type=MemoryType.decision,
                created_at=now - timedelta(days=i * 10),
                project="large-project",
                title=f"Decision {i}",
                tags=["cluster-topic"],
            )
            body = f"Body for decision {i}"
            items.append((item, body))

        store = MockItemsStore(items)
        detector = DriftDetector(store)
        report = detector.detect()

        assert report.drift_clusters >= 1
        cluster_findings = [f for f in report.findings if f.drift_type == DriftType.DRIFT_CLUSTER]
        assert len(cluster_findings) >= 1
        
        finding = cluster_findings[0]
        assert len(finding.item_ids) == 5


class TestCleanItems:
    """Test that clean items produce no findings."""

    def test_clean_items_no_drift(self):
        """Fresh, non-conflicting items should produce a clean report."""
        now = datetime.now(timezone.utc)
        
        item = create_memory_item(
            item_id="mem-20250501-120000-fresh-item",
            mem_type=MemoryType.fact,
            created_at=now - timedelta(days=10),
            project="current-project",
            title="Recent Fact",
        )
        body = "This is a recent fact with no issues."

        store = MockItemsStore([(item, body)])
        detector = DriftDetector(store, staleness_days=180)
        report = detector.detect()

        assert report.clean is True
        assert report.total_findings == 0
        assert report.scanned_items == 1
