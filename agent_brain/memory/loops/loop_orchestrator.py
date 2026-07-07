from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agent_brain.memory.loops.loop_contract import LoopContract, contract_digest, parse_loop_contract
from agent_brain.memory.loops.loop_contract_validator import validate_loop_contract
from agent_brain.memory.loops.loop_store import LoopStore
from agent_brain.memory.loops.loop_types import LoopRun
from agent_brain.memory.loops.loop_verifier import LoopVerifier


@dataclass(frozen=True)
class LoopRunSummary:
    loop_id: str
    contract_id: str
    decision: str
    completion_readiness: str
    status: str
    verification: dict[str, int]
    open_human_gates: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LoopOrchestrator:
    """Run the non-LLM control part of a Loop Contract."""

    def __init__(self, brain_dir: Path) -> None:
        self.brain_dir = Path(brain_dir)
        self.store = LoopStore(self.brain_dir)

    def run_contract(self, path: str | Path, *, timeout: int = 60) -> LoopRunSummary:
        contract = parse_loop_contract(path)
        validation = validate_loop_contract(contract)
        if not validation.valid:
            messages = ", ".join(f"{error.path}:{error.code}" for error in validation.errors)
            raise ValueError(f"invalid loop contract: {messages}")

        loop = self._create_loop(contract, Path(path))
        verification = LoopVerifier(self.brain_dir).verify(
            loop.loop_id,
            timeout=timeout,
            actor="orchestrator",
        )
        if verification.failed or verification.blocked or verification.timed_out:
            failed = self.store.fail(
                loop.loop_id,
                reason="verification did not pass",
                evidence=_verification_evidence(verification.to_dict()),
                actor="orchestrator",
            )
            return LoopRunSummary(
                loop_id=failed.loop_id,
                contract_id=contract.id,
                decision="failed",
                completion_readiness="blocked",
                status=failed.status,
                verification=_verification_counts(verification.to_dict()),
                open_human_gates=_open_gate_ids(failed.metadata),
            )

        for gate_id in _required_gate_ids(contract):
            loop = self.store.open_human_gate(
                loop.loop_id,
                gate_id=gate_id,
                reason=_gate_reason(contract, gate_id),
                actor="orchestrator",
            )

        open_gates = _open_gate_ids(loop.metadata)
        if open_gates:
            blocked = self.store.block(
                loop.loop_id,
                reason=f"waiting for human gates: {', '.join(open_gates)}",
                actor="orchestrator",
            )
            return LoopRunSummary(
                loop_id=blocked.loop_id,
                contract_id=contract.id,
                decision="blocked",
                completion_readiness="blocked",
                status=blocked.status,
                verification=_verification_counts(verification.to_dict()),
                open_human_gates=open_gates,
            )

        completed = self.store.complete(
            loop.loop_id,
            evidence=_verification_evidence(verification.to_dict()),
            actor="orchestrator",
        )
        return LoopRunSummary(
            loop_id=completed.loop_id,
            contract_id=contract.id,
            decision="completed",
            completion_readiness="ready",
            status=completed.status,
            verification=_verification_counts(verification.to_dict()),
            open_human_gates=[],
        )

    def _create_loop(self, contract: LoopContract, path: Path) -> LoopRun:
        digest = contract_digest(contract)
        return self.store.create(
            goal=contract.goal.statement,
            project=contract.scope.project,
            cwd=contract.scope.repo,
            verification_plan=[verifier.command for verifier in contract.verifiers if verifier.required],
            budget={
                "max_iterations": contract.budget.max_iterations,
                "max_verifier_runs": contract.budget.max_verifier_runs,
                "timeout_per_action_seconds": contract.budget.timeout_per_action_seconds,
                "max_parallel_agents": contract.budget.max_parallel_agents,
                "token_budget_hint": contract.budget.token_budget_hint,
            },
            context={
                "contract_title": contract.title,
                "contract_state": contract.to_dict()["state"],
            },
            metadata={
                "contract_id": contract.id,
                "contract_schema_version": contract.schema_version,
                "contract_digest": digest,
                "contract_source_path": str(path),
                "contract_verifiers": [
                    {"id": verifier.id, "command": verifier.command, "required": verifier.required}
                    for verifier in contract.verifiers
                ],
                "contract_human_gates": [
                    {"id": gate.id, "trigger": gate.trigger, "reason": gate.reason}
                    for gate in contract.human_gates
                ],
            },
            start=True,
            actor="orchestrator",
        )


def _required_gate_ids(contract: LoopContract) -> list[str]:
    ids: list[str] = []
    for action in contract.actions:
        if action.requires_gate and action.requires_gate not in ids:
            ids.append(action.requires_gate)
    return ids


def _gate_reason(contract: LoopContract, gate_id: str) -> str:
    for gate in contract.human_gates:
        if gate.id == gate_id:
            return gate.reason
    return f"human gate required: {gate_id}"


def _open_gate_ids(metadata: dict[str, Any]) -> list[str]:
    rows = metadata.get("open_human_gates")
    if not isinstance(rows, list):
        return []
    return [str(row.get("id") or "") for row in rows if isinstance(row, dict) and row.get("id")]


def _verification_counts(payload: dict[str, Any]) -> dict[str, int]:
    return {
        "attempted": int(payload.get("attempted") or 0),
        "passed": int(payload.get("passed") or 0),
        "failed": int(payload.get("failed") or 0),
        "blocked": int(payload.get("blocked") or 0),
        "timed_out": int(payload.get("timed_out") or 0),
    }


def _verification_evidence(payload: dict[str, Any]) -> str:
    counts = _verification_counts(payload)
    return (
        "loop orchestrator verification: "
        f"attempted={counts['attempted']} passed={counts['passed']} "
        f"failed={counts['failed']} blocked={counts['blocked']} timed_out={counts['timed_out']}"
    )
