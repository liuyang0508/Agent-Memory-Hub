"""Tests for semantic contradiction detection in DriftDetector."""
from __future__ import annotations

from datetime import datetime, timezone

from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.memory.governance.drift import DriftDetector, DriftType
from agent_brain.contracts.memory_item import MemoryItem, MemoryType


class MockItemsStore:
    def __init__(self, items: list[tuple[MemoryItem, str]]):
        self._items = items

    def iter_all(self):
        return iter(self._items)


def _item(suffix: str, project: str = "proj", **kw) -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260528-100000-{suffix}",
        type=kw.pop("type", MemoryType.decision),
        created_at=kw.pop("created_at", datetime.now(timezone.utc)),
        project=project,
        title=kw.pop("title", f"Decision {suffix}"),
        summary=kw.pop("summary", f"Summary {suffix}"),
        tags=kw.pop("tags", []),
    )


class TestSemanticContradiction:
    def test_heuristic_only_confidence_050(self):
        """Without embedder, contradiction confidence stays at 0.5."""
        a = _item("a", title="Framework Choice")
        b = _item("b", title="Framework Choice v2")
        store = MockItemsStore([
            (a, "We decided to use ReactRouter for the frontend framework."),
            (b, "We chose VueRouter for the frontend framework after review."),
        ])
        detector = DriftDetector(store, embedder=None)
        report = detector.detect()
        contradictions = [f for f in report.findings if f.drift_type == DriftType.CONTRADICTION]
        assert len(contradictions) >= 1
        assert contradictions[0].confidence == 0.5

    def test_semantic_boosts_confidence(self):
        """With embedder, heuristic contradiction + high similarity → confidence 0.8."""
        emb = HashingEmbedder(dim=8)
        a = _item("sem-a", title="Framework Choice")
        b = _item("sem-b", title="Framework Choice v2")
        store = MockItemsStore([
            (a, "We decided to use ReactRouter for the frontend framework."),
            (b, "We chose VueRouter for the frontend framework after review."),
        ])
        detector = DriftDetector(store, embedder=emb, semantic_threshold=0.0)
        report = detector.detect()
        contradictions = [f for f in report.findings if f.drift_type == DriftType.CONTRADICTION]
        assert len(contradictions) >= 1
        boosted = [c for c in contradictions if c.confidence == 0.8]
        assert len(boosted) >= 1
        assert "semantic sim=" in boosted[0].evidence

    def test_semantic_only_flags_similar_decisions(self):
        """High similarity without heuristic contradiction → confidence 0.6 advisory."""
        emb = HashingEmbedder(dim=8)
        a = _item("so-a", title="Database Choice")
        b = _item("so-b", title="Database Choice")
        store = MockItemsStore([
            (a, "We need a fast database with good indexing."),
            (b, "We need a reliable database with strong consistency."),
        ])
        detector = DriftDetector(store, embedder=emb, semantic_threshold=0.0)
        report = detector.detect()
        contradictions = [f for f in report.findings if f.drift_type == DriftType.CONTRADICTION]
        assert len(contradictions) >= 1
        advisory = [c for c in contradictions if c.confidence == 0.6]
        assert len(advisory) >= 1
        assert "Cosine similarity" in advisory[0].evidence

    def test_no_semantic_for_different_projects(self):
        """Items in different projects are not compared."""
        emb = HashingEmbedder(dim=8)
        a = _item("dp-a", project="proj-a", title="DB Choice")
        b = _item("dp-b", project="proj-b", title="DB Choice")
        store = MockItemsStore([
            (a, "We decided to use PostgreSQL."),
            (b, "We chose MySQL instead."),
        ])
        detector = DriftDetector(store, embedder=emb, semantic_threshold=0.0)
        report = detector.detect()
        contradictions = [f for f in report.findings if f.drift_type == DriftType.CONTRADICTION]
        assert len(contradictions) == 0

    def test_non_decision_items_skipped(self):
        """Only decision type items are checked for contradictions."""
        emb = HashingEmbedder(dim=8)
        a = _item("nd-a", type=MemoryType.fact, title="Fact A")
        b = _item("nd-b", type=MemoryType.fact, title="Fact B")
        store = MockItemsStore([
            (a, "We use React for the frontend."),
            (b, "We chose Vue for the frontend."),
        ])
        detector = DriftDetector(store, embedder=emb, semantic_threshold=0.0)
        report = detector.detect()
        contradictions = [f for f in report.findings if f.drift_type == DriftType.CONTRADICTION]
        assert len(contradictions) == 0

    def test_high_threshold_filters_low_similarity(self):
        """When threshold is high, low-similarity items are not flagged."""
        emb = HashingEmbedder(dim=8)
        a = _item("ht-a", title="Frontend Framework")
        b = _item("ht-b", title="Backend Infrastructure")
        store = MockItemsStore([
            (a, "Frontend uses React with TypeScript."),
            (b, "Backend infrastructure runs on Kubernetes with Istio."),
        ])
        detector = DriftDetector(store, embedder=emb, semantic_threshold=0.999)
        report = detector.detect()
        semantic_findings = [
            f for f in report.findings
            if f.drift_type == DriftType.CONTRADICTION and f.confidence == 0.6
        ]
        assert len(semantic_findings) == 0
