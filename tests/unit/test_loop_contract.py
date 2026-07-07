from __future__ import annotations

from pathlib import Path


VALID_CONTRACT_YAML = """
schema_version: loop-contract/v1
id: loop-contract-agent-memory-hub-adapter-doctor
title: AMH adapter doctor 漂移修复循环
goal:
  statement: 修复 adapter doctor 漂移。
  success:
    - repo-local memory CLI 可运行。
  failure:
    - 连续 3 轮 verifier 失败且失败类型相同。
scope:
  project: agent-memory-hub
  repo: /repo
  branch_policy: worktree_required
  allowed_paths:
    - agent_brain/
    - tests/
  protected_paths:
    - .env
state:
  memory_queries:
    - adapter doctor
  required_snapshots:
    - git_status
  evidence_refs:
    - docs/evaluation/latest-memory-benchmark-report.zh.md
actions:
  - id: inspect_repo
    kind: inspect_command
    command: git status --short --branch
    risk: safe
  - id: update_runtime_code
    kind: edit
    risk: mutation
    paths:
      - agent_brain/
    requires_gate: code_review
verifiers:
  - id: unit_tests
    command: python -m pytest tests/unit/test_adapters.py -q
    required: true
budget:
  max_iterations: 5
  timeout_per_action_seconds: 60
stop_conditions:
  complete_when:
    - all_required_verifiers_pass
    - no_open_human_gate
  fail_when:
    - budget_exhausted
human_gates:
  - id: code_review
    trigger: mutation_action
    reason: runtime code changes require review
"""


def test_parse_loop_contract_yaml_normalizes_defaults(tmp_path: Path) -> None:
    from agent_brain.memory.loops.loop_contract import parse_loop_contract

    path = tmp_path / "contract.yaml"
    path.write_text(VALID_CONTRACT_YAML, encoding="utf-8")

    contract = parse_loop_contract(path)

    assert contract.schema_version == "loop-contract/v1"
    assert contract.goal.statement == "修复 adapter doctor 漂移。"
    assert contract.scope.project == "agent-memory-hub"
    assert contract.scope.allowed_paths == ["agent_brain/", "tests/"]
    assert contract.actions[0].command == "git status --short --branch"
    assert contract.verifiers[0].required is True
    assert contract.feedback.retain["stdout"] == "bounded_summary"
    assert contract.budget.max_verifier_runs == 10
    assert contract.record_policy.runtime_events == "redacted"


def test_contract_digest_is_stable_for_equivalent_content(tmp_path: Path) -> None:
    from agent_brain.memory.loops.loop_contract import contract_digest, parse_loop_contract

    first = tmp_path / "first.yaml"
    second = tmp_path / "second.json"
    first.write_text(VALID_CONTRACT_YAML, encoding="utf-8")
    second.write_text(parse_loop_contract(first).to_json(), encoding="utf-8")

    assert contract_digest(parse_loop_contract(first)) == contract_digest(parse_loop_contract(second))
