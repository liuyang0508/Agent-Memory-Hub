"""Statistics helpers for the local Python SDK."""
from __future__ import annotations

from typing import Any


def build_client_stats(store: Any) -> dict[str, Any]:
    """Build the SDK stats payload from an ItemsStore-like object."""
    from agent_brain.memory.governance.drift import DriftDetector
    from agent_brain.memory.governance.pipeline import GovernancePipeline
    from agent_brain.observability import HealthScore, collect_stats

    items = list(store.iter_all())
    s = collect_stats(items, skipped_count=store.last_scan.skipped_count)

    gov = GovernancePipeline(items_store=store)
    gov_report = gov.run()
    detector = DriftDetector(items_store=store)
    drift_report = detector.detect()

    issue_ids = {i.item_id for i in gov_report.issues} | {
        mid for f in drift_report.findings for mid in f.item_ids
    }

    health = HealthScore(
        total_items=s.total_items,
        governance_issues=gov_report.total_issues,
        duplicates=gov_report.duplicates,
        noise=gov_report.noise,
        expired=gov_report.expired,
        low_quality=gov_report.low_quality,
        drift_findings=drift_report.total_findings,
        contradictions=drift_report.contradictions,
        stale=drift_report.stale,
        citation_rot=drift_report.citation_rot,
        drift_clusters=drift_report.drift_clusters,
        skipped_items=s.skipped_count,
        items_with_issues=len(issue_ids),
    )

    return {
        "total_items": s.total_items,
        "health_grade": health.grade,
        "healthy": health.healthy,
        "type_counts": s.type_counts,
        "project_counts": s.project_counts,
    }


__all__ = ["build_client_stats"]
