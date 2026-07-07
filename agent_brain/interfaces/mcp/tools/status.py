"""MCP status tool implementations."""
from __future__ import annotations

from agent_brain.interfaces.mcp.tools._shared import *  # noqa: F401,F403


def brain_stats(
    project: str | None = None,
) -> dict[str, Any]:
    """Return statistics and health score for the brain pool.

    Includes item counts by type/project/agent, tag distribution,
    weekly activity trend, and a composite health grade (A through D).

    WHEN TO USE
    -----------
    Call this at session end OR whenever the user asks about brain health,
    coverage, or recent activity. A grade drop (A→B, B→C) is a strong signal
    to follow up with `drift_check` + `govern` to find what regressed.

    CHAIN
    -----
    If `health_grade` is C or D, immediately call `drift_check()` and
    `govern()` to surface concrete issues, then suggest fixes to the user.
    """
    from agent_brain.memory.governance.drift import DriftDetector
    from agent_brain.memory.governance.pipeline import GovernancePipeline
    from agent_brain.observability import HealthScore, collect_stats

    store, _, _ = _components()
    items = list(store.iter_all())
    stats = collect_stats(items, project_filter=project, skipped_count=store.last_scan.skipped_count)

    gov = GovernancePipeline(items_store=store)
    gov_report = gov.run()

    detector = DriftDetector(items_store=store)
    drift_report = detector.detect()

    issue_ids = {i.item_id for i in gov_report.issues} | {
        mid for f in drift_report.findings for mid in f.item_ids
    }

    health = HealthScore(
        total_items=stats.total_items,
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
        skipped_items=stats.skipped_count,
        items_with_issues=len(issue_ids),
    )

    return {
        "total_items": stats.total_items,
        "type_counts": stats.type_counts,
        "project_counts": stats.project_counts,
        "agent_counts": stats.agent_counts,
        "tag_counts": stats.tag_counts,
        "avg_body_length": round(stats.avg_body_length),
        "oldest": stats.oldest.isoformat() if stats.oldest else None,
        "newest": stats.newest.isoformat() if stats.newest else None,
        "weekly_trend": stats.weekly_trend,
        "health_grade": health.grade,
        "healthy": health.healthy,
        "issue_rate": round(health.issue_rate, 3),
    }


__all__ = ["brain_stats"]
