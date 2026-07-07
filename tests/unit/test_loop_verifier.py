from __future__ import annotations

import json
from pathlib import Path


def test_loop_verifier_records_successful_feedback_and_event(tmp_path: Path) -> None:
    from agent_brain.memory.loops.loop_events import iter_loop_events
    from agent_brain.memory.loops.loop_store import LoopStore
    from agent_brain.memory.loops.loop_verifier import LoopVerifier

    store = LoopStore(tmp_path)
    loop = store.create(
        goal="verify loop feedback",
        cwd=str(tmp_path),
        verification_plan=["python -m pytest --version"],
        start=True,
    )

    summary = LoopVerifier(tmp_path).verify(loop.loop_id, timeout=30)

    assert summary.passed == 1
    updated = store.get(loop.loop_id)
    feedback = updated.verification_results[-1]
    assert feedback["feedback_id"].startswith("lfb-")
    assert feedback["status"] == "passed"
    assert feedback["category"] == "successful_execution"
    assert feedback["exit_code"] == 0
    assert "pytest" in feedback["stdout_summary"].lower()

    events = list(iter_loop_events(tmp_path, loop_id=loop.loop_id))
    verification_events = [event for event in events if event.event_type == "verification_added"]
    assert verification_events
    payload = verification_events[-1].payload
    assert payload["status"] == "passed"
    assert "stdout_summary" not in json.dumps(payload)


def test_loop_verifier_records_contract_verifier_id_and_event(tmp_path: Path) -> None:
    from agent_brain.memory.loops.loop_events import iter_loop_events
    from agent_brain.memory.loops.loop_store import LoopStore
    from agent_brain.memory.loops.loop_verifier import LoopVerifier

    store = LoopStore(tmp_path)
    loop = store.create(
        goal="contract verifier",
        cwd=str(tmp_path),
        verification_plan=["python -m pytest --version"],
        metadata={
            "contract_id": "contract-1",
            "contract_verifiers": [
                {
                    "id": "pytest_version",
                    "command": "python -m pytest --version",
                    "required": True,
                }
            ],
        },
        start=True,
    )

    LoopVerifier(tmp_path).verify(loop.loop_id, timeout=30)

    feedback = store.get(loop.loop_id).verification_results[-1]
    assert feedback["verifier_id"] == "pytest_version"
    assert feedback["contract_id"] == "contract-1"
    event = [
        event
        for event in iter_loop_events(tmp_path, loop_id=loop.loop_id)
        if event.event_type == "verification_added"
    ][-1]
    assert event.payload["verifier_id"] == "pytest_version"
    assert event.payload["contract_id"] == "contract-1"


def test_loop_verifier_blocks_invalid_action_without_execution(tmp_path: Path) -> None:
    from agent_brain.memory.loops.loop_store import LoopStore
    from agent_brain.memory.loops.loop_verifier import LoopVerifier

    store = LoopStore(tmp_path)
    loop = store.create(goal="invalid", verification_plan=["rm -rf /tmp/nope"], start=True)

    summary = LoopVerifier(tmp_path).verify(loop.loop_id)

    assert summary.blocked == 1
    feedback = store.get(loop.loop_id).verification_results[-1]
    assert feedback["status"] == "blocked"
    assert feedback["category"] == "invalid_action"
    assert feedback["exit_code"] is None


def test_loop_verifier_records_timeout(tmp_path: Path) -> None:
    from agent_brain.memory.loops.loop_store import LoopStore
    from agent_brain.memory.loops.loop_verifier import LoopVerifier

    slow_test = tmp_path / "test_slow.py"
    slow_test.write_text(
        "import time\n\ndef test_slow():\n    time.sleep(3)\n",
        encoding="utf-8",
    )
    store = LoopStore(tmp_path)
    loop = store.create(
        goal="timeout",
        cwd=str(tmp_path),
        verification_plan=["python -m pytest test_slow.py -q"],
        start=True,
    )

    summary = LoopVerifier(tmp_path).verify(loop.loop_id, timeout=1)

    assert summary.timed_out == 1
    feedback = store.get(loop.loop_id).verification_results[-1]
    assert feedback["status"] == "timed_out"
    assert feedback["category"] == "timeout"


def test_successful_feedback_counts_as_completion_evidence(tmp_path: Path) -> None:
    from agent_brain.memory.loops.loop_store import LoopStore
    from agent_brain.memory.loops.loop_verifier import LoopVerifier

    store = LoopStore(tmp_path)
    loop = store.create(
        goal="complete from feedback",
        cwd=str(tmp_path),
        verification_plan=["python -m pytest --version"],
        start=True,
    )

    LoopVerifier(tmp_path).verify(loop.loop_id, timeout=30)
    completed = store.complete(loop.loop_id)

    assert completed.status == "completed"


def test_loop_verify_cli_outputs_json_feedback(tmp_path: Path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from agent_brain.interfaces.cli import app
    from agent_brain.memory.loops.loop_store import LoopStore

    monkeypatch.setenv("BRAIN_DIR", str(tmp_path))
    loop = LoopStore(tmp_path).create(
        goal="cli verify",
        cwd=str(tmp_path),
        verification_plan=["python -m pytest --version"],
        start=True,
    )

    result = CliRunner().invoke(
        app,
        ["loop", "verify", loop.loop_id, "--timeout", "30", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["loop_id"] == loop.loop_id
    assert payload["passed"] == 1


def test_loop_feedback_cli_prints_agent_facing_view(tmp_path: Path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from agent_brain.interfaces.cli import app
    from agent_brain.memory.loops.loop_store import LoopStore
    from agent_brain.memory.loops.loop_verifier import LoopVerifier

    monkeypatch.setenv("BRAIN_DIR", str(tmp_path))
    loop = LoopStore(tmp_path).create(
        goal="cli feedback",
        cwd=str(tmp_path),
        verification_plan=["python -m pytest --version"],
        start=True,
    )
    LoopVerifier(tmp_path).verify(loop.loop_id, timeout=30)

    result = CliRunner().invoke(app, ["loop", "feedback", loop.loop_id])

    assert result.exit_code == 0, result.output
    assert "successful_execution" in result.output
    assert "python -m pytest --version" in result.output
