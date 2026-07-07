from __future__ import annotations

from typing import Any

from agent_brain.memory.governance.drift_types import DriftReport
from agent_brain.memory.governance.pipeline_types import GovernanceReport


def build_health_detail_payload(
    *,
    total_items: int,
    skipped_items: int,
    gov_report: GovernanceReport | None,
    drift_report: DriftReport | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"total_items": total_items}

    if gov_report:
        unique_ids = {issue.item_id for issue in gov_report.issues}
        issue_rate = len(unique_ids) / max(gov_report.scanned_items, 1)
        if issue_rate <= 0.05:
            grade = "A"
        elif issue_rate <= 0.15:
            grade = "B"
        elif issue_rate <= 0.30:
            grade = "C"
        else:
            grade = "D"
        result["governance"] = {
            "scanned": gov_report.scanned_items,
            "total_issues": gov_report.total_issues,
            "duplicates": gov_report.duplicates,
            "noise": gov_report.noise,
            "expired": gov_report.expired,
            "low_quality": gov_report.low_quality,
            "healthy": gov_report.healthy,
            "issues": [
                {
                    "item_id": issue.item_id,
                    "issue_type": issue.issue_type,
                    "severity": issue.severity,
                    "description": issue.description,
                    "suggestion": issue.suggestion,
                }
                for issue in gov_report.issues[:50]
            ],
        }
    else:
        grade = "?"
        result["governance"] = None

    if drift_report:
        result["drift"] = {
            "scanned": drift_report.scanned_items,
            "total_findings": drift_report.total_findings,
            "contradictions": drift_report.contradictions,
            "stale": drift_report.stale,
            "citation_rot": drift_report.citation_rot,
            "drift_clusters": drift_report.drift_clusters,
            "clean": drift_report.clean,
            "findings": [
                {
                    "drift_type": str(finding.drift_type.value),
                    "item_ids": finding.item_ids[:5],
                    "confidence": round(finding.confidence, 2),
                    "description": finding.description,
                    "evidence": finding.evidence[:200],
                }
                for finding in drift_report.findings[:30]
            ],
        }
        if drift_report.total_findings > 0 and grade in ("A", "B"):
            grade = "B" if grade == "A" else "C"
    else:
        result["drift"] = None

    result["grade"] = grade
    result["healthy"] = grade in ("A", "B")
    result["skipped_items"] = skipped_items
    return result
