from __future__ import annotations

from typing import Any

from agent_brain.observability import BrainStats, HealthScore


def build_stats_json_payload(stats: BrainStats) -> dict[str, Any]:
    return {
        "total_items": stats.total_items,
        "skipped": stats.skipped_count,
        "type_counts": stats.type_counts,
        "project_counts": stats.project_counts,
        "agent_counts": stats.agent_counts,
        "sensitivity_counts": stats.sensitivity_counts,
        "top_tags": stats.tag_counts,
        "oldest": stats.oldest.isoformat() if stats.oldest else None,
        "newest": stats.newest.isoformat() if stats.newest else None,
        "avg_body_length": round(stats.avg_body_length),
        "weekly_trend": stats.weekly_trend,
    }


def build_health_json_payload(score: HealthScore) -> dict[str, Any]:
    return {
        "grade": score.grade,
        "healthy": score.healthy,
        "total_items": score.total_items,
        "issue_rate": round(score.issue_rate, 3),
        "governance": {
            "total_issues": score.governance_issues,
            "duplicates": score.duplicates,
            "noise": score.noise,
            "expired": score.expired,
            "low_quality": score.low_quality,
        },
        "drift": {
            "total_findings": score.drift_findings,
            "contradictions": score.contradictions,
            "stale": score.stale,
            "citation_rot": score.citation_rot,
            "drift_clusters": score.drift_clusters,
        },
        "skipped_items": score.skipped_items,
    }
