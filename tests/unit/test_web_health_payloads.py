from __future__ import annotations

from agent_brain.memory.governance.drift_types import DriftFinding, DriftReport, DriftType
from agent_brain.memory.governance.pipeline_types import GovernanceIssue, GovernanceReport


def test_build_health_detail_payload_formats_governance_and_drift_sections() -> None:
    from web.health_payloads import build_health_detail_payload

    gov_report = GovernanceReport(
        scanned_items=10,
        issues=[
            GovernanceIssue(
                item_id="mem-20260610-100000-a",
                issue_type="duplicate",
                severity="warning",
                description="duplicate item",
                suggestion="merge",
            )
        ],
        duplicates=1,
    )
    drift_report = DriftReport(
        scanned_items=10,
        findings=[
            DriftFinding(
                drift_type=DriftType.STALENESS,
                item_ids=["a", "b", "c", "d", "e", "f"],
                confidence=0.876,
                description="stale",
                evidence="x" * 250,
            )
        ],
        stale=1,
    )

    payload = build_health_detail_payload(
        total_items=10,
        skipped_items=2,
        gov_report=gov_report,
        drift_report=drift_report,
    )

    assert payload["total_items"] == 10
    assert payload["grade"] == "C"
    assert payload["healthy"] is False
    assert payload["skipped_items"] == 2
    assert payload["governance"]["total_issues"] == 1
    assert payload["governance"]["issues"][0]["issue_type"] == "duplicate"
    assert payload["drift"]["total_findings"] == 1
    assert payload["drift"]["findings"][0]["confidence"] == 0.88
    assert payload["drift"]["findings"][0]["item_ids"] == ["a", "b", "c", "d", "e"]
    assert len(payload["drift"]["findings"][0]["evidence"]) == 200


def test_build_health_detail_payload_handles_missing_reports() -> None:
    from web.health_payloads import build_health_detail_payload

    payload = build_health_detail_payload(
        total_items=0,
        skipped_items=0,
        gov_report=None,
        drift_report=None,
    )

    assert payload == {
        "total_items": 0,
        "governance": None,
        "drift": None,
        "grade": "?",
        "healthy": False,
        "skipped_items": 0,
    }
