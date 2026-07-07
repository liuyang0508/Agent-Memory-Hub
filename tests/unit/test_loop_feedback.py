from __future__ import annotations


def test_verification_command_allowlist_accepts_safe_prefixes() -> None:
    from agent_brain.memory.loops.loop_feedback import validate_verification_command

    assert validate_verification_command("python -m pytest tests/unit/test_loop_store.py -q").allowed
    assert validate_verification_command("git diff --check").allowed
    assert validate_verification_command("memory adapter doctor codex --format json").allowed


def test_verification_command_rejects_shell_control_and_unknown_prefix() -> None:
    from agent_brain.memory.loops.loop_feedback import validate_verification_command

    shell = validate_verification_command("python -m pytest -q && rm -rf /tmp/nope")
    unknown = validate_verification_command("rm -rf /tmp/nope")

    assert not shell.allowed
    assert shell.reason == "shell_control"
    assert not unknown.allowed
    assert unknown.reason == "not_allowlisted"


def test_output_summary_is_bounded_hashes_and_counts_lines() -> None:
    from agent_brain.memory.loops.loop_feedback import summarize_output

    text = "\n".join(f"line-{i}" for i in range(40))
    summary = summarize_output(text, max_chars=80)

    assert summary.line_count == 40
    assert summary.sha256
    assert summary.truncated is True
    assert len(summary.text) <= 120
    assert "line-0" in summary.text
    assert "line-39" in summary.text


def test_feedback_view_keeps_each_feedback_row_on_one_line() -> None:
    from agent_brain.memory.loops.loop_feedback import LoopFeedback, format_feedback_view

    row = LoopFeedback(
        feedback_id="lfb-1",
        timestamp="2026-06-24T00:00:00+00:00",
        command="python -m pytest tests/unit/test_loop_store.py -q",
        cwd="/repo",
        status="passed",
        category="successful_execution",
        exit_code=0,
        duration_ms=10,
        stdout_summary="...... [100%]\n6 passed in 0.02s",
        stderr_summary="no output",
        stdout_sha256="abc",
        stderr_sha256="def",
        stdout_lines=2,
        stderr_lines=0,
        truncated=False,
    )

    view = format_feedback_view("loop-1", [row.to_dict()])

    assert "...... [100%] / 6 passed in 0.02s" in view


def test_feedback_view_reports_contract_readiness_for_required_verifiers() -> None:
    from agent_brain.memory.loops.loop_feedback import LoopFeedback, format_feedback_view

    row = LoopFeedback(
        feedback_id="lfb-1",
        timestamp="2026-06-25T00:00:00+00:00",
        command="python -m pytest --version",
        cwd="/repo",
        status="passed",
        category="successful_execution",
        exit_code=0,
        duration_ms=10,
        stdout_summary="pytest 8.4.1",
        stderr_summary="no output",
        stdout_sha256="abc",
        stderr_sha256="def",
        stdout_lines=1,
        stderr_lines=0,
        truncated=False,
        verifier_id="pytest_version",
        contract_id="contract-1",
    )

    view = format_feedback_view(
        "loop-1",
        [row.to_dict()],
        metadata={
            "contract_id": "contract-1",
            "contract_verifiers": [
                {"id": "pytest_version", "command": "python -m pytest --version", "required": True}
            ],
        },
    )

    assert "[pytest_version]" in view
    assert "completion_readiness: ready" in view
    assert "all_required_verifiers_pass: satisfied" in view


def test_feedback_view_blocks_completion_when_human_gate_is_open() -> None:
    from agent_brain.memory.loops.loop_feedback import LoopFeedback, format_feedback_view

    row = LoopFeedback(
        feedback_id="lfb-1",
        timestamp="2026-06-25T00:00:00+00:00",
        command="python -m pytest --version",
        cwd="/repo",
        status="passed",
        category="successful_execution",
        exit_code=0,
        duration_ms=10,
        stdout_summary="pytest 8.4.1",
        stderr_summary="no output",
        stdout_sha256="abc",
        stderr_sha256="def",
        stdout_lines=1,
        stderr_lines=0,
        truncated=False,
        verifier_id="pytest_version",
        contract_id="contract-1",
    )

    view = format_feedback_view(
        "loop-1",
        [row.to_dict()],
        metadata={
            "contract_id": "contract-1",
            "contract_verifiers": [
                {"id": "pytest_version", "command": "python -m pytest --version", "required": True}
            ],
            "open_human_gates": [{"id": "code_review", "reason": "review before merge"}],
        },
    )

    assert "completion_readiness: blocked" in view
    assert "all_required_verifiers_pass: satisfied" in view
    assert "no_open_human_gate: unsatisfied" in view
    assert "open_human_gates: code_review" in view
