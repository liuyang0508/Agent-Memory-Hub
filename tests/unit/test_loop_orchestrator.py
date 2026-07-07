from __future__ import annotations

from pathlib import Path


def _contract_yaml(tmp_path: Path, *, with_gate: bool = False) -> str:
    action = (
        """
  - id: update_runtime_code
    kind: edit
    risk: mutation
    paths:
      - agent_brain/
    requires_gate: code_review
"""
        if with_gate
        else """
  - id: inspect_repo
    kind: inspect_command
    command: python -m pytest --version
    risk: safe
"""
    )
    gates = (
        """
human_gates:
  - id: code_review
    trigger: mutation_action
    reason: runtime code changes require review
"""
        if with_gate
        else ""
    )
    return f"""
schema_version: loop-contract/v1
id: loop-contract-orchestrator-smoke
title: Loop orchestrator smoke
goal:
  statement: Verify orchestrator smoke contract.
scope:
  project: agent-memory-hub
  repo: {tmp_path}
  branch_policy: current
  allowed_paths:
    - agent_brain/
  protected_paths:
    - .env
state:
  memory_queries:
    - loop orchestrator
actions:{action}
verifiers:
  - id: pytest_version
    command: python -m pytest --version
    required: true
budget:
  max_iterations: 1
  timeout_per_action_seconds: 30
stop_conditions:
  complete_when:
    - all_required_verifiers_pass
    - no_open_human_gate
  fail_when:
    - budget_exhausted
{gates}
"""


def test_loop_orchestrator_completes_contract_when_verifiers_pass(tmp_path: Path) -> None:
    from agent_brain.memory.loops.loop_orchestrator import LoopOrchestrator
    from agent_brain.memory.loops.loop_store import LoopStore

    contract_path = tmp_path / "loop.yaml"
    contract_path.write_text(_contract_yaml(tmp_path), encoding="utf-8")

    summary = LoopOrchestrator(tmp_path).run_contract(contract_path, timeout=30)

    assert summary.decision == "completed"
    assert summary.completion_readiness == "ready"
    assert summary.verification["passed"] == 1
    loop = LoopStore(tmp_path).get(summary.loop_id)
    assert loop.status == "completed"
    assert loop.metadata["contract_id"] == "loop-contract-orchestrator-smoke"


def test_loop_orchestrator_blocks_on_required_human_gate(tmp_path: Path) -> None:
    from agent_brain.memory.loops.loop_orchestrator import LoopOrchestrator
    from agent_brain.memory.loops.loop_store import LoopStore

    contract_path = tmp_path / "loop.yaml"
    contract_path.write_text(_contract_yaml(tmp_path, with_gate=True), encoding="utf-8")

    summary = LoopOrchestrator(tmp_path).run_contract(contract_path, timeout=30)

    assert summary.decision == "blocked"
    assert summary.completion_readiness == "blocked"
    assert summary.open_human_gates == ["code_review"]
    loop = LoopStore(tmp_path).get(summary.loop_id)
    assert loop.status == "blocked"
    assert loop.metadata["open_human_gates"][0]["id"] == "code_review"
