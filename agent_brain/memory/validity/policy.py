from __future__ import annotations

import re
from typing import Any

from agent_brain.contracts.memory_enums import memory_enum_value
from agent_brain.memory.validity.types import (
    BuiltinValidityPolicy,
    LifecycleClass,
)

_TOKEN_PATTERN = re.compile(r"[a-z0-9_]+")

_HANDOFF_POLICY = BuiltinValidityPolicy(
    policy_id="builtin.handoff.v1",
    lifecycle_class=LifecycleClass.handoff,
    ttl_hours=720,
    evidence=("type:handoff",),
)
_DEPLOYMENT_POLICY = BuiltinValidityPolicy(
    policy_id="builtin.deployment_status.v1",
    lifecycle_class=LifecycleClass.deployment_status,
    ttl_hours=24,
    evidence=("deployment_terms",),
)
_VERIFICATION_POLICY = BuiltinValidityPolicy(
    policy_id="builtin.verification_result.v1",
    lifecycle_class=LifecycleClass.verification_result,
    ttl_hours=48,
    evidence=("verification_terms",),
)
_RUNTIME_STATE_POLICY = BuiltinValidityPolicy(
    policy_id="builtin.runtime_state.v1",
    lifecycle_class=LifecycleClass.runtime_state,
    ttl_hours=48,
    evidence=("runtime_state_terms",),
)
_SIGNAL_POLICY = BuiltinValidityPolicy(
    policy_id="builtin.signal.v1",
    lifecycle_class=LifecycleClass.signal,
    ttl_hours=336,
    evidence=("type:signal",),
)
_DECISION_POLICY = BuiltinValidityPolicy(
    policy_id="builtin.durable_decision.v1",
    lifecycle_class=LifecycleClass.durable_decision,
    ttl_hours=None,
    evidence=("type:decision",),
)
_SKILL_POLICY = BuiltinValidityPolicy(
    policy_id="builtin.skill.v1",
    lifecycle_class=LifecycleClass.skill,
    ttl_hours=None,
    evidence=("skill_maturity",),
)
_ARTIFACT_POLICY = BuiltinValidityPolicy(
    policy_id="builtin.artifact.v1",
    lifecycle_class=LifecycleClass.artifact,
    ttl_hours=None,
    evidence=("type:artifact",),
)
_FACT_POLICY = BuiltinValidityPolicy(
    policy_id="builtin.durable_fact.v1",
    lifecycle_class=LifecycleClass.durable_fact,
    ttl_hours=None,
    evidence=("type:fact",),
)
_UNKNOWN_POLICY = BuiltinValidityPolicy(
    policy_id="builtin.unknown.v1",
    lifecycle_class=LifecycleClass.unknown,
    ttl_hours=None,
    evidence=("fallback",),
)

_DEPLOYMENT_TERMS = frozenset((
    "deploy",
    "deployment",
    "release",
    "remote",
    "production",
    "prod",
    "health",
    "cdn",
    "rollout",
    "published",
))
_VERIFICATION_TERMS = frozenset((
    "verification",
    "verified",
    "pytest",
    "test",
    "tests",
    "smoke",
    "passed",
    "pass",
    "green",
))
_RUNTIME_STATE_TERMS = frozenset((
    "runtime",
    "unavailable",
    "permission",
    "operation not permitted",
    "ghostty",
    "tcc",
    "browser",
    "environment",
    "blocked",
))


def resolve_builtin_policy(item: Any, body: str = "") -> BuiltinValidityPolicy:
    """Resolve the first matching built-in lifecycle policy for a memory item."""
    item_type = _enum_text(getattr(item, "type", "") or "")
    maturity = _enum_text(getattr(item, "maturity", "") or "")
    text = _policy_text(item, body, item_type, maturity)
    tags = _tag_values(item)

    if item_type == "handoff":
        return _HANDOFF_POLICY
    if _has_match(text, tags, _DEPLOYMENT_TERMS):
        return _DEPLOYMENT_POLICY
    if _has_match(text, tags, _VERIFICATION_TERMS):
        return _VERIFICATION_POLICY
    if _has_match(text, tags, _RUNTIME_STATE_TERMS):
        return _RUNTIME_STATE_POLICY
    if item_type == "signal":
        return _SIGNAL_POLICY
    if item_type == "decision":
        return _DECISION_POLICY
    if item_type == "skill" or maturity == "skill":
        return _SKILL_POLICY
    if item_type == "artifact":
        return _ARTIFACT_POLICY
    if item_type == "fact":
        return _FACT_POLICY
    return _UNKNOWN_POLICY


def _policy_text(item: Any, body: str, item_type: str, maturity: str) -> str:
    parts = [
        item_type,
        str(getattr(item, "title", "") or ""),
        str(getattr(item, "summary", "") or ""),
        maturity,
        body or "",
    ]
    return _normalize_text(" ".join(parts))


def _tag_values(item: Any) -> frozenset[str]:
    tags = getattr(item, "tags", []) or []
    return frozenset(_normalize_text(_enum_text(tag)) for tag in tags)


def _has_match(text: str, tags: frozenset[str], terms: frozenset[str]) -> bool:
    if tags.intersection(terms):
        return True

    tokens = frozenset(_TOKEN_PATTERN.findall(text))
    for term in terms:
        normalized = _normalize_text(term)
        if _is_ascii(normalized):
            if " " in normalized:
                if normalized in text:
                    return True
            elif normalized in tokens:
                return True
        elif normalized in text:
            return True
    return False


def _enum_text(value: Any) -> str:
    return memory_enum_value(value).lower()


def _normalize_text(value: str) -> str:
    return " ".join(value.lower().split())


def _is_ascii(value: str) -> bool:
    return value.isascii()


__all__ = ["resolve_builtin_policy"]
