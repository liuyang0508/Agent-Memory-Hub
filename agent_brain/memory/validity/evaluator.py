from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.memory.context.context_firewall_rules import (
    REVIEW_REQUIRED_TAGS,
    has_strong_negative_feedback,
)
from agent_brain.memory.context.context_firewall_types import ContextFirewallConfig
from agent_brain.memory.validity.policy import resolve_builtin_policy
from agent_brain.memory.validity.types import (
    LifecycleClass,
    ValidityAction,
    ValidityEvaluation,
    ValidityState,
)

STATE_SCOPED_CLASSES = frozenset((
    LifecycleClass.runtime_state,
    LifecycleClass.verification_result,
    LifecycleClass.deployment_status,
    LifecycleClass.signal,
    LifecycleClass.handoff,
))
SCOPE_FIELDS = ("cwd", "repo", "branch", "os", "adapter")


@dataclass(frozen=True)
class ValidityEvaluatorConfig:
    feedback: ContextFirewallConfig = ContextFirewallConfig()


class ValidityEvaluator:
    def __init__(
        self,
        config: ValidityEvaluatorConfig | None = None,
        now: datetime | None = None,
    ) -> None:
        self.config = config or ValidityEvaluatorConfig()
        self.now = _utc_now(now)

    def evaluate(
        self,
        item: MemoryItem,
        body: str = "",
        *,
        current_scope: Mapping[str, str] | None = None,
        history_mode: bool = False,
    ) -> ValidityEvaluation:
        policy = resolve_builtin_policy(item, body)
        reasons: list[str] = []
        evidence = list(policy.evidence)
        ttl_hours = policy.ttl_hours

        validity = getattr(item, "validity", None)
        explicit_ttl = getattr(validity, "ttl_hours", None)
        if explicit_ttl is not None:
            ttl_hours = explicit_ttl
            reasons.append("explicit_ttl")

        item_tags = {str(tag).lower() for tag in getattr(item, "tags", []) or []}
        matched_review_tags = tuple(sorted(REVIEW_REQUIRED_TAGS & item_tags))
        if matched_review_tags:
            reasons.append("requires_review")
            evidence.extend(f"tag:{tag}" for tag in matched_review_tags)
            return _evaluation(
                state=ValidityState.review_required,
                action=ValidityAction.exclude,
                lifecycle_class=policy.lifecycle_class,
                policy_id=policy.policy_id,
                ttl_hours=ttl_hours,
                reasons=tuple(reasons),
                evidence=tuple(evidence),
            )

        superseded_by = getattr(item, "superseded_by", None)
        if superseded_by:
            reasons.append("superseded")
            evidence.append(f"superseded_by:{superseded_by}")
            return _maybe_history_mode(_evaluation(
                state=ValidityState.superseded,
                action=ValidityAction.exclude,
                lifecycle_class=policy.lifecycle_class,
                policy_id=policy.policy_id,
                ttl_hours=ttl_hours,
                reasons=tuple(reasons),
                evidence=tuple(evidence),
            ), history_mode)

        if has_strong_negative_feedback(item, self.config.feedback):
            reasons.append("negative_feedback")
            evidence.extend((
                f"support_count:{getattr(item, 'support_count', 0)}",
                f"contradict_count:{getattr(item, 'contradict_count', 0)}",
                f"gain_score:{getattr(item, 'gain_score', 0.0)}",
            ))
            return _evaluation(
                state=ValidityState.contradicted,
                action=ValidityAction.exclude,
                lifecycle_class=policy.lifecycle_class,
                policy_id=policy.policy_id,
                ttl_hours=ttl_hours,
                reasons=tuple(reasons),
                evidence=tuple(evidence),
            )

        scope_mismatches = _scope_mismatches(policy.lifecycle_class, validity, current_scope)
        if scope_mismatches:
            reasons.extend(f"scope_mismatch:{field}" for field in scope_mismatches)
            evidence.extend(f"scope:{field}" for field in scope_mismatches)
            return _evaluation(
                state=ValidityState.scope_mismatch,
                action=ValidityAction.exclude,
                lifecycle_class=policy.lifecycle_class,
                policy_id=policy.policy_id,
                ttl_hours=ttl_hours,
                reasons=tuple(reasons),
                evidence=tuple(evidence),
            )

        if ttl_hours is not None:
            observed_at = _ttl_reference_time(item, validity)
            elapsed_hours = max(0.0, (self.now - observed_at).total_seconds() / 3600)
            evidence.extend((
                f"ttl_hours:{ttl_hours}",
                f"ttl_elapsed_hours:{elapsed_hours:.2f}",
            ))
            if elapsed_hours > ttl_hours:
                reasons.append("ttl_expired")
                return _maybe_history_mode(_evaluation(
                    state=ValidityState.stale,
                    action=ValidityAction.exclude,
                    lifecycle_class=policy.lifecycle_class,
                    policy_id=policy.policy_id,
                    ttl_hours=ttl_hours,
                    reasons=tuple(reasons),
                    evidence=tuple(evidence),
                ), history_mode)

        return _evaluation(
            state=ValidityState.active,
            action=ValidityAction.include,
            lifecycle_class=policy.lifecycle_class,
            policy_id=policy.policy_id,
            ttl_hours=ttl_hours,
            reasons=tuple(reasons),
            evidence=tuple(evidence),
        )


def _evaluation(
    *,
    state: ValidityState,
    action: ValidityAction,
    lifecycle_class: LifecycleClass,
    policy_id: str,
    ttl_hours: int | None,
    reasons: tuple[str, ...],
    evidence: tuple[str, ...],
) -> ValidityEvaluation:
    return ValidityEvaluation(
        state=state,
        action=action,
        lifecycle_class=lifecycle_class,
        policy_id=policy_id,
        ttl_hours=ttl_hours,
        reasons=reasons,
        evidence=evidence,
    )


def _maybe_history_mode(
    evaluation: ValidityEvaluation,
    history_mode: bool,
) -> ValidityEvaluation:
    if not history_mode or evaluation.state not in {
        ValidityState.stale,
        ValidityState.superseded,
    }:
        return evaluation
    return _evaluation(
        state=ValidityState.historical,
        action=ValidityAction.history_only,
        lifecycle_class=evaluation.lifecycle_class,
        policy_id=evaluation.policy_id,
        ttl_hours=evaluation.ttl_hours,
        reasons=(*evaluation.reasons, "history_mode"),
        evidence=evaluation.evidence,
    )


def _scope_mismatches(
    lifecycle_class: LifecycleClass,
    validity: object,
    current_scope: Mapping[str, str] | None,
) -> tuple[str, ...]:
    if lifecycle_class not in STATE_SCOPED_CLASSES or not current_scope:
        return ()

    mismatches: list[str] = []
    for field in SCOPE_FIELDS:
        item_value = getattr(validity, field, None)
        current_value = current_scope.get(field)
        if item_value is not None and current_value is not None and str(item_value) != str(current_value):
            mismatches.append(field)
    return tuple(mismatches)


def _ttl_reference_time(item: MemoryItem, validity: object) -> datetime:
    observed_at = getattr(validity, "observed_at", None)
    if observed_at is not None:
        return _utc_now(observed_at)
    return _utc_now(getattr(item, "created_at"))


def _utc_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


__all__ = ["STATE_SCOPED_CLASSES", "ValidityEvaluator", "ValidityEvaluatorConfig"]
