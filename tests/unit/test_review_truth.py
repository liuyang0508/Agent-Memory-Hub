from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from typer.testing import CliRunner

from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Refs
from agent_brain.interfaces.cli import app
from agent_brain.memory.governance.review_queue import list_review_candidates
from agent_brain.memory.governance.review_truth import (
    build_review_truth_from_brain,
    build_review_truth_snapshot,
)
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.product.cockpit import build_cockpit_summary
from agent_brain.product.governance_readiness import (
    build_memory_lifecycle_readiness,
)


runner = CliRunner()


def _item(
    suffix: str,
    *,
    now: datetime,
    item_type: MemoryType = MemoryType.decision,
    days_ago: int = 0,
    confidence: float = 0.9,
    tags: list[str] | None = None,
    refs: Refs | None = None,
) -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260730-160000-{suffix}",
        type=item_type,
        created_at=now - timedelta(days=days_ago),
        title=f"Title {suffix}",
        summary=f"Summary {suffix}",
        confidence=confidence,
        tags=tags or [],
        refs=refs or Refs(),
    )


def test_review_truth_separates_review_candidates_from_lifecycle_due() -> None:
    now = datetime(2026, 7, 30, 16, 0, tzinfo=timezone.utc)
    items = [
        _item("source-gap", now=now, days_ago=8, confidence=0.35),
        _item(
            "contested",
            now=now,
            confidence=0.35,
            tags=["contested", "needs-review"],
        ),
        _item(
            "source-backed",
            now=now,
            confidence=0.35,
            refs=Refs(commits=["2658675"]),
        ),
        _item("explicit", now=now, tags=["needs-review", "contested"]),
        _item(
            "lifecycle-due",
            now=now,
            item_type=MemoryType.signal,
            days_ago=90,
        ),
    ]

    truth = build_review_truth_snapshot(items, now=now, active_deferrals=set())

    assert truth.status == "warn"
    assert truth.consistency_status == "consistent"
    assert truth.active_review_candidate_count == 4
    assert truth.active_review_candidate_sla_breach_count == 1
    assert truth.active_review_reason_counts == {
        "contested": 1,
        "explicit_review_tag": 1,
        "source_backed": 1,
        "source_gap": 1,
    }
    assert truth.active_review_type_counts == {"decision": 4}
    assert truth.active_review_contested_count == 2
    assert truth.active_review_contested_outside_low_confidence_count == 1
    assert truth.lifecycle_due_count == 1


def test_review_truth_honors_lifecycle_deferral_without_hiding_review_candidate() -> None:
    now = datetime(2026, 7, 30, 16, 0, tzinfo=timezone.utc)
    item = _item(
        "deferred-review",
        now=now,
        item_type=MemoryType.signal,
        days_ago=90,
        confidence=0.35,
        tags=["needs-review"],
    )

    truth = build_review_truth_snapshot(
        [item],
        now=now,
        active_deferrals={item.id},
    )

    assert truth.active_review_candidate_count == 1
    assert truth.lifecycle_due_count == 0


def test_review_truth_is_content_free_and_snapshot_isolated(tmp_path) -> None:
    now = datetime(2026, 7, 30, 16, 0, tzinfo=timezone.utc)
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    private = _item(
        "private-canary",
        now=now,
        confidence=0.35,
        tags=["needs-review"],
    ).model_copy(
        update={
            "title": "PRIVATE_TITLE_CANARY",
            "summary": "PRIVATE_SUMMARY_CANARY",
            "sensitivity": "private",
        }
    )
    store.write(private, "PRIVATE_BODY_CANARY")
    captured = tuple(item for item, _body in store.iter_all())
    store.write(
        _item("later-candidate", now=now, confidence=0.35),
        "later body",
    )

    captured_truth = build_review_truth_snapshot(
        captured,
        now=now,
        active_deferrals=set(),
    )
    live_truth = build_review_truth_from_brain(brain, now=now)
    serialized = json.dumps(live_truth.to_dict(), ensure_ascii=False)

    assert captured_truth.active_review_candidate_count == 1
    assert live_truth.active_review_candidate_count == 2
    assert "PRIVATE_TITLE_CANARY" not in serialized
    assert "PRIVATE_SUMMARY_CANARY" not in serialized
    assert "PRIVATE_BODY_CANARY" not in serialized


def test_review_truth_counts_match_cli_readiness_cockpit_and_queue(
    tmp_path,
    monkeypatch,
) -> None:
    now = datetime.now(timezone.utc)
    brain = tmp_path / "brain"
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    store = ItemsStore(brain / "items")
    store.write(
        _item(
            "cross-surface-review",
            now=now,
            days_ago=8,
            confidence=0.35,
            tags=["needs-review"],
        ),
        "review body",
    )
    store.write(
        _item(
            "cross-surface-lifecycle",
            now=now,
            item_type=MemoryType.signal,
            days_ago=90,
        ),
        "lifecycle body",
    )

    truth = build_review_truth_from_brain(brain, now=now)
    readiness = build_memory_lifecycle_readiness(brain)
    cockpit = build_cockpit_summary(brain, now=now)["review_truth"]
    cli_result = runner.invoke(app, ["review", "status", "--format", "json"])
    cli = json.loads(cli_result.output)
    queue = list_review_candidates(store)

    assert cli_result.exit_code == 0, cli_result.output
    assert truth.active_review_candidate_count == queue.total == 1
    assert (
        readiness.metrics["active_review_candidate_count"]
        == cockpit["active_review_candidate_count"]
        == cli["active_review_candidate_count"]
        == queue.total
    )
    assert (
        readiness.metrics["lifecycle_due_count"]
        == cockpit["lifecycle_due_count"]
        == cli["lifecycle_due_count"]
        == truth.lifecycle_due_count
        == 1
    )
    assert readiness.metrics["review_queue_count"] == queue.total
    assert readiness.metrics["review_truth_consistency_status"] == "consistent"
    assert (
        readiness.metrics["active_review_contested_count"]
        == cockpit["active_review_contested_count"]
        == cli["active_review_contested_count"]
        == 0
    )
