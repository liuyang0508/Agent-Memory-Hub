from __future__ import annotations

from datetime import datetime, timezone

from agent_brain.observability import BrainStats, HealthScore


def test_build_stats_json_payload_formats_dates_and_average_body_length() -> None:
    from agent_brain.interfaces.cli.status_payloads import build_stats_json_payload

    stats = BrainStats(
        total_items=3,
        skipped_count=1,
        type_counts={"fact": 2, "decision": 1},
        project_counts={"agent-memory-hub": 3},
        agent_counts={"codex": 3},
        sensitivity_counts={"internal": 3},
        tag_counts={"refactor": 2},
        oldest=datetime(2026, 6, 1, tzinfo=timezone.utc),
        newest=datetime(2026, 6, 10, tzinfo=timezone.utc),
        avg_body_length=42.6,
        weekly_trend=[("2026-W22", 3)],
    )

    payload = build_stats_json_payload(stats)

    assert payload["total_items"] == 3
    assert payload["skipped"] == 1
    assert payload["oldest"] == "2026-06-01T00:00:00+00:00"
    assert payload["newest"] == "2026-06-10T00:00:00+00:00"
    assert payload["avg_body_length"] == 43
    assert payload["top_tags"] == {"refactor": 2}


def test_build_health_json_payload_preserves_exit_relevant_fields() -> None:
    from agent_brain.interfaces.cli.status_payloads import build_health_json_payload

    score = HealthScore(
        total_items=10,
        items_with_issues=2,
        governance_issues=3,
        duplicates=1,
        noise=1,
        expired=1,
        low_quality=0,
        drift_findings=2,
        contradictions=1,
        stale=1,
        citation_rot=0,
        drift_clusters=0,
        skipped_items=1,
    )

    payload = build_health_json_payload(score)

    assert payload["grade"] == "C"
    assert payload["healthy"] is False
    assert payload["issue_rate"] == 0.2
    assert payload["governance"]["total_issues"] == 3
    assert payload["drift"]["total_findings"] == 2
    assert payload["skipped_items"] == 1
