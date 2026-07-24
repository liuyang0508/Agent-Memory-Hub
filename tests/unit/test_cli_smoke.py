"""Smoke tests for CLI commands that had NO direct test (QC test-gap closure).

These commands were exercised only indirectly (logic tested, CLI entry never
invoked) — so a broken command wiring would ship silently. Each test invokes the
real command via CliRunner against a seeded brain and asserts a clean exit.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_brain.interfaces.cli import app
from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Source

runner = CliRunner()


def _cli_pending_record(record_id: str, *, body: str = "queued body") -> dict[str, object]:
    return {
        "v": 2,
        "op": "write",
        "origin": "hook",
        "record_id": record_id,
        "enqueued_at": "2026-07-23T11:00:00+00:00",
        "original_created_at": "2026-07-23T10:00:00+00:00",
        "item": {
            "type": "fact",
            "title": f"queued {record_id}",
            "summary": f"summary {record_id}",
            "body": body,
            "tags": ["pending"],
            "sensitivity": "internal",
            "project": "amh",
            "tenant_id": "tenant-a",
        },
    }


def _cli_feedback_record(title: str) -> dict[str, object]:
    return {
        "v": 1,
        "op": "write",
        "origin": "hook",
        "ts": "2026-07-23T11:00:00+00:00",
        "item": {
            "type": "feedback",
            "title": title,
            "summary": f"summary {title}",
            "body": f"body {title}",
            "tags": ["pending"],
            "sensitivity": "internal",
        },
    }


def _tree_snapshot(root: Path) -> list[tuple[str, bytes | None]]:
    return [
        (
            path.relative_to(root).as_posix(),
            (
                os.fsencode(os.readlink(path))
                if path.is_symlink()
                else None if path.is_dir() else path.read_bytes()
            ),
        )
        for path in sorted(root.rglob("*"))
    ]


@pytest.fixture
def seeded_brain(tmp_brain_dir: Path, monkeypatch):
    from agent_brain.agent_integrations import claude_code, codex

    home = tmp_brain_dir.parent / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")
    monkeypatch.setenv("AGENT_MEMORY_HUB_BIN", str(home / ".local" / "bin"))
    monkeypatch.setattr(codex, "AGENTS_MD", home / ".codex" / "AGENTS.md")
    monkeypatch.setattr(codex, "CODEX_HOOKS_JSON", home / ".codex" / "hooks.json")
    monkeypatch.setattr(codex, "CODEX_CONFIG_TOML", home / ".codex" / "config.toml")
    monkeypatch.setattr(claude_code, "SETTINGS_PATH", home / ".claude" / "settings.json")
    monkeypatch.setattr(claude_code, "AWARENESS_PATH", home / ".claude" / "CLAUDE.md")
    store = ItemsStore(tmp_brain_dir / "items")
    for i, (typ, title) in enumerate([
        ("fact", "Python GIL"), ("decision", "use SSE"), ("episode", "debug crash"),
    ]):
        store.write(MemoryItem(
            id=f"mem-20260101-00000{i}-seed-{typ}", type=MemoryType(typ),
            created_at=datetime.now(timezone.utc), title=title, summary=f"summary {title}",
            project="alpha", tags=["test", typ],
        ), f"body {title}")
    yield tmp_brain_dir


@pytest.mark.parametrize("argv", [
    ["list-recent"],
    ["list-recent", "--type", "fact"],
    ["stats"],
    ["stats", "--project", "alpha"],
    ["decay-status"],
    ["health"],
    ["doctor"],
    ["doctor", "--offline"],
    ["tier", "show"],
    ["entity", "list"],
])
def test_cli_command_runs_clean(seeded_brain, argv):
    result = runner.invoke(app, argv)
    assert result.exit_code == 0, f"{argv} exited {result.exit_code}:\n{result.output}"


def test_cli_search_runs(seeded_brain):
    result = runner.invoke(app, ["search", "Python"])
    assert result.exit_code == 0, result.output


def test_cli_raw_search_cannot_record_an_injection_cohort(seeded_brain):
    from agent_brain.memory.context.injection_cohorts import iter_injection_cohorts

    result = runner.invoke(
        app,
        ["search", "Python", "--record-injection-cohort"],
    )

    assert result.exit_code == 2
    assert "--record-injection-cohort requires --context-firewall" in result.output
    assert list(iter_injection_cohorts(seeded_brain)) == []


def test_cli_sync_pending_dry_run_outputs_json_without_replay(tmp_brain):
    import json

    from agent_brain.memory.store.pending import PendingQueue, enqueue_write_record

    os.environ["BRAIN_DIR"] = str(tmp_brain)
    enqueue_write_record({
        "v": 1,
        "op": "write",
        "origin": "hook",
        "item": {
            "type": "fact",
            "title": "queued cli fact",
            "summary": "queued cli summary",
            "body": "queued cli body",
            "tags": ["cli"],
            "sensitivity": "internal",
            "confidence": 0.7,
        },
    })

    result = runner.invoke(app, ["sync-pending", "--dry-run", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["total"] == 1
    assert payload["records"][0]["title"] == "queued cli fact"
    assert PendingQueue().depth() == 1


def test_cli_sync_pending_bare_command_is_always_preview(tmp_brain):
    import json

    from agent_brain.memory.store.pending import PendingQueue, enqueue_write_record

    enqueue_write_record({"op": "write", "item": {"title": "bare preview", "summary": "s"}})

    result = runner.invoke(app, ["sync-pending", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["records"][0]["title"] == "bare preview"
    assert PendingQueue().depth() == 1
    assert list((tmp_brain / "items").glob("*.md")) == []


def test_cli_sync_pending_summary_only_json_is_low_sensitivity(tmp_brain):
    import json

    from agent_brain.memory.store.pending import PendingQueue, enqueue_write_record

    enqueue_write_record(
        {
            "op": "write",
            "record_id": "PRIVATE_CLI_RECORD_CANARY",
            "item": {
                "title": "PRIVATE_CLI_TITLE_CANARY",
                "summary": "PRIVATE_CLI_SUMMARY_CANARY",
            },
        }
    )

    result = runner.invoke(
        app,
        ["sync-pending", "--summary-only", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema_version"] == 1
    assert payload["total"] == 1
    assert payload["classification_counts"] == {"ready": 1}
    assert "records" not in payload
    assert "PRIVATE_CLI_RECORD_CANARY" not in result.output
    assert "PRIVATE_CLI_TITLE_CANARY" not in result.output
    assert "PRIVATE_CLI_SUMMARY_CANARY" not in result.output
    assert PendingQueue().depth() == 1


def test_cli_sync_pending_apply_summary_only_omits_per_record_results(tmp_brain):
    import json

    from agent_brain.memory.store.pending import PendingQueue, enqueue_write_record

    enqueue_write_record(
        {
            "op": "write",
            "record_id": "PRIVATE_APPLY_CLI_CANARY",
            "item": {"title": "apply summary", "summary": "low sensitivity"},
        }
    )

    result = runner.invoke(
        app,
        [
            "sync-pending",
            "--apply",
            "--record",
            "PRIVATE_APPLY_CLI_CANARY",
            "--summary-only",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["written"] == 1
    assert payload["status_counts"] == {"written": 1}
    assert payload["receipt"]["state"] == "completed"
    assert "results" not in payload
    assert "PRIVATE_APPLY_CLI_CANARY" not in result.output
    assert PendingQueue().depth() == 0


def test_cli_sync_pending_completion_receipt_failure_exits_one(
    tmp_brain,
    monkeypatch,
):
    import json

    from agent_brain.memory.governance.pending_receipts import (
        append_pending_receipt as real_append,
    )
    from agent_brain.memory.store import pending as pending_module
    from agent_brain.memory.store.pending import enqueue_write_record

    enqueue_write_record(
        {
            "op": "write",
            "record_id": "cli-completion-receipt-failure",
            "item": {"title": "completion failure", "summary": "must exit one"},
        }
    )
    calls = 0

    def fail_second_append(brain, receipt):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated completion receipt failure")
        real_append(brain, receipt)

    monkeypatch.setattr(pending_module, "append_pending_receipt", fail_second_append)

    result = runner.invoke(
        app,
        [
            "sync-pending",
            "--apply",
            "--record",
            "cli-completion-receipt-failure",
            "--summary-only",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["written"] == 1
    assert payload["governance_reason"] == "PENDING_RECEIPT_COMPLETION_FAILED"
    assert payload["receipt"]["state"] == "incomplete"


def test_cli_sync_pending_apply_requires_a_selection(tmp_brain):
    from agent_brain.memory.store.pending import PendingQueue, enqueue_write_record

    enqueue_write_record({"op": "write", "item": {"title": "selection required"}})

    result = runner.invoke(app, ["sync-pending", "--apply", "--format", "json"])

    assert result.exit_code == 2
    assert "--record or --safe-only" in result.output
    assert PendingQueue().depth() == 1


def test_cli_sync_pending_rejects_record_and_safe_only_together(tmp_brain):
    result = runner.invoke(
        app,
        [
            "sync-pending",
            "--apply",
            "--record",
            "one",
            "--safe-only",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


def test_cli_sync_pending_explicit_missing_record_emits_json_then_exits_one(tmp_brain):
    import json

    result = runner.invoke(
        app,
        ["sync-pending", "--apply", "--record", "missing", "--format", "json"],
    )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["results"][0]["reason"] == "RECORD_ID_NOT_FOUND"


def test_cli_sync_pending_safe_only_audit_blocked_emits_json_then_exits_one(tmp_brain):
    import json

    pending = tmp_brain / "pending"
    pending.mkdir(exist_ok=True)
    (pending / "malformed.jsonl").write_text("{bad json\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["sync-pending", "--apply", "--safe-only", "--format", "json"],
    )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["review_required"] == 1
    assert payload["results"][0]["classification"] == "malformed"


def test_cli_sync_pending_safe_only_no_ready_record_can_exit_zero(
    tmp_brain, monkeypatch
):
    from agent_brain.memory.store import pending as pending_module
    from agent_brain.memory.store.pending import enqueue_write_record

    monkeypatch.setattr(
        pending_module,
        "_utc_now",
        lambda: datetime(2026, 7, 20, tzinfo=timezone.utc),
    )
    enqueue_write_record(
        {
            "v": 2,
            "op": "write",
            "record_id": "old-signal",
            "enqueued_at": "2025-01-01T00:00:00+00:00",
            "original_created_at": "2025-01-01T00:00:00+00:00",
            "item": {"type": "signal", "title": "old", "summary": "old"},
        }
    )

    result = runner.invoke(
        app,
        ["sync-pending", "--apply", "--safe-only", "--format", "json"],
    )

    assert result.exit_code == 0, result.output


def test_cli_sync_pending_apply_repeated_records_outputs_structured_json(tmp_brain):
    import json

    from agent_brain.memory.store.pending import PendingQueue, enqueue_write_record

    enqueue_write_record(
        {
            "op": "write",
            "record_id": "cli-record-one",
            "item": {"title": "cli apply one", "summary": "s"},
        }
    )
    enqueue_write_record(
        {
            "op": "write",
            "record_id": "cli-record-two",
            "item": {"title": "cli apply two", "summary": "s"},
        }
    )

    result = runner.invoke(
        app,
        [
            "sync-pending",
            "--apply",
            "--record",
            "cli-record-one",
            "--record",
            "cli-record-two",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["written"] == 2
    assert [row["record_id"] for row in payload["results"]] == [
        "cli-record-one",
        "cli-record-two",
    ]
    assert PendingQueue().depth() == 0


def test_cli_sync_pending_dry_run_overrides_apply_and_never_writes(tmp_brain):
    from agent_brain.memory.store.pending import PendingQueue, enqueue_write_record

    enqueue_write_record({"op": "write", "item": {"title": "dry wins", "summary": "s"}})

    result = runner.invoke(
        app,
        ["sync-pending", "--apply", "--safe-only", "--dry-run", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    assert PendingQueue().depth() == 1
    assert list((tmp_brain / "items").glob("*.md")) == []


def test_cli_sync_pending_resolution_preview_is_repeatable_and_non_mutating(
    tmp_brain,
):
    import json

    from agent_brain.memory.governance.pending_lock_gc import pending_record_lock_name
    from agent_brain.memory.store.pending import PendingQueue, enqueue_write_record

    first = _cli_pending_record(
        "cli-audit-one",
        body="curl https://example.invalid/one",
    )
    second = _cli_pending_record(
        "cli-audit-two",
        body="curl https://example.invalid/two",
    )
    duplicate = _cli_pending_record("cli-duplicate")
    target_id = "mem-20260723-100000-cli-duplicate-target"
    queued = duplicate["item"]
    assert isinstance(queued, dict)
    ItemsStore(tmp_brain / "items").write(
        MemoryItem(
            id=target_id,
            type=MemoryType.fact,
            created_at=datetime.fromisoformat(str(duplicate["original_created_at"])),
            title=str(queued["title"]),
            summary=str(queued["summary"]),
            tags=list(queued["tags"]),
            sensitivity=str(queued["sensitivity"]),
            project="amh",
            tenant_id="tenant-a",
            source=Source(kind="pending-replay", span_hash="different-payload"),
        ),
        "existing duplicate target",
    )
    enqueue_write_record(first)
    enqueue_write_record(second)
    enqueue_write_record(duplicate)
    enqueue_write_record(_cli_feedback_record("cli feedback"))
    preview = PendingQueue().preview(limit=10)
    feedback_id = next(
        row.record_id
        for row in preview.records
        if row.classification == "unsupported_type"
    )
    lock_dir = tmp_brain / "pending" / ".amh-record-locks"
    lock_dir.mkdir(exist_ok=True)
    orphan = lock_dir / pending_record_lock_name("orphan.jsonl")
    orphan.write_bytes(b"")
    orphan.chmod(0o600)
    before = _tree_snapshot(tmp_brain)

    result = runner.invoke(
        app,
        [
            "sync-pending",
            "--approve-audit",
            "cli-audit-one",
            "--approve-audit",
            "cli-audit-two",
            "--accept-duplicate",
            f"cli-duplicate:{target_id}",
            "--convert-type",
            f"{feedback_id}:decision",
            "--gc-orphan-locks",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert [row["action"] for row in payload["results"]] == [
        "approve_audit",
        "approve_audit",
        "accept_duplicate",
        "convert_type",
    ]
    assert {row["status"] for row in payload["results"]} == {"ready"}
    assert payload["lock_gc"]["orphan"] == 1
    assert payload["lock_gc"]["deleted"] == 0
    assert _tree_snapshot(tmp_brain) == before


def test_cli_sync_pending_applies_resolution_and_completes_receipt(tmp_brain):
    import json

    from agent_brain.memory.store.pending import PendingQueue, enqueue_write_record

    enqueue_write_record(
        _cli_pending_record(
            "cli-approve-apply",
            body="curl https://example.invalid/apply",
        )
    )

    result = runner.invoke(
        app,
        [
            "sync-pending",
            "--approve-audit",
            "cli-approve-apply",
            "--apply",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dry_run"] is False
    assert payload["results"][0]["status"] == "applied"
    assert payload["receipt"]["state"] == "completed"
    assert PendingQueue().depth() == 0
    assert len(list((tmp_brain / "items").glob("*.md"))) == 1


def test_cli_sync_pending_standalone_gc_is_preview_first(tmp_brain):
    import json

    lock_dir = tmp_brain / "pending" / ".amh-record-locks"
    lock_dir.mkdir(parents=True)
    orphan = lock_dir / f"{'1' * 32}.lock"
    orphan.write_bytes(b"")
    orphan.chmod(0o600)

    preview = runner.invoke(
        app,
        ["sync-pending", "--gc-orphan-locks", "--format", "json"],
    )

    assert preview.exit_code == 0, preview.output
    assert json.loads(preview.output)["dry_run"] is True
    assert orphan.exists()
    assert not (tmp_brain / "runtime").exists()

    applied = runner.invoke(
        app,
        ["sync-pending", "--gc-orphan-locks", "--apply", "--format", "json"],
    )

    assert applied.exit_code == 0, applied.output
    payload = json.loads(applied.output)
    assert payload["dry_run"] is False
    assert payload["lock_gc"]["deleted"] == 1
    assert not orphan.exists()


@pytest.mark.parametrize(
    "selection",
    [
        ["--record", "pending-one"],
        ["--safe-only"],
    ],
)
def test_cli_sync_pending_rejects_resolution_selection_conflicts(
    tmp_brain,
    selection,
):
    result = runner.invoke(
        app,
        [
            "sync-pending",
            *selection,
            "--approve-audit",
            "pending-two",
            "--apply",
        ],
    )

    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


@pytest.mark.parametrize(
    ("option", "message"),
    [
        (["--accept-duplicate", "missing-separator"], "requires ID:ITEM"),
        (["--accept-duplicate", "record:"], "requires ID:ITEM"),
        (["--accept-duplicate", "record:extra:item"], "requires ID:ITEM"),
        (["--convert-type", "record:fact"], "requires ID:decision"),
        (["--convert-type", "record/extra:decision"], "requires ID:decision"),
    ],
)
def test_cli_sync_pending_rejects_malformed_resolution_options(
    tmp_brain,
    option,
    message,
):
    result = runner.invoke(app, ["sync-pending", *option, "--format", "json"])

    assert result.exit_code == 2
    assert message in result.output


def test_cli_sync_pending_parses_colons_inside_record_and_item_ids(tmp_brain):
    import json

    from agent_brain.memory.store.pending import enqueue_write_record

    duplicate = _cli_pending_record("cli:duplicate")
    queued = duplicate["item"]
    assert isinstance(queued, dict)
    target_id = "mem-20260723-100000-cli-target:tail"
    ItemsStore(tmp_brain / "items").write(
        MemoryItem(
            id=target_id,
            type=MemoryType.fact,
            created_at=datetime.fromisoformat(str(duplicate["original_created_at"])),
            title=str(queued["title"]),
            summary=str(queued["summary"]),
            tags=list(queued["tags"]),
            sensitivity=str(queued["sensitivity"]),
            project="amh",
            tenant_id="tenant-a",
            source=Source(kind="pending-replay", span_hash="different-payload"),
        ),
        "duplicate target",
    )
    feedback = _cli_pending_record("cli:feedback")
    feedback_item = feedback["item"]
    assert isinstance(feedback_item, dict)
    feedback_item["type"] = "feedback"
    enqueue_write_record(duplicate)
    enqueue_write_record(feedback)

    result = runner.invoke(
        app,
        [
            "sync-pending",
            "--accept-duplicate",
            f"cli:duplicate:{target_id}",
            "--convert-type",
            "cli:feedback:decision",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert [
        (row["action"], row["record_id"], row["target"])
        for row in payload["results"]
    ] == [
        ("accept_duplicate", "cli:duplicate", target_id),
        ("convert_type", "cli:feedback", "decision"),
    ]


def test_cli_sync_pending_rejects_ambiguous_colon_split_without_resolution(
    tmp_brain,
    monkeypatch,
):
    from agent_brain.memory.store.pending import PendingQueue

    monkeypatch.setattr(
        PendingQueue,
        "resolve",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ambiguous input must not reach resolution")
        ),
    )
    value = (
        "record:mem-20260723-100000-first:"
        "mem-20260723-100000-second"
    )

    result = runner.invoke(
        app,
        ["sync-pending", "--accept-duplicate", value, "--format", "json"],
    )

    assert result.exit_code == 2
    assert "requires ID:ITEM" in result.output


def test_cli_sync_pending_resolution_apply_failure_exits_one(tmp_brain):
    import json

    result = runner.invoke(
        app,
        [
            "sync-pending",
            "--approve-audit",
            "missing",
            "--apply",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 1
    assert json.loads(result.output)["results"][0]["status"] != "applied"


def test_cli_sync_pending_resolution_apply_requires_completed_receipt(
    tmp_brain,
    monkeypatch,
):
    import json

    from agent_brain.memory.store.pending import (
        PendingQueue,
        PendingResolutionResult,
        PendingResolutionStats,
    )

    monkeypatch.setattr(
        PendingQueue,
        "resolve",
        lambda *_args, **_kwargs: PendingResolutionStats(
            dry_run=False,
            results=[
                PendingResolutionResult(
                    action="approve_audit",
                    record_id="receiptless",
                    status="applied",
                    reason="PENDING_RESOLUTION_APPLIED",
                    classification="audit_blocked",
                )
            ],
        ),
    )

    result = runner.invoke(
        app,
        [
            "sync-pending",
            "--approve-audit",
            "receiptless",
            "--apply",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["results"][0]["status"] == "applied"
    assert payload["receipt"] is None


@pytest.mark.parametrize(
    ("results", "dry_run"),
    [
        ([], True),
        (
            [
                {
                    "action": "convert_type",
                    "record_id": "manifest-record",
                    "target": "decision",
                }
            ],
            True,
        ),
        (
            [
                {
                    "action": "approve_audit",
                    "record_id": "wrong-record",
                    "target": None,
                }
            ],
            True,
        ),
        (
            [
                {
                    "action": "approve_audit",
                    "record_id": "manifest-record",
                    "target": "unexpected",
                }
            ],
            True,
        ),
        (
            [
                {
                    "action": "approve_audit",
                    "record_id": "manifest-record",
                    "target": ["unhashable"],
                }
            ],
            True,
        ),
        (
            [
                {
                    "action": "approve_audit",
                    "record_id": "manifest-record",
                    "target": None,
                },
                {
                    "action": "approve_audit",
                    "record_id": "manifest-record",
                    "target": None,
                },
            ],
            True,
        ),
        (
            [
                {
                    "action": "approve_audit",
                    "record_id": "manifest-record",
                    "target": None,
                }
            ],
            False,
        ),
    ],
)
def test_cli_sync_pending_resolution_preview_requires_exact_manifest_coverage(
    tmp_brain,
    monkeypatch,
    results,
    dry_run,
):
    import json

    from agent_brain.memory.store.pending import (
        PendingQueue,
        PendingResolutionResult,
        PendingResolutionStats,
    )

    resolution_results = [
        PendingResolutionResult(
            status="ready",
            reason="PENDING_RESOLUTION_READY",
            classification="audit_blocked",
            **row,
        )
        for row in results
    ]
    monkeypatch.setattr(
        PendingQueue,
        "resolve",
        lambda *_args, **_kwargs: PendingResolutionStats(
            dry_run=dry_run,
            results=resolution_results,
        ),
    )

    result = runner.invoke(
        app,
        [
            "sync-pending",
            "--approve-audit",
            "manifest-record",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert json.loads(result.output)["dry_run"] is dry_run


def test_cli_sync_pending_identical_resolution_requests_are_idempotent(
    tmp_brain,
):
    import json

    from agent_brain.memory.store.pending import enqueue_write_record

    enqueue_write_record(
        _cli_pending_record(
            "duplicate-request",
            body="curl https://example.invalid/duplicate-request",
        )
    )

    result = runner.invoke(
        app,
        [
            "sync-pending",
            "--approve-audit",
            "duplicate-request",
            "--approve-audit",
            "duplicate-request",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload["results"]) == 1
    assert payload["results"][0]["status"] == "ready"


def test_cli_sync_pending_resolution_preview_failure_exits_one(tmp_brain):
    import json

    result = runner.invoke(
        app,
        [
            "sync-pending",
            "--approve-audit",
            "missing",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 1
    assert json.loads(result.output)["results"][0]["status"] != "ready"


@pytest.mark.parametrize(
    "selection",
    [
        ["--record", "pending-one"],
        ["--safe-only"],
    ],
)
@pytest.mark.parametrize("apply_option", [[], ["--apply"]])
def test_cli_sync_pending_rejects_gc_with_legacy_selection_without_mutation(
    tmp_brain,
    selection,
    apply_option,
):
    before = _tree_snapshot(tmp_brain)

    result = runner.invoke(
        app,
        [
            "sync-pending",
            *selection,
            "--gc-orphan-locks",
            *apply_option,
        ],
    )

    assert result.exit_code == 2
    assert "--gc-orphan-locks requires standalone or resolution mode" in result.output
    assert _tree_snapshot(tmp_brain) == before


def test_cli_sync_pending_gc_unsafe_entry_exits_one(tmp_brain):
    lock_dir = tmp_brain / "pending" / ".amh-record-locks"
    lock_dir.mkdir(parents=True)
    (lock_dir / "unsafe.lock").write_bytes(b"")

    result = runner.invoke(
        app,
        ["sync-pending", "--gc-orphan-locks", "--format", "json"],
    )

    assert result.exit_code == 1


def test_cli_sync_pending_resolution_summary_omits_identifiers(tmp_brain):
    import json

    from agent_brain.memory.store.pending import enqueue_write_record

    record_id = "PRIVATE_RESOLUTION_CLI_CANARY"
    enqueue_write_record(
        _cli_pending_record(
            record_id,
            body="curl https://example.invalid/private-canary",
        )
    )

    result = runner.invoke(
        app,
        [
            "sync-pending",
            "--approve-audit",
            record_id,
            "--summary-only",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["action_counts"] == {"approve_audit": 1}
    assert "results" not in payload
    assert record_id not in result.output


def test_cli_search_explain_prints_retrieval_trace(tmp_brain):
    store = ItemsStore(tmp_brain / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_brain / "index.db", embedding_dim=embedder.dim)
    item = MemoryItem(
        id="mem-20260620-020001-cli-trace",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="CLI trace",
        summary="cli trace locator",
        refs={"urls": ["https://example.test/cli-trace"]},
    )
    body = "cli trace body"
    store.write(item, body)
    idx.upsert(item, body, embedding=embedder.embed(body))
    idx.close()

    explained = runner.invoke(app, [
        "search",
        "cli trace",
        "--top-k",
        "1",
        "--format",
        "text",
        "--explain",
    ])
    plain = runner.invoke(app, [
        "search",
        "cli trace",
        "--top-k",
        "1",
        "--format",
        "text",
    ])

    assert explained.exit_code == 0, explained.output
    assert "trace: rrf(" in explained.output
    assert "final#1" in explained.output
    assert plain.exit_code == 0, plain.output
    assert "trace: rrf(" not in plain.output


def test_cli_search_context_firewall_filters_bad_injection_candidates(tmp_brain):
    store = ItemsStore(tmp_brain / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_brain / "index.db", embedding_dim=embedder.dim)
    now = datetime.now(timezone.utc)
    rows = [
        MemoryItem(
            id="mem-20260101-000001-goodfact",
            type=MemoryType.fact,
            created_at=now,
            title="Python sourced fact",
            summary="Python context with source",
            refs={"urls": ["https://example.test/python"]},
        ),
        MemoryItem(
            id="mem-20260101-000002-nosource",
            type=MemoryType.fact,
            created_at=now,
            title="Python unsourced fact",
            summary="Python context without source",
        ),
        MemoryItem(
            id="mem-20260101-000003-oldsignal",
            type=MemoryType.signal,
            created_at=now - timedelta(days=30),
            title="Python old signal",
            summary="Python stale blocker",
        ),
        MemoryItem(
            id="mem-20260101-000004-gooddup",
            type=MemoryType.episode,
            created_at=now,
            title="Python duplicate context",
            summary="Python repeated episode",
        ),
        MemoryItem(
            id="mem-20260101-000005-baddup",
            type=MemoryType.episode,
            created_at=now,
            title="Python duplicate context",
            summary="Python repeated episode",
        ),
    ]
    for item in rows:
        body = f"{item.title} body Python"
        store.write(item, body)
        idx.upsert(item, body, embedding=embedder.embed(body))
    idx.close()

    result = runner.invoke(app, ["search", "Python", "--top-k", "5", "--format", "text", "--context-firewall"])

    assert result.exit_code == 0, result.output
    assert "Python sourced fact" in result.output
    assert "Python duplicate context" in result.output
    assert "Python unsourced fact" not in result.output
    assert "Python old signal" not in result.output
    assert result.output.count("Python duplicate context") == 1
    idx = HubIndex(tmp_brain / "index.db", embedding_dim=embedder.dim)
    access_counts = idx.connection.execute(
        "SELECT SUM(access_count) FROM items_meta"
    ).fetchone()[0]
    assert access_counts == 2


@pytest.mark.parametrize("query", ["memory", ""])
def test_cli_gateway_noninjectable_query_never_falls_back_to_raw_hits(
    tmp_brain,
    query,
):
    store = ItemsStore(tmp_brain / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_brain / "index.db", embedding_dim=embedder.dim)
    item = MemoryItem(
        id="mem-20260711-020000-cli-weak-gateway",
        type=MemoryType.episode,
        created_at=datetime.now(timezone.utc),
        title="memory",
        summary="memory",
        confidence=0.9,
    )
    store.write(item, "memory")
    idx.upsert(item, "memory", embedding=embedder.embed("memory"))
    idx.close()

    result = runner.invoke(app, [
        "search", query, "--format", "text", "--context-firewall",
    ])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "no matches"
    assert item.id not in result.output


def test_cli_gateway_failure_never_falls_back_to_raw_hits(tmp_brain, monkeypatch):
    store = ItemsStore(tmp_brain / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_brain / "index.db", embedding_dim=embedder.dim)
    item = MemoryItem(
        id="mem-20260711-020001-cli-gateway-failure",
        type=MemoryType.episode,
        created_at=datetime.now(timezone.utc),
        title="CLI gateway failure boundary",
        summary="CLI gateway failure boundary",
        confidence=0.9,
    )
    body = "CLI gateway failure raw body"
    store.write(item, body)
    idx.upsert(item, body, embedding=embedder.embed(body))
    idx.close()

    from agent_brain.interfaces.cli.commands import query as query_module

    def fail_closed(*_args, **_kwargs):
        raise RuntimeError("synthetic gateway failure")

    monkeypatch.setattr(
        query_module,
        "build_injection_context",
        fail_closed,
        raising=False,
    )
    result = runner.invoke(app, [
        "search",
        "CLI gateway failure boundary",
        "--format",
        "text",
        "--context-firewall",
    ])

    assert result.exit_code != 0
    assert item.id not in result.output
    assert item.title not in result.output
    assert item.summary not in result.output
    assert body not in result.output


def test_cli_gateway_reports_ghost_hydrate_only_as_aggregate(tmp_brain, caplog):
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_brain / "index.db", embedding_dim=embedder.dim)
    ghost = MemoryItem(
        id="mem-20260711-020002-cli-ghost-private-title",
        type=MemoryType.episode,
        created_at=datetime.now(timezone.utc),
        title="CLI ghost private title",
        summary="CLI ghost private summary",
        confidence=0.9,
    )
    body = "CLI ghost private body"
    idx.upsert(ghost, body, embedding=embedder.embed(body))
    idx.close()

    result = runner.invoke(app, [
        "search",
        "CLI ghost private title",
        "--format",
        "text",
        "--context-firewall",
        "--record-recall-gap",
    ])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "no matches"
    assert "surface=cli-search reason=hydrate_error count=1" in caplog.text
    for forbidden in (ghost.id, ghost.title, ghost.summary, body):
        assert forbidden not in result.output
        assert forbidden not in caplog.text
    from agent_brain.memory.governance.recall_events import iter_gap_records

    gaps = list(iter_gap_records(tmp_brain))
    assert len(gaps) == 1
    assert gaps[0].reason == "all_candidates_rejected"
    assert "retrieved_count=1" in gaps[0].evidence
    assert "included_count=0" in gaps[0].evidence
    assert "hydrate_error_count=1" in gaps[0].evidence
    assert "excluded_count=1" in gaps[0].evidence
    assert "excluded_reason.hydrate_error=1" in gaps[0].evidence
    raw_gap = (tmp_brain / "runtime" / "recall-gaps.jsonl").read_text(
        encoding="utf-8",
    )
    for forbidden in (ghost.id, ghost.title, ghost.summary, body):
        assert forbidden not in raw_gap
    check = HubIndex(tmp_brain / "index.db", embedding_dim=embedder.dim)
    assert check.get_decay_data([ghost.id])[ghost.id][4] == 0
    check.close()


def test_cli_safe_plus_ghost_records_partial_gap_and_surface_cohort_metrics(
    tmp_brain,
):
    from agent_brain.memory.context.injection_cohorts import latest_injection_cohort
    from agent_brain.memory.context.injection_gateway import (
        HYDRATE_ERROR_REASON,
        INJECTION_EXCLUSION_REASONS,
    )
    from agent_brain.memory.governance.recall_events import iter_gap_records

    store = ItemsStore(tmp_brain / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_brain / "index.db", embedding_dim=embedder.dim)
    safe = MemoryItem(
        id="mem-20260711-020005-cli-hydrate-safe",
        type=MemoryType.episode,
        created_at=datetime.now(timezone.utc),
        title="CLI hydrate aggregate safe",
        summary="CLI hydrate aggregate safe",
        confidence=0.9,
    )
    ghost = MemoryItem(
        id="mem-20260711-020006-cli-hydrate-ghost-secret",
        type=MemoryType.episode,
        created_at=datetime.now(timezone.utc),
        title="CLI hydrate aggregate ghost secret",
        summary="CLI hydrate aggregate ghost secret",
        confidence=0.9,
    )
    safe_body = "CLI hydrate aggregate safe body"
    ghost_body = "CLI hydrate aggregate ghost private body sentinel"
    store.write(safe, safe_body)
    idx.upsert(safe, safe_body, embedding=embedder.embed(safe_body))
    idx.upsert(ghost, ghost_body, embedding=embedder.embed(ghost_body))
    idx.close()

    query = "CLI hydrate aggregate"
    result = runner.invoke(app, [
        "search",
        query,
        "--top-k",
        "5",
        "--format",
        "text",
        "--context-firewall",
        "--record-recall-gap",
        "--record-injection-cohort",
        "--adapter",
        "codex",
        "--session",
        "sess-hydrate-partial",
    ])

    assert result.exit_code == 0, result.output
    assert safe.title in result.output
    for forbidden in (ghost.id, ghost.title, ghost.summary, ghost_body):
        assert forbidden not in result.output

    gaps = list(iter_gap_records(tmp_brain))
    assert len(gaps) == 1
    assert gaps[0].reason == "partial_candidates_rejected"
    assert "retrieved_count=2" in gaps[0].evidence
    assert "included_count=1" in gaps[0].evidence
    assert "hydrate_error_count=1" in gaps[0].evidence
    assert "excluded_count=1" in gaps[0].evidence
    assert f"excluded_reason.{HYDRATE_ERROR_REASON}=1" in gaps[0].evidence
    emitted_reasons = {
        entry.removeprefix("excluded_reason.").split("=", 1)[0]
        for entry in gaps[0].evidence
        if entry.startswith("excluded_reason.")
    }
    assert emitted_reasons <= INJECTION_EXCLUSION_REASONS
    raw_gap = (tmp_brain / "runtime" / "recall-gaps.jsonl").read_text(
        encoding="utf-8",
    )
    for forbidden in (ghost.id, ghost.title, ghost.summary, ghost_body):
        assert forbidden not in raw_gap
    assert query not in raw_gap

    cohort = latest_injection_cohort(
        tmp_brain,
        adapter="codex",
        session_id="sess-hydrate-partial",
    )
    assert cohort is not None
    assert cohort.item_ids == (safe.id,)
    metrics = cohort.pack_metrics
    assert metrics is not None
    assert metrics["candidate_count"] == 2
    assert metrics["raw_candidate_count"] == 2
    assert metrics["gateway_candidate_count"] == 1
    assert metrics["included_count"] == 1
    assert metrics["hydrate_error_count"] == 1
    assert metrics["excluded_count"] == 1
    assert metrics["excluded_reasons"] == {HYDRATE_ERROR_REASON: 1}
    for forbidden in (ghost.id, ghost.title, ghost.summary, ghost_body):
        assert forbidden not in repr(metrics)

    check = HubIndex(tmp_brain / "index.db", embedding_dim=embedder.dim)
    decay = check.get_decay_data([safe.id, ghost.id])
    assert decay[safe.id][4] == 1
    assert decay[ghost.id][4] == 0
    check.close()


def test_cli_raw_search_preserves_single_access_record(tmp_brain):
    store = ItemsStore(tmp_brain / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_brain / "index.db", embedding_dim=embedder.dim)
    item = MemoryItem(
        id="mem-20260711-020003-cli-raw-access",
        type=MemoryType.episode,
        created_at=datetime.now(timezone.utc),
        title="CLI raw access boundary",
        summary="CLI raw access boundary",
        confidence=0.9,
    )
    body = "CLI raw access boundary"
    store.write(item, body)
    idx.upsert(item, body, embedding=embedder.embed(body))
    idx.close()

    result = runner.invoke(app, [
        "search", "CLI raw access boundary", "--format", "text",
    ])

    assert result.exit_code == 0, result.output
    assert item.title in result.output
    check = HubIndex(tmp_brain / "index.db", embedding_dim=embedder.dim)
    assert check.get_decay_data([item.id])[item.id][4] == 1
    check.close()


def test_cli_prompt_renderer_requires_gateway_context_pack():
    from agent_brain.interfaces.cli.commands.query import _render_text_hit

    item = MemoryItem(
        id="mem-20260711-020004-cli-pack-required",
        type=MemoryType.episode,
        created_at=datetime.now(timezone.utc),
        title="CLI gateway pack required",
        summary="CLI gateway pack required",
    )

    with pytest.raises(
        RuntimeError,
        match="gateway context pack required for prompt output",
    ):
        _render_text_hit(
            item,
            body="must not be packed here",
            include_audit_metadata=True,
        )


def test_cli_context_firewall_packs_each_included_item_once(tmp_brain, monkeypatch):
    from agent_brain.memory.context import injection_gateway

    store = ItemsStore(tmp_brain / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_brain / "index.db", embedding_dim=embedder.dim)
    item = MemoryItem(
        id="mem-20260711-020005-cli-single-pack",
        type=MemoryType.episode,
        created_at=datetime.now(timezone.utc),
        title="CLI single gateway pack boundary",
        summary="CLI single gateway pack boundary",
        confidence=0.9,
    )
    body = "CLI single gateway pack boundary detail"
    store.write(item, body)
    idx.upsert(item, body, embedding=embedder.embed(body))
    idx.close()

    original_pack_decisions = injection_gateway.pack_decisions
    pack_calls = 0

    def counting_pack_decisions(*args, **kwargs):
        nonlocal pack_calls
        pack_calls += 1
        return original_pack_decisions(*args, **kwargs)

    monkeypatch.setattr(
        injection_gateway,
        "pack_decisions",
        counting_pack_decisions,
    )

    result = runner.invoke(app, [
        "search",
        "CLI single gateway pack boundary",
        "--top-k",
        "1",
        "--format",
        "text",
        "--context-firewall",
    ])

    assert result.exit_code == 0, result.output
    assert item.title in result.output
    assert pack_calls == 1


def test_cli_search_context_firewall_applies_cohort_gate(tmp_brain):
    store = ItemsStore(tmp_brain / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_brain / "index.db", embedding_dim=embedder.dim)
    now = datetime.now(timezone.utc)
    item = MemoryItem(
        id="mem-20260101-000006-dws-only",
        type=MemoryType.episode,
        created_at=now,
        title="DWS verification",
        summary="DWS 验证通过",
    )
    body = "DWS 验证通过"
    store.write(item, body)
    idx.upsert(item, body, embedding=embedder.embed(body))
    idx.close()

    result = runner.invoke(app, [
        "search",
        "dws linux 验证",
        "--top-k",
        "5",
        "--format",
        "text",
        "--context-firewall",
    ])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "no matches"


def test_cli_context_firewall_recalls_multiple_memory_types_for_anchored_query(tmp_brain):
    os.environ["BRAIN_DIR"] = str(tmp_brain)
    os.environ["MEMORY_HUB_TEST_EMBEDDING"] = "1"
    store = ItemsStore(tmp_brain / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_brain / "index.db", embedding_dim=embedder.dim)
    now = datetime.now(timezone.utc)
    rows = [
        (
            MemoryType.decision,
            "召回矩阵 decision 场景",
            "召回矩阵 decision 场景说明",
            {"files": ["docs/decision.md"]},
        ),
        (
            MemoryType.fact,
            "召回矩阵 fact 场景",
            "召回矩阵 fact 场景说明",
            {"files": ["docs/fact.md"]},
        ),
        (
            MemoryType.signal,
            "召回矩阵 signal 场景",
            "召回矩阵 signal 场景说明",
            {},
        ),
        (
            MemoryType.handoff,
            "召回矩阵 handoff 场景",
            "召回矩阵 handoff 场景说明",
            {},
        ),
        (
            MemoryType.artifact,
            "召回矩阵 artifact 场景",
            "召回矩阵 artifact 场景说明",
            {},
        ),
    ]
    for index, (memory_type, title, summary, refs) in enumerate(rows):
        item = MemoryItem(
            id=f"mem-20260628-1210{index:02d}-matrix-{memory_type.value}",
            type=memory_type,
            created_at=now,
            title=title,
            summary=summary,
            refs=refs,
        )
        body = f"{title} body 召回矩阵"
        store.write(item, body)
        idx.upsert(item, body, embedding=embedder.embed(body))
    idx.close()

    result = runner.invoke(app, [
        "search",
        "为什么召回矩阵没有进入后处理",
        "--top-k",
        "5",
        "--format",
        "text",
        "--context-firewall",
    ])

    assert result.exit_code == 0, result.output
    assert "召回矩阵 decision 场景" in result.output
    assert "召回矩阵 fact 场景" in result.output
    assert "召回矩阵 signal 场景" in result.output
    assert "召回矩阵 handoff 场景" in result.output
    assert "召回矩阵 artifact 场景" in result.output


def test_cli_search_records_gap_when_firewall_rejects_all(tmp_brain):
    from agent_brain.memory.governance.recall_events import iter_gap_records

    store = ItemsStore(tmp_brain / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_brain / "index.db", embedding_dim=embedder.dim)
    item = MemoryItem(
        id="mem-20260101-000012-python-unsourced-gap",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="Python unsourced gap",
        summary="Python context without source",
    )
    body = "Python context without source"
    store.write(item, body)
    idx.upsert(item, body, embedding=embedder.embed(body))
    idx.close()

    result = runner.invoke(app, [
        "search",
        "Python",
        "--top-k",
        "5",
        "--format",
        "text",
        "--context-firewall",
        "--record-recall-gap",
        "--adapter",
        "codex",
        "--session",
        "sess-gap",
        "--cwd",
        "/repo",
    ])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "no matches"
    gaps = list(iter_gap_records(tmp_brain))
    assert len(gaps) == 1
    assert gaps[0].reason == "all_candidates_rejected"
    assert gaps[0].query.startswith("sha256:")
    assert gaps[0].injected_ids == ()
    assert gaps[0].rejected_ids == ()
    assert gaps[0].adapter == "codex"
    assert gaps[0].session_id.startswith("sha256:")
    assert gaps[0].cwd.startswith("sha256:")
    assert "retrieved_count=1" in gaps[0].evidence
    assert "hydrate_error_count=0" in gaps[0].evidence
    assert "excluded_count=1" in gaps[0].evidence
    assert "excluded_reason.missing_source=1" in gaps[0].evidence
    raw_gap = (tmp_brain / "runtime" / "recall-gaps.jsonl").read_text(encoding="utf-8")
    for forbidden in ("Python", item.id, item.title, item.summary, body):
        assert forbidden not in raw_gap


def test_cli_search_records_gap_when_retrieval_is_empty(tmp_brain):
    from agent_brain.memory.governance.recall_events import iter_gap_records

    result = runner.invoke(app, [
        "search",
        "browser nothing matches",
        "--top-k",
        "5",
        "--format",
        "text",
        "--context-firewall",
        "--record-recall-gap",
        "--adapter",
        "codex",
        "--session",
        "sess-empty-gap",
        "--cwd",
        "/repo",
    ])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "no matches"
    gaps = list(iter_gap_records(tmp_brain))
    assert len(gaps) == 1
    assert gaps[0].reason == "empty_recall"
    assert gaps[0].query.startswith("sha256:")
    assert gaps[0].adapter == "codex"
    assert gaps[0].injected_ids == ()
    assert gaps[0].rejected_ids == ()
    assert gaps[0].evidence == (
        "retrieved_count=0",
        "included_count=0",
        "hydrate_error_count=0",
        "excluded_count=0",
    )
    raw_gap = (tmp_brain / "runtime" / "recall-gaps.jsonl").read_text(encoding="utf-8")
    assert "browser nothing matches" not in raw_gap


def test_cli_raw_empty_gap_uses_cli_query_not_hook_prompt(tmp_brain, monkeypatch):
    import hashlib

    from agent_brain.memory.governance.recall_events import iter_gap_records

    monkeypatch.setenv("AGENT_MEMORY_HUB_RAW_QUERY", "SECRET_HOOK_PROMPT")
    result = runner.invoke(app, [
        "search",
        "explicit raw diagnostic query",
        "--record-recall-gap",
    ])

    assert result.exit_code == 0, result.output
    gaps = list(iter_gap_records(tmp_brain))
    assert len(gaps) == 1
    assert gaps[0].query == "sha256:" + hashlib.sha256(
        b"explicit raw diagnostic query"
    ).hexdigest()
    assert "SECRET_HOOK_PROMPT" not in repr(gaps[0])


def test_cli_search_empty_gap_distinguishes_candidate_search_from_block(tmp_brain):
    from agent_brain.memory.governance.recall_events import iter_gap_records

    result = runner.invoke(app, [
        "search",
        "多Agent共享第二大脑 多agent共享第二大脑",
        "--top-k",
        "3",
        "--format",
        "text",
        "--context-firewall",
        "--record-recall-gap",
        "--adapter",
        "codex",
        "--session",
        "sess-mixed-empty",
        "--cwd",
        "/repo",
    ])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "no matches"
    gaps = list(iter_gap_records(tmp_brain))
    assert len(gaps) == 1
    assert gaps[0].reason == "empty_recall"
    assert gaps[0].query.startswith("sha256:")
    assert gaps[0].injected_ids == ()
    assert gaps[0].rejected_ids == ()
    assert gaps[0].evidence == (
        "retrieved_count=0",
        "included_count=0",
        "hydrate_error_count=0",
        "excluded_count=0",
    )
    raw_gap = (tmp_brain / "runtime" / "recall-gaps.jsonl").read_text(encoding="utf-8")
    assert "多Agent共享第二大脑" not in raw_gap
    assert "多agent共享第二大脑" not in raw_gap


def test_cli_search_records_partial_gap_when_firewall_rejects_risky_candidates(tmp_brain):
    from agent_brain.memory.governance.recall_events import iter_gap_records

    store = ItemsStore(tmp_brain / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_brain / "index.db", embedding_dim=embedder.dim)
    now = datetime.now(timezone.utc)
    keep = MemoryItem(
        id="mem-20260101-000017-python-sourced-keep",
        type=MemoryType.fact,
        created_at=now,
        title="Python sourced keep",
        summary="Python verified sourced context",
        refs={"urls": ["https://example.test/python-keep"]},
    )
    drop = MemoryItem(
        id="mem-20260101-000018-python-unsourced-drop",
        type=MemoryType.fact,
        created_at=now,
        title="Python unsourced drop",
        summary="Python risky unsourced context",
    )
    for item in (drop, keep):
        body = f"{item.title} {item.summary} Python"
        store.write(item, body)
        idx.upsert(item, body, embedding=embedder.embed(body))
    idx.close()

    result = runner.invoke(app, [
        "search",
        "Python",
        "--top-k",
        "5",
        "--format",
        "text",
        "--context-firewall",
        "--record-recall-gap",
        "--adapter",
        "codex",
        "--session",
        "sess-partial-gap",
        "--cwd",
        "/repo",
    ])

    assert result.exit_code == 0, result.output
    assert "Python sourced keep" in result.output
    assert "Python unsourced drop" not in result.output
    gaps = list(iter_gap_records(tmp_brain))
    assert len(gaps) == 1
    assert gaps[0].reason == "partial_candidates_rejected"
    assert gaps[0].query.startswith("sha256:")
    assert gaps[0].injected_ids == ()
    assert gaps[0].rejected_ids == ()
    assert gaps[0].adapter == "codex"
    assert gaps[0].session_id.startswith("sha256:")
    assert "retrieved_count=2" in gaps[0].evidence
    assert "hydrate_error_count=0" in gaps[0].evidence
    assert "included_count=1" in gaps[0].evidence
    assert "excluded_count=1" in gaps[0].evidence
    assert "excluded_reason.missing_source=1" in gaps[0].evidence
    raw_gap = (tmp_brain / "runtime" / "recall-gaps.jsonl").read_text(encoding="utf-8")
    for forbidden in ("Python", keep.id, keep.title, drop.id, drop.title, drop.summary):
        assert forbidden not in raw_gap


def test_partial_gap_rejections_ignore_query_mismatch_noise() -> None:
    from agent_brain.interfaces.cli.commands.query import _significant_rejected_decisions
    from agent_brain.memory.context.context_firewall import ContextCandidate, FirewallDecision

    item = MemoryItem(
        id="mem-20260101-000019-query-mismatch-noise",
        type=MemoryType.episode,
        created_at=datetime.now(timezone.utc),
        title="Query mismatch noise",
        summary="Retrieval overfetch candidate that does not match query",
    )
    mismatch = FirewallDecision(
        candidate=ContextCandidate(item, score=10.0),
        action="exclude",
        reasons=("query_mismatch",),
        score=10.0,
        effective_score=0.0,
    )
    missing_source = FirewallDecision(
        candidate=ContextCandidate(item, score=9.0),
        action="exclude",
        reasons=("missing_source",),
        score=9.0,
        effective_score=0.0,
    )
    max_items = FirewallDecision(
        candidate=ContextCandidate(item, score=8.0),
        action="exclude",
        reasons=("max_items_exceeded",),
        score=8.0,
        effective_score=0.0,
    )

    assert _significant_rejected_decisions([mismatch, max_items]) == []
    assert _significant_rejected_decisions([mismatch, missing_source, max_items]) == [missing_source]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("retrieved_count=0", True),
        ("hydrate_error_count=1", True),
        ("included_count=1", True),
        ("excluded_reason.hydrate_error=1", True),
        ("excluded_reason.missing_source=2", True),
        ("private_prompt_token=1", False),
        ("excluded_reason.secret_prompt_words=1", False),
        ("excluded_reason.missing_source=SECRET", False),
    ],
)
def test_cli_safe_gap_evidence_uses_closed_aggregate_vocabulary(value, expected):
    from agent_brain.interfaces.cli.commands.query import _is_aggregate_gap_evidence

    assert _is_aggregate_gap_evidence(value) is expected


def test_cli_search_context_firewall_excludes_scope_mismatch_state(tmp_brain):
    store = ItemsStore(tmp_brain / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_brain / "index.db", embedding_dim=embedder.dim)
    now = datetime.now(timezone.utc)
    keep = MemoryItem(
        id="mem-20260101-000009-browser-current",
        type=MemoryType.signal,
        created_at=now,
        title="Browser current repo",
        summary="Browser available in current repo",
        tags=["browser", "runtime"],
        validity={"cwd": "/repo/current", "adapter": "codex"},
    )
    drop = MemoryItem(
        id="mem-20260101-000010-browser-other",
        type=MemoryType.signal,
        created_at=now,
        title="Browser other repo",
        summary="Browser unavailable in another repo",
        tags=["browser", "runtime"],
        validity={"cwd": "/repo/other", "adapter": "codex"},
    )
    for item in (keep, drop):
        body = f"{item.title} body Browser"
        store.write(item, body)
        idx.upsert(item, body, embedding=embedder.embed(body))
    idx.close()

    result = runner.invoke(app, [
        "search",
        "Browser",
        "--top-k",
        "5",
        "--format",
        "text",
        "--context-firewall",
        "--adapter",
        "codex",
        "--cwd",
        "/repo/current",
    ])

    assert result.exit_code == 0, result.output
    assert "Browser current repo" in result.output
    assert "Browser other repo" not in result.output


def test_cli_search_context_firewall_keeps_cross_agent_artifact_guides(tmp_brain):
    store = ItemsStore(tmp_brain / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_brain / "index.db", embedding_dim=embedder.dim)
    item = MemoryItem(
        id="mem-20260624-161446-wukong-linux-guide",
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        title="悟空适配 Linux 指南",
        summary="覆盖 Linux 安装、回归测试、已修复能力和可用排障命令",
        tags=["wukong", "linux", "AppImage"],
        validity={"os": "darwin", "adapter": "codex"},
    )
    body = "悟空适配 Linux 文档产物，包含 install.sh、pytest passed、fixed、available。"
    store.write(item, body)
    idx.upsert(item, body, embedding=embedder.embed(body))
    idx.close()

    result = runner.invoke(app, [
        "search",
        "悟空 适配 Linux",
        "--top-k",
        "3",
        "--format",
        "text",
        "--context-firewall",
        "--adapter",
        "qoder_work",
        "--cwd",
        "<workspace>",
    ])

    assert result.exit_code == 0, result.output
    assert "悟空适配 Linux 指南" in result.output


def test_cli_search_context_firewall_overfetches_after_rejected_top_hit(tmp_brain):
    store = ItemsStore(tmp_brain / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_brain / "index.db", embedding_dim=embedder.dim)
    now = datetime.now(timezone.utc)
    wrong_scope = MemoryItem(
        id="mem-20260101-000010-python-other-scope",
        type=MemoryType.signal,
        created_at=now,
        title="Python Python Python wrong scope",
        summary="Python runtime state belongs to another repo",
        tags=["runtime", "status"],
        validity={"cwd": "/repo/other", "adapter": "codex"},
    )
    current_scope = MemoryItem(
        id="mem-20260101-000011-python-current-scope",
        type=MemoryType.fact,
        created_at=now,
        title="Python current sourced fact",
        summary="Python valid context for this repo",
        refs={"urls": ["https://example.test/python-current"]},
        validity={"cwd": "/repo/current", "adapter": "codex"},
    )
    for item in (wrong_scope, current_scope):
        body = f"{item.title} {item.summary} Python"
        store.write(item, body)
        idx.upsert(item, body, embedding=embedder.embed(body))
    idx.close()

    result = runner.invoke(app, [
        "search",
        "Python",
        "--top-k",
        "1",
        "--format",
        "text",
        "--context-firewall",
        "--adapter",
        "codex",
        "--cwd",
        "/repo/current",
    ])

    assert result.exit_code == 0, result.output
    assert "Python current sourced fact" in result.output
    assert "Python Python Python wrong scope" not in result.output


def test_cli_search_context_firewall_applies_prefer_type_before_top_k(tmp_brain):
    store = ItemsStore(tmp_brain / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_brain / "index.db", embedding_dim=embedder.dim)
    now = datetime.now(timezone.utc)
    noisy_episode = MemoryItem(
        id="mem-20260101-000012-python-noisy-episode",
        type=MemoryType.episode,
        created_at=now,
        title="Python noisy episode",
        summary="Python " * 20,
    )
    critical_decision = MemoryItem(
        id="mem-20260101-000013-python-critical-decision",
        type=MemoryType.decision,
        created_at=now,
        title="Python critical decision",
        summary="Python critical decision",
        refs={"urls": ["https://example.test/python-decision"]},
    )
    for item in (noisy_episode, critical_decision):
        body = f"{item.title} {item.summary} Python"
        store.write(item, body)
        idx.upsert(item, body, embedding=embedder.embed(body))
    idx.close()

    result = runner.invoke(app, [
        "search",
        "Python",
        "--top-k",
        "1",
        "--format",
        "text",
        "--context-firewall",
        "--prefer-type",
        "decision,episode",
    ])

    assert result.exit_code == 0, result.output
    assert "Python critical decision" in result.output
    assert "Python noisy episode" not in result.output


def test_cli_search_context_firewall_text_includes_compact_context_pack_hint(tmp_brain):
    store = ItemsStore(tmp_brain / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_brain / "index.db", embedding_dim=embedder.dim)
    item = MemoryItem(
        id="mem-20260101-000014-python-audit-metadata",
        type=MemoryType.fact,
        created_at=datetime(2026, 1, 1, 8, 30, tzinfo=timezone.utc),
        title="Python audit metadata",
        summary="Python context with visible metadata",
        project="alpha",
        tags=["python", "evidence"],
        refs={
            "urls": ["https://example.test/python-audit"],
            "files": ["/repo/current/docs/python.md"],
            "resources": ["res-20260101-083000-python-a1b2c3d4"],
        },
        validity={"cwd": "/repo/current", "adapter": "codex"},
        support_count=2,
        contradict_count=1,
        gain_score=0.25,
    )
    body = "Python body-only audit marker"
    store.write(item, body)
    idx.upsert(item, body, embedding=embedder.embed(body))
    idx.close()

    injected = runner.invoke(app, [
        "search",
        "Python audit metadata",
        "--top-k",
        "1",
        "--format",
        "text",
        "--context-firewall",
        "--adapter",
        "codex",
        "--cwd",
        "/repo/current",
    ])
    plain = runner.invoke(app, [
        "search",
        "Python audit metadata",
        "--top-k",
        "1",
        "--format",
        "text",
    ])

    assert injected.exit_code == 0, injected.output
    assert "view=locator" in injected.output
    assert "Python body-only audit marker" not in injected.output
    assert "packed=" in injected.output
    assert (
        'retrieve="memory read mem-20260101-000014-python-audit-metadata '
        '--head 2000 --view detail"'
    ) in injected.output
    assert "created_at=2026-01-01T08:30:00+00:00" not in injected.output
    assert "project=alpha" not in injected.output
    assert "tags=python,evidence" not in injected.output
    assert "scope=cwd=/repo/current adapter=codex" not in injected.output
    assert "refs=urls:https://example.test/python-audit" not in injected.output
    assert "files:/repo/current/docs/python.md" not in injected.output
    assert "resources:res-20260101-083000-python-a1b2c3d4" not in injected.output
    assert "feedback=support:2 contradict:1 gain:0.25" not in injected.output
    assert "meta:" not in injected.output

    assert plain.exit_code == 0, plain.output
    assert "Python audit metadata" in plain.output
    assert "created_at=" not in plain.output
    assert "refs=urls:" not in plain.output


def test_cli_broad_explicit_detail_warns_without_blocking_body(tmp_brain):
    store = ItemsStore(tmp_brain / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_brain / "index.db", embedding_dim=embedder.dim)
    item = MemoryItem(
        id="mem-20260715-000015-staged-detail-warning",
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        title="Staged detail warning",
        summary="staged warning locator",
        abstraction="L0",
        refs={"files": ["/tmp/staged-warning.log"]},
    )
    body = "staged cli body-only marker"
    store.write(item, body)
    idx.upsert(item, body, embedding=embedder.embed(item.context_views.locator))
    idx.close()

    result = runner.invoke(app, [
        "search",
        "Staged detail warning",
        "--top-k",
        "5",
        "--format",
        "text",
        "--verbosity",
        "detail",
    ])

    assert result.exit_code == 0, result.output
    assert "staged cli body-only marker" in result.stdout
    assert "bypasses staged recall" in result.stderr


def test_cli_search_context_firewall_text_uses_full_ids_for_feedback(tmp_brain):
    store = ItemsStore(tmp_brain / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_brain / "index.db", embedding_dim=embedder.dim)
    first = MemoryItem(
        id="mem-20260101-000015-python-first-full-id",
        type=MemoryType.episode,
        created_at=datetime.now(timezone.utc),
        title="Python first full id",
        summary="Python full id context one",
    )
    second = MemoryItem(
        id="mem-20260101-000016-python-second-full-id",
        type=MemoryType.episode,
        created_at=datetime.now(timezone.utc),
        title="Python second full id",
        summary="Python full id context two",
    )
    for item in (first, second):
        body = f"{item.title} {item.summary}"
        store.write(item, body)
        idx.upsert(item, body, embedding=embedder.embed(body))
    idx.close()

    injected = runner.invoke(app, [
        "search",
        "Python full id context",
        "--top-k",
        "2",
        "--format",
        "text",
        "--context-firewall",
    ])
    plain = runner.invoke(app, [
        "search",
        "Python full id context",
        "--top-k",
        "1",
        "--format",
        "text",
    ])

    assert injected.exit_code == 0, injected.output
    assert f"id:{first.id}" in injected.output
    assert f"id:{second.id}" in injected.output
    assert "id:mem-2026)" not in injected.output

    assert plain.exit_code == 0, plain.output
    assert f"id:{first.id}" not in plain.output


def test_cli_search_can_include_stale_state_for_audit(tmp_brain):
    store = ItemsStore(tmp_brain / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_brain / "index.db", embedding_dim=embedder.dim)
    old = MemoryItem(
        id="mem-20260101-000011-browser-stale",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc) - timedelta(days=5),
        title="Browser currently limited",
        summary="Browser unavailable due to permission denied",
        tags=["browser", "runtime"],
    )
    body = "browser browser standard browser unavailable permission denied"
    store.write(old, body)
    idx.upsert(old, body, embedding=embedder.embed(body))
    idx.close()

    default = runner.invoke(app, ["search", "browser", "--top-k", "5", "--format", "text"])
    audit = runner.invoke(
        app,
        [
            "search",
            "browser",
            "--top-k",
            "5",
            "--format",
            "text",
            "--include-stale-state",
        ],
    )

    assert default.exit_code == 0, default.output
    assert audit.exit_code == 0, audit.output
    assert "Browser currently limited" not in default.output
    assert "Browser currently limited" in audit.output


def test_cli_search_records_final_firewalled_injection_cohort(tmp_brain):
    from agent_brain.memory.context.injection_cohorts import latest_injection_cohort

    store = ItemsStore(tmp_brain / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_brain / "index.db", embedding_dim=embedder.dim)
    now = datetime.now(timezone.utc)
    keep = MemoryItem(
        id="mem-20260101-000007-python-keep",
        type=MemoryType.episode,
        created_at=now,
        title="Python verified implementation",
        summary="Python implementation context",
    )
    drop = MemoryItem(
        id="mem-20260101-000008-python-old-signal",
        type=MemoryType.signal,
        created_at=now - timedelta(days=30),
        title="Python stale signal",
        summary="Python stale blocker",
    )
    for item in (keep, drop):
        body = f"{item.title} body Python"
        store.write(item, body)
        idx.upsert(item, body, embedding=embedder.embed(body))
    idx.close()

    result = runner.invoke(app, [
        "search",
        "Python",
        "--top-k",
        "5",
        "--format",
        "text",
        "--context-firewall",
        "--explain",
        "--record-injection-cohort",
        "--adapter",
        "codex",
        "--session",
        "sess-search",
        "--cwd",
        "/repo",
    ])

    assert result.exit_code == 0, result.output
    assert "Python verified implementation" in result.output
    assert "Python stale signal" not in result.output
    cohort = latest_injection_cohort(tmp_brain, adapter="codex", session_id="sess-search")
    assert cohort is not None
    assert cohort.item_ids == (keep.id,)
    assert cohort.cwd == "/repo"
    assert cohort.query_sha256 is not None
    assert cohort.query_terms == ("Python",)
    assert cohort.pack_metrics is not None
    assert "items" not in cohort.pack_metrics
    assert cohort.pack_metrics["included_count"] == 1
    assert sum(cohort.pack_metrics["selected_views"].values()) == 1
    assert isinstance(cohort.pack_metrics["compressed_count"], int)
    assert keep.id not in repr(cohort.pack_metrics)
    trace_rows = cohort.pack_metrics["retrieval_trace"]
    assert isinstance(trace_rows, list)
    assert len(trace_rows) == 1
    trace = trace_rows[0]
    assert trace["final_rank"] == 1
    assert "stages" in trace


def test_cli_consolidate_dry_run(seeded_brain):
    result = runner.invoke(app, ["consolidate", "--project", "alpha"])
    assert result.exit_code == 0, result.output


def test_cli_anti_drift_semantic_runs(seeded_brain):
    result = runner.invoke(app, ["anti-drift", "--semantic", "--format", "json"])
    assert result.exit_code == 0, result.output


def test_api_docs_endpoint_rows_are_split():
    from agent_brain.interfaces.cli.commands.api_docs import API_ENDPOINTS

    assert len(API_ENDPOINTS) == 105
    assert any(method == "GET" and path == "/api/chain-logs" for method, path, _desc in API_ENDPOINTS)
    assert any(method == "GET" and path == "/api/chain-logs/{chain_id}" for method, path, _desc in API_ENDPOINTS)
    assert any(method == "GET" and path == "/api/health" for method, path, _desc in API_ENDPOINTS)
    assert any(method == "GET" and path == "/api/items/{item_id}" for method, path, _desc in API_ENDPOINTS)
    assert any(method == "GET" and path == "/api/data-flow" for method, path, _desc in API_ENDPOINTS)
    assert any(method == "GET" and path == "/api/memory-lineage" for method, path, _desc in API_ENDPOINTS)
    assert any(method == "GET" and path == "/api/governance/lifecycle-review" for method, path, _desc in API_ENDPOINTS)
    assert any(method == "GET" and path == "/api/agents/local-history" for method, path, _desc in API_ENDPOINTS)
    assert any(method == "POST" and path == "/api/agents/{agent}/local-history/sync" for method, path, _desc in API_ENDPOINTS)
    assert any(method == "POST" and path == "/api/governance/lifecycle-apply" for method, path, _desc in API_ENDPOINTS)
    assert any(method == "POST" and path == "/api/adapters/{name}/install-verify" for method, path, _desc in API_ENDPOINTS)
    assert any(method == "POST" and path == "/api/adapters/{name}/uninstall" for method, path, _desc in API_ENDPOINTS)
    assert not any(path == "/api/items/{id}" for _method, path, _desc in API_ENDPOINTS)


def test_api_docs_discovery_tolerates_missing_web_dependency():
    from agent_brain.interfaces.cli.commands.api_docs import discover_api_endpoints

    def missing_web_app(_name: str):
        raise ModuleNotFoundError("No module named 'fastapi'")

    assert discover_api_endpoints(import_module=missing_web_app) == []


def test_api_docs_cli_uses_current_web_route_count():
    result = runner.invoke(app, ["api-docs"])

    assert result.exit_code == 0, result.output
    assert "Total: 105 endpoints" in result.output
    assert "/api/chain-logs" in result.output
    assert "/api/chain-logs/{chain_id}" in result.output
    assert "/api/data-flow" in result.output
    assert "/api/memory-lineage" in result.output
    assert "/api/governance/lifecycle-review" in result.output
    assert "/api/governance/lifecycle-apply" in result.output
    assert "/api/adapters/{name}/install-verify" in result.output
