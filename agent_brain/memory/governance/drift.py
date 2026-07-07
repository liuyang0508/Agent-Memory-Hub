"""Drift detection for memory items - M3 Anti-drift."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from agent_brain.memory.governance.drift_citations import URL_PATTERN, extract_urls
from agent_brain.memory.governance.drift_clusters import detect_drift_clusters
from agent_brain.memory.governance.drift_contradictions import detect_contradictions
from agent_brain.memory.governance.drift_patterns import DecisionPatternExtractor
from agent_brain.memory.governance.drift_staleness import detect_staleness
from agent_brain.memory.governance.drift_types import DriftFinding, DriftReport, DriftType


class DriftDetector:
    """Detect drift in memory items."""

    def __init__(
        self,
        items_store: Any,
        staleness_days: int = 180,
        check_urls: bool = False,
        url_timeout: float = 5.0,
        embedder: Any = None,
        semantic_threshold: float = 0.8,
    ):
        self.items_store = items_store
        self.staleness_days = staleness_days
        # When True, _detect_citation_rot performs real HTTP HEAD requests
        # against each URL found in old items. Default off — offline-safe and
        # fast — because a brain pool spanning hundreds of URLs makes the
        # check minute-scale even on a fast network, and proxy-restricted
        # environments may block the requests outright.
        self.check_urls = check_urls
        self.url_timeout = url_timeout
        self.embedder = embedder
        self.semantic_threshold = semantic_threshold
        self.pattern_extractor = DecisionPatternExtractor()

    def detect(self) -> DriftReport:
        """Run all drift detection checks."""
        # iter_all returns tuples of (MemoryItem, body)
        items_with_bodies = list(self.items_store.iter_all())
        items = [item for item, body in items_with_bodies]
        report = DriftReport(scanned_items=len(items))

        contradictions = self._detect_contradictions(items_with_bodies)
        staleness = self._detect_staleness(items)
        citation_rot = self._detect_citation_rot(items_with_bodies)
        drift_clusters = self._detect_drift_clusters(items)

        report.findings.extend(contradictions)
        report.findings.extend(staleness)
        report.findings.extend(citation_rot)
        report.findings.extend(drift_clusters)

        report.contradictions = len(contradictions)
        report.stale = len(staleness)
        report.citation_rot = len(citation_rot)
        report.drift_clusters = len(drift_clusters)
        if getattr(self, "confidence_feedback", False):
            from agent_brain.memory.governance.feedback import apply_contradiction_feedback
            apply_contradiction_feedback(report, getattr(self, "index", None), self.items_store, getattr(self, "contradiction_penalty", 0.15), getattr(self, "supersede_penalty", 0.15))
        return report

    def _detect_contradictions(self, items_with_bodies: list) -> list[DriftFinding]:
        """Find items with same tags/project but contradicting decisions.

        Two layers:
          1. Heuristic: keyword extraction for 'use X' / 'chose Y' patterns
             with tool-name set comparison (confidence 0.5).
          2. Semantic (when embedder is provided): cosine similarity between
             item texts. High-similarity pairs with heuristic contradiction
             get boosted confidence (0.8). High-similarity pairs without
             heuristic contradiction are flagged as potential conflicts (0.6).
        """
        return detect_contradictions(
            items_with_bodies,
            pattern_extractor=self.pattern_extractor,
            embedder=self.embedder,
            semantic_threshold=self.semantic_threshold,
        )

    def _detect_staleness(self, items: list) -> list[DriftFinding]:
        """Find items that are older than staleness_days."""
        return detect_staleness(items, staleness_days=self.staleness_days)

    def _detect_citation_rot(self, items_with_bodies: list) -> list[DriftFinding]:
        """Find items whose URLs are likely broken.

        Two modes:
          - check_urls=False (default): age-based heuristic. Items older than
            90 days containing URLs are flagged at confidence 0.4 (advisory).
          - check_urls=True: real HTTP HEAD against each URL with self.url_timeout.
            Only 4xx/5xx/network-error URLs are flagged, confidence 0.95.
        """
        findings: list[DriftFinding] = []
        ninety_days_ago = datetime.now(timezone.utc) - timedelta(days=90)

        for item, body in items_with_bodies:
            urls = extract_urls(body)
            if not urls:
                continue

            if self.check_urls:
                broken = self._probe_urls_for_rot(urls)
                if broken:
                    findings.append(DriftFinding(
                        drift_type=DriftType.CITATION_ROT,
                        item_ids=[item.id],
                        confidence=0.95,
                        description=(
                            f"Item has {len(broken)} of {len(urls)} URL(s) "
                            f"returning error or unreachable"
                        ),
                        evidence="; ".join(f"{u} → {reason}" for u, reason in broken[:3]),
                    ))
            else:
                if item.created_at < ninety_days_ago:
                    age_days = (datetime.now(timezone.utc) - item.created_at).days
                    findings.append(DriftFinding(
                        drift_type=DriftType.CITATION_ROT,
                        item_ids=[item.id],
                        confidence=0.4,
                        description=(
                            f"Item contains {len(urls)} URL(s) and is "
                            f"{age_days} days old (not verified — pass "
                            f"check_urls=True to probe)"
                        ),
                        evidence=f"URLs: {', '.join(urls[:3])}",
                    ))

        return findings

    def _probe_urls_for_rot(self, urls: list[str]) -> list[tuple[str, str]]:
        """HTTP HEAD each URL. Returns list of (url, reason) for ones that
        responded 4xx/5xx or failed to connect. Uses stdlib urllib so no
        new runtime dep beyond what's already imported."""
        from agent_brain.memory.governance import drift_citations

        return drift_citations.probe_urls_for_rot(urls, timeout=self.url_timeout)

    def _detect_drift_clusters(self, items: list) -> list[DriftFinding]:
        """Find groups of items on same topic that evolved over time (>3 items same project+similar tags).
        
        These might need consolidation.
        """
        return detect_drift_clusters(items)
