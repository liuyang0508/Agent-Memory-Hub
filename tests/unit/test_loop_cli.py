from __future__ import annotations

import json

from typer.testing import CliRunner

from agent_brain.interfaces.cli import app
from tests.unit.test_loop_contract import VALID_CONTRACT_YAML


runner = CliRunner()


def test_loop_create_status_and_list_json(tmp_brain_dir, monkeypatch) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))

    created = runner.invoke(
        app,
        [
            "loop",
            "create",
            "--goal",
            "实现 Loop Ledger",
            "--project",
            "agent-memory-hub",
            "--adapter",
            "codex",
            "--session",
            "sess-loop",
            "--verifier",
            "python -m pytest tests/unit/test_loop_store.py -q",
            "--start",
            "--format",
            "json",
        ],
    )

    assert created.exit_code == 0, created.output
    payload = json.loads(created.output)
    loop_id = payload["loop_id"]
    assert payload["status"] == "running"
    assert payload["goal"] == "实现 Loop Ledger"
    assert payload["verification_plan"] == ["python -m pytest tests/unit/test_loop_store.py -q"]

    status = runner.invoke(app, ["loop", "status", loop_id, "--format", "json"])
    assert status.exit_code == 0, status.output
    assert json.loads(status.output)["loop_id"] == loop_id

    listed = runner.invoke(
        app,
        [
            "loop",
            "list",
            "--status",
            "running",
            "--project",
            "agent-memory-hub",
            "--format",
            "json",
        ],
    )
    assert listed.exit_code == 0, listed.output
    rows = json.loads(listed.output)
    assert [row["loop_id"] for row in rows] == [loop_id]


def test_loop_checkpoint_complete_and_fail_validation(tmp_brain_dir, monkeypatch) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))

    created = runner.invoke(
        app,
        ["loop", "create", "--goal", "验证完成门禁", "--start", "--format", "json"],
    )
    assert created.exit_code == 0, created.output
    loop_id = json.loads(created.output)["loop_id"]

    checkpoint = runner.invoke(
        app,
        [
            "loop",
            "checkpoint",
            loop_id,
            "--note",
            "写入测试",
            "--artifact",
            "tests/unit/test_loop_cli.py",
            "--format",
            "json",
        ],
    )
    assert checkpoint.exit_code == 0, checkpoint.output
    assert json.loads(checkpoint.output)["checkpoints"][-1]["note"] == "写入测试"

    missing_evidence = runner.invoke(app, ["loop", "complete", loop_id])
    assert missing_evidence.exit_code == 2
    assert "verification evidence" in missing_evidence.output

    completed = runner.invoke(
        app,
        [
            "loop",
            "complete",
            loop_id,
            "--evidence",
            "pytest passed",
            "--artifact",
            "commit abc1234",
            "--format",
            "json",
        ],
    )
    assert completed.exit_code == 0, completed.output
    assert json.loads(completed.output)["status"] == "completed"

    failed = runner.invoke(app, ["loop", "fail", loop_id, "--reason", "too late"])
    assert failed.exit_code == 2
    assert "illegal loop status transition" in failed.output


def test_loop_status_missing_id_exits_cleanly(tmp_brain_dir, monkeypatch) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))

    result = runner.invoke(app, ["loop", "status", "loop-missing"])

    assert result.exit_code == 1
    assert "loop not found" in result.output


def test_loop_contract_validate_outputs_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path / "brain"))
    path = tmp_path / "contract.yaml"
    path.write_text(VALID_CONTRACT_YAML, encoding="utf-8")

    result = runner.invoke(app, ["loop", "contract", "validate", str(path), "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["valid"] is True
    assert payload["contract_id"] == "loop-contract-agent-memory-hub-adapter-doctor"
    assert payload["schema_version"] == "loop-contract/v1"
    assert payload["digest"]
    assert payload["errors"] == []


def test_loop_contract_validate_invalid_exits_nonzero(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path / "brain"))
    path = tmp_path / "contract.yaml"
    path.write_text(VALID_CONTRACT_YAML.replace("required: true", "required: false"), encoding="utf-8")

    result = runner.invoke(app, ["loop", "contract", "validate", str(path), "--format", "json"])

    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["valid"] is False
    assert any(error["code"] == "missing_required_verifier" for error in payload["errors"])


def test_loop_create_from_contract_outputs_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path / "brain"))
    contract_path = tmp_path / "contract.yaml"
    contract_path.write_text(VALID_CONTRACT_YAML, encoding="utf-8")

    result = runner.invoke(
        app,
        ["loop", "create", "--contract", str(contract_path), "--start", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "running"
    assert payload["goal"] == "修复 adapter doctor 漂移。"
    assert payload["project"] == "agent-memory-hub"
    assert payload["cwd"] == "/repo"
    assert payload["verification_plan"] == ["python -m pytest tests/unit/test_adapters.py -q"]
    assert payload["budget"]["max_iterations"] == 5
    assert payload["metadata"]["contract_id"] == "loop-contract-agent-memory-hub-adapter-doctor"
    assert payload["metadata"]["contract_schema_version"] == "loop-contract/v1"
    assert payload["metadata"]["contract_digest"]
    assert payload["metadata"]["contract_source_path"] == str(contract_path)


def test_loop_create_from_contract_records_verifier_metadata(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path / "brain"))
    contract_path = tmp_path / "contract.yaml"
    contract_path.write_text(VALID_CONTRACT_YAML, encoding="utf-8")

    result = runner.invoke(
        app,
        ["loop", "create", "--contract", str(contract_path), "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["metadata"]["contract_verifiers"] == [
        {
            "id": "unit_tests",
            "command": "python -m pytest tests/unit/test_adapters.py -q",
            "required": True,
        }
    ]
    assert payload["metadata"]["contract_human_gates"] == [
        {
            "id": "code_review",
            "trigger": "mutation_action",
            "reason": "runtime code changes require review",
        }
    ]


def test_loop_gate_cli_opens_lists_and_approves_gate(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path / "brain"))
    contract_path = tmp_path / "contract.yaml"
    contract_path.write_text(VALID_CONTRACT_YAML, encoding="utf-8")
    created = runner.invoke(
        app,
        ["loop", "create", "--contract", str(contract_path), "--start", "--format", "json"],
    )
    assert created.exit_code == 0, created.output
    loop_id = json.loads(created.output)["loop_id"]

    opened = runner.invoke(
        app,
        [
            "loop",
            "gate",
            "open",
            loop_id,
            "--gate",
            "code_review",
            "--reason",
            "review before merge",
            "--format",
            "json",
        ],
    )
    assert opened.exit_code == 0, opened.output
    opened_payload = json.loads(opened.output)
    assert opened_payload["metadata"]["open_human_gates"][0]["id"] == "code_review"

    listed = runner.invoke(app, ["loop", "gate", "list", loop_id, "--format", "json"])
    assert listed.exit_code == 0, listed.output
    listed_payload = json.loads(listed.output)
    assert listed_payload["open"][0]["reason"] == "review before merge"
    assert listed_payload["resolved"] == []

    approved = runner.invoke(
        app,
        [
            "loop",
            "gate",
            "approve",
            loop_id,
            "--gate",
            "code_review",
            "--note",
            "reviewed by maintainer",
            "--format",
            "json",
        ],
    )
    assert approved.exit_code == 0, approved.output
    approved_payload = json.loads(approved.output)
    assert approved_payload["metadata"]["open_human_gates"] == []
    assert approved_payload["metadata"]["resolved_human_gates"][-1]["decision"] == "approved"


def test_loop_gate_cli_rejects_missing_open_gate(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path / "brain"))
    created = runner.invoke(
        app,
        ["loop", "create", "--goal", "manual loop", "--start", "--format", "json"],
    )
    assert created.exit_code == 0, created.output
    loop_id = json.loads(created.output)["loop_id"]

    result = runner.invoke(
        app,
        [
            "loop",
            "gate",
            "reject",
            loop_id,
            "--gate",
            "merge_main",
            "--reason",
            "not approved",
        ],
    )

    assert result.exit_code == 2
    assert "not open" in result.output


def test_loop_run_cli_orchestrates_contract_to_completion(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path / "brain"))
    contract_path = tmp_path / "contract.yaml"
    contract_path.write_text(
        VALID_CONTRACT_YAML.replace("repo: /repo", f"repo: {tmp_path}")
        .replace("command: git status --short --branch", "command: python -m pytest --version")
        .replace("risk: mutation", "risk: safe")
        .replace("    requires_gate: code_review\n", "")
        .replace(
            "    command: python -m pytest tests/unit/test_adapters.py -q",
            "    command: python -m pytest --version",
        )
        .replace(
            """
human_gates:
  - id: code_review
    trigger: mutation_action
    reason: runtime code changes require review
""",
            "",
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["loop", "run", "--contract", str(contract_path), "--timeout", "30", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["decision"] == "completed"
    assert payload["completion_readiness"] == "ready"
    assert payload["verification"]["passed"] == 1
    assert payload["loop_id"].startswith("loop-")


def test_loop_create_contract_rejects_manual_fields(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path / "brain"))
    contract_path = tmp_path / "contract.yaml"
    contract_path.write_text(VALID_CONTRACT_YAML, encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "loop",
            "create",
            "--contract",
            str(contract_path),
            "--goal",
            "manual override",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 2
    assert "cannot combine --contract with manual loop fields" in result.output
