from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import get_args, get_type_hints

import pytest


def test_meaningful_queries_are_admitted_independently_of_query_signal() -> None:
    from agent_brain.memory.recall.admission import analyze_recall_admission

    for query in (
        "网关呢",
        "hooks 为什么没有召回记忆",
        "Why did the memory hooks miss the relevant context?",
    ):
        admission = analyze_recall_admission(query)

        assert admission.allowed, query
        assert admission.reason == "meaningful_query", query


@pytest.mark.parametrize(
    "query",
    (
        "网关呢",
        "hooks 为什么没有召回记忆",
        "Why did the memory hooks miss the relevant context?",
    ),
)
def test_build_request_keeps_admission_when_query_signal_has_no_terms(
    monkeypatch: pytest.MonkeyPatch,
    query: str,
) -> None:
    from agent_brain.memory.context.query_signal import QuerySignal
    from agent_brain.memory.recall import admission as admission_module

    blocked_signal = QuerySignal(
        terms=(),
        strong_terms=(),
        weak_terms=(),
        injectable=False,
        reason="too_weak",
        specificity=0.0,
    )
    monkeypatch.setattr(
        admission_module,
        "analyze_injection_query",
        lambda prompt: blocked_signal,
    )

    request = admission_module.build_recall_request(query, adapter="codex")

    assert request.admission.allowed
    assert request.lexical_terms == ()
    assert request.query_signal is blocked_signal


@pytest.mark.parametrize(
    ("query", "reason"),
    [
        ("", "empty_query"),
        ("  \n\t", "empty_query"),
        ("？！...", "punctuation_only"),
        ("/remember", "adapter_control_command"),
        ("/goal pause", "adapter_control_command"),
        ("/compact", "adapter_control_command"),
        ("/clear", "adapter_control_command"),
        ("是", "weak_confirmation"),
        ("确认", "weak_confirmation"),
        ("继续", "weak_confirmation"),
        ("OK", "weak_confirmation"),
        ("okay", "weak_confirmation"),
        ("1", "weak_confirmation"),
    ],
)
def test_admission_rejects_only_fixed_non_meaningful_classes(
    query: str,
    reason: str,
) -> None:
    from agent_brain.memory.recall.admission import analyze_recall_admission

    admission = analyze_recall_admission(query)

    assert not admission.allowed
    assert admission.reason == reason


@pytest.mark.parametrize(
    "query",
    (
        "/opt/agent-memory-hub/project/README.md",
        "/tmp/agent-memory-hub",
    ),
)
def test_standalone_absolute_paths_are_not_adapter_control_commands(query: str) -> None:
    from agent_brain.memory.recall.admission import analyze_recall_admission

    admission = analyze_recall_admission(query)

    assert admission.allowed
    assert admission.reason == "meaningful_query"


@pytest.mark.parametrize("query", ("🤔", "💾", "€"))
def test_symbol_only_queries_default_to_allowed(query: str) -> None:
    from agent_brain.memory.recall.admission import analyze_recall_admission

    admission = analyze_recall_admission(query)

    assert admission.allowed
    assert admission.reason == "meaningful_query"


@pytest.mark.parametrize("query", ("OK…", "确认：", "继续～"))
def test_weak_confirmations_ignore_unicode_punctuation_and_symbol_edges(query: str) -> None:
    from agent_brain.memory.recall.admission import analyze_recall_admission

    admission = analyze_recall_admission(query)

    assert not admission.allowed
    assert admission.reason == "weak_confirmation"


def test_admission_uses_shared_prompt_normalization() -> None:
    from agent_brain.memory.recall.admission import analyze_recall_admission

    admission = analyze_recall_admission(
        "<system-reminder>ignore this content</system-reminder>\n确认"
    )

    assert not admission.allowed
    assert admission.reason == "weak_confirmation"


def test_admission_reason_literal_contract_is_closed() -> None:
    from agent_brain.memory.recall.admission import AdmissionReason

    assert set(get_args(AdmissionReason)) == {
        "meaningful_query",
        "empty_query",
        "punctuation_only",
        "adapter_control_command",
        "weak_confirmation",
    }


def test_project_scope_rejects_hard_filter_for_soft_sources() -> None:
    from agent_brain.memory.recall.routed_types import ProjectScope

    assert ProjectScope("agent-memory-hub", "explicit", hard_filter=True).hard_filter
    assert not ProjectScope("agent-memory-hub", "cwd", hard_filter=False).hard_filter
    assert not ProjectScope("agent-memory-hub", "agent_inferred", hard_filter=False).hard_filter

    with pytest.raises(ValueError, match="explicit"):
        ProjectScope("agent-memory-hub", "cwd", hard_filter=True)
    with pytest.raises(ValueError, match="explicit"):
        ProjectScope("agent-memory-hub", "agent_inferred", hard_filter=True)


def test_project_scope_strips_value_and_rejects_blank_values() -> None:
    from agent_brain.memory.recall.routed_types import ProjectScope

    scope = ProjectScope("  agent-memory-hub  ", "explicit")

    assert scope.value == "agent-memory-hub"
    for value in ("", "  \n\t"):
        with pytest.raises(ValueError, match="non-empty"):
            ProjectScope(value, "explicit")


def test_build_request_preserves_payload_and_truncates_lexical_terms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_brain.memory.context.query_signal import QuerySignal
    from agent_brain.memory.recall import admission as admission_module
    from agent_brain.memory.recall.routed_types import ProjectScope

    signal = QuerySignal(
        terms=("one", "two", "three", "four", "five", "six", "seven"),
        strong_terms=("one",),
        weak_terms=(),
        injectable=True,
        reason="strong_term",
        specificity=1.0,
    )
    seen_prompts: list[str] = []

    def fake_analyze(prompt: str) -> QuerySignal:
        seen_prompts.append(prompt)
        return signal

    monkeypatch.setattr(admission_module, "analyze_injection_query", fake_analyze)
    scope = ProjectScope("agent-memory-hub", "explicit", hard_filter=True)
    raw_query = (
        "hooks 为什么没有召回记忆\n"
        "<agent_brain>stale memory candidate</agent_brain>"
    )

    request = admission_module.build_recall_request(
        raw_query,
        adapter="codex",
        project_scope=scope,
        cwd="/workspace/project",
        session_id="session-123",
    )

    assert request.raw_query == raw_query
    assert request.normalized_query == "hooks 为什么没有召回记忆"
    assert seen_prompts == [request.normalized_query]
    assert request.lexical_terms == ("one", "two", "three", "four", "five", "six")
    assert request.query_signal is signal
    assert request.project_scope is scope
    assert request.cwd == "/workspace/project"
    assert request.adapter == "codex"
    assert request.session_id == "session-123"


def test_routed_contract_dataclasses_are_frozen() -> None:
    from agent_brain.memory.context.query_signal import QuerySignal
    from agent_brain.memory.recall.admission import RecallAdmission
    from agent_brain.memory.recall.routed_types import (
        ProjectScope,
        RecallRequest,
        RouteEvidence,
        RoutedSearchResult,
        RouteTrace,
    )

    admission = RecallAdmission(True, "meaningful_query")
    scope = ProjectScope("agent-memory-hub", "explicit", hard_filter=True)
    signal = QuerySignal((), (), (), False, "too_weak", 0.0)
    request = RecallRequest(
        raw_query="网关呢",
        normalized_query="网关呢",
        lexical_terms=(),
        admission=admission,
        query_signal=signal,
        project_scope=scope,
        cwd="/workspace/project",
        adapter="codex",
        session_id="session-123",
    )
    trace = RouteTrace("semantic", "ok", 3.5, 1, "route_completed")
    evidence = RouteEvidence(("semantic",), 0.91, 1, None, None)
    result = RoutedSearchResult([], (trace,), admission, {"mem-1": evidence})

    objects_and_fields = (
        (admission, "allowed"),
        (scope, "value"),
        (request, "adapter"),
        (trace, "latency_ms"),
        (evidence, "semantic_similarity"),
        (result, "hits"),
    )
    for value, field_name in objects_and_fields:
        with pytest.raises(FrozenInstanceError):
            setattr(value, field_name, None)


def test_route_literal_contracts_are_closed() -> None:
    from agent_brain.memory.recall.routed_types import (
        RouteReason,
        RouteStatus,
    )

    assert set(get_args(RouteStatus)) == {"ok", "skipped", "timeout", "error"}
    assert set(get_args(RouteReason)) == {
        "route_completed",
        "admission_rejected",
        "lexical_terms_empty",
        "semantic_not_ready",
        "route_timeout",
        "route_error",
    }


@pytest.mark.parametrize(
    ("status", "reason"),
    (
        ("ok", "route_completed"),
        ("skipped", "admission_rejected"),
        ("skipped", "lexical_terms_empty"),
        ("skipped", "semantic_not_ready"),
        ("timeout", "route_timeout"),
        ("error", "route_error"),
    ),
)
def test_route_trace_accepts_only_coherent_status_reason_pairs(
    status: str,
    reason: str,
) -> None:
    from agent_brain.memory.recall.routed_types import RouteTrace

    trace = RouteTrace("semantic", status, 1.0, 0, reason)  # type: ignore[arg-type]

    assert trace.status == status
    assert trace.reason == reason


@pytest.mark.parametrize(
    ("status", "reason"),
    (
        ("ok", "route_error"),
        ("skipped", "route_completed"),
        ("timeout", "semantic_not_ready"),
        ("error", "route_timeout"),
    ),
)
def test_route_trace_rejects_contradictory_status_reason_pairs(
    status: str,
    reason: str,
) -> None:
    from agent_brain.memory.recall.routed_types import RouteTrace

    with pytest.raises(ValueError, match="status/reason"):
        RouteTrace("semantic", status, 1.0, 0, reason)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("latency_ms", "candidate_count"),
    ((-0.1, 0), (0.0, -1)),
)
def test_route_trace_rejects_negative_metrics(
    latency_ms: float,
    candidate_count: int,
) -> None:
    from agent_brain.memory.recall.routed_types import RouteTrace

    with pytest.raises(ValueError, match="non-negative"):
        RouteTrace("semantic", "ok", latency_ms, candidate_count, "route_completed")


def test_route_evidence_keeps_cosine_separate_from_hit_score() -> None:
    from agent_brain.memory.recall.retrieval_types import RetrievedItem
    from agent_brain.memory.recall.routed_types import RouteEvidence, RoutedSearchResult
    from agent_brain.memory.recall.admission import RecallAdmission

    hit = RetrievedItem("mem-1", 0.03, 2, 1)
    evidence = RouteEvidence(
        routes=("semantic", "lexical"),
        semantic_similarity=0.92,
        semantic_rank=1,
        lexical_terms_rank=2,
        lexical_raw_rank=3,
    )
    result = RoutedSearchResult(
        hits=[hit],
        routes=(),
        admission=RecallAdmission(True, "meaningful_query"),
        evidence_by_id={hit.id: evidence},
    )

    assert result.hits[0].score == 0.03
    assert isinstance(result.hits, list)
    assert get_type_hints(RoutedSearchResult)["hits"] == list[RetrievedItem]
    assert result.evidence_by_id[hit.id].semantic_similarity == 0.92
    assert not hasattr(result.hits[0], "semantic_similarity")


def test_routed_result_copies_hits_input_but_keeps_a_mutable_list() -> None:
    from agent_brain.memory.recall.admission import RecallAdmission
    from agent_brain.memory.recall.retrieval_types import RetrievedItem
    from agent_brain.memory.recall.routed_types import RoutedSearchResult

    hit = RetrievedItem("mem-1", 0.03, 2, 1)
    source_hits = [hit]
    result = RoutedSearchResult(
        hits=source_hits,
        routes=(),
        admission=RecallAdmission(True, "meaningful_query"),
        evidence_by_id={},
    )

    assert result.hits is not source_hits
    source_hits.clear()
    assert result.hits == [hit]
    result.hits.append(hit)
    assert result.hits == [hit, hit]
