from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.memory.governance.auto_governance import (
    AutoGovernanceAction,
    AutoGovernanceReport,
)


BASE_TIME = datetime(2026, 7, 19, 10, 0, tzinfo=timezone.utc)


def _lifecycle_item(
    item_id: str,
    *,
    created_at: datetime,
    title: str,
    summary: str = "lifecycle summary",
    locator: str = "lifecycle locator",
    refs: dict[str, list[str]] | None = None,
    superseded_by: str | None = None,
) -> MemoryItem:
    return MemoryItem(
        id=item_id,
        type=MemoryType.signal,
        created_at=created_at,
        project="agent-memory-hub",
        tenant_id="tenant-a",
        title=title,
        summary=summary,
        refs=refs or {},
        context_views={"locator": locator},
        superseded_by=superseded_by,
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

    item_id = "mem-20260101-000000-stale-signal"
    report = AutoGovernanceReport(
        scanned_items=1,
        actions=[
            AutoGovernanceAction(
                action="review_archive",
                risk="review_required",
                title="Review stale signal: hook warning",
                reason="stale_signal_older_than_30_days",
                item_ids=[item_id],
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
    assert payload["review_queue"] == [
        {
            "item_id": item_id,
            "action": "review_archive",
            "category": "lifecycle",
            "title": "Review stale signal: hook warning",
            "read_command": f"memory read {item_id} --head 2000 --view detail",
            "recommended_next": "archive_after_review",
            "can_auto_apply": False,
            "boundary": "确认是否已有更新 item 可以 supersede，不能确认再 archive",
            "candidates": [],
            "reviewed_at": None,
        }
    ]


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


def test_lifecycle_review_queue_serializes_empty_candidate_defaults_compatibly() -> None:
    from agent_brain.memory.governance.maintenance_plan import build_maintenance_plan

    obsolete_id = "mem-20260719-100000-obsolete-empty"
    report = AutoGovernanceReport(
        scanned_items=1,
        actions=[
            AutoGovernanceAction(
                action="review_archive",
                risk="review_required",
                title="Review stale signal",
                reason="stale_signal_older_than_30_days",
                item_ids=[obsolete_id],
                details={"issue_type": "stale_signal"},
            )
        ],
    )

    row = build_maintenance_plan(report).to_dict()["review_queue"][0]

    assert row["recommended_next"] == "archive_after_review"
    assert row["candidates"] == []
    assert row["reviewed_at"] is None
    assert row["can_auto_apply"] is False


def test_lifecycle_review_queue_adds_ranked_private_candidates_without_applying() -> None:
    from agent_brain.memory.governance.maintenance_plan import build_maintenance_plan

    obsolete = _lifecycle_item(
        "mem-20260719-100000-obsolete-queue",
        created_at=BASE_TIME,
        title="login hook incident",
    )
    replacement = _lifecycle_item(
        "mem-20260719-110000-replacement-queue",
        created_at=BASE_TIME + timedelta(hours=1),
        title="CANDIDATE_TITLE_SECRET login hook incident",
        summary="CANDIDATE_SUMMARY_SECRET issue resolved",
        locator="CANDIDATE_LOCATOR_SECRET",
        refs={"mems": [obsolete.id]},
    )
    report = AutoGovernanceReport(
        scanned_items=2,
        actions=[
            AutoGovernanceAction(
                action="review_archive",
                risk="review_required",
                title="Review stale signal",
                reason="stale_signal_older_than_30_days",
                item_ids=[obsolete.id],
                details={"issue_type": "stale_signal"},
            )
        ],
    )

    plan = build_maintenance_plan(
        report,
        items_by_id={obsolete.id: obsolete, replacement.id: replacement},
        supersedes_edges={(replacement.id, obsolete.id)},
    )
    row = plan.to_dict()["review_queue"][0]

    assert row["recommended_next"] == "select_supersession_or_keep_active"
    assert row["can_auto_apply"] is False
    assert row["reviewed_at"] is None
    assert row["candidates"] == [
        {
            "replacement_id": replacement.id,
            "score": 1.0,
            "evidence_codes": [
                "EXPLICIT_SUPERSEDES_EDGE",
                "EXPLICIT_MEMORY_REF",
                "TOPIC_OVERLAP",
                "CLOSURE_LANGUAGE",
                "NEWER_ITEM",
            ],
        }
    ]
    serialized = str(row["candidates"])
    assert "CANDIDATE_TITLE_SECRET" not in serialized
    assert "CANDIDATE_SUMMARY_SECRET" not in serialized
    assert "CANDIDATE_LOCATOR_SECRET" not in serialized


def test_lifecycle_review_queue_uses_frontmatter_supersession_edges() -> None:
    from agent_brain.memory.governance.maintenance_plan import build_maintenance_plan

    replacement = _lifecycle_item(
        "mem-20260719-110000-frontmatter-replacement",
        created_at=BASE_TIME + timedelta(hours=1),
        title="new unrelated state",
    )
    obsolete = _lifecycle_item(
        "mem-20260719-100000-frontmatter-obsolete",
        created_at=BASE_TIME,
        title="old unrelated state",
        superseded_by=replacement.id,
    )
    report = AutoGovernanceReport(
        scanned_items=2,
        actions=[
            AutoGovernanceAction(
                action="review_archive",
                risk="review_required",
                title="Review stale signal",
                reason="stale_signal_older_than_30_days",
                item_ids=[obsolete.id],
                details={"issue_type": "stale_signal"},
            )
        ],
    )

    row = build_maintenance_plan(
        report,
        items_by_id={obsolete.id: obsolete, replacement.id: replacement},
    ).to_dict()["review_queue"][0]

    assert row["candidates"][0]["replacement_id"] == replacement.id
    assert row["candidates"][0]["score"] == 1.0
    assert row["candidates"][0]["evidence_codes"][0] == "EXPLICIT_SUPERSEDES_EDGE"
