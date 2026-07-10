"""Tests for the Web request-chain log read model."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Refs
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.store.write_service import WriteService


def _item(item_id: str = "mem-20260706-010203-chain-demo") -> MemoryItem:
    return MemoryItem(
        id=item_id,
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        agent="codex",
        session="sess-chain",
        project="agent-memory-hub",
        tags=["chain-log", "retrieval"],
        title="Chain log demo",
        summary="Used to verify request-chain reporting",
        refs=Refs(),
        confidence=0.84,
    )


def _write_item(brain_dir: Path, item_id: str = "mem-20260706-010203-chain-demo") -> str:
    item = _item(item_id)
    WriteService(ItemsStore(brain_dir / "items"), brain_dir=brain_dir).write(
        item=item,
        body="secret memory body should not leak into chain logs",
        allow_unsafe=True,
    )
    return item.id


def _collect_pack_metric_keys(payload: object) -> list[str]:
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            found.append(str(key))
            found.extend(_collect_pack_metric_keys(value))
    elif isinstance(payload, list):
        for value in payload:
            found.extend(_collect_pack_metric_keys(value))
    return found


def _algorithm_status(detail: dict[str, object], algorithm_id: str) -> str:
    algorithm_trace = detail["algorithm_trace"]
    assert isinstance(algorithm_trace, list)
    stage = next(row for row in algorithm_trace if row["algorithm_id"] == algorithm_id)
    return str(stage["status"])


def test_chain_candidate_loaded_view_requires_unambiguous_item_bound_pack_metric(
    tmp_path: Path,
) -> None:
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.product.chain_log import build_chain_log_detail, build_chain_log_report

    detail_id = _write_item(tmp_path, "mem-20260711-050601-loaded-detail")
    missing_id = _write_item(tmp_path, "mem-20260711-050602-loaded-missing")
    conflicting_id = _write_item(tmp_path, "mem-20260711-050603-loaded-conflicting")
    record_injection_cohort(
        tmp_path,
        item_ids=[detail_id, missing_id, conflicting_id],
        adapter="codex",
        session_id="sess-loaded-view",
        pack_metrics={
            "items": [
                {"id": detail_id, "selected_view": "detail"},
                {"id": conflicting_id, "selected_view": "overview"},
                {"id": conflicting_id, "selected_view": "locator"},
            ]
        },
    )

    report = build_chain_log_report(tmp_path, hours=72).to_dict()
    detail = build_chain_log_detail(tmp_path, report["chains"][0]["chain_id"]).to_dict()
    candidates = {row["item_id"]: row for row in detail["candidates"]}

    assert candidates[detail_id]["loaded_view"] == "detail"
    assert candidates[missing_id]["loaded_view"] is None
    assert candidates[conflicting_id]["loaded_view"] is None


def test_chain_log_groups_hook_injection_and_gap_by_session(tmp_path: Path) -> None:
    from agent_brain.agent_integrations.runtime_events import record_runtime_event
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.memory.governance.recall_events import record_gap
    from agent_brain.product.chain_log import build_chain_log_detail, build_chain_log_report

    item_id = _write_item(tmp_path)
    rejected_id = _write_item(tmp_path, "mem-20260706-010203-rejected")
    record_runtime_event(
        tmp_path,
        adapter="codex",
        event_name="UserPromptSubmit",
        session_id="sess-chain",
        cwd="/repo/agent-memory-hub",
    )
    record_injection_cohort(
        tmp_path,
        item_ids=[item_id],
        adapter="codex",
        session_id="sess-chain",
        cwd="/repo/agent-memory-hub",
        query="raw user query should not leak",
        source="search",
        pack_metrics={
            "context_pack_chars": 128,
            "detail_refs": 1,
            "candidate_count": 3,
            "included_count": 1,
            "excluded_count": 2,
            "excluded_reasons": {"missing_source": 2},
            "query_terms_count": 4,
        },
    )
    record_gap(
        tmp_path,
        query="another raw query should not leak",
        reason="partial_candidates_rejected",
        injected_ids=[item_id],
        rejected_ids=[rejected_id],
        evidence=[f"{rejected_id}:query_mismatch"],
        adapter="codex",
        session_id="sess-chain",
        cwd="/repo/agent-memory-hub",
    )

    report = build_chain_log_report(tmp_path, hours=72, limit=20).to_dict()

    assert report["summary"]["total_chains"] == 1
    chain = report["chains"][0]
    assert chain["adapter"] == "codex"
    assert chain["session_id"] == "sess-chain"
    assert chain["final_outcome"] == "partial"
    assert chain["injected_count"] == 1
    assert chain["rejected_count"] == 1
    assert chain["completeness"]["expected_stage_count"] == 9
    assert chain["completeness"]["observed_stage_count"] >= 4

    detail = build_chain_log_detail(tmp_path, chain["chain_id"]).to_dict()
    stage_ids = [stage["stage_id"] for stage in detail["stages"]]
    assert stage_ids == [
        "hook_capture",
        "prompt_frame",
        "query_gate",
        "retrieval",
        "context_firewall",
        "context_loading",
        "packing",
        "injection",
        "feedback",
    ]
    assert any(stage["status"] == "partial" for stage in detail["stages"])
    assert "raw user query should not leak" not in json.dumps(detail)
    assert "another raw query should not leak" not in json.dumps(detail)
    assert "secret memory body" not in json.dumps(detail)


def test_chain_log_candidate_trace_preserves_reject_reason_without_raw_query(tmp_path: Path) -> None:
    from agent_brain.memory.governance.recall_events import record_gap
    from agent_brain.product.chain_log import build_chain_log_detail, build_chain_log_report

    _write_item(tmp_path, "mem-20260706-010203-rejected-known")
    record_gap(
        tmp_path,
        query="raw rejected query should not leak",
        reason="all_candidates_rejected",
        rejected_ids=["mem-20260706-010203-rejected-known"],
        evidence=["mem-20260706-010203-rejected-known:answerability_mismatch"],
        adapter="codex",
        session_id="sess-reject",
        cwd="/repo",
    )

    report = build_chain_log_report(tmp_path, hours=72).to_dict()
    detail = build_chain_log_detail(tmp_path, report["chains"][0]["chain_id"]).to_dict()

    assert detail["final_outcome"] == "blocked"
    candidate = detail["candidates"][0]
    assert candidate["firewall_action"] == "exclude"
    assert "answerability_mismatch" in candidate["firewall_reasons"]
    assert "raw rejected query should not leak" not in json.dumps(detail)


def test_chain_log_binds_ordered_traces_before_filtering_all_raw_identity_sources(
    tmp_path: Path,
) -> None:
    from agent_brain.agent_integrations.runtime_events import record_runtime_event
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.memory.governance.recall_events import record_gap, record_task_outcome
    from agent_brain.product.chain_log import build_chain_log_detail, build_chain_log_report

    first_id = _write_item(tmp_path, "mem-20260711-010203-chain-safe-first")
    second_id = _write_item(tmp_path, "mem-20260711-010204-chain-safe-second")
    rejected_id = _write_item(tmp_path, "mem-20260711-010205-chain-safe-rejected")
    dirty_id = "mem-20260711-010206-chain-dirty-SECRET_ID"

    record_runtime_event(
        tmp_path,
        adapter="codex",
        event_name="UserPromptSubmit",
        session_id="sess-chain-identity",
        cwd="/repo",
    )
    record_injection_cohort(
        tmp_path,
        item_ids=[first_id, dirty_id, second_id],
        adapter="codex",
        session_id="sess-chain-identity",
        cwd="/repo",
        pack_metrics={
            "candidate_count": 2,
            "included_count": 2,
            "excluded_count": 0,
            "selected_views": {"overview": 2},
            "retrieval_trace": [
                {"final_rank": 11},
                {"final_rank": 999},
                {"final_rank": 33},
            ],
        },
    )
    record_gap(
        tmp_path,
        query="raw gap query",
        reason="SECRET_GAP_REASON",
        injected_ids=[first_id, dirty_id],
        rejected_ids=[rejected_id, dirty_id],
        evidence=[
            f"{rejected_id}:answerability_mismatch",
            f"{dirty_id}:answerability_mismatch",
            "SECRET_FREE_EVIDENCE:missing_source",
        ],
        adapter="codex",
        session_id="sess-chain-identity",
        cwd="/repo",
    )
    record_task_outcome(
        tmp_path,
        task_id="task-dirty-identity",
        question="raw outcome question",
        outcome="accepted",
        injected_ids=[first_id, dirty_id],
        adopted_ids=[first_id, dirty_id],
        rejected_ids=[dirty_id],
        adapter="SECRET_OUTCOME_ADAPTER",
        session_id="sess-outcome-identity",
        cwd="/repo",
    )

    report = build_chain_log_report(tmp_path, hours=72, limit=20).to_dict()
    injection_summary = next(
        row for row in report["chains"] if row["session_id"] == "sess-chain-identity"
    )
    outcome_summary = next(
        row for row in report["chains"] if row["session_id"] == "sess-outcome-identity"
    )
    detail = build_chain_log_detail(tmp_path, injection_summary["chain_id"]).to_dict()
    outcome_detail = build_chain_log_detail(tmp_path, outcome_summary["chain_id"]).to_dict()
    packing = next(stage for stage in detail["stages"] if stage["stage_id"] == "packing")
    candidates = {row["item_id"]: row for row in detail["candidates"]}
    serialized = json.dumps({"report": report, "detail": detail, "outcome": outcome_detail})

    assert injection_summary["injected_count"] == 2
    assert injection_summary["rejected_count"] == 1
    assert detail["final_outcome"] == "partial"
    assert set(candidates) == {first_id, second_id, rejected_id}
    assert candidates[first_id]["score_trace"] == {"final_rank": 11}
    assert candidates[second_id]["score_trace"] == {"final_rank": 33}
    assert "answerability_mismatch" in candidates[rejected_id]["firewall_reasons"]
    assert packing["preview"]["pack_metrics"] == [{
        "candidate_count": 2,
        "excluded_count": 0,
        "included_count": 2,
        "retrieval_trace": [{"final_rank": 11}, {"final_rank": 33}],
        "selected_views": {"overview": 2},
    }]
    assert next(
        stage for stage in detail["stages"] if stage["stage_id"] == "query_gate"
    )["preview"]["gap_reason"] == "unclassified"
    assert outcome_detail["adapter"] == "unknown"
    assert "SECRET" not in serialized


def test_chain_log_keeps_user_prompt_requests_from_same_session_as_separate_chains(
    tmp_path: Path,
) -> None:
    from agent_brain.agent_integrations.runtime_events import record_runtime_event
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.product.chain_log import build_chain_log_report

    base_time = datetime.now(timezone.utc).replace(microsecond=0)
    first_submit = base_time - timedelta(minutes=14)
    second_submit = base_time - timedelta(minutes=7)

    first_item = _write_item(tmp_path, item_id="mem-20260706-010203-first")
    second_item = _write_item(tmp_path, item_id="mem-20260706-010204-second")

    record_runtime_event(
        tmp_path,
        adapter="codex",
        event_name="UserPromptSubmit",
        session_id="sess-chain",
        cwd="/repo/agent-memory-hub",
        now=first_submit,
    )
    record_injection_cohort(
        tmp_path,
        item_ids=[first_item],
        adapter="codex",
        session_id="sess-chain",
        cwd="/repo/agent-memory-hub",
        query="first query",
        now=first_submit + timedelta(seconds=30),
        source="search",
    )

    record_runtime_event(
        tmp_path,
        adapter="codex",
        event_name="UserPromptSubmit",
        session_id="sess-chain",
        cwd="/repo/agent-memory-hub",
        now=second_submit,
    )
    record_injection_cohort(
        tmp_path,
        item_ids=[second_item],
        adapter="codex",
        session_id="sess-chain",
        cwd="/repo/agent-memory-hub",
        query="second query",
        now=second_submit + timedelta(seconds=40),
        source="search",
    )

    report = build_chain_log_report(tmp_path, hours=72, limit=20).to_dict()

    assert report["summary"]["total_chains"] == 2
    assert len({chain["chain_id"] for chain in report["chains"]}) == 2
    assert all(chain["session_id"] == "sess-chain" for chain in report["chains"])


def test_chain_log_excludes_future_dated_rows_from_72h_report(tmp_path: Path) -> None:
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.product.chain_log import build_chain_log_report

    now = datetime.now(timezone.utc).replace(microsecond=0)
    _write_item(tmp_path)
    record_injection_cohort(
        tmp_path,
        item_ids=["mem-20260706-010203-chain-demo"],
        adapter="codex",
        session_id="sess-chain",
        cwd="/repo/agent-memory-hub",
        query="current query",
        now=now,
    )
    record_injection_cohort(
        tmp_path,
        item_ids=["mem-20260706-010204-chain-demo"],
        adapter="codex",
        session_id="sess-chain",
        cwd="/repo/agent-memory-hub",
        query="future query",
        now=now + timedelta(hours=1),
    )

    report = build_chain_log_report(tmp_path, hours=72, limit=20).to_dict()

    assert report["summary"]["total_chains"] == 1
    chain = report["chains"][0]
    assert chain["session_id"] == "sess-chain"
    assert chain["final_outcome"] == "injected"


def test_chain_log_bounds_invalid_hours_and_limit_inputs(tmp_path: Path) -> None:
    from agent_brain.product.chain_log import build_chain_log_report

    report = build_chain_log_report(tmp_path, hours="bad", limit="bad").to_dict()

    assert report["filters"]["hours"] == 72
    assert report["filters"]["limit"] == 100
    assert report["summary"]["total_chains"] == 0


def test_chain_log_pack_metrics_are_allowlisted_not_alias_blacklisted(tmp_path: Path) -> None:
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.product.chain_log import build_chain_log_detail, build_chain_log_report

    item_id = _write_item(tmp_path)
    record_injection_cohort(
        tmp_path,
        item_ids=[item_id],
        adapter="codex",
        session_id="sess-chain",
        cwd="/repo",
        query="pack metrics query",
        pack_metrics={
            "packed_tokens": 12,
            "full_tokens": 30,
            "raw_prompt": "SECRET_RAW_PROMPT",
            "prompt_text": "SECRET_PROMPT_TEXT",
            "raw_query": "SECRET_RAW_QUERY",
            "query_text": "SECRET_QUERY_TEXT",
            "question_text": "SECRET_QUESTION_TEXT",
            "request_body": "SECRET_REQUEST_BODY",
            "tool_args_json": {"command": "SECRET_TOOL_ARGS"},
            "nested": {
                "tool_args": {"name": "rm"},
                "tool_args_raw": {"name": "rm"},
                "tool_arguments": {"name": "rm"},
                "arguments": {"name": "rm"},
                "args_raw": {"name": "rm"},
            }
        },
    )

    report = build_chain_log_report(tmp_path, hours=72, limit=20).to_dict()
    detail = build_chain_log_detail(tmp_path, report["chains"][0]["chain_id"]).to_dict()
    packing = next(stage for stage in detail["stages"] if stage["stage_id"] == "packing")
    serialized = json.dumps(detail)
    metric_keys = _collect_pack_metric_keys(packing["preview"]["pack_metrics"])
    assert "SECRET_" not in serialized
    assert "raw_prompt" not in metric_keys
    assert "prompt_text" not in metric_keys
    assert "raw_query" not in metric_keys
    assert "query_text" not in metric_keys
    assert "question_text" not in metric_keys
    assert "request_body" not in metric_keys
    assert "tool_args" not in metric_keys
    assert "tool_args_raw" not in metric_keys
    assert "tool_arguments" not in metric_keys
    assert "arguments" not in metric_keys
    assert "args_raw" not in metric_keys
    assert "tool_args_json" not in metric_keys
    assert packing["preview"]["pack_metrics"] == [{"packed_tokens": 12, "full_tokens": 30}]


def test_chain_log_preserves_aggregate_gateway_metrics_without_content(
    tmp_path: Path,
) -> None:
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.product.chain_log import build_chain_log_detail, build_chain_log_report

    item_id = _write_item(tmp_path)
    record_injection_cohort(
        tmp_path,
        item_ids=[item_id],
        adapter="codex",
        session_id="sess-aggregate-pack",
        cwd="/repo",
        pack_metrics={
            "candidate_count": 4,
            "raw_candidate_count": 4,
            "gateway_candidate_count": 3,
            "hydrate_error_count": 1,
            "included_count": 1,
            "excluded_count": 3,
            "excluded_reasons": {
                "missing_source": 2,
                "hydrate_error": 1,
            },
            "selected_views": {"locator": 1},
            "compressed_count": 0,
            "packed_tokens": 12,
            "full_tokens": 30,
            "title": "SECRET_PRIVATE_TITLE",
            "body": "SECRET_PRIVATE_BODY",
            "query": "SECRET_PRIVATE_QUERY",
        },
    )

    report = build_chain_log_report(tmp_path, hours=72, limit=20).to_dict()
    detail = build_chain_log_detail(tmp_path, report["chains"][0]["chain_id"]).to_dict()
    packing = next(stage for stage in detail["stages"] if stage["stage_id"] == "packing")
    metrics = packing["preview"]["pack_metrics"]

    assert metrics == [
        {
            "candidate_count": 4,
            "compressed_count": 0,
            "excluded_count": 3,
            "excluded_reasons": {"hydrate_error": 1, "missing_source": 2},
            "full_tokens": 30,
            "gateway_candidate_count": 3,
            "hydrate_error_count": 1,
            "included_count": 1,
            "packed_tokens": 12,
            "raw_candidate_count": 4,
            "selected_views": {"locator": 1},
        }
    ]
    serialized = json.dumps(detail)
    assert "SECRET_PRIVATE" not in serialized
    assert "title" not in _collect_pack_metric_keys(metrics)
    assert "body" not in _collect_pack_metric_keys(metrics)
    assert "query" not in _collect_pack_metric_keys(metrics)


def test_chain_log_preserves_valid_bare_gateway_aggregate_bundle(
    tmp_path: Path,
) -> None:
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.product.chain_log import build_chain_log_detail, build_chain_log_report

    item_id = _write_item(tmp_path)
    record_injection_cohort(
        tmp_path,
        item_ids=[item_id],
        adapter="codex",
        session_id="sess-bare-aggregate",
        cwd="/repo",
        pack_metrics={
            "candidate_count": 3,
            "included_count": 1,
            "excluded_count": 2,
            "excluded_reasons": {"missing_source": 2, "scope_mismatch": 1},
            "selected_views": {"overview": 1},
            "compressed_count": 1,
            "packed_tokens": 7,
            "full_tokens": 21,
        },
    )

    report = build_chain_log_report(tmp_path, hours=72).to_dict()
    detail = build_chain_log_detail(tmp_path, report["chains"][0]["chain_id"]).to_dict()
    packing = next(stage for stage in detail["stages"] if stage["stage_id"] == "packing")

    assert packing["preview"]["pack_metrics"] == [
        {
            "candidate_count": 3,
            "compressed_count": 1,
            "excluded_count": 2,
            "excluded_reasons": {"missing_source": 2, "scope_mismatch": 1},
            "full_tokens": 21,
            "included_count": 1,
            "packed_tokens": 7,
            "selected_views": {"overview": 1},
        }
    ]


def test_chain_log_preserves_surface_bundle_without_hydrate_reason_at_zero(
    tmp_path: Path,
) -> None:
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.product.chain_log import build_chain_log_detail, build_chain_log_report

    item_id = _write_item(tmp_path)
    record_injection_cohort(
        tmp_path,
        item_ids=[item_id],
        adapter="codex",
        session_id="sess-surface-zero-hydrate",
        cwd="/repo",
        pack_metrics={
            "candidate_count": 2,
            "included_count": 1,
            "excluded_count": 1,
            "excluded_reasons": {"missing_source": 1},
            "raw_candidate_count": 2,
            "gateway_candidate_count": 2,
            "hydrate_error_count": 0,
            "packed_tokens": 7,
        },
    )

    report = build_chain_log_report(tmp_path, hours=72).to_dict()
    detail = build_chain_log_detail(tmp_path, report["chains"][0]["chain_id"]).to_dict()
    packing = next(stage for stage in detail["stages"] if stage["stage_id"] == "packing")

    assert packing["preview"]["pack_metrics"] == [{
        "candidate_count": 2,
        "excluded_count": 1,
        "excluded_reasons": {"missing_source": 1},
        "gateway_candidate_count": 2,
        "hydrate_error_count": 0,
        "included_count": 1,
        "packed_tokens": 7,
        "raw_candidate_count": 2,
    }]


@pytest.mark.parametrize(
    "aggregate_metrics",
    [
        {
            "candidate_count": 2,
            "included_count": 1,
            "excluded_count": 1,
            "selected_views": {"locator": 2},
            "compressed_count": 0,
        },
        {
            "candidate_count": 1,
            "included_count": 1,
            "excluded_count": 0,
            "selected_views": {"locator": 1},
            "compressed_count": 2,
        },
        {
            "candidate_count": 3,
            "included_count": 1,
            "excluded_count": 1,
        },
        {
            "candidate_count": 2,
            "included_count": 1,
        },
        {
            "candidate_count": 2,
            "included_count": 1,
            "excluded_count": 1,
            "raw_candidate_count": 2,
            "gateway_candidate_count": 1,
        },
        {
            "candidate_count": 3,
            "included_count": 1,
            "excluded_count": 2,
            "raw_candidate_count": 3,
            "gateway_candidate_count": 1,
            "hydrate_error_count": 1,
        },
        {
            "candidate_count": 3,
            "included_count": 1,
            "excluded_count": 1,
            "raw_candidate_count": 3,
            "gateway_candidate_count": 2,
            "hydrate_error_count": 1,
        },
        {
            "candidate_count": 2,
            "included_count": 1,
            "excluded_count": 1,
            "raw_candidate_count": 2,
            "gateway_candidate_count": 1,
            "hydrate_error_count": 1,
            "excluded_reasons": {"hydrate_error": 0},
        },
        {
            "candidate_count": True,
            "included_count": 1,
            "excluded_count": 0,
        },
        {
            "candidate_count": 1,
            "included_count": 1,
            "excluded_count": 0,
            "selected_views": {"overview": 1, "secret_view": 0},
        },
        {
            "candidate_count": 2,
            "included_count": 1,
            "excluded_count": 1,
            "raw_candidate_count": 2,
            "gateway_candidate_count": 1,
            "hydrate_error_count": True,
        },
        {
            "candidate_count": 1,
            "included_count": 0,
            "excluded_count": 1,
            "excluded_reasons": {"missing_source": 999},
        },
        {
            "candidate_count": 3,
            "included_count": 1,
            "excluded_count": 2,
            "raw_candidate_count": 3,
            "gateway_candidate_count": 2,
            "hydrate_error_count": 1,
            "excluded_reasons": {"hydrate_error": 1, "missing_source": 999},
        },
        {
            "candidate_count": 1,
            "included_count": 0,
            "excluded_count": 1,
            "excluded_reasons": {"hydrate_error": 1},
        },
        {
            "candidate_count": 2,
            "included_count": 1,
            "excluded_count": 1,
            "raw_candidate_count": 2,
            "gateway_candidate_count": 1,
            "hydrate_error_count": 1,
        },
        {
            "candidate_count": 2,
            "included_count": 1,
            "excluded_count": 1,
            "raw_candidate_count": 2,
            "gateway_candidate_count": 2,
            "hydrate_error_count": 0,
            "excluded_reasons": {"hydrate_error": 0, "missing_source": 1},
        },
        {
            "candidate_count": 1,
            "included_count": 0,
            "excluded_count": 1,
            "excluded_reasons": {"hydrate_error": 0},
        },
        {
            "candidate_count": 2,
            "included_count": 2,
            "excluded_count": 0,
            "raw_candidate_count": 2,
            "gateway_candidate_count": 0,
            "hydrate_error_count": 2,
            "excluded_reasons": {"hydrate_error": 2},
        },
        {
            "candidate_count": 2,
            "included_count": 0,
            "excluded_count": 2,
            "excluded_reasons": {"missing_source": 1},
        },
        {
            "candidate_count": 1,
            "included_count": 0,
            "excluded_count": 1,
            "excluded_reasons": {},
        },
        {
            "candidate_count": 4,
            "included_count": 1,
            "excluded_count": 3,
            "raw_candidate_count": 4,
            "gateway_candidate_count": 3,
            "hydrate_error_count": 1,
            "excluded_reasons": {"hydrate_error": 1, "missing_source": 1},
        },
        {
            "candidate_count": 1,
            "included_count": 1,
            "excluded_count": 0,
            "excluded_reasons": {"missing_source": 0},
        },
    ],
    ids=[
        "selected-views-sum",
        "compressed-over-included",
        "bare-equation",
        "bare-missing-field",
        "surface-missing-field",
        "surface-gateway-equation",
        "surface-total-equation",
        "surface-hydrate-reason",
        "malformed-base-count",
        "malformed-selected-views",
        "malformed-surface-count",
        "bare-reason-over-excluded",
        "surface-reason-over-gateway-excluded",
        "bare-hydrate-reason",
        "surface-hydrate-reason-missing",
        "surface-zero-hydrate-reason-present",
        "bare-zero-hydrate-reason-present",
        "surface-negative-gateway-excluded",
        "bare-reason-sum-under-partition",
        "bare-reasons-empty-for-partition",
        "surface-reason-sum-under-partition",
        "zero-partition-with-nonhydrate-reason",
    ],
)
def test_chain_log_drops_entire_inconsistent_aggregate_bundle(
    tmp_path: Path,
    aggregate_metrics: dict[str, object],
) -> None:
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.product.chain_log import build_chain_log_detail, build_chain_log_report

    item_id = _write_item(tmp_path)
    record_injection_cohort(
        tmp_path,
        item_ids=[item_id],
        adapter="codex",
        session_id="sess-inconsistent-aggregate",
        cwd="/repo",
        pack_metrics={
            **aggregate_metrics,
            "packed_tokens": 7,
            "full_tokens": 21,
            "retrieval_trace": [
                {
                    "initial_score": 0.4,
                    "final_rank": 1,
                    "final_score": 0.5,
                }
            ],
        },
    )

    report = build_chain_log_report(tmp_path, hours=72).to_dict()
    detail = build_chain_log_detail(tmp_path, report["chains"][0]["chain_id"]).to_dict()
    packing = next(stage for stage in detail["stages"] if stage["stage_id"] == "packing")
    metrics = packing["preview"]["pack_metrics"][0]
    aggregate_keys = {
        "candidate_count",
        "compressed_count",
        "excluded_count",
        "excluded_reasons",
        "gateway_candidate_count",
        "hydrate_error_count",
        "included_count",
        "raw_candidate_count",
        "selected_views",
    }

    assert aggregate_keys.isdisjoint(metrics)
    assert metrics == {
        "full_tokens": 21,
        "packed_tokens": 7,
        "retrieval_trace": [
            {
                "initial_score": 0.4,
                "final_rank": 1,
                "final_score": 0.5,
            }
        ],
    }


def test_chain_log_field_schema_rejects_sentinels_and_unbound_ids(
    tmp_path: Path,
) -> None:
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.product.chain_log import build_chain_log_detail, build_chain_log_report

    item_id = _write_item(tmp_path)
    record_injection_cohort(
        tmp_path,
        item_ids=[item_id],
        adapter="codex",
        session_id="sess-field-schema",
        cwd="/repo",
        pack_metrics={
            "candidate_count": 1,
            "included_count": 1,
            "excluded_count": 0,
            "context_pack_chars": "SECRET_CONTEXT_CHARS",
            "detail_refs": True,
            "packed_tokens": "SECRET_PACKED_TOKENS",
            "full_tokens": -1,
            "query_terms_count": 1.5,
            "items": [
                {
                    "id": item_id,
                    "selected_view": "SECRET_SELECTED_VIEW",
                    "packed_tokens": "SECRET_ITEM_PACKED_TOKENS",
                    "full_tokens": True,
                    "compressed": "SECRET_COMPRESSED",
                },
                {
                    "id": "SECRET_LEGACY_ITEM_ID",
                    "selected_view": "locator",
                    "packed_tokens": 1,
                    "full_tokens": 2,
                    "compressed": False,
                },
            ],
            "trimmed_ids": ["SECRET_TRIMMED_ID"],
            "retrieval_trace": {
                item_id: {
                    "initial_bm25_rank": "SECRET_BM25_RANK",
                    "initial_vector_rank": True,
                    "initial_score": "SECRET_INITIAL_SCORE",
                    "final_rank": -1,
                    "final_score": "SECRET_FINAL_SCORE",
                    "signals": [
                        "bm25",
                        "feedback_value:rescored",
                        "SECRET_SIGNAL",
                        "secret_stage:kept",
                        "feedback_value:secret_effect",
                    ],
                    "stages": [
                        {
                            "name": "feedback_value",
                            "effect": "rescored",
                            "before_rank": "SECRET_BEFORE_RANK",
                            "after_rank": True,
                            "before_score": "SECRET_BEFORE_SCORE",
                            "after_score": "SECRET_AFTER_SCORE",
                        },
                        {
                            "name": "SECRET_STAGE_NAME",
                            "effect": "SECRET_STAGE_EFFECT",
                        },
                    ],
                },
                "SECRET_TRACE_KEY": {
                    "initial_score": 0.4,
                    "final_rank": 1,
                    "final_score": 0.5,
                },
            },
        },
    )

    report = build_chain_log_report(tmp_path, hours=72).to_dict()
    detail = build_chain_log_detail(tmp_path, report["chains"][0]["chain_id"]).to_dict()
    packing = next(stage for stage in detail["stages"] if stage["stage_id"] == "packing")
    metrics = packing["preview"]["pack_metrics"][0]

    assert "SECRET" not in json.dumps(detail)
    assert metrics == {
        "candidate_count": 1,
        "excluded_count": 0,
        "included_count": 1,
        "items": [{"id": item_id}],
        "retrieval_trace": {
            item_id: {
                "signals": ["bm25", "feedback_value:rescored"],
                "stages": [{"name": "feedback_value", "effect": "rescored"}],
            }
        },
        "trimmed_count": 1,
    }
    assert _algorithm_status(detail, "budget_trim") == "applied"


def test_chain_log_preserves_valid_legacy_item_metrics_for_cohort_ids_only(
    tmp_path: Path,
) -> None:
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.product.chain_log import build_chain_log_detail, build_chain_log_report

    item_id = _write_item(tmp_path)
    record_injection_cohort(
        tmp_path,
        item_ids=[item_id],
        adapter="codex",
        session_id="sess-legacy-item-schema",
        cwd="/repo",
        pack_metrics={
            "packed_tokens": 7,
            "full_tokens": 21,
            "items": [
                {
                    "id": item_id,
                    "selected_view": "overview",
                    "packed_tokens": 7,
                    "full_tokens": 21,
                    "compressed": True,
                },
                {
                    "id": "mem-20260706-010299-not-in-cohort",
                    "selected_view": "locator",
                    "packed_tokens": 1,
                    "full_tokens": 2,
                    "compressed": False,
                },
            ],
        },
    )

    report = build_chain_log_report(tmp_path, hours=72).to_dict()
    detail = build_chain_log_detail(tmp_path, report["chains"][0]["chain_id"]).to_dict()
    packing = next(stage for stage in detail["stages"] if stage["stage_id"] == "packing")

    assert packing["preview"]["pack_metrics"] == [{
        "full_tokens": 21,
        "items": [{
            "compressed": True,
            "full_tokens": 21,
            "id": item_id,
            "packed_tokens": 7,
            "selected_view": "overview",
        }],
        "packed_tokens": 7,
    }]


def test_chain_log_accepts_retrieval_trace_like_pack_metrics(tmp_path: Path) -> None:
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.product.chain_log import build_chain_log_detail, build_chain_log_report

    item_id = _write_item(tmp_path)
    record_injection_cohort(
        tmp_path,
        item_ids=[item_id],
        adapter="codex",
        session_id="sess-trace",
        cwd="/repo",
        query="trace query should not leak",
        pack_metrics={
            "retrieval_trace": {
                item_id: {
                    "initial_bm25_rank": 2,
                    "initial_vector_rank": 1,
                    "initial_score": 0.42,
                    "final_rank": 1,
                    "final_score": 0.61,
                    "retrieval_query": "SECRET_RAW_QUERY",
                    "stages": [
                        {
                            "name": "decay",
                            "before_rank": 1,
                            "after_rank": 1,
                            "before_score": 0.7,
                            "after_score": 0.61,
                            "effect": "rescored",
                        },
                        {
                            "name": "mmr",
                            "before_rank": 1,
                            "after_rank": 1,
                            "before_score": 0.61,
                            "after_score": 0.61,
                            "effect": "kept",
                        },
                        {
                            "name": "feedback_value",
                            "before_rank": 1,
                            "after_rank": 1,
                            "before_score": 0.61,
                            "after_score": 0.71,
                            "effect": "rescored",
                        },
                        {
                            "name": "hopfield_expand",
                            "effect": "added",
                        },
                        {
                            "name": "graph_expand",
                            "effect": "added",
                        },
                    ],
                }
            }
        },
    )

    report = build_chain_log_report(tmp_path, hours=72).to_dict()
    detail = build_chain_log_detail(tmp_path, report["chains"][0]["chain_id"]).to_dict()
    candidate = detail["candidates"][0]
    assert candidate["score_trace"]["initial_bm25_rank"] == 2
    assert candidate["score_trace"]["final_score"] == 0.61
    assert "retrieval_query" not in candidate["score_trace"]
    assert "SECRET_RAW_QUERY" not in json.dumps(detail)
    assert _algorithm_status(detail, "feedback_value") == "applied"
    assert _algorithm_status(detail, "retention") == "applied"
    assert _algorithm_status(detail, "decay_coefficient") == "applied"
    assert _algorithm_status(detail, "mmr") == "no_change"
    assert _algorithm_status(detail, "hopfield") == "applied"
    assert _algorithm_status(detail, "graph_expansion") == "applied"


def test_chain_log_accepts_id_free_ordered_retrieval_trace_metrics(tmp_path: Path) -> None:
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.product.chain_log import build_chain_log_detail, build_chain_log_report

    item_id = _write_item(tmp_path)
    record_injection_cohort(
        tmp_path,
        item_ids=[item_id],
        adapter="codex",
        session_id="sess-id-free-trace",
        cwd="/repo",
        pack_metrics={
            "retrieval_trace": [
                {
                    "initial_bm25_rank": 3,
                    "initial_score": 0.31,
                    "final_rank": 1,
                    "final_score": 0.54,
                    "stages": [
                        {
                            "name": "feedback_value",
                            "before_score": 0.31,
                            "after_score": 0.54,
                            "effect": "rescored",
                        }
                    ],
                }
            ]
        },
    )

    report = build_chain_log_report(tmp_path, hours=72).to_dict()
    detail = build_chain_log_detail(tmp_path, report["chains"][0]["chain_id"]).to_dict()

    candidate = detail["candidates"][0]
    assert candidate["score_trace"]["initial_bm25_rank"] == 3
    assert candidate["score_trace"]["final_score"] == 0.54
    assert _algorithm_status(detail, "feedback_value") == "applied"


def test_chain_log_drops_cohort_rows_with_integers_beyond_javascript_safe_range(
    tmp_path: Path,
) -> None:
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.product.chain_log import build_chain_log_detail, build_chain_log_report

    item_id = _write_item(tmp_path)
    oversized_score = 10**400
    record_injection_cohort(
        tmp_path,
        item_ids=[item_id],
        adapter="codex",
        session_id="sess-large-finite-score",
        cwd="/repo",
        pack_metrics={
            "retrieval_trace": [{
                "initial_score": oversized_score,
                "final_rank": 1,
                "final_score": oversized_score,
            }]
        },
    )

    report = build_chain_log_report(tmp_path, hours=72).to_dict()
    assert report["chains"] == []
    assert str(oversized_score) not in json.dumps(report)


def test_chain_log_metrics_are_json_safe_and_bounded_for_web_consumers(
    tmp_path: Path,
) -> None:
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.product.chain_log import build_chain_log_detail, build_chain_log_report

    item_id = _write_item(tmp_path)
    oversized = 2**53
    record_injection_cohort(
        tmp_path,
        item_ids=[item_id],
        adapter="codex",
        session_id="sess-js-safe-metrics",
        cwd="/repo",
        pack_metrics={
            "candidate_count": oversized + 1,
            "included_count": 1,
            "excluded_count": oversized,
            "excluded_reasons": {"missing_source": oversized},
            "packed_tokens": oversized,
            "retrieval_trace": [
                {
                    "initial_bm25_rank": oversized,
                    "initial_score": oversized,
                    "final_rank": 1,
                    "final_score": 0.5,
                    "stages": [
                        {
                            "name": "feedback_value",
                            "effect": "rescored",
                            "before_rank": oversized,
                            "before_score": float(oversized),
                            "after_rank": 1,
                            "after_score": 0.5,
                        }
                    ],
                }
            ],
        },
    )

    report = build_chain_log_report(tmp_path, hours=72).to_dict()
    serialized = json.dumps(report, allow_nan=False)

    assert report["chains"] == []
    assert str(oversized) not in serialized


def test_chain_log_does_not_bind_mismatched_id_free_trace_rows(tmp_path: Path) -> None:
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.product.chain_log import build_chain_log_detail, build_chain_log_report

    first_id = _write_item(tmp_path, "mem-20260706-010203-chain-first")
    second_id = _write_item(tmp_path, "mem-20260706-010204-chain-second")
    record_injection_cohort(
        tmp_path,
        item_ids=[first_id, second_id],
        adapter="codex",
        session_id="sess-id-free-trace-mismatch",
        cwd="/repo",
        pack_metrics={
            "retrieval_trace": [
                {
                    "initial_score": 0.31,
                    "final_score": 0.54,
                    "stages": [
                        {"name": "feedback_value", "effect": "rescored"}
                    ],
                }
            ]
        },
    )

    report = build_chain_log_report(tmp_path, hours=72).to_dict()
    detail = build_chain_log_detail(tmp_path, report["chains"][0]["chain_id"]).to_dict()

    assert all(candidate["score_trace"] == {} for candidate in detail["candidates"])
    assert _algorithm_status(detail, "feedback_value") == "not_observed"


def test_chain_log_does_not_observe_mmr_from_free_text_pack_metrics(tmp_path: Path) -> None:
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.product.chain_log import build_chain_log_detail, build_chain_log_report

    item_id = _write_item(tmp_path)
    record_injection_cohort(
        tmp_path,
        item_ids=[item_id],
        adapter="codex",
        session_id="sess-no-mmr",
        cwd="/repo",
        query="trace query should not leak",
        pack_metrics={
            "retrieval_trace": {
                item_id: {
                    "initial_score": 0.42,
                    "final_score": 0.61,
                    "note": "mmr not run",
                    "stages": [
                        {
                            "name": "feedback_value",
                            "before_score": 0.42,
                            "after_score": 0.61,
                            "effect": "rescored",
                        }
                    ],
                }
            }
        },
    )

    report = build_chain_log_report(tmp_path, hours=72).to_dict()
    detail = build_chain_log_detail(tmp_path, report["chains"][0]["chain_id"]).to_dict()

    assert _algorithm_status(detail, "feedback_value") == "applied"
    assert _algorithm_status(detail, "mmr") == "not_observed"


def test_chain_log_does_not_observe_mmr_from_non_run_structured_effects(
    tmp_path: Path,
) -> None:
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.product.chain_log import build_chain_log_detail, build_chain_log_report

    item_id = _write_item(tmp_path)
    record_injection_cohort(
        tmp_path,
        item_ids=[item_id],
        adapter="codex",
        session_id="sess-structured-no-mmr",
        cwd="/repo",
        query="trace query should not leak",
        pack_metrics={
            "retrieval_trace": {
                item_id: {
                    "stages": [
                        {"name": "mmr"},
                        {"name": "mmr", "effect": "not run"},
                    ]
                }
            }
        },
    )

    report = build_chain_log_report(tmp_path, hours=72).to_dict()
    detail = build_chain_log_detail(tmp_path, report["chains"][0]["chain_id"]).to_dict()

    assert _algorithm_status(detail, "mmr") == "not_observed"


def test_chain_log_drops_malformed_top_level_retrieval_trace_from_pack_preview(
    tmp_path: Path,
) -> None:
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.product.chain_log import build_chain_log_detail, build_chain_log_report

    item_id = _write_item(tmp_path)
    record_injection_cohort(
        tmp_path,
        item_ids=[item_id],
        adapter="codex",
        session_id="sess-top-level-trace",
        cwd="/repo",
        query="trace query should not leak",
        pack_metrics={
            "safe_value": 1,
            "retrieval_trace": "SECRET_TOP_LEVEL_TRACE",
        },
    )

    report = build_chain_log_report(tmp_path, hours=72).to_dict()
    detail = build_chain_log_detail(tmp_path, report["chains"][0]["chain_id"]).to_dict()
    packing = next(stage for stage in detail["stages"] if stage["stage_id"] == "packing")

    assert "SECRET_TOP_LEVEL_TRACE" not in json.dumps(detail)
    assert packing["preview"]["pack_metrics"] == [{}]


def test_chain_log_tolerates_malformed_retrieval_trace_without_leaking_unknown_fields(
    tmp_path: Path,
) -> None:
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.product.chain_log import build_chain_log_detail, build_chain_log_report

    valid_item_id = _write_item(tmp_path)
    non_dict_item_id = _write_item(tmp_path, "mem-20260706-010204-non-dict")
    non_list_stages_item_id = _write_item(
        tmp_path, "mem-20260706-010205-non-list-stages"
    )
    non_dict_stage_item_id = _write_item(
        tmp_path, "mem-20260706-010206-non-dict-stage"
    )
    record_injection_cohort(
        tmp_path,
        item_ids=[
            valid_item_id,
            non_dict_item_id,
            non_list_stages_item_id,
            non_dict_stage_item_id,
        ],
        adapter="codex",
        session_id="sess-malformed-trace",
        cwd="/repo",
        query="trace query should not leak",
        pack_metrics={
            "retrieval_trace": {
                valid_item_id: {
                    "initial_score": 0.42,
                    "signals": [
                        "bm25",
                        {"unknown_key": "SECRET_SIGNAL"},
                        "feedback_value:rescored",
                        7,
                    ],
                    "retrieval_query": "SECRET_VALID_QUERY",
                    "stages": [
                        {
                            "name": "feedback_value",
                            "effect": "rescored",
                            "retrieval_query": "SECRET_STAGE_QUERY",
                        }
                    ],
                },
                non_dict_item_id: "SECRET_NON_DICT_TRACE",
                non_list_stages_item_id: {
                    "initial_score": 0.33,
                    "retrieval_query": "SECRET_NON_LIST_QUERY",
                    "stages": "SECRET_NON_LIST_STAGES",
                },
                non_dict_stage_item_id: {
                    "initial_score": 0.22,
                    "extra": "SECRET_EXTRA_FIELD",
                    "stages": [
                        "SECRET_STAGE_ROW",
                        {
                            "name": "mmr",
                            "effect": "kept",
                            "retrieval_query": "SECRET_STAGE_QUERY",
                        },
                    ],
                },
            }
        },
    )

    report = build_chain_log_report(tmp_path, hours=72).to_dict()
    detail = build_chain_log_detail(tmp_path, report["chains"][0]["chain_id"]).to_dict()
    serialized = json.dumps(detail)

    assert "SECRET_" not in serialized
    candidates_by_id = {candidate["item_id"]: candidate for candidate in detail["candidates"]}
    assert candidates_by_id[valid_item_id]["score_trace"] == {
        "initial_score": 0.42,
        "signals": ["bm25", "feedback_value:rescored"],
        "stages": [{"name": "feedback_value", "effect": "rescored"}],
    }
    assert candidates_by_id[non_dict_item_id]["score_trace"] == {}
    assert candidates_by_id[non_list_stages_item_id]["score_trace"] == {"initial_score": 0.33}
    assert candidates_by_id[non_dict_stage_item_id]["score_trace"] == {
        "initial_score": 0.22,
        "stages": [{"name": "mmr", "effect": "kept"}],
    }


def test_chain_log_keeps_algorithm_nodes_visible_when_not_observed(tmp_path: Path) -> None:
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.product.chain_log import build_chain_log_detail, build_chain_log_report

    item_id = _write_item(tmp_path)
    record_injection_cohort(
        tmp_path,
        item_ids=[item_id],
        adapter="codex",
        session_id="sess-algo",
        cwd="/repo",
        query="algorithm trace query should not leak",
        pack_metrics={"context_pack_chars": 90},
    )

    report = build_chain_log_report(tmp_path, hours=72, limit=20).to_dict()
    detail = build_chain_log_detail(tmp_path, report["chains"][0]["chain_id"]).to_dict()

    algorithm_ids = [stage["algorithm_id"] for stage in detail["algorithm_trace"]]
    assert algorithm_ids == [
        "metadata_filter",
        "bm25",
        "vector",
        "rrf",
        "cross_encoder",
        "retention",
        "decay_coefficient",
        "feedback_value",
        "runtime_status",
        "temporal_supersession",
        "mmr",
        "hopfield",
        "graph_expansion",
        "budget_trim",
    ]
    assert any(stage["status"] == "not_observed" for stage in detail["algorithm_trace"])
    assert any(stage["algorithm_id"] == "rrf" for stage in detail["algorithm_trace"])
    assert detail["candidates"][0]["item_id"] == item_id
    assert detail["candidates"][0]["firewall_action"] in {"include", "defer"}
    assert detail["candidates"][0]["title"] == "Chain log demo"


def test_chain_log_filters_by_adapter_status_and_session(tmp_path: Path) -> None:
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.memory.governance.recall_events import record_gap
    from agent_brain.product.chain_log import build_chain_log_report

    item_id = _write_item(tmp_path)
    record_injection_cohort(
        tmp_path,
        item_ids=[item_id],
        adapter="codex",
        session_id="sess-injected",
        cwd="/repo",
        query="injected query",
    )
    record_gap(
        tmp_path,
        query="blocked query",
        reason="query_not_injectable",
        adapter="claude_code",
        session_id="sess-blocked",
        cwd="/repo",
    )

    codex = build_chain_log_report(tmp_path, hours=72, adapter="codex").to_dict()
    assert [chain["adapter"] for chain in codex["chains"]] == ["codex"]

    blocked = build_chain_log_report(tmp_path, hours=72, status="blocked").to_dict()
    assert blocked["chains"][0]["session_id"] == "sess-blocked"

    injected = build_chain_log_report(tmp_path, hours=72, session_id="sess-injected").to_dict()
    assert injected["chains"][0]["final_outcome"] == "injected"
