from datetime import datetime, timezone

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
    ).metrics()

    assert "items" not in metrics
    assert metrics["candidate_count"] == 2
    assert metrics["included_count"] == 1
    assert metrics["excluded_count"] == 1
    rendered_metrics = repr(metrics)
    for value, context_candidate in (
        (safe, safe_candidate),
        (rejected, rejected_candidate),
    ):
        assert value.id not in rendered_metrics
        assert value.title not in rendered_metrics
        assert value.summary not in rendered_metrics
        assert context_candidate.body not in rendered_metrics


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
