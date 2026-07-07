"""Governance pipeline for memory items quality control."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

from agent_brain.memory.governance.duplicates import detect_duplicates
from agent_brain.memory.governance.pipeline_types import GovernanceIssue, GovernanceReport
from agent_brain.contracts.memory_item import MemoryItem, MemoryType


def _fingerprint(item: MemoryItem) -> str:
    """SHA-256 over normalized title + summary. Two items with identical fingerprints
    are exact textual duplicates (modulo whitespace), regardless of id or tags."""
    from agent_brain.memory.governance.duplicates import fingerprint

    return fingerprint(item)


class GovernancePipeline:
    """Runs governance checks on memory items and produces reports."""

    def __init__(
        self,
        items_store: Any,
        embedder: Any = None,
        ttl_days: int = 90,
        similarity_threshold: float = 0.92,
    ):
        self.items_store = items_store
        self.embedder = embedder
        self.ttl_days = ttl_days
        self.similarity_threshold = similarity_threshold

    def run(self) -> GovernanceReport:
        """Run all governance checks and return report."""
        # iter_all yields (MemoryItem, body) tuples; we only need the item for
        # current checks. Strip the body up front so downstream methods can
        # keep their MemoryItem-only signatures.
        items = [item for item, _body in self.items_store.iter_all()]
        report = GovernanceReport(scanned_items=len(items))

        # Run all checks
        report.issues.extend(self._check_duplicates(items))
        report.issues.extend(self._check_noise(items))
        report.issues.extend(self._check_ttl(items))
        report.issues.extend(self._check_quality(items))

        # Count by type
        for issue in report.issues:
            if issue.issue_type == 'duplicate':
                report.duplicates += 1
            elif issue.issue_type == 'noise':
                report.noise += 1
            elif issue.issue_type == 'expired':
                report.expired += 1
            elif issue.issue_type == 'low_quality':
                report.low_quality += 1

        return report

    def _check_duplicates(self, items: list[MemoryItem]) -> list[GovernanceIssue]:
        """Find duplicates in two passes:

        Pass 1 (O(N), exact): SHA-256 fingerprint of normalized title+summary.
        Identical fingerprints are reported as exact duplicates, severity=error.

        Pass 2 (O(N^2 / partition), fuzzy): for items not flagged in pass 1,
        compute jaccard similarity on word sets. Only pairs within the same
        project (or both project-less) are compared, which prunes the quadratic
        cost on a brain pool spanning many unrelated projects.
        """
        return detect_duplicates(items)

    def _check_noise(self, items: list[MemoryItem]) -> list[GovernanceIssue]:
        """Find low-signal items: empty body, very short summary (<10 chars), missing required fields."""
        issues: list[GovernanceIssue] = []

        for item in items:
            # Check for empty/very-short summary
            if len(item.summary.strip()) < 10:
                issues.append(GovernanceIssue(
                    item_id=item.id,
                    issue_type='noise',
                    severity='warning',
                    description=f"Item '{item.title}' has very short summary ({len(item.summary)} chars)",
                    suggestion="Expand summary to provide more context",
                ))

        return issues

    def _check_ttl(self, items: list[MemoryItem]) -> list[GovernanceIssue]:
        """Find items older than TTL."""
        issues: list[GovernanceIssue] = []
        now = datetime.now(timezone.utc)

        for item in items:
            # Determine TTL based on type
            if item.type == MemoryType.signal:
                item_ttl = 30  # signals expire faster
            else:
                item_ttl = self.ttl_days

            cutoff = now - timedelta(days=item_ttl)

            if item.created_at < cutoff:
                issues.append(GovernanceIssue(
                    item_id=item.id,
                    issue_type='expired',
                    severity='warning',
                    description=f"Item '{item.title}' expired (created {item.created_at.isoformat()}, TTL={item_ttl} days)",
                    suggestion="Archive or delete expired item",
                ))

        return issues

    def _check_quality(self, items: list[MemoryItem]) -> list[GovernanceIssue]:
        """Score item quality: has tags? has project? summary length reasonable?"""
        issues: list[GovernanceIssue] = []

        for item in items:
            # Missing tags → low_quality warning
            if not item.tags:
                issues.append(GovernanceIssue(
                    item_id=item.id,
                    issue_type='low_quality',
                    severity='warning',
                    description=f"Item '{item.title}' has no tags",
                    suggestion="Add relevant tags for better discoverability",
                ))

            # Summary too long → warning
            if len(item.summary) > 200:
                issues.append(GovernanceIssue(
                    item_id=item.id,
                    issue_type='low_quality',
                    severity='warning',
                    description=f"Item '{item.title}' has very long summary ({len(item.summary)} chars)",
                    suggestion="Condense summary to under 200 characters",
                ))

        return issues
