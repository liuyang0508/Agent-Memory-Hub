from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


def test_loop_create_writes_snapshot_and_event_without_raw_prompt(tmp_path: Path) -> None:
    from agent_brain.memory.loops.loop_store import LoopStore

    store = LoopStore(tmp_path)
    loop = store.create(
        goal="修复 README 预览样式路径",
        project="agent-memory-hub",
        adapter="codex",
        session_id="sess-loop",
        cwd="/repo",
        verification_plan=["python -m pytest tests/unit/test_docs_truth_contract.py -q"],
        trigger={"kind": "manual", "prompt": "raw prompt must not persist"},
        start=True,
        now=datetime(2026, 6, 23, 1, 0, tzinfo=timezone.utc),
    )

    assert loop.loop_id.startswith("loop-20260623-010000-")
    assert loop.status == "running"
    assert loop.goal == "修复 README 预览样式路径"
    assert loop.trigger == {"kind": "manual"}
    assert loop.verification_plan == ["python -m pytest tests/unit/test_docs_truth_contract.py -q"]

    snapshot = json.loads(
        (tmp_path / "runtime" / "loops" / f"{loop.loop_id}.json").read_text(encoding="utf-8")
    )
    assert snapshot["loop_id"] == loop.loop_id
    serialized = json.dumps(snapshot, ensure_ascii=False)
    assert "raw prompt must not persist" not in serialized
    assert "prompt" not in serialized
    assert "body" not in serialized

    events = (
        (tmp_path / "runtime" / "loop-events.jsonl")
        .read_text(encoding="utf-8")
        .strip()
        .splitlines()
    )
    assert len(events) == 2
    assert json.loads(events[0])["event_type"] == "created"
    assert json.loads(events[1])["event_type"] == "status_changed"


def test_loop_store_create_persists_contract_metadata(tmp_path: Path) -> None:
    from agent_brain.memory.loops.loop_store import LoopStore

    store = LoopStore(tmp_path)
    loop = store.create(
        goal="contract goal",
        metadata={
            "contract_id": "loop-contract-agent-memory-hub-adapter-doctor",
            "contract_schema_version": "loop-contract/v1",
            "contract_digest": "abc123",
            "contract_source_path": "/tmp/contract.yaml",
        },
    )

    stored = store.get(loop.loop_id)

    assert stored.metadata["contract_id"] == "loop-contract-agent-memory-hub-adapter-doctor"
    assert stored.metadata["contract_digest"] == "abc123"


def test_loop_store_human_gate_lifecycle_updates_metadata_and_events(tmp_path: Path) -> None:
    from agent_brain.memory.loops.loop_events import iter_loop_events
    from agent_brain.memory.loops.loop_store import LoopStore
    from agent_brain.memory.loops.loop_types import LoopTransitionError

    store = LoopStore(tmp_path)
    loop = store.create(
        goal="gate lifecycle",
        metadata={
            "contract_id": "contract-1",
            "contract_human_gates": [
                {
                    "id": "code_review",
                    "trigger": "mutation_action",
                    "reason": "runtime code changes require review",
                }
            ],
        },
        start=True,
    )

    opened = store.open_human_gate(
        loop.loop_id,
        gate_id="code_review",
        reason="review before merge",
        trigger="mutation_action",
    )
    assert opened.metadata["open_human_gates"] == [
        {
            "id": "code_review",
            "reason": "review before merge",
            "trigger": "mutation_action",
            "opened_at": opened.updated_at,
            "actor": "cli",
        }
    ]

    try:
        store.open_human_gate(loop.loop_id, gate_id="code_review", reason="again")
    except LoopTransitionError as exc:
        assert "already open" in str(exc)
    else:
        raise AssertionError("duplicate open gate should fail")

    approved = store.approve_human_gate(
        loop.loop_id,
        gate_id="code_review",
        note="reviewed by maintainer",
    )

    assert approved.metadata["open_human_gates"] == []
    assert approved.metadata["resolved_human_gates"][-1]["id"] == "code_review"
    assert approved.metadata["resolved_human_gates"][-1]["decision"] == "approved"
    assert approved.metadata["resolved_human_gates"][-1]["note"] == "reviewed by maintainer"

    events = list(iter_loop_events(tmp_path, loop_id=loop.loop_id))
    assert [event.event_type for event in events][-2:] == [
        "human_gate_opened",
        "human_gate_approved",
    ]


def test_loop_store_rejects_unknown_or_missing_human_gate(tmp_path: Path) -> None:
    from agent_brain.memory.loops.loop_store import LoopStore
    from agent_brain.memory.loops.loop_types import LoopTransitionError

    store = LoopStore(tmp_path)
    loop = store.create(
        goal="gate lifecycle",
        metadata={
            "contract_human_gates": [
                {
                    "id": "merge_main",
                    "trigger": "protected_branch",
                    "reason": "merge requires owner approval",
                }
            ]
        },
        start=True,
    )

    try:
        store.open_human_gate(loop.loop_id, gate_id="unknown", reason="bad")
    except LoopTransitionError as exc:
        assert "not defined by contract" in str(exc)
    else:
        raise AssertionError("unknown contract gate should fail")

    try:
        store.reject_human_gate(loop.loop_id, gate_id="merge_main", reason="not open")
    except LoopTransitionError as exc:
        assert "not open" in str(exc)
    else:
        raise AssertionError("rejecting unopened gate should fail")


def test_loop_run_from_dict_defaults_missing_metadata() -> None:
    from agent_brain.memory.loops.loop_types import LoopRun

    data = {
        "loop_id": "loop-1",
        "created_at": "2026-06-25T00:00:00+00:00",
        "updated_at": "2026-06-25T00:00:00+00:00",
        "status": "created",
        "goal": "old loop",
    }

    assert LoopRun.from_dict(data).metadata == {}


def test_loop_checkpoint_adds_artifact_and_starts_created_loop(tmp_path: Path) -> None:
    from agent_brain.memory.loops.loop_store import LoopStore

    store = LoopStore(tmp_path)
    loop = store.create(goal="实现 Loop Ledger")

    updated = store.checkpoint(
        loop.loop_id,
        note="写入 store 测试",
        artifact="tests/unit/test_loop_store.py",
        actor="codex",
        now=datetime(2026, 6, 23, 1, 5, tzinfo=timezone.utc),
    )

    assert updated.status == "running"
    assert updated.checkpoints[-1]["note"] == "写入 store 测试"
    assert updated.artifacts[-1]["value"] == "tests/unit/test_loop_store.py"
    assert updated.updated_at == "2026-06-23T01:05:00+00:00"


def test_loop_complete_requires_verification_evidence(tmp_path: Path) -> None:
    from agent_brain.memory.loops.loop_store import LoopStore
    from agent_brain.memory.loops.loop_types import LoopTransitionError

    store = LoopStore(tmp_path)
    loop = store.create(goal="需要验证", start=True)

    with pytest.raises(LoopTransitionError, match="verification evidence"):
        store.complete(loop.loop_id)

    completed = store.complete(
        loop.loop_id,
        evidence="pytest passed: 15 passed",
        artifact="commit abc1234",
        actor="codex",
    )
    assert completed.status == "completed"
    assert completed.verification_results[-1]["evidence"] == "pytest passed: 15 passed"
    assert completed.artifacts[-1]["value"] == "commit abc1234"
    assert completed.outcome == {"status": "completed"}


def test_loop_fail_requires_reason(tmp_path: Path) -> None:
    from agent_brain.memory.loops.loop_store import LoopStore
    from agent_brain.memory.loops.loop_types import LoopTransitionError

    store = LoopStore(tmp_path)
    loop = store.create(goal="失败示例", start=True)

    with pytest.raises(LoopTransitionError, match="reason"):
        store.fail(loop.loop_id, reason="")

    failed = store.fail(loop.loop_id, reason="verification failed", evidence="pytest failed")
    assert failed.status == "failed"
    assert failed.outcome == {"status": "failed", "reason": "verification failed"}
    assert failed.verification_results[-1]["evidence"] == "pytest failed"


def test_loop_list_filters_by_status_and_project(tmp_path: Path) -> None:
    from agent_brain.memory.loops.loop_store import LoopStore

    store = LoopStore(tmp_path)
    running = store.create(goal="running", project="agent-memory-hub", start=True)
    store.create(goal="created", project="agent-memory-hub")
    store.create(goal="other", project="other", start=True)

    rows = store.list(status="running", project="agent-memory-hub")

    assert [row.loop_id for row in rows] == [running.loop_id]


def test_loop_events_skip_malformed_lines(tmp_path: Path) -> None:
    from agent_brain.memory.loops.loop_events import iter_loop_events
    from agent_brain.memory.loops.loop_store import LoopStore

    store = LoopStore(tmp_path)
    loop = store.create(goal="event parse")
    path = tmp_path / "runtime" / "loop-events.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "not json\n", encoding="utf-8")

    events = list(iter_loop_events(tmp_path))

    assert [event.loop_id for event in events] == [loop.loop_id]
