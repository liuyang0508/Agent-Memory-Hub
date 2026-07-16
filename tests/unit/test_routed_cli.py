from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from typer.testing import CliRunner

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.memory.context.context_firewall_types import ContextCandidate, FirewallDecision
from agent_brain.memory.context.context_packing import PackedDecision, build_context_pack
from agent_brain.memory.context.injection_gateway import InjectionResult
from agent_brain.memory.recall.retrieval import SearchFilter
from agent_brain.memory.recall.retrieval_types import RetrievedItem
from agent_brain.memory.recall.routed_types import (
    RouteEvidence,
    RoutedSearchResult,
    RouteTrace,
)


NOW = datetime(2026, 7, 16, tzinfo=timezone.utc)
RUNNER = CliRunner()


def _item(suffix: str, *, title: str | None = None) -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260716-120000-{suffix}",
        type=MemoryType.episode,
        created_at=NOW,
        title=title or f"Routed CLI {suffix}",
        summary=f"Routed CLI summary {suffix}",
        confidence=0.9,
    )


def _hit(value: MemoryItem, score: float = 0.9) -> RetrievedItem:
    return RetrievedItem(
        id=value.id,
        score=score,
        bm25_rank=None,
        vector_rank=None,
    )


def _evidence(rank: int = 1) -> RouteEvidence:
    return RouteEvidence(
        routes=("lexical_terms",),
        semantic_similarity=None,
        semantic_rank=None,
        lexical_terms_rank=rank,
        lexical_raw_rank=None,
    )


def _included(value: MemoryItem, body: str) -> PackedDecision:
    candidate = ContextCandidate(value, body=body, score=0.9, source="cli-routed")
    decision = FirewallDecision(candidate, "include", (), 0.9, 0.9)
    return PackedDecision(decision, build_context_pack(value, body, requested="auto"))


def _excluded(value: MemoryItem, body: str) -> FirewallDecision:
    candidate = ContextCandidate(value, body=body, score=0.8, source="cli-routed")
    return FirewallDecision(candidate, "exclude", ("query_mismatch",), 0.8, 0.0)


class _Store:
    def __init__(self, rows):
        self.rows = rows

    def iter_all(self):
        return iter(self.rows)


class _Retriever:
    def __init__(self, result: RoutedSearchResult):
        self.result = result
        self.routed_calls = []
        self.legacy_calls = []
        self.accesses = []

    def search_routed(self, request, **kwargs):
        self.routed_calls.append((request, kwargs))
        return self.result

    def search(self, query, **kwargs):
        self.legacy_calls.append((query, kwargs))
        return self.result.hits

    def record_accesses(self, hits):
        self.accesses.append([hit.id for hit in hits])


def test_hook_payload_has_exact_stable_keys_and_json_contract() -> None:
    from agent_brain.interfaces.cli.routed_query import HookSearchPayload

    payload = HookSearchPayload(
        status="injected",
        reason="included",
        context="safe packed context",
        routes=(
            {
                "route": "lexical_terms",
                "status": "ok",
                "candidate_count": 1,
                "reason": "route_completed",
            },
        ),
    )

    decoded = json.loads(json.dumps(payload.to_dict()))
    assert list(decoded) == ["status", "reason", "context", "routes"]
    assert decoded["context"] == "safe packed context"


def test_execute_routed_query_uses_full_query_hard_project_scope_and_final_access_only(
    tmp_path,
    monkeypatch,
) -> None:
    from agent_brain.interfaces.cli import routed_query

    keep = _item("keep")
    drop = _item("drop")
    raw_query = "complete positional query with routed evidence"
    result = RoutedSearchResult(
        [_hit(keep), _hit(drop, 0.8)],
        (RouteTrace("lexical_terms", "ok", 1.0, 2, "route_completed"),),
        routed_query.build_recall_request(raw_query, adapter="codex").admission,
        {keep.id: _evidence(1), drop.id: _evidence(2)},
    )
    retriever = _Retriever(result)
    observed = {}

    def gateway(candidates, **kwargs):
        observed["candidates"] = candidates
        observed["kwargs"] = kwargs
        return InjectionResult(
            [_included(keep, "packed keep body")],
            [_excluded(drop, "raw rejected sentinel")],
            (),
            2,
            2,
        )

    monkeypatch.setattr(routed_query, "build_injection_context", gateway)
    payload = routed_query.execute_routed_query(
        raw_query=raw_query,
        store=_Store([(keep, "packed keep body"), (drop, "raw rejected sentinel")]),
        retriever=retriever,
        top_k=1,
        filters=SearchFilter(project="alpha"),
        requested="auto",
        project="alpha",
        adapter="codex",
        session_id="sess-1",
        cwd="/repo/alpha",
        brain_dir=tmp_path,
    )

    request, kwargs = retriever.routed_calls[0]
    assert request.raw_query == raw_query
    assert request.project_scope is not None
    assert request.project_scope.value == "alpha"
    assert request.project_scope.source == "explicit"
    assert request.project_scope.hard_filter is True
    assert kwargs["record_access"] is False
    assert observed["kwargs"]["query_context"].raw_query == raw_query
    assert observed["kwargs"]["max_items"] == 1
    assert retriever.accesses == [[keep.id]]
    assert payload.status == "injected"
    assert payload.reason == "included"
    assert "Routed CLI summary keep" in payload.context
    assert "raw rejected sentinel" not in payload.context


@pytest.mark.parametrize(
    ("raw_query", "mode", "status", "reason"),
    [
        ("ok", "result", "empty", "admission_rejected"),
        ("meaningful query with no candidates", "result", "empty", "no_candidates"),
        ("meaningful query timeout", "timeout", "timeout", "overall_timeout"),
        ("meaningful query internal", "error", "error", "internal_error"),
    ],
)
def test_execute_routed_query_four_statuses_are_fail_closed(
    raw_query,
    mode,
    status,
    reason,
) -> None:
    from agent_brain.interfaces.cli import routed_query

    request = routed_query.build_recall_request(raw_query, adapter="codex")

    class Retriever(_Retriever):
        def search_routed(self, request, **kwargs):
            if mode == "timeout":
                raise TimeoutError("SECRET_TIMEOUT_DETAIL")
            if mode == "error":
                raise RuntimeError("SECRET_INTERNAL_DETAIL")
            return RoutedSearchResult([], (), request.admission, {})

    payload = routed_query.execute_routed_query(
        raw_query=raw_query,
        store=_Store([]),
        retriever=Retriever(RoutedSearchResult([], (), request.admission, {})),
        top_k=3,
        filters=SearchFilter(),
        requested="auto",
        project=None,
        adapter="codex",
        session_id=None,
        cwd=None,
    )

    decoded = json.loads(json.dumps(payload.to_dict()))
    assert list(decoded) == ["status", "reason", "context", "routes"]
    assert decoded["status"] == status
    assert decoded["reason"] == reason
    assert decoded["context"] == ""
    assert "SECRET_" not in json.dumps(decoded)


def test_gateway_all_reject_and_error_never_leak_raw_hits(monkeypatch) -> None:
    from agent_brain.interfaces.cli import routed_query

    value = _item("private-raw", title="SECRET_RAW_TITLE")
    query = "gateway routed rejection sentinel"
    request = routed_query.build_recall_request(query, adapter="codex")
    retriever = _Retriever(
        RoutedSearchResult(
            [_hit(value)],
            (RouteTrace("lexical_terms", "ok", 0.5, 1, "route_completed"),),
            request.admission,
            {value.id: _evidence()},
        )
    )
    calls = []

    def reject(*args, **kwargs):
        calls.append((args, kwargs))
        return InjectionResult([], [], (), 0, 0)

    monkeypatch.setattr(routed_query, "build_injection_context", reject)
    payload = routed_query.execute_routed_query(
        raw_query=query,
        store=_Store([(value, "SECRET_RAW_BODY")]),
        retriever=retriever,
        top_k=3,
        filters=SearchFilter(),
        requested="auto",
        project=None,
        adapter="codex",
        session_id=None,
        cwd=None,
    )
    assert len(calls) == 1
    assert payload.status == "empty"
    assert payload.reason == "all_rejected"
    assert payload.context == ""
    assert "SECRET" not in json.dumps(payload.to_dict())

    monkeypatch.setattr(
        routed_query,
        "build_injection_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("SECRET_GATEWAY")),
    )
    failed = routed_query.execute_routed_query(
        raw_query=query,
        store=_Store([(value, "SECRET_RAW_BODY")]),
        retriever=retriever,
        top_k=3,
        filters=SearchFilter(),
        requested="auto",
        project=None,
        adapter="codex",
        session_id=None,
        cwd=None,
    )
    assert failed.status == "error"
    assert failed.reason == "internal_error"
    assert failed.context == ""
    assert "SECRET" not in json.dumps(failed.to_dict())


def test_missing_hydration_is_aggregate_only(caplog) -> None:
    from agent_brain.interfaces.cli import routed_query

    value = _item("SECRET_GHOST")
    query = "ghost hydration routed query"
    request = routed_query.build_recall_request(query, adapter="codex")
    retriever = _Retriever(
        RoutedSearchResult(
            [_hit(value)],
            (),
            request.admission,
            {value.id: _evidence()},
        )
    )

    payload = routed_query.execute_routed_query(
        raw_query=query,
        store=_Store([]),
        retriever=retriever,
        top_k=3,
        filters=SearchFilter(),
        requested="auto",
        project=None,
        adapter="codex",
        session_id=None,
        cwd=None,
    )

    assert payload.status == "empty"
    assert payload.reason == "all_rejected"
    assert "reason=hydrate_error count=1" in caplog.text
    assert value.id not in caplog.text


def test_flag_zero_uses_legacy_candidates_but_still_calls_gateway_once(monkeypatch) -> None:
    from agent_brain.interfaces.cli import routed_query

    value = _item("legacy")
    query = "legacy rollback routed query"
    request = routed_query.build_recall_request(query, adapter="codex")
    retriever = _Retriever(
        RoutedSearchResult(
            [_hit(value)],
            (),
            request.admission,
            {value.id: _evidence()},
        )
    )
    calls = []
    monkeypatch.setenv("AGENT_MEMORY_HUB_ROUTED_RECALL", "0")
    monkeypatch.setattr(
        routed_query,
        "build_injection_context",
        lambda *args, **kwargs: calls.append((args, kwargs)) or InjectionResult([], [], (), 0, 0),
    )

    payload = routed_query.execute_routed_query(
        raw_query=query,
        store=_Store([(value, "legacy raw body")]),
        retriever=retriever,
        top_k=3,
        filters=SearchFilter(),
        requested="auto",
        project=None,
        adapter="codex",
        session_id=None,
        cwd=None,
    )

    assert retriever.routed_calls == []
    assert len(retriever.legacy_calls) == 1
    assert retriever.legacy_calls[0][1]["record_access"] is False
    assert len(calls) == 1
    assert payload.context == ""


def test_default_flag_uses_search_routed(monkeypatch) -> None:
    from agent_brain.interfaces.cli import routed_query

    query = "default routed generator query"
    request = routed_query.build_recall_request(query, adapter="codex")
    retriever = _Retriever(RoutedSearchResult([], (), request.admission, {}))
    monkeypatch.delenv("AGENT_MEMORY_HUB_ROUTED_RECALL", raising=False)

    routed_query.execute_routed_query(
        raw_query=query,
        store=_Store([]),
        retriever=retriever,
        top_k=3,
        filters=SearchFilter(),
        requested="auto",
        project=None,
        adapter="codex",
        session_id=None,
        cwd=None,
    )

    assert len(retriever.routed_calls) == 1
    assert retriever.legacy_calls == []


def test_open_hook_components_uses_degraded_hashing_without_default_embedder(
    tmp_brain,
    monkeypatch,
) -> None:
    import agent_brain.interfaces.cli as cli
    from agent_brain.interfaces.cli import _shared

    monkeypatch.setattr(cli, "get_default_embedder", pytest.fail)
    store, index, retriever = _shared._open_hook_components()
    try:
        assert store.items_dir == tmp_brain / "items"
        assert retriever.embedder.dim == 384
        assert retriever.embedder.degraded is True
        request = __import__(
            "agent_brain.memory.recall.admission", fromlist=["build_recall_request"]
        ).build_recall_request("term fallback query", adapter="codex")
        result = retriever.search_routed(request, top_k=2, record_access=False)
        traces = {trace.route: trace for trace in result.routes}
        assert traces["semantic_raw"].status == "skipped"
        assert traces["semantic_raw"].reason == "semantic_not_ready"
        assert traces["lexical_terms"].status == "ok"
        assert traces["lexical_raw_fallback"].status == "ok"
    finally:
        index.close()


def test_hook_json_uses_positional_query_and_forces_safe_routed_path(
    tmp_brain,
    monkeypatch,
) -> None:
    import agent_brain.interfaces.cli as cli
    from agent_brain.interfaces.cli import app
    from agent_brain.interfaces.cli.commands import query as query_command
    from agent_brain.interfaces.cli.routed_query import HookSearchPayload

    observed = {}
    sentinel_components = (object(), object(), object())
    monkeypatch.setenv("AGENT_MEMORY_HUB_RAW_QUERY", "WRONG_ENV_QUERY")
    monkeypatch.setattr(cli, "get_default_embedder", pytest.fail)
    monkeypatch.setattr(cli, "_open_hook_components", lambda: sentinel_components)

    def execute(**kwargs):
        observed.update(kwargs)
        return HookSearchPayload("empty", "no_candidates", "", ())

    monkeypatch.setattr(query_command, "execute_routed_query", execute)
    result = RUNNER.invoke(
        app,
        [
            "search",
            "complete positional hook query",
            "--format",
            "hook-json",
            "--explain",
            "--project",
            "alpha",
        ],
    )

    assert result.exit_code == 0, result.output
    decoded = json.loads(result.output)
    assert list(decoded) == ["status", "reason", "context", "routes"]
    assert observed["raw_query"] == "complete positional hook query"
    assert observed["store"] is sentinel_components[0]
    assert observed["retriever"] is sentinel_components[2]
    assert observed["project"] == "alpha"
    assert observed["requested"] == "auto"
    assert "WRONG_ENV_QUERY" not in result.output


def test_hook_json_component_initialization_failure_emits_only_stable_error_json(
    tmp_brain,
    monkeypatch,
) -> None:
    import sys

    import agent_brain.interfaces.cli as cli
    from agent_brain.interfaces.cli import app

    def fail_initialization():
        print("SECRET_COMPONENT_STDOUT_DETAIL")
        print("SECRET_COMPONENT_STDERR_DETAIL", file=sys.stderr)
        raise RuntimeError("SECRET_COMPONENT_INITIALIZATION_DETAIL")

    monkeypatch.setattr(cli, "_open_hook_components", fail_initialization)
    result = RUNNER.invoke(
        app,
        ["search", "valid hook query", "--format", "hook-json"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "status": "error",
        "reason": "internal_error",
        "context": "",
        "routes": [],
    }
    assert result.stdout.count("\n") == 1
    assert "Traceback" not in result.stdout
    assert "SECRET_COMPONENT" not in result.stdout
    assert "SECRET_COMPONENT" not in result.stderr


@pytest.mark.parametrize("output_format", ["text", "table"])
def test_human_routed_formats_smoke_use_routed_orchestration(
    tmp_brain,
    monkeypatch,
    output_format,
) -> None:
    import agent_brain.interfaces.cli as cli
    from agent_brain.interfaces.cli import app
    from agent_brain.interfaces.cli.commands import query as query_command
    from agent_brain.interfaces.cli.routed_query import HookSearchPayload

    monkeypatch.setattr(cli, "_open_components", lambda: (object(), object(), object()))
    monkeypatch.setattr(
        query_command,
        "execute_routed_query",
        lambda **_kwargs: HookSearchPayload(
            "injected", "included", "packed human routed context", ()
        ),
    )
    result = RUNNER.invoke(
        app,
        [
            "search",
            "human routed query",
            "--routed-recall",
            "--format",
            output_format,
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "packed human routed context"


def test_real_hook_json_degraded_recall_goes_through_gateway_and_records_cohort(
    tmp_brain,
) -> None:
    from agent_brain.interfaces.cli import app
    from agent_brain.memory.context.injection_cohorts import (
        injection_cohorts_path,
        latest_injection_cohort,
    )
    from agent_brain.platform.embedding import HashingEmbedder
    from agent_brain.platform.indexing.index import HubIndex
    from agent_brain.memory.store.items_store import ItemsStore

    secret_token = "QzSecretRoutedToken9X"
    raw_query = f"Routed hook protocol verified implementation {secret_token}"
    value = _item(
        "real-hook",
        title=raw_query,
    )
    body = f"{raw_query} with gateway context pack"
    ItemsStore(tmp_brain / "items").write(value, body)
    embedder = HashingEmbedder()
    index = HubIndex(tmp_brain / "index.db", embedding_dim=embedder.dim)
    index.upsert(value, body, embedding=embedder.embed(body))
    index.close()

    result = RUNNER.invoke(
        app,
        [
            "search",
            raw_query,
            "--format",
            "hook-json",
            "--top-k",
            "1",
            "--record-injection-cohort",
            "--adapter",
            "codex",
            "--session",
            "sess-real-hook",
        ],
    )

    assert result.exit_code == 0, result.output
    decoded = json.loads(result.output)
    assert decoded["status"] == "injected"
    assert decoded["reason"] == "included"
    assert value.title in decoded["context"]
    assert all(
        set(route) == {"route", "status", "candidate_count", "reason"}
        for route in decoded["routes"]
    )
    assert any(
        route["route"] == "semantic_raw"
        and route["status"] == "skipped"
        and route["reason"] == "semantic_not_ready"
        for route in decoded["routes"]
    )
    cohort = latest_injection_cohort(
        tmp_brain,
        adapter="codex",
        session_id="sess-real-hook",
    )
    assert cohort is not None
    assert cohort.item_ids == (value.id,)
    assert cohort.query_sha256 is not None
    assert cohort.query_terms == ()
    assert cohort.pack_metrics is not None
    assert value.id not in repr(cohort.pack_metrics)
    runtime_record = injection_cohorts_path(tmp_brain).read_text(encoding="utf-8")
    for rendered in (repr(cohort), runtime_record):
        normalized = rendered.casefold()
        assert raw_query.casefold() not in normalized
        assert secret_token.casefold() not in normalized


def test_hook_json_empty_gap_is_hash_and_aggregate_only(tmp_brain) -> None:
    from agent_brain.interfaces.cli import app
    from agent_brain.memory.governance.recall_events import iter_gap_records

    raw_query = "PRIVATE ROUTED GAP QUERY SENTINEL"
    result = RUNNER.invoke(
        app,
        [
            "search",
            raw_query,
            "--format",
            "hook-json",
            "--record-recall-gap",
            "--adapter",
            "codex",
        ],
    )

    assert result.exit_code == 0, result.output
    decoded = json.loads(result.output)
    assert decoded["status"] == "empty"
    assert decoded["context"] == ""
    gaps = list(iter_gap_records(tmp_brain))
    assert len(gaps) == 1
    assert gaps[0].query.startswith("sha256:")
    assert raw_query not in repr(gaps[0])
    assert gaps[0].injected_ids == ()
    assert gaps[0].rejected_ids == ()
    assert all(evidence.partition("=")[2].isdigit() for evidence in gaps[0].evidence)


def test_hook_json_admission_rejection_maps_gap_to_query_not_injectable(
    tmp_brain,
) -> None:
    from agent_brain.interfaces.cli import app
    from agent_brain.memory.governance.recall_events import iter_gap_records

    result = RUNNER.invoke(
        app,
        [
            "search",
            "ok",
            "--format",
            "hook-json",
            "--record-recall-gap",
            "--adapter",
            "codex",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "empty"
    assert payload["reason"] == "admission_rejected"
    assert payload["context"] == ""
    gaps = list(iter_gap_records(tmp_brain))
    assert len(gaps) == 1
    assert gaps[0].reason == "query_not_injectable"


def test_malformed_routed_result_returns_internal_error_without_details() -> None:
    from agent_brain.interfaces.cli import routed_query

    class BadTrace:
        route = "lexical_terms"
        status = "SECRET_BAD_STATUS"
        candidate_count = 1
        reason = "SECRET_BAD_REASON"

    class Retriever:
        def search_routed(self, request, **_kwargs):
            return RoutedSearchResult([], (), request.admission, {}).__class__(
                [],
                (BadTrace(),),  # type: ignore[arg-type]
                request.admission,
                {},
            )

    payload = routed_query.execute_routed_query(
        raw_query="malformed routed result",
        store=_Store([]),
        retriever=Retriever(),
        top_k=3,
        filters=SearchFilter(),
        requested="auto",
        project=None,
        adapter="codex",
        session_id=None,
        cwd=None,
    )

    assert payload.status == "error"
    assert payload.reason == "internal_error"
    assert payload.context == ""
    assert "SECRET" not in json.dumps(payload.to_dict())


def test_conflicting_routed_admission_is_malformed_and_fails_closed() -> None:
    from agent_brain.interfaces.cli import routed_query
    from agent_brain.memory.recall.admission import RecallAdmission

    class Retriever:
        def search_routed(self, _request, **_kwargs):
            return RoutedSearchResult(
                [],
                (),
                RecallAdmission(False, "weak_confirmation"),
                {},
            )

    payload = routed_query.execute_routed_query(
        raw_query="meaningful conflicting admission query",
        store=_Store([]),
        retriever=Retriever(),
        top_k=3,
        filters=SearchFilter(),
        requested="auto",
        project=None,
        adapter="codex",
        session_id=None,
        cwd=None,
    )

    assert payload.status == "error"
    assert payload.reason == "internal_error"
    assert payload.context == ""


@pytest.mark.parametrize(
    "semantic_deadline",
    [float("nan"), float("inf"), True, "not-a-deadline"],
)
def test_feature_off_still_rejects_non_finite_semantic_deadline(
    monkeypatch: pytest.MonkeyPatch,
    semantic_deadline: object,
) -> None:
    from agent_brain.interfaces.cli import routed_query

    class Retriever:
        def __init__(self) -> None:
            self.calls = 0

        def search(self, *_args, **_kwargs):
            self.calls += 1
            return []

    retriever = Retriever()
    monkeypatch.setenv("AGENT_MEMORY_HUB_ROUTED_RECALL", "0")

    payload = routed_query.execute_routed_query(
        raw_query="feature off deadline validation probe",
        store=_Store([]),
        retriever=retriever,
        top_k=3,
        filters=SearchFilter(),
        requested="auto",
        project=None,
        adapter="codex",
        session_id=None,
        cwd=None,
        clock=lambda: 0.0,
        semantic_deadline=semantic_deadline,  # type: ignore[arg-type]
    )

    assert payload.to_dict() == {
        "status": "error",
        "reason": "internal_error",
        "context": "",
        "routes": [],
    }
    assert retriever.calls == 0


@pytest.mark.parametrize("clock_value", [float("nan"), float("inf"), True, "now"])
def test_feature_off_still_rejects_non_finite_explicit_clock(
    monkeypatch: pytest.MonkeyPatch,
    clock_value: object,
) -> None:
    from agent_brain.interfaces.cli import routed_query

    class Retriever:
        calls = 0

        def search(self, *_args, **_kwargs):
            self.calls += 1
            return []

    retriever = Retriever()
    monkeypatch.setenv("AGENT_MEMORY_HUB_ROUTED_RECALL", "0")
    payload = routed_query.execute_routed_query(
        raw_query="feature off explicit clock validation probe",
        store=_Store([]),
        retriever=retriever,
        top_k=3,
        filters=SearchFilter(),
        requested="auto",
        project=None,
        adapter="codex",
        session_id=None,
        cwd=None,
        clock=lambda: clock_value,  # type: ignore[return-value]
        semantic_deadline=1.0,
    )

    assert payload.status == "error"
    assert payload.reason == "internal_error"
    assert payload.context == ""
    assert retriever.calls == 0
