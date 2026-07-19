from __future__ import annotations

import json
import os
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


def test_govern_plan_lifecycle_category_reports_stale_signal_and_handoff(
    tmp_brain_dir: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    store = ItemsStore(tmp_brain_dir / "items")
    stale_signal = MemoryItem(
        id="mem-20260101-170007-cli-plan-stale-signal",
        type=MemoryType.signal,
        created_at=datetime.now(timezone.utc) - timedelta(days=60),
        title="Stale hook signal",
        summary="Stale hook signal summary",
        tags=["runtime"],
    )
    stale_handoff = MemoryItem(
        id="mem-20260101-170008-cli-plan-stale-handoff",
        type=MemoryType.handoff,
        created_at=datetime.now(timezone.utc) - timedelta(days=45),
        title="Stale benchmark handoff",
        summary="Stale benchmark handoff summary",
        tags=["handoff"],
    )
    fresh_signal = MemoryItem(
        id="mem-20260618-170009-cli-plan-fresh-signal",
        type=MemoryType.signal,
        created_at=datetime.now(timezone.utc) - timedelta(days=3),
        title="Fresh hook signal",
        summary="Fresh hook signal summary",
        tags=["runtime"],
    )
    for item in (stale_signal, stale_handoff, fresh_signal):
        store.write(item, f"{item.title}\nbody")

    result = runner.invoke(
        app,
        [
            "govern",
            "plan",
            "--format",
            "json",
            "--category",
            "lifecycle",
            "--no-index-repair",
            "--no-evolve",
            "--no-conversations",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["filters"] == {
        "action": None,
        "category": "lifecycle",
    }
    assert payload["action_counts"] == {"review_archive": 2}
    assert payload["category_counts"] == {"lifecycle": 2}
    actions = payload["lanes"][1]["actions"]
    assert {action["item_ids"][0] for action in actions} == {
        stale_signal.id,
        stale_handoff.id,
    }
    assert {action["details"]["lifecycle_type"] for action in actions} == {
        "signal",
        "handoff",
    }
    assert all(
        action["details"]["recommended_action"] == "archive_or_supersede"
        for action in actions
    )

    unchanged, _ = store.get(stale_signal.id)
    assert unchanged.title == stale_signal.title


def test_govern_plan_lifecycle_json_includes_read_only_review_queue(
    tmp_brain_dir: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    store = ItemsStore(tmp_brain_dir / "items")
    item = MemoryItem(
        id="mem-20260101-170011-cli-plan-lifecycle-queue",
        type=MemoryType.signal,
        created_at=datetime.now(timezone.utc) - timedelta(days=60),
        title="Lifecycle queue signal",
        summary="Lifecycle queue signal summary",
        tags=["runtime"],
    )
    store.write(item, "Lifecycle queue signal\nbody")

    result = runner.invoke(
        app,
        [
            "govern",
            "plan",
            "--format",
            "json",
            "--category",
            "lifecycle",
            "--no-index-repair",
            "--no-evolve",
            "--no-conversations",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["review_queue"] == [
        {
            "item_id": item.id,
            "action": "review_archive",
            "category": "lifecycle",
            "title": "Review stale signal: Lifecycle queue signal",
            "read_command": f"memory read {item.id} --head 2000 --view detail",
            "recommended_next": "archive_after_review",
            "can_auto_apply": False,
            "boundary": "确认是否已有更新 item 可以 supersede，不能确认再 archive",
            "candidates": [],
            "reviewed_at": None,
        }
    ]

    unchanged, _ = store.get(item.id)
    assert unchanged.title == item.title


def test_govern_plan_lifecycle_markdown_includes_review_details(
    tmp_brain_dir: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    store = ItemsStore(tmp_brain_dir / "items")
    item = MemoryItem(
        id="mem-20260101-170010-cli-plan-stale-signal-markdown",
        type=MemoryType.signal,
        created_at=datetime.now(timezone.utc) - timedelta(days=60),
        title="Stale markdown signal",
        summary="Stale markdown signal summary",
        tags=["runtime"],
    )
    store.write(item, "Stale markdown signal\nbody")

    result = runner.invoke(
        app,
        [
            "govern",
            "plan",
            "--format",
            "markdown",
            "--category",
            "lifecycle",
            "--no-index-repair",
            "--no-evolve",
            "--no-conversations",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "**review_archive** [lifecycle]" in result.output
    assert "## Review Checklist" in result.output
    assert f"`memory read {item.id} --head 2000 --view detail`" in result.output
    assert "确认是否已有更新 item 可以 supersede，不能确认再 archive" in result.output
    assert "lifecycle_type: signal" in result.output
    assert "stale_after_days: 30" in result.output
    assert "recommended_action: archive_or_supersede" in result.output


def test_govern_apply_lifecycle_dry_run_reports_queue_item_without_archiving(
    tmp_brain_dir: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    store = ItemsStore(tmp_brain_dir / "items")
    item = MemoryItem(
        id="mem-20260101-170012-cli-apply-lifecycle-dry-run",
        type=MemoryType.signal,
        created_at=datetime.now(timezone.utc) - timedelta(days=60),
        title="Lifecycle dry run signal",
        summary="Lifecycle dry run signal summary",
        tags=["runtime"],
    )
    store.write(item, "Lifecycle dry run signal\nbody")

    result = runner.invoke(
        app,
        [
            "govern",
            "apply-lifecycle",
            item.id,
            "--dry-run",
            "--format",
            "json",
            "--no-index-repair",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert payload["requested"] == [item.id]
    assert payload["archived"] == []
    assert payload["skipped"] == []
    assert payload["candidates"][0]["item_id"] == item.id
    assert payload["candidates"][0]["can_auto_apply"] is False
    assert (tmp_brain_dir / "items" / f"{item.id}.md").exists()
    assert not (tmp_brain_dir / "items" / "archived" / f"{item.id}.md").exists()


def test_govern_apply_lifecycle_apply_archives_only_review_queue_items(
    tmp_brain_dir: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    store = ItemsStore(tmp_brain_dir / "items")
    stale = MemoryItem(
        id="mem-20260101-170013-cli-apply-lifecycle-stale",
        type=MemoryType.handoff,
        created_at=datetime.now(timezone.utc) - timedelta(days=45),
        title="Lifecycle apply stale handoff",
        summary="Lifecycle apply stale handoff summary",
        tags=["handoff"],
    )
    fresh = MemoryItem(
        id="mem-20260701-170014-cli-apply-lifecycle-fresh",
        type=MemoryType.signal,
        created_at=datetime.now(timezone.utc) - timedelta(days=3),
        title="Lifecycle apply fresh signal",
        summary="Lifecycle apply fresh signal summary",
        tags=["runtime"],
    )
    store.write(stale, "Lifecycle stale handoff\nbody")
    store.write(fresh, "Lifecycle fresh signal\nbody")

    result = runner.invoke(
        app,
        [
            "govern",
            "apply-lifecycle",
            stale.id,
            fresh.id,
            "--apply",
            "--format",
            "json",
            "--no-index-repair",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dry_run"] is False
    assert payload["archived"] == [stale.id]
    assert payload["skipped"] == [
        {
            "id": fresh.id,
            "reason": "not_in_lifecycle_review_queue",
        }
    ]
    assert not (tmp_brain_dir / "items" / f"{stale.id}.md").exists()
    assert (tmp_brain_dir / "items" / "archived" / f"{stale.id}.md").exists()
    assert (tmp_brain_dir / "items" / f"{fresh.id}.md").exists()


def test_govern_apply_lifecycle_action_flags_default_to_preview(
    tmp_brain_dir: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    store = ItemsStore(tmp_brain_dir / "items")
    obsolete = MemoryItem(
        id="mem-20260101-171001-cli-lifecycle-obsolete",
        type=MemoryType.signal,
        created_at=datetime.now(timezone.utc) - timedelta(days=60),
        project="agent-memory-hub",
        title="Obsolete lifecycle signal",
        summary="Obsolete lifecycle signal summary",
        tags=["runtime"],
    )
    replacement = MemoryItem(
        id="mem-20260701-171002-cli-lifecycle-replacement",
        type=MemoryType.signal,
        created_at=datetime.now(timezone.utc) - timedelta(days=2),
        project="agent-memory-hub",
        title="Replacement lifecycle signal",
        summary="Replacement lifecycle signal summary",
        tags=["runtime"],
    )
    store.write(obsolete, "obsolete body")
    store.write(replacement, "replacement body")
    original_obsolete = (store.items_dir / f"{obsolete.id}.md").read_bytes()
    original_replacement = (store.items_dir / f"{replacement.id}.md").read_bytes()

    commands = [
        ["--supersede", f"{obsolete.id}:{replacement.id}"],
        ["--archive", obsolete.id],
        ["--keep-active", obsolete.id],
    ]
    for action_args in commands:
        result = runner.invoke(
            app,
            [
                "govern",
                "apply-lifecycle",
                *action_args,
                "--format",
                "json",
                "--no-index-repair",
            ],
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["dry_run"] is True
        assert payload["results"][0]["status"] == "ready"
        assert payload["results"][0]["reason"] == "OK"
        assert payload["results"][0]["dry_run"] is True
        assert payload["results"][0]["index_repair_required"] is False
        assert (store.items_dir / f"{obsolete.id}.md").read_bytes() == original_obsolete
        assert (store.items_dir / f"{replacement.id}.md").read_bytes() == original_replacement
        assert not (store.items_dir / "archived" / f"{obsolete.id}.md").exists()
        assert not (tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl").exists()


def test_govern_apply_lifecycle_pair_option_supports_escaped_colons_in_both_ids(
    tmp_brain_dir: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    store = ItemsStore(tmp_brain_dir / "items")
    old = MemoryItem(
        id="mem-20260101-171009-cli-old:colon",
        type=MemoryType.signal,
        created_at=datetime.now(timezone.utc) - timedelta(days=60),
        project="amh",
        title="Old colon",
        summary="Old colon",
    )
    new = MemoryItem(
        id="mem-20260701-171010-cli-new:colon",
        type=MemoryType.signal,
        created_at=datetime.now(timezone.utc) - timedelta(days=2),
        project="amh",
        title="New colon",
        summary="New colon",
    )
    store.write(old, "old")
    store.write(new, "new")
    escaped_old = old.id.replace(":", "\\:")
    escaped_new = new.id.replace(":", "\\:")
    pair = f"{escaped_old}:{escaped_new}"

    result = runner.invoke(
        app,
        [
            "govern",
            "apply-lifecycle",
            "--supersede",
            pair,
            "--format",
            "json",
            "--no-index-repair",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["results"][0]["item_id"] == old.id
    assert payload["results"][0]["replacement_id"] == new.id
    assert payload["results"][0]["status"] == "ready"


def test_govern_apply_lifecycle_conflicting_actions_exit_two_with_json(
    tmp_brain_dir: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    item_id = "mem-20260101-171003-cli-lifecycle-conflict"

    result = runner.invoke(
        app,
        [
            "govern",
            "apply-lifecycle",
            "--archive",
            item_id,
            "--keep-active",
            item_id,
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 2
    assert json.loads(result.output) == {
        "error": "CONFLICTING_ACTIONS",
        "item_id": item_id,
    }


def test_govern_apply_lifecycle_rejects_noncanonical_ids_with_json(
    tmp_brain_dir: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))

    result = runner.invoke(
        app,
        [
            "govern",
            "apply-lifecycle",
            "--supersede",
            "../old:mem-20260701-171004-cli-valid-replacement",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 2
    assert json.loads(result.output) == {
        "error": "INVALID_ITEM_ID",
        "item_id": "../old",
    }


def test_govern_apply_lifecycle_rejects_format_before_applying(
    tmp_brain_dir: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    store = ItemsStore(tmp_brain_dir / "items")
    item = MemoryItem(
        id="mem-20260101-171005-cli-lifecycle-invalid-format",
        type=MemoryType.signal,
        created_at=datetime.now(timezone.utc) - timedelta(days=60),
        title="Invalid format must not mutate",
        summary="Invalid format must not mutate",
        tags=["runtime"],
    )
    store.write(item, "body")

    result = runner.invoke(
        app,
        [
            "govern",
            "apply-lifecycle",
            "--archive",
            item.id,
            "--apply",
            "--format",
            "yaml",
        ],
    )

    assert result.exit_code == 2
    assert (store.items_dir / f"{item.id}.md").exists()
    assert not (store.items_dir / "archived" / f"{item.id}.md").exists()


def test_govern_apply_lifecycle_supersede_and_revert_use_old_new_direction(
    tmp_brain_dir: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    store = ItemsStore(tmp_brain_dir / "items")
    old = MemoryItem(
        id="mem-20260101-171006-cli-lifecycle-old",
        type=MemoryType.signal,
        created_at=datetime.now(timezone.utc) - timedelta(days=60),
        project="agent-memory-hub",
        title="Old lifecycle state",
        summary="Old lifecycle state",
        tags=["runtime"],
    )
    new = MemoryItem(
        id="mem-20260701-171007-cli-lifecycle-new",
        type=MemoryType.signal,
        created_at=datetime.now(timezone.utc) - timedelta(days=2),
        project="agent-memory-hub",
        title="New lifecycle state",
        summary="New lifecycle state",
        tags=["runtime"],
    )
    store.write(old, "old body")
    store.write(new, "new body")

    applied = runner.invoke(
        app,
        [
            "govern",
            "apply-lifecycle",
            "--supersede",
            f"{old.id}:{new.id}",
            "--apply",
            "--format",
            "json",
            "--no-index-repair",
        ],
    )

    assert applied.exit_code == 0, applied.output
    applied_payload = json.loads(applied.output)
    assert applied_payload["results"][0]["status"] == "applied"
    old_after, _ = store.get(old.id)
    new_after, _ = store.get(new.id)
    assert old_after.superseded_by == new.id
    assert old.id in new_after.refs.mems

    reverted = runner.invoke(
        app,
        [
            "govern",
            "apply-lifecycle",
            "--revert-supersession",
            f"{old.id}:{new.id}",
            "--apply",
            "--format",
            "json",
            "--no-index-repair",
        ],
    )

    assert reverted.exit_code == 0, reverted.output
    reverted_payload = json.loads(reverted.output)
    assert reverted_payload["results"][0]["status"] == "reverted"
    old_after, _ = store.get(old.id)
    new_after, _ = store.get(new.id)
    assert old_after.superseded_by is None
    assert old.id not in new_after.refs.mems


def test_govern_apply_lifecycle_archive_rolls_back_when_directory_fsync_fails(
    tmp_brain_dir: Path,
    monkeypatch,
) -> None:
    from agent_brain.memory.governance import lifecycle_review
    from agent_brain.memory.store import durable_fs
    from agent_brain.memory.store.durable_fs import SecureDirectory

    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    store = ItemsStore(tmp_brain_dir / "items")
    item = MemoryItem(
        id="mem-20260101-171008-cli-lifecycle-archive-fsync",
        type=MemoryType.signal,
        created_at=datetime.now(timezone.utc) - timedelta(days=60),
        title="Archive fsync rollback",
        summary="Archive fsync rollback",
        tags=["runtime"],
    )
    store.write(item, "body")
    original_rename = os.rename
    original_fsync = SecureDirectory.fsync
    state = {"renamed": False, "failed": False}

    def tracking_rename(source, destination, *args, **kwargs):
        result = original_rename(source, destination, *args, **kwargs)
        if source == f"{item.id}.md":
            state["renamed"] = True
        return result

    def fail_after_archive_rename(directory):
        if state["renamed"] and not state["failed"]:
            state["failed"] = True
            raise OSError("simulated directory fsync failure")
        return original_fsync(directory)

    monkeypatch.setattr(os, "rename", tracking_rename)
    monkeypatch.setattr(SecureDirectory, "fsync", fail_after_archive_rename)
    monkeypatch.setattr(lifecycle_review, "lifecycle_mutation_capability", lambda: True)
    monkeypatch.setattr(durable_fs, "lifecycle_mutation_capability", lambda: True)

    result = runner.invoke(
        app,
        [
            "govern",
            "apply-lifecycle",
            "--archive",
            item.id,
            "--apply",
            "--format",
            "json",
            "--no-index-repair",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["results"][0]["status"] == "blocked"
    assert payload["results"][0]["reason"] == "ARCHIVE_FAILED"
    assert (store.items_dir / f"{item.id}.md").exists()
    assert not (store.items_dir / "archived" / f"{item.id}.md").exists()


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
