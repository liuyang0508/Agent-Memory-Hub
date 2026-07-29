from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_brain.contracts.memory_enums import MemoryType
from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.memory.governance.auto_governance import AutoGovernanceCycle
from agent_brain.memory.governance.maintenance_plan import build_maintenance_plan
from agent_brain.memory.governance.lifecycle_review import (
    LifecycleReviewAction,
    apply_lifecycle_review_actions,
)
from agent_brain.memory.governance.signal_state import assess_signal_state
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.product.governance_readiness import build_memory_lifecycle_readiness


def _item(
    item_id: str,
    *,
    type_: MemoryType = MemoryType.signal,
    title: str = "等待接线",
    summary: str = "尚未部署",
    tags: list[str] | None = None,
) -> MemoryItem:
    return MemoryItem(
        id=item_id,
        type=type_,
        created_at=datetime.now(timezone.utc),
        title=title,
        summary=summary,
        tags=tags or [],
    )


@pytest.mark.parametrize(
    ("item", "state", "issues"),
    [
        (
            _item(
                "mem-20260729-100000-signal-conflict",
                title="生产接线已完成",
                summary="阻塞已解除",
                tags=["resolved", "pending", "blocked"],
            ),
            "ambiguous",
            {
                "terminal_and_active_tags",
                "closure_text_with_active_tags",
            },
        ),
        (
            _item(
                "mem-20260729-100001-signal-unmarked",
                title="问题已解决",
                summary="线上恢复",
                tags=[],
            ),
            "ambiguous",
            {"closure_text_without_terminal_tag"},
        ),
        (
            _item(
                "mem-20260729-100002-signal-open",
                tags=["pending", "blocked"],
            ),
            "open",
            set(),
        ),
        (
            _item(
                "mem-20260729-100003-signal-resolved",
                title="生产接线已完成",
                summary="阻塞已解除",
                tags=["resolved"],
            ),
            "resolved",
            set(),
        ),
        (
            _item(
                "mem-20260729-100004-fact-resolved",
                type_=MemoryType.fact,
                title="问题已解决",
                tags=["pending", "resolved"],
            ),
            "open",
            set(),
        ),
    ],
)
def test_signal_state_assessment_is_deterministic(
    item: MemoryItem,
    state: str,
    issues: set[str],
) -> None:
    assessment = assess_signal_state(item)

    assert assessment.state == state
    assert set(assessment.issues) >= issues
    assert assessment.consistent is (not issues)


def test_signal_state_inconsistency_fails_readiness_and_enters_lifecycle_plan(
    tmp_path,
) -> None:
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    signal = _item(
        "mem-20260729-100005-signal-readiness",
        title="官网部署已完成",
        summary="阻塞已解除",
        tags=["resolved", "pending", "blocked"],
    )
    store.write(signal, "**当前状态**\n\n尚未部署")

    lane = build_memory_lifecycle_readiness(brain)
    check = next(
        row for row in lane.checks if row.id == "signal_state_consistency"
    )
    report = AutoGovernanceCycle(
        brain_dir=brain,
        items_store=store,
        include_index=False,
        include_conversations=False,
        include_evolve=False,
    ).run()
    plan = build_maintenance_plan(report)
    actions = [
        action
        for plan_lane in plan.lanes
        for action in plan_lane.actions
        if action.action == "review_signal_state"
    ]

    assert lane.metrics["signal_state_inconsistent_count"] == 1
    assert check.status == "fail"
    assert check.evidence["count"] == 1
    assert len(actions) == 1
    assert actions[0].category == "lifecycle"
    assert actions[0].item_ids == [signal.id]


def test_inconsistent_signal_can_be_explicitly_archived_after_revalidation(
    tmp_path,
) -> None:
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    signal = _item(
        "mem-20260729-100006-signal-archive",
        title="官网部署已完成",
        summary="阻塞已解除",
        tags=["resolved", "pending"],
    )
    store.write(signal, "**当前状态**\n\n旧阻塞描述")

    payload = apply_lifecycle_review_actions(
        brain_dir=brain,
        items_store=store,
        actions=[LifecycleReviewAction("archive", signal.id)],
        apply=True,
        index_repair=False,
    )

    assert payload["results"][0]["status"] == "applied"
    assert not (brain / "items" / f"{signal.id}.md").exists()
    assert (brain / "items" / "archived" / f"{signal.id}.md").exists()
