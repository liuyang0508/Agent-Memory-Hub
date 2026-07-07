from __future__ import annotations

from pathlib import Path

from tests.unit.test_loop_contract import VALID_CONTRACT_YAML


def test_valid_contract_passes_validation(tmp_path: Path) -> None:
    from agent_brain.memory.loops.loop_contract import parse_loop_contract
    from agent_brain.memory.loops.loop_contract_validator import validate_loop_contract

    path = tmp_path / "contract.yaml"
    path.write_text(VALID_CONTRACT_YAML, encoding="utf-8")

    result = validate_loop_contract(parse_loop_contract(path))

    assert result.valid is True
    assert result.errors == []


def test_validator_rejects_missing_required_verifier(tmp_path: Path) -> None:
    from agent_brain.memory.loops.loop_contract import parse_loop_contract
    from agent_brain.memory.loops.loop_contract_validator import validate_loop_contract

    path = tmp_path / "contract.yaml"
    path.write_text(VALID_CONTRACT_YAML.replace("required: true", "required: false"), encoding="utf-8")

    result = validate_loop_contract(parse_loop_contract(path))

    assert result.valid is False
    assert any(error.path == "verifiers" and error.code == "missing_required_verifier" for error in result.errors)


def test_validator_rejects_unsafe_verifier_command(tmp_path: Path) -> None:
    from agent_brain.memory.loops.loop_contract import parse_loop_contract
    from agent_brain.memory.loops.loop_contract_validator import validate_loop_contract

    path = tmp_path / "contract.yaml"
    path.write_text(
        VALID_CONTRACT_YAML.replace(
            "python -m pytest tests/unit/test_adapters.py -q",
            "python -m pytest -q && rm -rf /tmp/nope",
        ),
        encoding="utf-8",
    )

    result = validate_loop_contract(parse_loop_contract(path))

    assert result.valid is False
    assert any(
        error.path == "verifiers[0].command" and error.code == "shell_control"
        for error in result.errors
    )


def test_validator_requires_gate_for_mutation_action(tmp_path: Path) -> None:
    from agent_brain.memory.loops.loop_contract import parse_loop_contract
    from agent_brain.memory.loops.loop_contract_validator import validate_loop_contract

    path = tmp_path / "contract.yaml"
    path.write_text(VALID_CONTRACT_YAML.replace("    requires_gate: code_review\n", ""), encoding="utf-8")

    result = validate_loop_contract(parse_loop_contract(path))

    assert result.valid is False
    assert any(
        error.path == "actions[1].requires_gate" and error.code == "missing_gate"
        for error in result.errors
    )


def test_validator_rejects_bad_budget_and_protected_allowed_path(tmp_path: Path) -> None:
    from agent_brain.memory.loops.loop_contract import parse_loop_contract
    from agent_brain.memory.loops.loop_contract_validator import validate_loop_contract

    path = tmp_path / "contract.yaml"
    path.write_text(
        VALID_CONTRACT_YAML.replace("max_iterations: 5", "max_iterations: 0").replace(
            "    - tests/\n  protected_paths:",
            "    - tests/\n    - .env\n  protected_paths:",
        ),
        encoding="utf-8",
    )

    result = validate_loop_contract(parse_loop_contract(path))

    assert result.valid is False
    assert any(
        error.path == "budget.max_iterations" and error.code == "out_of_range"
        for error in result.errors
    )
    assert any(
        error.path == "scope.protected_paths[0]"
        and error.code == "protected_path_in_allowed_paths"
        for error in result.errors
    )
