"""Tests for the Web request-chain log read model."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


def test_chain_log_groups_hook_injection_and_gap_by_session(tmp_path: Path) -> None:
    from agent_brain.agent_integrations.runtime_events import record_runtime_event
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.memory.governance.recall_events import record_gap
    from agent_brain.product.chain_log import build_chain_log_detail, build_chain_log_report

    item_id = _write_item(tmp_path)
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
            "query_terms_count": 4,
        },
    )
    record_gap(
        tmp_path,
        query="another raw query should not leak",
        reason="partial_candidates_rejected",
        injected_ids=[item_id],
        rejected_ids=["mem-20260706-010203-rejected"],
        evidence=["mem-20260706-010203-rejected:query_mismatch"],
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
    non_dict_item_id = "mem-20260706-010204-non-dict"
    non_list_stages_item_id = "mem-20260706-010205-non-list-stages"
    non_dict_stage_item_id = "mem-20260706-010206-non-dict-stage"
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
