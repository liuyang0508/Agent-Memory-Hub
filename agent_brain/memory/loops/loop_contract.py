from __future__ import annotations

import json
import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml


SCHEMA_VERSION = "loop-contract/v1"

DEFAULT_FEEDBACK_CATEGORIES = [
    "successful_execution",
    "verification_failed",
    "invalid_action",
    "timeout",
    "runner_error",
]

DEFAULT_RETAIN_POLICY = {
    "stdout": "bounded_summary",
    "stderr": "bounded_summary",
    "raw_output": "artifact_path_only",
}


@dataclass(frozen=True)
class LoopGoal:
    statement: str
    success: list[str] = field(default_factory=list)
    failure: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LoopScope:
    project: str | None = None
    repo: str | None = None
    branch_policy: str = "current"
    allowed_paths: list[str] = field(default_factory=list)
    protected_paths: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LoopStatePolicy:
    memory_queries: list[str] = field(default_factory=list)
    required_snapshots: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LoopAction:
    id: str
    kind: str
    risk: str
    command: str | None = None
    paths: list[str] = field(default_factory=list)
    requires_gate: str | None = None


@dataclass(frozen=True)
class LoopVerifierSpec:
    id: str
    command: str
    required: bool = False
    timeout_seconds: int | None = None
    cwd: str | None = None
    success_pattern: str | None = None


@dataclass(frozen=True)
class LoopFeedbackPolicy:
    classify: list[str] = field(default_factory=lambda: list(DEFAULT_FEEDBACK_CATEGORIES))
    retain: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_RETAIN_POLICY))


@dataclass(frozen=True)
class LoopBudget:
    max_iterations: int = 3
    max_verifier_runs: int = 10
    timeout_per_action_seconds: int = 60
    max_parallel_agents: int = 1
    token_budget_hint: int | None = None


@dataclass(frozen=True)
class LoopStopConditions:
    complete_when: list[str] = field(default_factory=list)
    fail_when: list[Any] = field(default_factory=list)


@dataclass(frozen=True)
class LoopHumanGate:
    id: str
    trigger: str
    reason: str


@dataclass(frozen=True)
class LoopRecordPolicy:
    runtime_events: str = "redacted"
    memory_candidates: str = "review_only"
    artifacts: str = "paths_only"
    include_contract_digest: bool = True


@dataclass(frozen=True)
class LoopContract:
    schema_version: str
    id: str
    title: str
    goal: LoopGoal
    scope: LoopScope
    state: LoopStatePolicy
    actions: list[LoopAction]
    verifiers: list[LoopVerifierSpec]
    feedback: LoopFeedbackPolicy
    budget: LoopBudget
    stop_conditions: LoopStopConditions
    human_gates: list[LoopHumanGate] = field(default_factory=list)
    record_policy: LoopRecordPolicy = field(default_factory=LoopRecordPolicy)
    source_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("source_path", None)
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def parse_loop_contract(path: str | Path) -> LoopContract:
    source = Path(path)
    data = _load_mapping(source)
    return LoopContract(
        schema_version=str(data.get("schema_version") or ""),
        id=str(data.get("id") or ""),
        title=str(data.get("title") or ""),
        goal=_parse_goal(_mapping(data.get("goal"))),
        scope=_parse_scope(_mapping(data.get("scope"))),
        state=_parse_state(_mapping(data.get("state"))),
        actions=[_parse_action(_mapping(item)) for item in _sequence(data.get("actions"))],
        verifiers=[_parse_verifier(_mapping(item)) for item in _sequence(data.get("verifiers"))],
        feedback=_parse_feedback(_mapping(data.get("feedback"))),
        budget=_parse_budget(_mapping(data.get("budget"))),
        stop_conditions=_parse_stop_conditions(_mapping(data.get("stop_conditions"))),
        human_gates=[_parse_human_gate(_mapping(item)) for item in _sequence(data.get("human_gates"))],
        record_policy=_parse_record_policy(_mapping(data.get("record_policy"))),
        source_path=str(source),
    )


def contract_digest(contract: LoopContract) -> str:
    canonical = json.dumps(
        contract.to_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load_mapping(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        loaded = json.loads(raw)
    else:
        loaded = yaml.safe_load(raw)
    if not isinstance(loaded, dict):
        raise ValueError("loop contract must be a mapping")
    return loaded


def _parse_goal(data: dict[str, Any]) -> LoopGoal:
    return LoopGoal(
        statement=str(data.get("statement") or ""),
        success=_str_list(data.get("success")),
        failure=_str_list(data.get("failure")),
    )


def _parse_scope(data: dict[str, Any]) -> LoopScope:
    return LoopScope(
        project=_optional_str(data.get("project")),
        repo=_optional_str(data.get("repo")),
        branch_policy=str(data.get("branch_policy") or "current"),
        allowed_paths=_str_list(data.get("allowed_paths")),
        protected_paths=_str_list(data.get("protected_paths")),
    )


def _parse_state(data: dict[str, Any]) -> LoopStatePolicy:
    return LoopStatePolicy(
        memory_queries=_str_list(data.get("memory_queries")),
        required_snapshots=_str_list(data.get("required_snapshots")),
        evidence_refs=_str_list(data.get("evidence_refs")),
    )


def _parse_action(data: dict[str, Any]) -> LoopAction:
    return LoopAction(
        id=str(data.get("id") or ""),
        kind=str(data.get("kind") or ""),
        risk=str(data.get("risk") or ""),
        command=_optional_str(data.get("command")),
        paths=_str_list(data.get("paths")),
        requires_gate=_optional_str(data.get("requires_gate")),
    )


def _parse_verifier(data: dict[str, Any]) -> LoopVerifierSpec:
    return LoopVerifierSpec(
        id=str(data.get("id") or ""),
        command=str(data.get("command") or ""),
        required=bool(data.get("required")),
        timeout_seconds=_optional_int(data.get("timeout_seconds")),
        cwd=_optional_str(data.get("cwd")),
        success_pattern=_optional_str(data.get("success_pattern")),
    )


def _parse_feedback(data: dict[str, Any]) -> LoopFeedbackPolicy:
    retain = dict(DEFAULT_RETAIN_POLICY)
    retain.update({str(key): str(value) for key, value in _mapping(data.get("retain")).items()})
    return LoopFeedbackPolicy(
        classify=_str_list(data.get("classify")) or list(DEFAULT_FEEDBACK_CATEGORIES),
        retain=retain,
    )


def _parse_budget(data: dict[str, Any]) -> LoopBudget:
    return LoopBudget(
        max_iterations=_int_or_default(data.get("max_iterations"), 3),
        max_verifier_runs=_int_or_default(data.get("max_verifier_runs"), 10),
        timeout_per_action_seconds=_int_or_default(data.get("timeout_per_action_seconds"), 60),
        max_parallel_agents=_int_or_default(data.get("max_parallel_agents"), 1),
        token_budget_hint=_optional_int(data.get("token_budget_hint")),
    )


def _parse_stop_conditions(data: dict[str, Any]) -> LoopStopConditions:
    return LoopStopConditions(
        complete_when=_str_list(data.get("complete_when")),
        fail_when=list(_sequence(data.get("fail_when"))),
    )


def _parse_human_gate(data: dict[str, Any]) -> LoopHumanGate:
    return LoopHumanGate(
        id=str(data.get("id") or ""),
        trigger=str(data.get("trigger") or ""),
        reason=str(data.get("reason") or ""),
    )


def _parse_record_policy(data: dict[str, Any]) -> LoopRecordPolicy:
    return LoopRecordPolicy(
        runtime_events=str(data.get("runtime_events") or "redacted"),
        memory_candidates=str(data.get("memory_candidates") or "review_only"),
        artifacts=str(data.get("artifacts") or "paths_only"),
        include_contract_digest=bool(data.get("include_contract_digest", True)),
    )


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _sequence(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _str_list(value: Any) -> list[str]:
    return [str(item) for item in _sequence(value)]


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _int_or_default(value: Any, default: int) -> int:
    if value is None:
        return default
    return int(value)
