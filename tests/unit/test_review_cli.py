from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from typer.testing import CliRunner

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.interfaces.cli import app
from agent_brain.memory.store.items_store import ItemsStore


runner = CliRunner()


def _candidate(store: ItemsStore, suffix: str, *, days_ago: int = 0) -> MemoryItem:
    item = MemoryItem(
        id=f"mem-20260701-12000{days_ago}-{suffix}",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
        title=f"Candidate {suffix}",
        summary=f"Review candidate {suffix}",
        tags=["needs-review", suffix],
        confidence=0.3,
    )
    store.write(item, "candidate body")
    return item


def _contradictory_decisions(
    store: ItemsStore,
) -> tuple[MemoryItem, MemoryItem]:
    first = MemoryItem(
        id="mem-20260701-140001-cli-react",
        type=MemoryType.decision,
        created_at=datetime.now(timezone.utc),
        project="review-cli-case",
        title="Frontend Framework Choice",
        summary="Use React",
        tags=["framework-choice"],
    )
    second = first.model_copy(
        update={
            "id": "mem-20260701-140002-cli-vue",
            "title": "Frontend Framework Choice Updated",
            "summary": "Use Vue",
        }
    )
    store.write(first, "We decided to use React for the frontend framework.")
    store.write(second, "After evaluation, we chose Vue instead of React.")
    return first, second


def test_review_status_reports_oldest_age_and_sla_alert(tmp_brain):
    store = ItemsStore(tmp_brain / "items")
    _candidate(store, "old", days_ago=8)

    result = runner.invoke(app, ["review", "status", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "warn"
    assert payload["review_total"] == 1
    assert payload["review_oldest_age_seconds"] >= 8 * 86400
    assert "review queue oldest candidate exceeds 7d SLA" in payload["alerts"]
    assert payload["recommended_next"] == "memory review list --format json"


def test_review_approve_many_resolves_all_before_mutating(tmp_brain):
    store = ItemsStore(tmp_brain / "items")
    first = _candidate(store, "first")
    second = _candidate(store, "second")

    result = runner.invoke(
        app,
        ["review", "approve-many", first.id, second.id, "--confidence", "0.8"],
    )

    assert result.exit_code == 0, result.output
    assert "approved=2" in result.output
    for item_id in (first.id, second.id):
        updated, _body = store.get(item_id)
        assert "needs-review" not in updated.tags
        assert "review-approved" in updated.tags
        assert updated.confidence == 0.8


def test_review_many_rejects_non_candidate_without_partial_mutation(tmp_brain):
    store = ItemsStore(tmp_brain / "items")
    candidate = _candidate(store, "candidate")
    normal = candidate.model_copy(
        update={
            "id": "mem-20260701-130000-normal",
            "title": "Normal",
            "tags": ["normal"],
            "confidence": 0.7,
        }
    )
    store.write(normal, "normal body")

    result = runner.invoke(app, ["review", "reject-many", candidate.id, normal.id])

    assert result.exit_code == 2
    unchanged, _body = store.get(candidate.id)
    assert "needs-review" in unchanged.tags


def test_review_resolve_requires_preview_digest_and_writes_receipt(tmp_brain):
    store = ItemsStore(tmp_brain / "items")
    candidate = _candidate(store, "receipted")

    preview = runner.invoke(
        app,
        ["review", "resolve", candidate.id, "--action", "approve"],
    )
    preview_payload = json.loads(preview.output)
    applied = runner.invoke(
        app,
        [
            "review",
            "resolve",
            candidate.id,
            "--action",
            "approve",
            "--expected-sha256",
            preview_payload["expected_sha256"],
            "--apply",
        ],
    )

    assert preview.exit_code == 0
    assert preview_payload["status"] == "ready"
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.output)["status"] == "applied"
    assert (tmp_brain / "runtime" / "review-resolution-receipts.jsonl").exists()


def test_review_case_cli_lists_previews_and_applies_coexistence(tmp_brain):
    store = ItemsStore(tmp_brain / "items")
    _contradictory_decisions(store)

    listed = runner.invoke(app, ["review", "cases", "--format", "json"])
    listed_payload = json.loads(listed.output)
    case_id = listed_payload["cases"][0]["case_id"]
    preview = runner.invoke(
        app,
        ["review", "resolve-case", case_id, "--action", "coexist"],
    )
    preview_payload = json.loads(preview.output)
    applied = runner.invoke(
        app,
        [
            "review",
            "resolve-case",
            case_id,
            "--action",
            "coexist",
            "--expected-intent-sha256",
            preview_payload["expected_intent_sha256"],
            "--apply",
        ],
    )

    assert listed.exit_code == 0, listed.output
    assert listed_payload["open_count"] == 1
    assert preview.exit_code == 0, preview.output
    assert preview_payload["status"] == "ready"
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.output)["status"] == "applied"
    assert (
        tmp_brain / "runtime" / "contradiction-case-receipts.jsonl"
    ).exists()


def test_review_signal_cli_previews_and_applies_terminal_state(tmp_brain):
    store = ItemsStore(tmp_brain / "items")
    signal = MemoryItem(
        id="mem-20260730-032000-cli-signal",
        type=MemoryType.signal,
        created_at=datetime.now(timezone.utc),
        project="review-cli-signal",
        title="CLI open blocker",
        summary="Waiting for operator",
        tags=["blocked", "pending"],
    )
    store.write(signal, "waiting")

    preview = runner.invoke(
        app,
        [
            "review",
            "resolve-signal",
            signal.id,
            "--action",
            "obsolete",
            "--reason",
            "test environment retired",
        ],
    )
    preview_payload = json.loads(preview.output)
    applied = runner.invoke(
        app,
        [
            "review",
            "resolve-signal",
            signal.id,
            "--action",
            "obsolete",
            "--reason",
            "test environment retired",
            "--expected-intent-sha256",
            preview_payload["intent_sha256"],
            "--apply",
        ],
    )
    updated, _body = store.get(signal.id)

    assert preview.exit_code == 0, preview.output
    assert preview_payload["status"] == "ready"
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.output)["status"] == "applied"
    assert updated.signal_state is not None
    assert updated.signal_state.status == "obsolete"
    assert (tmp_brain / "runtime" / "signal-state-receipts.jsonl").exists()
