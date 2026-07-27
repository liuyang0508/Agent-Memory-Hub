from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_brain.interfaces.cli import app
from agent_brain.memory.loops.loop_store import LoopStore
from agent_brain.memory.loops.loop_types import LoopTransitionError


def test_task_steps_enforce_dependencies_evidence_and_blockers(tmp_path: Path) -> None:
    store = LoopStore(tmp_path)
    loop = store.create(
        goal="Ship durable task state",
        verification_plan=["pytest -q"],
        start=True,
    )
    loop = store.add_step(loop.loop_id, title="Implement", assignee="codex")
    first = str(loop.steps[0]["id"])
    loop = store.add_step(loop.loop_id, title="Verify", depends_on=[first])
    second = str(loop.steps[1]["id"])

    with pytest.raises(LoopTransitionError, match="dependencies are not verified"):
        store.transition_step(loop.loop_id, step_id=second, status="running")

    store.transition_step(loop.loop_id, step_id=first, status="running")
    store.transition_step(loop.loop_id, step_id=first, status="completed")
    with pytest.raises(LoopTransitionError, match="requires --evidence"):
        store.transition_step(loop.loop_id, step_id=first, status="verified")
    store.transition_step(
        loop.loop_id,
        step_id=first,
        status="verified",
        evidence="unit test passed",
    )
    store.transition_step(loop.loop_id, step_id=second, status="running")
    blocked = store.transition_step(
        loop.loop_id,
        step_id=second,
        status="blocked",
        blocker="waiting for remote",
    )
    assert blocked.status == "blocked"
    assert blocked.blockers[-1]["status"] == "open"
    resumed = store.transition_step(loop.loop_id, step_id=second, status="running")
    assert resumed.status == "running"
    assert resumed.blockers[-1]["status"] == "resolved"
    store.transition_step(loop.loop_id, step_id=second, status="completed")
    store.transition_step(
        loop.loop_id,
        step_id=second,
        status="verified",
        evidence="remote round-trip passed",
    )

    completed = store.complete(loop.loop_id, evidence="all task steps verified")
    assert completed.status == "completed"
    assert [step["status"] for step in completed.steps] == ["verified", "verified"]
    assert LoopStore(tmp_path).get(loop.loop_id).steps == completed.steps


def test_loop_cannot_complete_with_unverified_step(tmp_path: Path) -> None:
    store = LoopStore(tmp_path)
    loop = store.create(goal="Guard completion", start=True)
    store.add_step(loop.loop_id, title="Not done")

    with pytest.raises(LoopTransitionError, match="every task step verified"):
        store.complete(loop.loop_id, evidence="not enough")


def test_handoff_derives_structured_task_state_from_loop(tmp_path: Path) -> None:
    brain = tmp_path / "brain"
    repo = tmp_path / "repo"
    repo.mkdir()
    store = LoopStore(brain)
    loop = store.create(
        goal="Continue stateful work",
        project="memory-hub",
        verification_plan=["pytest -q"],
        start=True,
    )
    loop = store.add_step(loop.loop_id, title="Finish API", assignee="codex")

    os.environ["BRAIN_DIR"] = str(brain)
    try:
        result = CliRunner().invoke(
            app,
            [
                "handoff",
                "--loop",
                loop.loop_id,
                "--project",
                "memory-hub",
                "--repo",
                str(repo),
            ],
        )
        assert result.exit_code == 0, result.output
        resumed = CliRunner().invoke(
            app,
            ["resume", "--project", "memory-hub", "--fail-empty"],
        )
        assert resumed.exit_code == 0, resumed.output
        assert "Continue stateful work" in resumed.output
        assert "Finish API [pending]" in resumed.output
        assert str(loop.steps[0]["id"]) in resumed.output
    finally:
        os.environ.pop("BRAIN_DIR", None)
