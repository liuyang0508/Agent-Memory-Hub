from datetime import datetime, timezone
import json
import math

import pytest

from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Sensitivity
from agent_brain.memory.context.context_firewall_types import ContextCandidate

NOW = datetime(2026, 7, 11, tzinfo=timezone.utc)


def item(
    suffix,
    *,
    sensitivity=Sensitivity.internal,
    tags=None,
    superseded_by=None,
    validity=None,
    context_views=None,
):
    return MemoryItem(
        id=f"mem-20260711-000000-{suffix}",
        type=MemoryType.episode,
        created_at=NOW,
        title=f"Injection gateway {suffix}",
        summary=f"Gateway boundary {suffix}",
        tags=tags or [],
        sensitivity=sensitivity,
        superseded_by=superseded_by,
        validity=validity or {},
        context_views=context_views or {},
        confidence=0.9,
    )


def candidate(value, score=1.0):
    return ContextCandidate(item=value, body=f"body:{value.id}", score=score)


def test_gateway_keeps_noninjectable_query_signal_fail_closed():
    from agent_brain.memory.context.injection_gateway import evaluate_injection_candidates

    value = item("weak-query")
    result = evaluate_injection_candidates([candidate(value)], query="memory")
    assert result.included == []
    assert "query_not_injectable" in result.cohort_reasons


def test_gateway_keeps_empty_query_fail_closed():
    from agent_brain.memory.context.injection_gateway import evaluate_injection_candidates

    value = item("empty-query")
    result = evaluate_injection_candidates([candidate(value)], query="")
    assert result.included == []
    assert "query_not_injectable" in result.cohort_reasons


def test_gateway_builds_routed_context_when_legacy_signal_is_blocked():
    from agent_brain.memory.context.injection_gateway import build_injection_context
    from agent_brain.memory.context.injection_query_context import InjectionQueryContext
    from agent_brain.memory.context.query_signal import QuerySignal
    from agent_brain.memory.recall.admission import RecallAdmission
    from agent_brain.memory.recall.routed_types import RouteEvidence

    value = item("routed-safe")
    signal = QuerySignal((), (), (), False, "too_weak", 0.0)
    query_context = InjectionQueryContext(
        raw_query="complete routed raw query sentinel",
        admission=RecallAdmission(True, "meaningful_query"),
        query_signal=signal,
        evidence_by_id={
            value.id: RouteEvidence(
                routes=("semantic_raw",),
                semantic_similarity=0.82,
                semantic_rank=1,
                lexical_terms_rank=None,
                lexical_raw_rank=None,
            ),
        },
    )

    result = build_injection_context(
        [candidate(value)],
        query_context=query_context,
    )

    assert [entry.decision.candidate.item.id for entry in result.included] == [value.id]


def test_gateway_rejects_invalid_or_conflicting_routed_context_without_query_leak():
    from agent_brain.memory.context.injection_gateway import evaluate_injection_candidates
    from agent_brain.memory.context.injection_query_context import InjectionQueryContext
    from agent_brain.memory.context.query_signal import QuerySignal
    from agent_brain.memory.recall.admission import RecallAdmission

    value = item("routed-context-conflict")
    raw_sentinel = "SECRET_RAW_QUERY_SENTINEL"
    context = InjectionQueryContext(
        raw_query=raw_sentinel,
        admission=RecallAdmission(True, "meaningful_query"),
        query_signal=QuerySignal((), (), (), False, "too_weak", 0.0),
        evidence_by_id={},
    )

    with pytest.raises(TypeError):
        evaluate_injection_candidates([candidate(value)], query_context=object())
    with pytest.raises(ValueError) as raised:
        evaluate_injection_candidates(
            [candidate(value)],
            query="different query",
            query_context=context,
        )

    rendered = f"{raised.value!s} {raised.value!r}"
    assert raw_sentinel not in rendered


def test_route_answerability_reason_is_in_gateway_closed_contract():
    from agent_brain.memory.context.injection_gateway import INJECTION_EXCLUSION_REASONS

    assert "route_answerability_insufficient" in INJECTION_EXCLUSION_REASONS


def test_gateway_reexports_single_shared_exclusion_reason_contract():
    from agent_brain.memory.context.injection_contract import (
        INJECTION_EXCLUSION_REASONS as CANONICAL_EXCLUSION_REASONS,
    )
    from agent_brain.memory.context.injection_gateway import (
        INJECTION_EXCLUSION_REASONS,
    )

    assert INJECTION_EXCLUSION_REASONS is CANONICAL_EXCLUSION_REASONS


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (item("private", sensitivity=Sensitivity.private), "sensitivity_not_allowed"),
        (item("secret", sensitivity=Sensitivity.secret), "sensitivity_not_allowed"),
        (item("review", tags=["needs-review"]), "requires_review"),
        (item("unverified", tags=["unverified-boundary"]), "requires_review"),
        (item("superseded", superseded_by="mem-new"), "superseded"),
    ],
)
def test_gateway_excludes_noninjectable_item_states(value, reason):
    from agent_brain.memory.context.injection_gateway import evaluate_injection_candidates

    result = evaluate_injection_candidates([candidate(value)])
    assert result.included == []
    assert reason in result.excluded[0].reasons


def test_gateway_builds_existing_context_pack_contract():
    from agent_brain.memory.context.injection_gateway import build_injection_context

    value = item("safe")
    result = build_injection_context(
        [candidate(value)], query="injection gateway safe", requested="auto", max_items=1,
    )
    pack = result.included[0].pack
    assert pack.item_id == value.id
    assert pack.detail_uri == f"memory://items/{value.id}/body"
    assert pack.retrieve_hint == f"read_memory(id='{value.id}', head=2000, view='detail')"
    assert result.metrics()["packed_tokens"] == pack.packed_tokens


def test_gateway_preserves_scope_and_max_item_gates():
    from agent_brain.memory.context.injection_gateway import evaluate_injection_candidates

    scoped = item(
        "scope-gate",
        tags=["state"],
        validity={"cwd": "/expected"},
    )
    scope_result = evaluate_injection_candidates(
        [candidate(scoped)],
        current_scope={"cwd": "/other"},
    )
    assert scope_result.included == []
    assert "scope_mismatch" in scope_result.excluded[0].reasons

    first = item("max-first")
    second = item("max-second")
    max_result = evaluate_injection_candidates(
        [candidate(first, 2.0), candidate(second, 1.0)],
        max_items=1,
    )
    assert len(max_result.included) == 1
    assert "max_items_exceeded" in max_result.excluded[0].reasons


def test_gateway_applies_final_pack_budget():
    from agent_brain.memory.context.injection_gateway import build_injection_context

    value = item(
        "pack-budget",
        context_views={
            "locator": "pack budget locator",
            "overview": "pack budget overview",
        },
    )
    result = build_injection_context(
        [candidate(value)],
        requested="detail",
        budget_tokens=0,
    )
    assert result.included == []
    assert "pack_budget_exceeded" in result.excluded[0].reasons


def test_gateway_rejects_invalid_context_verbosity_before_packing():
    from agent_brain.memory.context.injection_gateway import build_injection_context

    with pytest.raises(ValueError):
        build_injection_context(
            [candidate(item("invalid-verbosity"))],
            requested="bogus",
        )


def test_gateway_metrics_are_aggregate_only():
    from agent_brain.memory.context.injection_gateway import build_injection_context

    safe = item("safe-metrics")
    rejected = item("rejected-metrics", sensitivity=Sensitivity.private)
    safe_candidate = candidate(safe)
    rejected_candidate = candidate(rejected)
    metrics = build_injection_context(
        [safe_candidate, rejected_candidate],
        requested="detail",
    ).metrics()

    assert "items" not in metrics
    assert metrics["candidate_count"] == 2
    assert metrics["included_count"] == 1
    assert metrics["excluded_count"] == 1
    assert metrics["selected_views"] == {"detail": 1}
    assert metrics["compressed_count"] == 0
    rendered_metrics = repr(metrics)
    for value, context_candidate in (
        (safe, safe_candidate),
        (rejected, rejected_candidate),
    ):
        assert value.id not in rendered_metrics
        assert value.title not in rendered_metrics
        assert value.summary not in rendered_metrics
        assert context_candidate.body not in rendered_metrics


@pytest.mark.parametrize(
    ("with_safe_candidate", "raw_candidate_count", "expected_gateway_count"),
    [
        (False, 1, 0),
        (True, 2, 1),
    ],
)
def test_surface_metrics_count_hydrate_failures_without_fake_candidates(
    with_safe_candidate,
    raw_candidate_count,
    expected_gateway_count,
):
    from agent_brain.memory.context.injection_gateway import (
        HYDRATE_ERROR_REASON,
        build_injection_context,
        surface_injection_metrics,
    )

    safe = item("surface-hydrate-safe")
    candidates = [candidate(safe)] if with_safe_candidate else []
    result = build_injection_context(candidates)
    gateway_metrics = result.metrics()

    metrics = surface_injection_metrics(
        result,
        raw_candidate_count=raw_candidate_count,
        hydrate_error_count=1,
    )

    # Bare Gateway metrics describe only candidates that reached the Gateway.
    assert gateway_metrics["candidate_count"] == expected_gateway_count
    assert gateway_metrics["excluded_count"] == 0
    # Surface metrics describe the full raw-hit partition, including ghosts.
    assert metrics["candidate_count"] == raw_candidate_count
    assert metrics["raw_candidate_count"] == raw_candidate_count
    assert metrics["gateway_candidate_count"] == expected_gateway_count
    assert metrics["included_count"] == expected_gateway_count
    assert metrics["hydrate_error_count"] == 1
    assert metrics["excluded_count"] == 1
    assert metrics["excluded_reasons"] == {HYDRATE_ERROR_REASON: 1}
    for count_key in (
        "candidate_count",
        "raw_candidate_count",
        "gateway_candidate_count",
        "included_count",
        "hydrate_error_count",
        "excluded_count",
    ):
        assert type(metrics[count_key]) is int
    rendered = repr(metrics)
    for forbidden in (safe.id, safe.title, safe.summary, "body:"):
        assert forbidden not in rendered


def test_gateway_synthetic_exclusion_reasons_are_canonical(monkeypatch):
    import agent_brain.memory.context.injection_gateway as gateway

    broken = item("canonical-pack-error")

    def fail_pack(*_args, **_kwargs):
        raise RuntimeError("synthetic pack failure")

    monkeypatch.setattr(gateway, "pack_decisions", fail_pack)
    result = gateway.build_injection_context([candidate(broken)])
    emitted = {
        reason
        for decision in result.excluded
        for reason in decision.reasons
    }

    assert gateway.HYDRATE_ERROR_REASON in gateway.INJECTION_EXCLUSION_REASONS
    assert gateway.PACK_ERROR_REASON in emitted
    assert emitted <= gateway.INJECTION_EXCLUSION_REASONS


def test_unknown_exclusion_reason_error_never_echoes_reason_content():
    from agent_brain.memory.context.context_firewall_types import FirewallDecision
    from agent_brain.memory.context.injection_gateway import (
        injection_exclusion_reason_counts,
    )

    sentinel = "SECRET_PRIVATE_UNKNOWN_EXCLUSION_REASON"
    context_candidate = candidate(item("unknown-reason-sentinel"))
    decision = FirewallDecision(
        candidate=context_candidate,
        action="exclude",
        reasons=(sentinel,),
        score=context_candidate.score,
        effective_score=0.0,
    )

    with pytest.raises(ValueError) as raised:
        injection_exclusion_reason_counts([decision])

    rendered = f"{raised.value!s} {raised.value!r}"
    assert sentinel not in rendered
    assert str(raised.value) == "unsupported injection exclusion reason"


@pytest.mark.parametrize("invalid_count", [False, True, 0.0, 1.0, -1])
def test_exclusion_reason_counts_require_nonnegative_plain_int(invalid_count):
    from agent_brain.memory.context.injection_gateway import (
        injection_exclusion_reason_counts,
    )

    with pytest.raises(ValueError):
        injection_exclusion_reason_counts(
            [],
            hydrate_error_count=invalid_count,
        )


@pytest.mark.parametrize(
    ("raw_candidate_count", "hydrate_error_count"),
    [
        (False, 0),
        (True, 1),
        (0.0, 0),
        (1.0, 1),
        (-1, 0),
        (0, False),
        (1, True),
        (0, 0.0),
        (1, 1.0),
        (0, -1),
    ],
)
def test_surface_metrics_require_nonnegative_plain_int_counts(
    raw_candidate_count,
    hydrate_error_count,
):
    from agent_brain.memory.context.injection_gateway import (
        build_injection_context,
        surface_injection_metrics,
    )

    result = build_injection_context([])

    with pytest.raises(ValueError):
        surface_injection_metrics(
            result,
            raw_candidate_count=raw_candidate_count,
            hydrate_error_count=hydrate_error_count,
        )


def test_gateway_diagnostic_logs_only_aggregate_reason(caplog):
    from agent_brain.memory.context.injection_gateway import _record_injection_diagnostic

    _record_injection_diagnostic(
        surface="mcp-search",
        reason="hydrate_error",
        count=2,
    )
    assert "surface=mcp-search reason=hydrate_error count=2" in caplog.text
    assert "mem-" not in caplog.text


def test_gateway_excludes_one_pack_error_without_dropping_safe_peer(monkeypatch):
    import agent_brain.memory.context.injection_gateway as gateway

    broken = item("broken-pack")
    safe = item("safe-pack")
    real_pack = gateway.pack_decisions

    def conditional_pack(decisions, **kwargs):
        if decisions[0].candidate.item.id == broken.id:
            raise RuntimeError("synthetic pack failure")
        return real_pack(decisions, **kwargs)

    monkeypatch.setattr(gateway, "pack_decisions", conditional_pack)
    result = gateway.build_injection_context(
        [candidate(broken, 2.0), candidate(safe, 1.0)],
        query="injection gateway pack",
        max_items=2,
    )
    assert [entry.decision.candidate.item.id for entry in result.included] == [safe.id]
    rejected = {decision.candidate.item.id: decision for decision in result.excluded}
    assert "pack_error" in rejected[broken.id].reasons
    assert result.metrics()["excluded_reasons"]["pack_error"] == 1
    assert broken.id not in repr(result.metrics())
    assert broken.title not in repr(result.metrics())


def test_gateway_refills_max_item_slot_after_pack_error(monkeypatch):
    import agent_brain.memory.context.injection_gateway as gateway

    broken = item("broken-pack-refill")
    safe = item("safe-pack-refill")
    real_pack = gateway.pack_decisions

    def conditional_pack(decisions, **kwargs):
        if decisions[0].candidate.item.id == broken.id:
            raise RuntimeError("synthetic pack failure")
        return real_pack(decisions, **kwargs)

    monkeypatch.setattr(gateway, "pack_decisions", conditional_pack)
    result = gateway.build_injection_context(
        [candidate(broken, 2.0), candidate(safe, 1.0)],
        max_items=1,
    )

    assert [entry.decision.candidate.item.id for entry in result.included] == [safe.id]
    assert [decision.candidate.item.id for decision in result.excluded] == [broken.id]
    assert "pack_error" in result.excluded[0].reasons
    assert result.used_tokens == result.included[0].pack.packed_tokens
    assert result.full_tokens == result.included[0].pack.full_tokens


def test_gateway_analyzes_query_once_during_pack_refill(monkeypatch):
    import agent_brain.memory.context.injection_gateway as gateway

    stable = item("stable-query-pack-refill")
    broken = item("broken-query-pack-refill")
    replacement = item("replacement-query-pack-refill")
    real_analyze = gateway.analyze_injection_query
    real_pack = gateway.pack_decisions
    analyze_calls = 0

    def counting_analyze(query):
        nonlocal analyze_calls
        analyze_calls += 1
        return real_analyze(query)

    def conditional_pack(decisions, **kwargs):
        if decisions[0].candidate.item.id == broken.id:
            raise RuntimeError("synthetic pack failure")
        return real_pack(decisions, **kwargs)

    monkeypatch.setattr(gateway, "analyze_injection_query", counting_analyze)
    monkeypatch.setattr(gateway, "pack_decisions", conditional_pack)
    result = gateway.build_injection_context(
        [
            candidate(stable, 3.0),
            candidate(broken, 2.0),
            candidate(replacement, 1.0),
        ],
        query="injection gateway query pack refill",
        max_items=2,
    )

    assert analyze_calls == 1
    assert [entry.decision.candidate.item.id for entry in result.included] == [
        stable.id,
        replacement.id,
    ]
    assert result.used_tokens == sum(
        entry.pack.packed_tokens for entry in result.included
    )
    assert result.full_tokens == sum(
        entry.pack.full_tokens for entry in result.included
    )


def test_gateway_refills_max_item_slot_after_pack_budget_exclusion():
    from agent_brain.memory.context.injection_gateway import build_injection_context

    oversized = item(
        "oversized-pack-refill",
        context_views={"locator": "oversized locator " * 20},
    )
    small = item(
        "small-pack-refill",
        context_views={"locator": "ok"},
    )
    result = build_injection_context(
        [candidate(oversized, 2.0), candidate(small, 1.0)],
        requested="locator",
        max_items=1,
        budget_tokens=2,
    )

    assert [entry.decision.candidate.item.id for entry in result.included] == [small.id]
    assert [decision.candidate.item.id for decision in result.excluded] == [oversized.id]
    assert "pack_budget_exceeded" in result.excluded[0].reasons
    assert result.used_tokens == result.included[0].pack.packed_tokens
    assert result.full_tokens == result.included[0].pack.full_tokens


def test_gateway_packs_each_candidate_once_during_multiple_refills(monkeypatch):
    from collections import Counter

    import agent_brain.memory.context.injection_gateway as gateway

    stable = item("stable-multi-refill")
    broken_one = item("broken-one-multi-refill")
    broken_two = item("broken-two-multi-refill")
    replacement = item("replacement-multi-refill")
    broken_ids = {broken_one.id, broken_two.id}
    real_pack = gateway.pack_decisions
    pack_calls = Counter()

    def conditional_pack(decisions, **kwargs):
        item_id = decisions[0].candidate.item.id
        pack_calls[item_id] += 1
        if item_id in broken_ids:
            raise RuntimeError("synthetic pack failure")
        return real_pack(decisions, **kwargs)

    monkeypatch.setattr(gateway, "pack_decisions", conditional_pack)
    result = gateway.build_injection_context(
        [
            candidate(stable, 4.0),
            candidate(broken_one, 3.0),
            candidate(broken_two, 2.0),
            candidate(replacement, 1.0),
        ],
        max_items=2,
    )

    assert [entry.decision.candidate.item.id for entry in result.included] == [
        stable.id,
        replacement.id,
    ]
    assert pack_calls == Counter({
        stable.id: 1,
        broken_one.id: 1,
        broken_two.id: 1,
        replacement.id: 1,
    })


def test_gateway_runs_semantic_answerability_once_per_candidate(monkeypatch):
    from collections import Counter

    import agent_brain.memory.context.injection_gateway as gateway
    from agent_brain.memory.context.answerability import SemanticAnswerabilityDecision
    from agent_brain.memory.context.context_firewall import ContextFirewall

    stable = item("stable-semantic-refill")
    broken = item("broken-semantic-refill")
    replacement = item("replacement-semantic-refill")
    verify_calls = Counter()

    class CountingVerifier:
        def verify(self, *, query, candidate, signal, deterministic):
            del query, signal, deterministic
            verify_calls[candidate.item.id] += 1
            return SemanticAnswerabilityDecision(answerable=True)

    verifier = CountingVerifier()
    monkeypatch.setattr(
        gateway,
        "ContextFirewall",
        lambda: ContextFirewall(answerability_verifier=verifier),
    )
    real_pack = gateway.pack_decisions

    def conditional_pack(decisions, **kwargs):
        if decisions[0].candidate.item.id == broken.id:
            raise RuntimeError("synthetic pack failure")
        return real_pack(decisions, **kwargs)

    monkeypatch.setattr(gateway, "pack_decisions", conditional_pack)
    result = gateway.build_injection_context(
        [
            candidate(stable, 3.0),
            candidate(broken, 2.0),
            candidate(replacement, 1.0),
        ],
        query="injection gateway semantic refill",
        max_items=2,
    )

    assert [entry.decision.candidate.item.id for entry in result.included] == [
        stable.id,
        replacement.id,
    ]
    assert verify_calls == Counter({
        stable.id: 1,
        broken.id: 1,
        replacement.id: 1,
    })


def test_gateway_revalidates_final_max_limited_cohort() -> None:
    from agent_brain.memory.context.injection_gateway import build_injection_context

    alpha = item("alpha-only").model_copy(update={
        "title": "Alpha implementation",
        "summary": "Alpha implementation detail",
    })
    beta = item("beta-only").model_copy(update={
        "title": "Alpha beta implementation",
        "summary": "Alpha beta implementation detail",
    })

    result = build_injection_context(
        [candidate(alpha, 2.0), candidate(beta, 1.0)],
        query="alpha beta",
        max_items=1,
    )

    assert result.included == []
    assert result.used_tokens == 0
    assert result.full_tokens == 0
    rejected = {
        decision.candidate.item.id: decision
        for decision in result.excluded
    }
    assert "cohort_strong_anchor_undercovered" in rejected[alpha.id].reasons
    assert "max_items_exceeded" in rejected[beta.id].reasons


def test_gateway_metrics_ignore_pack_annotations_on_final_cohort_exclusions() -> None:
    from agent_brain.memory.context.injection_gateway import build_injection_context

    alpha = item(
        "alpha-budget-downgrade",
        validity={"cwd": "/repo/alpha"},
        context_views={
            "locator": "alpha",
            "overview": "alpha overview " * 20,
        },
    ).model_copy(update={
        "title": "Alpha implementation",
        "summary": "Alpha implementation detail",
    })
    beta = item("beta-budget-peer").model_copy(update={
        "title": "Alpha beta implementation",
        "summary": "Alpha beta implementation detail",
    })

    result = build_injection_context(
        [candidate(alpha, 2.0), candidate(beta, 1.0)],
        query="alpha beta",
        requested="auto",
        max_items=1,
        budget_tokens=3,
    )

    rejected = {
        decision.candidate.item.id: decision.reasons
        for decision in result.excluded
    }
    assert "budget_downgraded_to_locator" in rejected[alpha.id]
    assert result.metrics()["excluded_reasons"] == {
        "cohort_strong_anchor_undercovered": 1,
        "max_items_exceeded": 1,
    }
    assert result.metrics()["candidate_count"] == 2
    assert result.metrics()["included_count"] == 0
    assert result.metrics()["excluded_count"] == 2


@pytest.mark.parametrize(
    "unsafe_score",
    [float("nan"), float("inf"), float("-inf"), 2**53, -(2**53)],
)
def test_gateway_rejects_nonfinite_and_javascript_unsafe_candidate_scores(
    unsafe_score,
    monkeypatch,
) -> None:
    import agent_brain.memory.context.injection_gateway as gateway

    value = item("unsafe-retrieval-score")

    def fail_pack(*_args, **_kwargs):
        raise AssertionError("unsafe candidate scores must never reach packing")

    monkeypatch.setattr(gateway, "pack_decisions", fail_pack)

    result = gateway.build_injection_context(
        [candidate(value, unsafe_score)],
        query="unsafe retrieval score boundary",
    )

    assert result.included == []
    assert len(result.excluded) == 1
    decision = result.excluded[0]
    assert decision.reasons == ("invalid_candidate_score",)
    assert math.isfinite(decision.score)
    assert decision.score == 0.0
    assert result.metrics()["excluded_reasons"] == {"invalid_candidate_score": 1}
    json.dumps(result.metrics(), allow_nan=False)
