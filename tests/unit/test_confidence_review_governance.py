from __future__ import annotations

import json
from datetime import datetime, timezone

from typer.testing import CliRunner

from agent_brain.contracts.memory_enums import MemoryType
from agent_brain.contracts.memory_item import MemoryItem, Refs
from agent_brain.interfaces.cli import app
from agent_brain.memory.governance.auto_governance import AutoGovernanceCycle
from agent_brain.memory.governance.confidence_review import assess_low_confidence
from agent_brain.memory.governance.maintenance_plan import build_maintenance_plan
from agent_brain.memory.governance.review_queue import list_review_candidates
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.product.governance_readiness import build_memory_lifecycle_readiness


runner = CliRunner()


def _item(
    suffix: str,
    *,
    confidence: float = 0.35,
    tags: list[str] | None = None,
    refs: Refs | None = None,
) -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260729-120000-{suffix}",
        type=MemoryType.decision,
        created_at=datetime.now(timezone.utc),
        title=f"Confidence review {suffix}",
        summary=f"Governed low-confidence candidate {suffix}",
        tags=tags or [],
        confidence=confidence,
        refs=refs or Refs(),
    )


def test_low_confidence_assessment_separates_contested_gap_backed_and_terminal() -> None:
    contested = assess_low_confidence(_item("contested", tags=["contested"]))
    gap = assess_low_confidence(_item("gap"))
    backed = assess_low_confidence(
        _item("backed", refs=Refs(commits=["a75eaa8"]))
    )
    terminal = assess_low_confidence(
        _item("terminal", tags=["review-rejected"])
    )

    assert contested is not None and contested.disposition == "contested"
    assert gap is not None and gap.disposition == "source_gap"
    assert backed is not None and backed.disposition == "source_backed"
    assert terminal is not None and terminal.disposition == "terminal"
    assert terminal.actionable is False


def test_low_confidence_enters_dedicated_plan_and_review_queue(tmp_path) -> None:
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    item = _item("plan-source-gap")
    store.write(item, "candidate body")

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
        for lane in plan.lanes
        for action in lane.actions
        if action.action == "review_low_confidence"
    ]

    assert len(actions) == 1
    assert actions[0].category == "low_confidence"
    assert actions[0].details["disposition"] == "source_gap"
    assert actions[0].command == "memory review list --format json"
    queued = next(row for row in plan.review_queue if row.item_id == item.id)
    assert queued.recommended_next == "attach_source_or_reject"
    assert queued.can_auto_apply is False


def test_terminal_low_confidence_is_measured_but_does_not_warn(tmp_path) -> None:
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    item = _item("terminal-readiness", tags=["review-rejected"], confidence=0.1)
    store.write(item, "rejected candidate")

    lane = build_memory_lifecycle_readiness(brain)
    low_confidence = next(
        check for check in lane.checks if check.id == "low_confidence_count"
    )

    assert lane.metrics["low_confidence_count"] == 0
    assert lane.metrics["low_confidence_total_count"] == 1
    assert lane.metrics["low_confidence_terminal_count"] == 1
    assert lane.metrics["low_confidence_dispositions"] == {"terminal": 1}
    assert low_confidence.status == "pass"
    assert list_review_candidates(store).total == 0


def test_review_attach_source_preserves_confidence_and_review_state(
    tmp_brain,
) -> None:
    store = ItemsStore(tmp_brain / "items")
    item = _item("attach-source", tags=["needs-review", "unverified-boundary"])
    store.write(item, "candidate body")

    attached = runner.invoke(
        app,
        [
            "review",
            "attach-source",
            item.id,
            "--commit",
            "a75eaa8",
            "--file",
            "agent_brain/memory/governance/confidence_review.py",
        ],
    )
    listed = runner.invoke(app, ["review", "list", "--format", "json"])

    assert attached.exit_code == 0, attached.output
    updated, _body = store.get(item.id)
    assert updated.confidence == 0.35
    assert updated.refs.commits == ["a75eaa8"]
    assert updated.refs.files == [
        "agent_brain/memory/governance/confidence_review.py"
    ]
    assert "needs-review" in updated.tags
    payload = json.loads(listed.output)
    candidate = next(row for row in payload["items"] if row["id"] == item.id)
    assert candidate["review_reason"] == "source_backed"
    assert candidate["explicit_source_ref_count"] == 2


def test_review_attach_source_rejects_non_https_url(tmp_brain) -> None:
    store = ItemsStore(tmp_brain / "items")
    item = _item("invalid-url", tags=["needs-review"])
    store.write(item, "candidate body")

    result = runner.invoke(
        app,
        [
            "review",
            "attach-source",
            item.id,
            "--url",
            "http://example.com/evidence",
        ],
    )

    assert result.exit_code == 2
    assert store.get(item.id)[0].refs.urls == []
