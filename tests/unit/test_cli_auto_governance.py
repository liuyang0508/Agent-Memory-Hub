from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from typer.testing import CliRunner

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.interfaces.cli import app
from agent_brain.memory.store.items_store import ItemsStore

runner = CliRunner()


def _write_matureable_item(store: ItemsStore, item_id: str) -> MemoryItem:
    item = MemoryItem(
        id=item_id,
        type=MemoryType.decision,
        created_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
        title="CLI auto governance",
        summary="CLI auto governance locator",
        confidence=0.85,
        tags=["auto-governance"],
        refs={"files": ["docs/architecture.md"], "mems": ["mem-20260618-010101-source"]},
        support_count=4,
        gain_score=0.3,
        context_views={
            "locator": "CLI auto governance locator",
            "overview": "CLI auto governance overview with evidence.",
        },
    )
    store.write(item, "body")
    return item


def test_govern_auto_json_reports_safe_plan_without_apply(
    tmp_brain_dir: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    store = ItemsStore(tmp_brain_dir / "items")
    item = _write_matureable_item(store, "mem-20260618-170000-cli-auto")

    result = runner.invoke(app, ["govern", "auto", "--format", "json", "--no-index-repair"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["safe_apply_count"] == 1
    assert payload["applied_count"] == 0
    assert payload["actions"][0]["action"] == "update_maturity"

    unchanged, _ = store.get(item.id)
    assert unchanged.maturity == "raw"
    assert unchanged.abstraction == "L0"


def test_govern_auto_apply_persists_safe_actions(
    tmp_brain_dir: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    store = ItemsStore(tmp_brain_dir / "items")
    item = _write_matureable_item(store, "mem-20260618-170001-cli-auto-apply")

    result = runner.invoke(
        app,
        ["govern", "auto", "--format", "json", "--apply", "--no-index-repair"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["safe_apply_count"] == 1
    assert payload["applied_count"] == 1

    updated, _ = store.get(item.id)
    assert updated.maturity == "consolidated"
    assert updated.abstraction == "L1"


def test_govern_plan_json_reports_maintenance_lanes_without_apply(
    tmp_brain_dir: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    store = ItemsStore(tmp_brain_dir / "items")
    item = _write_matureable_item(store, "mem-20260618-170002-cli-plan")

    result = runner.invoke(
        app,
        [
            "govern",
            "plan",
            "--format",
            "json",
            "--no-index-repair",
            "--no-evolve",
            "--no-conversations",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert payload["safe_apply_count"] == 1
    assert payload["lanes"][0]["risk"] == "safe_apply"
    assert payload["lanes"][0]["actions"][0]["action"] == "update_maturity"
    assert "memory govern auto --apply" in payload["next_commands"]

    unchanged, _ = store.get(item.id)
    assert unchanged.maturity == "raw"
    assert unchanged.abstraction == "L0"


def test_govern_plan_filters_by_action_and_category(
    tmp_brain_dir: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    store = ItemsStore(tmp_brain_dir / "items")
    long_summary = MemoryItem(
        id="mem-20260618-170003-cli-plan-long-summary",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="Long summary item",
        summary="x" * 220,
        tags=["quality"],
    )
    expired = MemoryItem(
        id="mem-20250101-170004-cli-plan-expired",
        type=MemoryType.signal,
        created_at=datetime.now(timezone.utc) - timedelta(days=90),
        title="Expired signal",
        summary="Expired signal summary",
        tags=["signal"],
    )
    store.write(long_summary, "long summary body")
    store.write(expired, "expired body")

    result = runner.invoke(
        app,
        [
            "govern",
            "plan",
            "--format",
            "json",
            "--action",
            "review_quality",
            "--category",
            "summary_too_long",
            "--no-index-repair",
            "--no-evolve",
            "--no-conversations",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["filters"] == {
        "action": "review_quality",
        "category": "summary_too_long",
    }
    assert payload["action_count"] == 1
    assert payload["action_counts"] == {"review_quality": 1}
    assert payload["category_counts"] == {"summary_too_long": 1}
    assert payload["lanes"][1]["actions"][0]["item_ids"] == [long_summary.id]


def test_govern_plan_summary_too_long_includes_rewrite_preview(
    tmp_brain_dir: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    store = ItemsStore(tmp_brain_dir / "items")
    long_text = (
        "This summary is intentionally long and starts with the key locator. "
        "It continues with detailed validation notes, command names, ownership "
        "context, and historical details that make the summary exceed the "
        "governance threshold for concise metadata."
    )
    item = MemoryItem(
        id="mem-20260618-170005-cli-plan-summary-preview",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="Summary rewrite preview",
        summary=long_text,
        tags=["quality"],
    )
    store.write(item, "summary body")

    result = runner.invoke(
        app,
        [
            "govern",
            "plan",
            "--format",
            "json",
            "--action",
            "review_quality",
            "--category",
            "summary_too_long",
            "--no-index-repair",
            "--no-evolve",
            "--no-conversations",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    details = payload["lanes"][1]["actions"][0]["details"]
    assert details["summary_rewrite"]["current_length"] == len(long_text)
    assert details["summary_rewrite"]["target_length"] == 200
    assert len(details["summary_rewrite"]["candidate_summary"]) <= 200

    unchanged, _ = store.get(item.id)
    assert unchanged.summary == long_text


def test_govern_apply_summary_rewrites_dry_run_and_apply_and_rollback(
    tmp_brain_dir: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    store = ItemsStore(tmp_brain_dir / "items")
    original = (
        "This summary is intentionally long and starts with the key locator. "
        "It continues with detailed validation notes, command names, ownership "
        "context, and historical details that make the summary exceed the "
        "governance threshold for concise metadata."
    )
    item = MemoryItem(
        id="mem-20260618-170006-cli-apply-summary",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="Apply summary rewrite",
        summary=original,
        tags=["quality"],
    )
    store.write(item, "summary body")

    dry_run = runner.invoke(
        app,
        [
            "govern",
            "apply-summary-rewrites",
            "--dry-run",
            "--format",
            "json",
            "--limit",
            "1",
        ],
    )

    assert dry_run.exit_code == 0, dry_run.output
    dry_payload = json.loads(dry_run.output)
    assert dry_payload["candidate_count"] == 1
    assert dry_payload["applied_count"] == 0
    unchanged, _ = store.get(item.id)
    assert unchanged.summary == original

    applied = runner.invoke(
        app,
        [
            "govern",
            "apply-summary-rewrites",
            "--format",
            "json",
            "--limit",
            "1",
        ],
    )

    assert applied.exit_code == 0, applied.output
    apply_payload = json.loads(applied.output)
    assert apply_payload["candidate_count"] == 1
    assert apply_payload["applied_count"] == 1
    assert apply_payload["snapshot_sha"]
    updated, _ = store.get(item.id)
    assert updated.summary != original
    assert len(updated.summary) <= 200

    rollback = runner.invoke(app, ["govern", "apply-summary-rewrites", "--rollback"])

    assert rollback.exit_code == 0, rollback.output
    restored, _ = store.get(item.id)
    assert restored.summary == original
