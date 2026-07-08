from __future__ import annotations

from agent_brain.memory.governance.auto_governance import (
    AutoGovernanceAction,
    AutoGovernanceReport,
)


def test_maintenance_plan_groups_actions_by_execution_lane() -> None:
    from agent_brain.memory.governance.maintenance_plan import build_maintenance_plan

    report = AutoGovernanceReport(
        scanned_items=3,
        actions=[
            AutoGovernanceAction(
                action="update_maturity",
                risk="safe_apply",
                title="Update maturity",
                reason="maturity_score_recommendation",
                item_ids=["mem-20260618-160000-maturity"],
            ),
            AutoGovernanceAction(
                action="review_archive",
                risk="review_required",
                title="Review expired signal",
                reason="expired signal",
                item_ids=["mem-20250101-000000-expired"],
                details={"issue_type": "expired"},
            ),
            AutoGovernanceAction(
                action="review_evolve_consolidate",
                risk="blocked",
                title="Blocked consolidation",
                reason="audit gate blocked",
                item_ids=["mem-a", "mem-b"],
            ),
        ],
    )

    plan = build_maintenance_plan(report, limit_per_lane=10)
    payload = plan.to_dict()

    assert payload["dry_run"] is True
    assert payload["scanned_items"] == 3
    assert payload["action_counts"] == {
        "review_archive": 1,
        "review_evolve_consolidate": 1,
        "update_maturity": 1,
    }
    assert payload["lanes"][0]["risk"] == "safe_apply"
    assert payload["lanes"][0]["count"] == 1
    assert payload["lanes"][0]["next_command"] == "memory govern auto --apply"
    assert payload["lanes"][1]["risk"] == "review_required"
    assert payload["lanes"][1]["actions"][0]["action"] == "review_archive"
    assert payload["lanes"][2]["risk"] == "blocked"
    assert payload["next_commands"] == [
        "memory govern auto --apply",
        "memory govern run --format json",
        "memory evolve --format json",
    ]


def test_maintenance_plan_respects_lane_limit() -> None:
    from agent_brain.memory.governance.maintenance_plan import build_maintenance_plan

    report = AutoGovernanceReport(
        scanned_items=5,
        actions=[
            AutoGovernanceAction(
                action="review_quality",
                risk="review_required",
                title=f"Review quality {i}",
                reason="missing tags",
                item_ids=[f"mem-20260618-16000{i}-quality"],
            )
            for i in range(5)
        ],
    )

    plan = build_maintenance_plan(report, limit_per_lane=2)
    lane = plan.to_dict()["lanes"][1]

    assert lane["count"] == 5
    assert lane["returned"] == 2
    assert lane["truncated"] is True


def test_maintenance_plan_suppresses_duplicate_archive_proposals() -> None:
    from agent_brain.memory.governance.maintenance_plan import build_maintenance_plan

    item_id = "mem-20250101-000000-expired"
    report = AutoGovernanceReport(
        scanned_items=1,
        actions=[
            AutoGovernanceAction(
                action="review_archive",
                risk="review_required",
                title="Review expired signal",
                reason="expired signal",
                item_ids=[item_id],
            ),
            AutoGovernanceAction(
                action="review_evolve_archive",
                risk="review_required",
                title="Archive expired signal",
                reason="evolve archive candidate",
                item_ids=[item_id],
            ),
        ],
    )

    payload = build_maintenance_plan(report).to_dict()

    assert payload["raw_action_count"] == 2
    assert payload["action_count"] == 1
    assert payload["suppressed_action_count"] == 1
    assert payload["action_counts"] == {"review_archive": 1}


def test_maintenance_plan_prefers_lifecycle_archive_over_generic_expired() -> None:
    from agent_brain.memory.governance.maintenance_plan import build_maintenance_plan

    item_id = "mem-20250101-000000-stale-signal"
    report = AutoGovernanceReport(
        scanned_items=1,
        actions=[
            AutoGovernanceAction(
                action="review_archive",
                risk="review_required",
                title="Review stale signal",
                reason="stale_signal_older_than_30_days",
                item_ids=[item_id],
                details={"issue_type": "stale_signal", "lifecycle_type": "signal"},
            ),
            AutoGovernanceAction(
                action="review_archive",
                risk="review_required",
                title="Review expired",
                reason="expired signal",
                item_ids=[item_id],
                details={"issue_type": "expired"},
            ),
        ],
    )

    payload = build_maintenance_plan(report).to_dict()

    assert payload["raw_action_count"] == 2
    assert payload["action_count"] == 1
    assert payload["suppressed_action_count"] == 1
    assert payload["lanes"][1]["actions"][0]["category"] == "lifecycle"
    assert payload["category_counts"] == {"lifecycle": 1}


def test_maintenance_plan_categorizes_review_quality_subtypes() -> None:
    from agent_brain.memory.governance.maintenance_plan import build_maintenance_plan

    report = AutoGovernanceReport(
        scanned_items=3,
        actions=[
            AutoGovernanceAction(
                action="review_quality",
                risk="review_required",
                title="Review duplicate",
                reason="Item 'A' is near-duplicate of 'B' (jaccard=0.81)",
                item_ids=["mem-dup"],
                details={"issue_type": "duplicate"},
            ),
            AutoGovernanceAction(
                action="review_quality",
                risk="review_required",
                title="Review long summary",
                reason="Item 'A' has very long summary (240 chars)",
                item_ids=["mem-long"],
                details={"issue_type": "low_quality"},
            ),
            AutoGovernanceAction(
                action="review_quality",
                risk="review_required",
                title="Review no tags",
                reason="Item 'A' has no tags",
                item_ids=["mem-tags"],
                details={"issue_type": "low_quality"},
            ),
        ],
    )

    payload = build_maintenance_plan(report).to_dict()
    actions = payload["lanes"][1]["actions"]

    assert [action["category"] for action in actions] == [
        "near_duplicate",
        "summary_too_long",
        "missing_tags",
    ]
    assert payload["category_counts"] == {
        "missing_tags": 1,
        "near_duplicate": 1,
        "summary_too_long": 1,
    }


def test_maintenance_plan_categorizes_stale_signal_archive_as_lifecycle() -> None:
    from agent_brain.memory.governance.maintenance_plan import build_maintenance_plan

    report = AutoGovernanceReport(
        scanned_items=1,
        actions=[
            AutoGovernanceAction(
                action="review_archive",
                risk="review_required",
                title="Review stale signal: hook warning",
                reason="stale_signal_older_than_30_days",
                item_ids=["mem-20260101-000000-stale-signal"],
                details={
                    "issue_type": "stale_signal",
                    "lifecycle_type": "signal",
                    "age_days": 60,
                    "stale_after_days": 30,
                    "recommended_action": "archive_or_supersede",
                },
            ),
        ],
    )

    payload = build_maintenance_plan(report).to_dict()
    action = payload["lanes"][1]["actions"][0]

    assert action["category"] == "lifecycle"
    assert action["details"] == {
        "issue_type": "stale_signal",
        "lifecycle_type": "signal",
        "age_days": 60,
        "stale_after_days": 30,
        "recommended_action": "archive_or_supersede",
    }
    assert action["command"] == "memory govern plan --category lifecycle --format markdown"
    assert payload["category_counts"] == {"lifecycle": 1}


def test_maintenance_plan_filters_by_action_and_category() -> None:
    from agent_brain.memory.governance.maintenance_plan import build_maintenance_plan

    report = AutoGovernanceReport(
        scanned_items=3,
        actions=[
            AutoGovernanceAction(
                action="review_quality",
                risk="review_required",
                title="Review no tags",
                reason="Item 'A' has no tags",
                item_ids=["mem-tags"],
                details={"issue_type": "low_quality"},
            ),
            AutoGovernanceAction(
                action="review_quality",
                risk="review_required",
                title="Review long summary",
                reason="Item 'A' has very long summary (240 chars)",
                item_ids=["mem-long"],
                details={"issue_type": "low_quality"},
            ),
            AutoGovernanceAction(
                action="review_archive",
                risk="review_required",
                title="Review expired",
                reason="expired signal",
                item_ids=["mem-expired"],
            ),
        ],
    )

    payload = build_maintenance_plan(
        report,
        action_filter="review_quality",
        category_filter="summary_too_long",
    ).to_dict()

    assert payload["filters"] == {
        "action": "review_quality",
        "category": "summary_too_long",
    }
    assert payload["raw_action_count"] == 3
    assert payload["suppressed_action_count"] == 0
    assert payload["filtered_out_count"] == 2
    assert payload["action_count"] == 1
    assert payload["review_required_count"] == 1
    assert payload["action_counts"] == {"review_quality": 1}
    assert payload["category_counts"] == {"summary_too_long": 1}
    assert payload["lanes"][1]["actions"][0]["item_ids"] == ["mem-long"]
