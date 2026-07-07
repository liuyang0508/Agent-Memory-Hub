from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from agent_brain.memory.loops.loop_contract import SCHEMA_VERSION, LoopContract
from agent_brain.memory.loops.loop_feedback import validate_verification_command


_CONTRACT_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_GATED_RISKS = {"mutation", "external_side_effect", "destructive"}
_GATED_KINDS = {"write_memory", "external_call"}


@dataclass(frozen=True)
class LoopContractViolation:
    path: str
    code: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LoopContractValidationResult:
    contract_id: str
    schema_version: str
    errors: list[LoopContractViolation] = field(default_factory=list)
    warnings: list[LoopContractViolation] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "contract_id": self.contract_id,
            "schema_version": self.schema_version,
            "errors": [error.to_dict() for error in self.errors],
            "warnings": [warning.to_dict() for warning in self.warnings],
        }


def validate_loop_contract(contract: LoopContract) -> LoopContractValidationResult:
    errors: list[LoopContractViolation] = []
    warnings: list[LoopContractViolation] = []

    if contract.schema_version != SCHEMA_VERSION:
        errors.append(
            _error(
                "schema_version",
                "invalid_schema_version",
                f"schema_version must be {SCHEMA_VERSION}",
            )
        )
    if not contract.id:
        errors.append(_error("id", "required", "id is required"))
    elif not _CONTRACT_ID_RE.match(contract.id):
        errors.append(_error("id", "invalid_id", "id contains unsupported characters"))
    if not contract.title.strip():
        errors.append(_error("title", "required", "title is required"))
    if not contract.goal.statement.strip():
        errors.append(_error("goal.statement", "required", "goal.statement is required"))

    if not contract.verifiers or not any(verifier.required for verifier in contract.verifiers):
        errors.append(
            _error(
                "verifiers",
                "missing_required_verifier",
                "at least one verifier must be required",
            )
        )

    if "all_required_verifiers_pass" not in contract.stop_conditions.complete_when:
        errors.append(
            _error(
                "stop_conditions.complete_when",
                "missing_required_stop_condition",
                "complete_when must include all_required_verifiers_pass",
            )
        )

    _validate_budget(contract, errors)
    _validate_paths(contract, errors)
    _validate_verifiers(contract, errors)
    _validate_actions(contract, errors)

    return LoopContractValidationResult(
        contract_id=contract.id,
        schema_version=contract.schema_version,
        errors=errors,
        warnings=warnings,
    )


def _validate_budget(contract: LoopContract, errors: list[LoopContractViolation]) -> None:
    _range_check(errors, "budget.max_iterations", contract.budget.max_iterations, 1, 20)
    _range_check(errors, "budget.max_verifier_runs", contract.budget.max_verifier_runs, 1, 100)
    _range_check(
        errors,
        "budget.timeout_per_action_seconds",
        contract.budget.timeout_per_action_seconds,
        1,
        600,
    )
    _range_check(errors, "budget.max_parallel_agents", contract.budget.max_parallel_agents, 1, 8)


def _validate_paths(contract: LoopContract, errors: list[LoopContractViolation]) -> None:
    allowed = {_normalize_path(path) for path in contract.scope.allowed_paths}
    for index, path in enumerate(contract.scope.protected_paths):
        if _normalize_path(path) in allowed:
            errors.append(
                _error(
                    f"scope.protected_paths[{index}]",
                    "protected_path_in_allowed_paths",
                    "protected path must not also be allowed",
                )
            )


def _validate_verifiers(contract: LoopContract, errors: list[LoopContractViolation]) -> None:
    for index, verifier in enumerate(contract.verifiers):
        if not verifier.id:
            errors.append(_error(f"verifiers[{index}].id", "required", "verifier id is required"))
        if not verifier.command:
            errors.append(
                _error(f"verifiers[{index}].command", "required", "verifier command is required")
            )
            continue
        validation = validate_verification_command(verifier.command)
        if not validation.allowed:
            errors.append(
                _error(
                    f"verifiers[{index}].command",
                    validation.reason or "invalid_command",
                    "verifier command is not allowed",
                )
            )
        if verifier.timeout_seconds is not None and not 1 <= verifier.timeout_seconds <= 600:
            errors.append(
                _error(
                    f"verifiers[{index}].timeout_seconds",
                    "out_of_range",
                    "verifier timeout must be between 1 and 600 seconds",
                )
            )


def _validate_actions(contract: LoopContract, errors: list[LoopContractViolation]) -> None:
    gate_ids = {gate.id for gate in contract.human_gates}
    for index, action in enumerate(contract.actions):
        if not action.id:
            errors.append(_error(f"actions[{index}].id", "required", "action id is required"))
        if not action.kind:
            errors.append(_error(f"actions[{index}].kind", "required", "action kind is required"))
        if not action.risk:
            errors.append(_error(f"actions[{index}].risk", "required", "action risk is required"))

        if action.command and action.kind in {"inspect_command", "verify_command"}:
            validation = validate_verification_command(action.command)
            if not validation.allowed:
                errors.append(
                    _error(
                        f"actions[{index}].command",
                        validation.reason or "invalid_command",
                        "action command is not allowed",
                    )
                )

        requires_gate = action.risk in _GATED_RISKS or action.kind in _GATED_KINDS
        if requires_gate and not action.requires_gate:
            errors.append(
                _error(
                    f"actions[{index}].requires_gate",
                    "missing_gate",
                    "risk or action kind requires a human gate",
                )
            )
        elif action.requires_gate and action.requires_gate not in gate_ids:
            errors.append(
                _error(
                    f"actions[{index}].requires_gate",
                    "unknown_gate",
                    "requires_gate must reference human_gates.id",
                )
            )


def _range_check(
    errors: list[LoopContractViolation],
    path: str,
    value: int,
    minimum: int,
    maximum: int,
) -> None:
    if value < minimum or value > maximum:
        errors.append(
            _error(path, "out_of_range", f"{path} must be between {minimum} and {maximum}")
        )


def _normalize_path(path: str) -> str:
    return path.strip().rstrip("/") or "."


def _error(path: str, code: str, message: str) -> LoopContractViolation:
    return LoopContractViolation(path=path, code=code, message=message)
