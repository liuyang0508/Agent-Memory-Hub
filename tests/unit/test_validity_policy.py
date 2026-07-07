from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from agent_brain.contracts.memory_enums import Maturity, MemoryType
from agent_brain.contracts.memory_item import MemoryItem


NOW = datetime(2026, 6, 27, 9, 0, tzinfo=timezone.utc)


def _item(
    suffix: str,
    type_: str,
    title: str,
    summary: str,
    *,
    hours_ago: int = 0,
    tags: list[str] | None = None,
    validity: dict | None = None,
    maturity: str = "raw",
    superseded_by: str | None = None,
    support_count: int = 0,
    contradict_count: int = 0,
    gain_score: float = 0.0,
) -> MemoryItem:
    return MemoryItem.model_validate({
        "id": f"mem-20260627-090000-{suffix}",
        "type": type_,
        "created_at": (NOW - timedelta(hours=hours_ago)).isoformat(),
        "title": title,
        "summary": summary,
        "tags": tags or [],
        "validity": validity or {},
        "maturity": maturity,
        "superseded_by": superseded_by,
        "support_count": support_count,
        "contradict_count": contradict_count,
        "gain_score": gain_score,
    })


def test_runtime_state_policy_matches_state_signal() -> None:
    from agent_brain.memory.validity import LifecycleClass, resolve_builtin_policy

    item = _item(
        "runtime-state",
        "signal",
        "Browser unavailable",
        "Ghostty Operation not permitted; browser unavailable",
        tags=["browser", "runtime"],
    )

    policy = resolve_builtin_policy(item, "")

    assert policy.policy_id == "builtin.runtime_state.v1"
    assert policy.lifecycle_class == LifecycleClass.runtime_state
    assert policy.ttl_hours == 48


def test_plain_signal_policy_uses_signal_ttl() -> None:
    from agent_brain.memory.validity import LifecycleClass, resolve_builtin_policy

    item = _item(
        "plain-signal",
        "signal",
        "Waiting for review",
        "Waiting for current reviewer response",
    )

    policy = resolve_builtin_policy(item, "")

    assert policy.policy_id == "builtin.signal.v1"
    assert policy.lifecycle_class == LifecycleClass.signal
    assert policy.ttl_hours == 336


def test_verification_result_policy_matches_test_terms() -> None:
    from agent_brain.memory.validity import LifecycleClass, resolve_builtin_policy

    item = _item(
        "verification",
        "episode",
        "Pytest passed",
        "python -m pytest tests/unit/test_context_firewall.py -q passed",
        tags=["verification"],
    )

    policy = resolve_builtin_policy(item, "")

    assert policy.policy_id == "builtin.verification_result.v1"
    assert policy.lifecycle_class == LifecycleClass.verification_result
    assert policy.ttl_hours == 48


def test_deployment_status_policy_matches_remote_health_terms() -> None:
    from agent_brain.memory.validity import LifecycleClass, resolve_builtin_policy

    item = _item(
        "deployment-status",
        "artifact",
        "Remote sync health",
        "Remote release health check passed",
        tags=["remote", "release"],
    )

    policy = resolve_builtin_policy(item, "")

    assert policy.policy_id == "builtin.deployment_status.v1"
    assert policy.lifecycle_class == LifecycleClass.deployment_status
    assert policy.ttl_hours == 24


def test_stable_decision_policy_has_no_ttl() -> None:
    from agent_brain.memory.validity import LifecycleClass, resolve_builtin_policy

    item = _item(
        "stable-decision",
        "decision",
        "Use SSE",
        "Use SSE instead of WebSocket for one-way status updates",
        hours_ago=24 * 90,
        tags=["architecture"],
    )

    policy = resolve_builtin_policy(item, "")

    assert policy.policy_id == "builtin.durable_decision.v1"
    assert policy.lifecycle_class == LifecycleClass.durable_decision
    assert policy.ttl_hours is None


def test_validity_types_match_p0_contract() -> None:
    from dataclasses import fields

    from agent_brain.memory.validity import (
        BuiltinValidityPolicy,
        LifecycleClass,
        ValidityAction,
        ValidityEvaluation,
        ValidityState,
    )

    assert [state.value for state in ValidityState] == [
        "active",
        "stale",
        "scope_mismatch",
        "superseded",
        "contradicted",
        "review_required",
        "historical",
    ]
    assert [action.value for action in ValidityAction] == [
        "include",
        "demote",
        "exclude",
        "history_only",
    ]
    assert LifecycleClass.durable_fact.value == "durable_fact"
    assert [field.name for field in fields(ValidityEvaluation)] == [
        "state",
        "action",
        "lifecycle_class",
        "policy_id",
        "ttl_hours",
        "reasons",
        "evidence",
    ]
    assert [field.name for field in fields(BuiltinValidityPolicy)] == [
        "policy_id",
        "lifecycle_class",
        "ttl_hours",
        "evidence",
    ]


def test_handoff_policy_ttl_is_720_hours() -> None:
    from agent_brain.memory.validity import LifecycleClass, resolve_builtin_policy

    item = _item(
        "handoff",
        "handoff",
        "Resume Task 1",
        "Continue from current handoff",
    )

    policy = resolve_builtin_policy(item, "")

    assert policy.policy_id == "builtin.handoff.v1"
    assert policy.lifecycle_class == LifecycleClass.handoff
    assert policy.ttl_hours == 720


def test_stable_fact_policy_returns_durable_fact() -> None:
    from agent_brain.memory.validity import LifecycleClass, resolve_builtin_policy

    item = _item(
        "stable-fact",
        "fact",
        "BSD grep behavior",
        "BSD grep uses basic regular expressions by default",
        tags=["cli"],
    )

    policy = resolve_builtin_policy(item, "")

    assert policy.policy_id == "builtin.durable_fact.v1"
    assert policy.lifecycle_class == LifecycleClass.durable_fact
    assert policy.ttl_hours is None


def test_non_signal_runtime_state_policy_matches_runtime_terms() -> None:
    from agent_brain.memory.validity import LifecycleClass, resolve_builtin_policy

    item = _item(
        "runtime-artifact",
        "artifact",
        "Browser runtime note",
        "Browser unavailable in current runtime",
        tags=["browser", "runtime"],
    )

    policy = resolve_builtin_policy(item, "")

    assert policy.policy_id == "builtin.runtime_state.v1"
    assert policy.lifecycle_class == LifecycleClass.runtime_state
    assert policy.ttl_hours == 48


def test_durable_fact_does_not_match_embedded_prod_test_pass_terms() -> None:
    from agent_brain.memory.validity import LifecycleClass, resolve_builtin_policy

    item = _item(
        "product-principles",
        "fact",
        "Product principles",
        "Product docs describe contest rules and passive voice guidance",
        tags=["writing"],
    )

    policy = resolve_builtin_policy(item, "")

    assert policy.policy_id == "builtin.durable_fact.v1"
    assert policy.lifecycle_class == LifecycleClass.durable_fact


def test_enum_item_type_resolves_handoff_policy() -> None:
    from agent_brain.memory.validity import LifecycleClass, resolve_builtin_policy

    item = SimpleNamespace(
        type=MemoryType.handoff,
        maturity=Maturity.raw,
        title="Resume work",
        summary="Continue from handoff",
        tags=[],
    )

    policy = resolve_builtin_policy(item, "")

    assert policy.policy_id == "builtin.handoff.v1"
    assert policy.lifecycle_class == LifecycleClass.handoff
    assert policy.ttl_hours == 720


def test_enum_skill_maturity_resolves_skill_policy() -> None:
    from agent_brain.memory.validity import LifecycleClass, resolve_builtin_policy

    item = SimpleNamespace(
        type=MemoryType.episode,
        maturity=Maturity.skill,
        title="Skill distilled",
        summary="Reusable workflow",
        tags=[],
    )

    policy = resolve_builtin_policy(item, "")

    assert policy.policy_id == "builtin.skill.v1"
    assert policy.lifecycle_class == LifecycleClass.skill


def test_builtin_policies_expose_stable_evidence_labels() -> None:
    from agent_brain.memory.validity import resolve_builtin_policy

    runtime_policy = resolve_builtin_policy(
        _item(
            "evidence-runtime",
            "signal",
            "Browser unavailable",
            "Browser unavailable in runtime",
            tags=["browser"],
        ),
        "",
    )
    verification_policy = resolve_builtin_policy(
        _item(
            "evidence-verification",
            "episode",
            "Pytest passed",
            "python -m pytest tests/unit/test_validity_policy.py -q passed",
        ),
        "",
    )
    deployment_policy = resolve_builtin_policy(
        _item(
            "evidence-deployment",
            "artifact",
            "Release health",
            "Release health check passed",
            tags=["release"],
        ),
        "",
    )
    handoff_policy = resolve_builtin_policy(
        _item(
            "evidence-handoff",
            "handoff",
            "Resume Task 1",
            "Continue from handoff",
        ),
        "",
    )

    assert "runtime_state_terms" in runtime_policy.evidence
    assert "verification_terms" in verification_policy.evidence
    assert "deployment_terms" in deployment_policy.evidence
    assert "type:handoff" in handoff_policy.evidence


def test_review_required_item_is_excluded() -> None:
    from agent_brain.memory.validity import ValidityAction, ValidityEvaluator, ValidityState

    item = _item(
        "needs-review",
        "episode",
        "Browser might work",
        "No verification exists",
        tags=["needs-review", "unverified-boundary"],
    )

    evaluation = ValidityEvaluator(now=NOW).evaluate(item)

    assert evaluation.state == ValidityState.review_required
    assert evaluation.action == ValidityAction.exclude
    assert "requires_review" in evaluation.reasons


def test_superseded_item_is_excluded() -> None:
    from agent_brain.memory.validity import ValidityAction, ValidityEvaluator, ValidityState

    item = _item(
        "superseded",
        "decision",
        "Old queue choice",
        "Use the old queue",
        superseded_by="mem-20260627-090000-new-queue-choice",
    )

    evaluation = ValidityEvaluator(now=NOW).evaluate(item)

    assert evaluation.state == ValidityState.superseded
    assert evaluation.action == ValidityAction.exclude
    assert "superseded" in evaluation.reasons


def test_strong_negative_feedback_is_excluded() -> None:
    from agent_brain.memory.validity import ValidityAction, ValidityEvaluator, ValidityState

    item = _item(
        "negative-feedback",
        "episode",
        "Old workaround",
        "User rejected this workaround repeatedly",
        contradict_count=3,
        gain_score=-0.6,
        support_count=0,
    )

    evaluation = ValidityEvaluator(now=NOW).evaluate(item)

    assert evaluation.state == ValidityState.contradicted
    assert evaluation.action == ValidityAction.exclude
    assert "negative_feedback" in evaluation.reasons


def test_evaluator_item_type_hint_is_memory_item() -> None:
    from typing import get_type_hints

    from agent_brain.contracts.memory_item import MemoryItem
    from agent_brain.memory.validity import ValidityEvaluator

    hints = get_type_hints(ValidityEvaluator.evaluate)

    assert hints["item"] is MemoryItem


def test_review_required_evidence_includes_matched_tags() -> None:
    from agent_brain.memory.validity import ValidityEvaluator

    item = _item(
        "needs-review-evidence",
        "episode",
        "Browser might work",
        "No verification exists",
        tags=["needs-review", "unverified-boundary"],
    )

    evaluation = ValidityEvaluator(now=NOW).evaluate(item)

    assert "tag:needs-review" in evaluation.evidence
    assert "tag:unverified-boundary" in evaluation.evidence


def test_runtime_failure_expires_after_builtin_ttl() -> None:
    from agent_brain.memory.validity import LifecycleClass, ValidityAction, ValidityEvaluator, ValidityState

    item = _item(
        "old-runtime-failure",
        "signal",
        "Browser unavailable",
        "Ghostty Operation not permitted; browser unavailable",
        hours_ago=49,
        tags=["browser", "runtime"],
    )

    evaluation = ValidityEvaluator(now=NOW).evaluate(item)

    assert evaluation.lifecycle_class == LifecycleClass.runtime_state
    assert evaluation.ttl_hours == 48
    assert evaluation.state == ValidityState.stale
    assert evaluation.action == ValidityAction.exclude
    assert "ttl_expired" in evaluation.reasons


def test_recent_runtime_failure_is_active() -> None:
    from agent_brain.memory.validity import ValidityAction, ValidityEvaluator, ValidityState

    item = _item(
        "recent-runtime-failure",
        "signal",
        "Browser unavailable",
        "Ghostty Operation not permitted; browser unavailable",
        hours_ago=24,
        tags=["browser", "runtime"],
    )

    evaluation = ValidityEvaluator(now=NOW).evaluate(item)

    assert evaluation.state == ValidityState.active
    assert evaluation.action == ValidityAction.include


def test_stable_decision_stays_active_after_ninety_days() -> None:
    from agent_brain.memory.validity import LifecycleClass, ValidityAction, ValidityEvaluator, ValidityState

    item = _item(
        "old-stable-decision",
        "decision",
        "Use SSE",
        "Use SSE instead of WebSocket for one-way status updates",
        hours_ago=24 * 90,
        tags=["architecture"],
    )

    evaluation = ValidityEvaluator(now=NOW).evaluate(item)

    assert evaluation.lifecycle_class == LifecycleClass.durable_decision
    assert evaluation.ttl_hours is None
    assert evaluation.state == ValidityState.active
    assert evaluation.action == ValidityAction.include


def test_explicit_ttl_overrides_builtin_policy() -> None:
    from agent_brain.memory.validity import ValidityAction, ValidityEvaluator, ValidityState

    item = _item(
        "explicit-ttl",
        "signal",
        "Browser unavailable",
        "Ghostty Operation not permitted; browser unavailable",
        hours_ago=13,
        tags=["browser", "runtime"],
        validity={"ttl_hours": 12},
    )

    evaluation = ValidityEvaluator(now=NOW).evaluate(item)

    assert evaluation.ttl_hours == 12
    assert evaluation.state == ValidityState.stale
    assert evaluation.action == ValidityAction.exclude
    assert "explicit_ttl" in evaluation.reasons


def test_observed_at_controls_ttl_age_when_present() -> None:
    from agent_brain.memory.validity import ValidityAction, ValidityEvaluator, ValidityState

    item = _item(
        "observed-at",
        "signal",
        "Browser unavailable",
        "Ghostty Operation not permitted; browser unavailable",
        hours_ago=72,
        tags=["browser", "runtime"],
        validity={"observed_at": (NOW - timedelta(hours=12)).isoformat()},
    )

    evaluation = ValidityEvaluator(now=NOW).evaluate(item)

    assert evaluation.ttl_hours == 48
    assert evaluation.state == ValidityState.active
    assert evaluation.action == ValidityAction.include


def test_scope_mismatch_excludes_runtime_state() -> None:
    from agent_brain.memory.validity import ValidityAction, ValidityEvaluator, ValidityState

    item = _item(
        "scope-runtime",
        "signal",
        "Browser currently limited",
        "Browser unavailable in another repo",
        tags=["browser", "runtime"],
        validity={"cwd": "/repo/other", "adapter": "codex"},
    )

    evaluation = ValidityEvaluator(now=NOW).evaluate(
        item,
        current_scope={"cwd": "/repo/current", "adapter": "codex"},
    )

    assert evaluation.state == ValidityState.scope_mismatch
    assert evaluation.action == ValidityAction.exclude
    assert "scope_mismatch:cwd" in evaluation.reasons


def test_scope_mismatch_does_not_exclude_durable_fact() -> None:
    from agent_brain.memory.validity import LifecycleClass, ValidityAction, ValidityEvaluator, ValidityState

    item = _item(
        "scope-fact",
        "fact",
        "HTTP 424 meaning",
        "HTTP 424 maps to quota policy",
        tags=["api"],
        validity={"cwd": "/repo/other"},
    )

    evaluation = ValidityEvaluator(now=NOW).evaluate(
        item,
        current_scope={"cwd": "/repo/current"},
    )

    assert evaluation.lifecycle_class == LifecycleClass.durable_fact
    assert evaluation.state == ValidityState.active
    assert evaluation.action == ValidityAction.include


def test_history_mode_keeps_stale_item_as_history_only() -> None:
    from agent_brain.memory.validity import ValidityAction, ValidityEvaluator, ValidityState

    item = _item(
        "history-stale",
        "signal",
        "Browser unavailable",
        "Ghostty Operation not permitted; browser unavailable",
        hours_ago=49,
        tags=["browser", "runtime"],
    )

    evaluation = ValidityEvaluator(now=NOW).evaluate(item, history_mode=True)

    assert evaluation.state == ValidityState.historical
    assert evaluation.action == ValidityAction.history_only
    assert "history_mode" in evaluation.reasons


def test_future_observed_at_clamps_ttl_elapsed_hours() -> None:
    from agent_brain.memory.validity import ValidityAction, ValidityEvaluator, ValidityState

    item = _item(
        "future-observed-at",
        "signal",
        "Browser unavailable",
        "Ghostty Operation not permitted; browser unavailable",
        tags=["browser", "runtime"],
        validity={"observed_at": (NOW + timedelta(hours=6)).isoformat()},
    )

    evaluation = ValidityEvaluator(now=NOW).evaluate(item)

    assert evaluation.state == ValidityState.active
    assert evaluation.action == ValidityAction.include
    assert "ttl_elapsed_hours:0.00" in evaluation.evidence
    assert not any(evidence.startswith("ttl_elapsed_hours:-") for evidence in evaluation.evidence)


def test_scope_mismatch_reports_all_mismatched_fields() -> None:
    from agent_brain.memory.validity import ValidityAction, ValidityEvaluator, ValidityState

    item = _item(
        "scope-runtime-all",
        "signal",
        "Browser currently limited",
        "Browser unavailable in another repo",
        tags=["browser", "runtime"],
        validity={"cwd": "/repo/other", "branch": "old", "adapter": "claude-code"},
    )

    evaluation = ValidityEvaluator(now=NOW).evaluate(
        item,
        current_scope={"cwd": "/repo/current", "branch": "main", "adapter": "codex"},
    )

    assert evaluation.state == ValidityState.scope_mismatch
    assert evaluation.action == ValidityAction.exclude
    assert "scope_mismatch:cwd" in evaluation.reasons
    assert "scope_mismatch:branch" in evaluation.reasons
    assert "scope_mismatch:adapter" in evaluation.reasons
    assert "scope:cwd" in evaluation.evidence
    assert "scope:branch" in evaluation.evidence
    assert "scope:adapter" in evaluation.evidence
