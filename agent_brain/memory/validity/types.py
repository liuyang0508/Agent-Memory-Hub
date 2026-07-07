from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ValidityState(str, Enum):
    active = "active"
    stale = "stale"
    scope_mismatch = "scope_mismatch"
    superseded = "superseded"
    contradicted = "contradicted"
    review_required = "review_required"
    historical = "historical"


class ValidityAction(str, Enum):
    include = "include"
    demote = "demote"
    exclude = "exclude"
    history_only = "history_only"


class LifecycleClass(str, Enum):
    handoff = "handoff"
    deployment_status = "deployment_status"
    verification_result = "verification_result"
    runtime_state = "runtime_state"
    signal = "signal"
    durable_decision = "durable_decision"
    skill = "skill"
    artifact = "artifact"
    durable_fact = "durable_fact"
    unknown = "unknown"


@dataclass(frozen=True)
class BuiltinValidityPolicy:
    policy_id: str
    lifecycle_class: LifecycleClass
    ttl_hours: int | None
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidityEvaluation:
    state: ValidityState
    action: ValidityAction
    lifecycle_class: LifecycleClass
    policy_id: str
    ttl_hours: int | None
    reasons: tuple[str, ...]
    evidence: tuple[str, ...]


__all__ = [
    "BuiltinValidityPolicy",
    "LifecycleClass",
    "ValidityAction",
    "ValidityEvaluation",
    "ValidityState",
]
