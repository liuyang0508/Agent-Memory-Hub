"""Tests for the three-day data-flow observability read model."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Refs


def _write_item(brain_dir: Path, item_id: str) -> MemoryItem:
    from agent_brain.memory.store.items_store import ItemsStore

    item = MemoryItem(
        id=item_id,
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        agent="codex",
        session="sess-data-flow",
        project="agent-memory-hub",
        tags=["data-flow"],
        title="Data-flow contract fixture",
        summary="Safe fixture used by data-flow contract tests",
        refs=Refs(),
        confidence=0.9,
    )
    ItemsStore(brain_dir / "items").write(item, "safe fixture body")
    return item


def test_data_flow_ledger_merges_recent_runtime_sources_and_redacts_raw_text(tmp_brain: Path):
    from agent_brain.agent_integrations.runtime_events import record_runtime_event
    from agent_brain.agent_integrations.verifications import record_adapter_verification
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.memory.governance.recall_events import record_gap, record_task_outcome
    from agent_brain.memory.loops.loop_events import append_loop_event
    from agent_brain.memory.loops.loop_types import LoopEvent
    from agent_brain.observability.data_flow import DataFlowLedger

    now = datetime(2026, 6, 23, 9, 30, tzinfo=timezone.utc)
    old = now - timedelta(days=4)
    first = _write_item(tmp_brain, "mem-20260623-010203-data-flow-first")
    second = _write_item(tmp_brain, "mem-20260623-010204-data-flow-second")

    record_runtime_event(
        tmp_brain,
        adapter="codex",
        event_name="UserPromptSubmit",
        session_id="sess-1",
        now=now - timedelta(minutes=6),
    )
    record_runtime_event(
        tmp_brain,
        adapter="codex",
        event_name="SessionStart",
        session_id="old-session",
        now=old,
    )
    record_adapter_verification(
        tmp_brain,
        adapter="codex",
        status="passed",
        verifier="pytest",
        evidence=["doctor", "runtime_events=1"],
        now=now - timedelta(minutes=5),
    )
    append_loop_event(
        tmp_brain,
        LoopEvent(
            event_id="lev-1",
            loop_id="loop-1",
            timestamp=(now - timedelta(minutes=4)).isoformat(),
            event_type="checkpoint_added",
            actor="pytest",
            summary="checkpoint",
            payload={
                "prompt": "secret prompt",
                "body": "secret body",
                "kept": "safe",
            },
        ),
    )
    record_gap(
        tmp_brain,
        query="secret query",
        reason="firewall_rejected_all",
        injected_ids=[first.id],
        rejected_ids=[second.id],
        adapter="codex",
        session_id="sess-1",
        now=now - timedelta(minutes=3),
    )
    record_task_outcome(
        tmp_brain,
        task_id="task-1",
        question="secret question",
        outcome="accepted",
        injected_ids=[first.id],
        adopted_ids=[first.id],
        adapter="codex",
        session_id="sess-1",
        now=now - timedelta(minutes=2),
    )
    record_injection_cohort(
        tmp_brain,
        item_ids=[first.id, second.id],
        query="secret injection query",
        adapter="codex",
        session_id="sess-1",
        pack_metrics={"token_budget": 2048, "body": "secret pack body"},
        now=now - timedelta(minutes=1),
    )

    ledger = DataFlowLedger(tmp_brain)
    events = ledger.list_events(now=now, since_hours=72, limit=20)
    payload = [event.to_dict() for event in events]

    assert [event["source"] for event in payload] == [
        "injection",
        "task_outcome",
        "recall_gap",
        "loop",
        "adapter_verification",
        "adapter_runtime",
    ]
    assert payload[0]["summary"] == "记录 2 条 injection cohort 记忆"
    assert {event["stage"] for event in payload} >= {
        "触发采集",
        "适配器验证",
        "循环工程",
        "召回诊断",
        "结果反馈",
        "上下文注入",
    }
    assert all(event["event_id"] for event in payload)
    assert not any(event["session_id"] == "old-session" for event in payload)

    serialized = json.dumps(payload, ensure_ascii=False)
    assert "secret prompt" not in serialized
    assert "secret body" not in serialized
    assert "secret query" not in serialized
    assert "secret question" not in serialized
    assert '"prompt"' not in serialized
    assert '"body"' not in serialized
    assert '"query"' not in serialized
    assert '"question"' not in serialized

    summary = ledger.summary(events).to_dict()
    assert summary["total"] == 6
    assert summary["window_hours"] == 72
    assert summary["by_source"]["adapter_runtime"] == 1
    assert summary["by_stage"]["上下文注入"] == 1
    assert summary["last_event_at"] == payload[0]["timestamp"]


def test_data_flow_recall_gap_evidence_uses_closed_aggregate_vocabulary(
    tmp_brain: Path,
):
    from agent_brain.memory.governance.recall_events import record_gap
    from agent_brain.observability.data_flow import DataFlowLedger

    record_gap(
        tmp_brain,
        query="sha256:prompt-fingerprint",
        reason="partial_candidates_rejected",
        evidence=[
            "excluded_count=1",
            "excluded_reason.missing_source=1",
            "included_count=1",
            "retrieved_count=2",
            "hydrate_error_count=0",
            "source_evidence_count=3",
            "mem-secret-id:missing_source",
            "SECRET_RAW_RECALL_EVIDENCE",
            "excluded_reason.unknown_private_reason=1",
        ],
        adapter="codex",
    )

    gap = next(
        event
        for event in DataFlowLedger(tmp_brain).list_events(since_hours=72)
        if event.source == "recall_gap"
    )

    assert gap.evidence == (
        "retrieved_count=2",
        "included_count=1",
        "hydrate_error_count=0",
        "excluded_count=1",
        "excluded_reason.missing_source=1",
    )
    assert "SECRET_RAW_RECALL_EVIDENCE" not in repr(gap.to_dict())


@pytest.mark.parametrize(
    "evidence",
    [
        [
            "retrieved_count=2",
            "retrieved_count=2",
            "included_count=1",
            "hydrate_error_count=0",
            "excluded_count=1",
            "excluded_reason.missing_source=1",
        ],
        [
            "retrieved_count=2",
            "included_count=1",
            "excluded_count=1",
            "excluded_reason.missing_source=1",
        ],
        [
            "retrieved_count=3",
            "included_count=1",
            "hydrate_error_count=0",
            "excluded_count=1",
            "excluded_reason.missing_source=1",
        ],
        [
            "retrieved_count=2",
            "included_count=1",
            "hydrate_error_count=0",
            "excluded_count=1",
            "excluded_reason.missing_source=SECRET_EVIDENCE_COUNT",
        ],
        [
            "retrieved_count=2",
            "included_count=1",
            "hydrate_error_count=0",
            "excluded_count=1",
        ],
    ],
)
def test_data_flow_recall_gap_evidence_fails_closed_as_a_complete_bundle(
    tmp_brain: Path,
    evidence: list[str],
) -> None:
    from agent_brain.memory.governance.recall_events import record_gap
    from agent_brain.observability.data_flow import DataFlowLedger

    record_gap(
        tmp_brain,
        query="sha256:prompt-fingerprint",
        reason="partial_candidates_rejected",
        evidence=evidence,
        adapter="codex",
    )

    gap = next(
        event
        for event in DataFlowLedger(tmp_brain).list_events(since_hours=72)
        if event.source == "recall_gap"
    )

    assert gap.evidence == ()


def test_data_flow_gap_and_lineage_drop_unknown_reason_and_non_store_item_ids(
    tmp_brain: Path,
) -> None:
    from agent_brain.memory.governance.recall_events import record_gap
    from agent_brain.observability.data_flow import DataFlowLedger
    from agent_brain.product.memory_lineage import build_memory_lineage_report

    item = _write_item(tmp_brain, "mem-20260711-010203-safe-gap-item")
    raw_reason = "SECRET_UNKNOWN_REASON\nwith-details"
    dirty_id = "mem-20260711-010204-dirty\nSECRET_ITEM_ID"
    missing_unicode_id = "mem-20260711-010205-不存在"
    cwd = "/repo/keep-as-recorded"
    record_gap(
        tmp_brain,
        query="sha256:prompt-fingerprint",
        reason=raw_reason,
        injected_ids=[item.id, dirty_id],
        rejected_ids=[missing_unicode_id, item.id],
        evidence=[
            "retrieved_count=1",
            "included_count=1",
            "hydrate_error_count=0",
            "excluded_count=0",
            "SECRET_GAP_EVIDENCE",
        ],
        adapter="codex",
        cwd=cwd,
    )

    raw_ledger = (tmp_brain / "runtime" / "recall-gaps.jsonl").read_text(
        encoding="utf-8"
    )
    assert "SECRET_UNKNOWN_REASON" in raw_ledger
    assert "SECRET_ITEM_ID" in raw_ledger

    gap = next(
        event
        for event in DataFlowLedger(tmp_brain).list_events(since_hours=72)
        if event.source == "recall_gap"
    )
    payload = gap.to_dict()
    assert payload["summary"] == "召回缺口：unclassified"
    assert payload["metadata"]["reason"] == "unclassified"
    assert payload["metadata"]["cwd"] == cwd
    assert payload["item_ids"] == [item.id]
    assert "SECRET" not in json.dumps(payload, ensure_ascii=False)
    assert "不存在" not in json.dumps(payload, ensure_ascii=False)

    report = build_memory_lineage_report(tmp_brain, hours=72).to_dict()
    serialized = json.dumps(report, ensure_ascii=False)
    lineage_gap = next(event for event in report["events"] if event["kind"] == "gap")
    assert lineage_gap["item_ids"] == [item.id]
    assert lineage_gap["metrics"]["reason"] == "unclassified"
    assert "SECRET" not in serialized
    assert "不存在" not in serialized


def test_data_flow_injection_metrics_use_strict_schema_and_safe_cohort_ids(
    tmp_brain: Path,
) -> None:
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.observability.data_flow import DataFlowLedger
    from agent_brain.product.memory_lineage import build_memory_lineage_report

    item = _write_item(tmp_brain, "mem-20260711-020304-safe-injection-item")
    dirty_id = "mem-20260711-020305-dirty\nSECRET_COHORT_ID"
    record_injection_cohort(
        tmp_brain,
        item_ids=[item.id, dirty_id],
        adapter="codex",
        session_id="strict-safe",
        source="search",
        pack_metrics={
            "candidate_count": 2,
            "included_count": 1,
            "excluded_count": 1,
            "raw_candidate_count": 2,
            "gateway_candidate_count": 2,
            "hydrate_error_count": 0,
            "excluded_reasons": {"missing_source": 1},
            "selected_views": {"overview": 1},
            "compressed_count": 0,
            "context_pack_chars": 88,
            "detail_refs": True,
            "query_terms_count": "SECRET_WRONG_TYPE",
            "title": "SECRET_TITLE",
            "summary": "SECRET_SUMMARY",
            "unknown": {"nested": "SECRET_NESTED"},
            "items": [
                {
                    "id": item.id,
                    "selected_view": "overview",
                    "packed_tokens": 7,
                    "title": "SECRET_ITEM_TITLE",
                },
                {"id": dirty_id, "selected_view": "detail"},
            ],
            "retrieval_trace": {
                item.id: {"final_rank": 1, "query": "SECRET_TRACE_QUERY"},
                dirty_id: {"final_rank": 2},
            },
        },
    )
    record_injection_cohort(
        tmp_brain,
        item_ids=[dirty_id],
        adapter="codex",
        session_id="mismatched-history",
        source="historical-sidecar",
        pack_metrics={
            "candidate_count": 1,
            "included_count": 1,
            "excluded_count": 0,
            "selected_views": {"overview": 1},
            "context_pack_chars": 55,
        },
    )

    injections = {
        event.session_id: event
        for event in DataFlowLedger(tmp_brain).list_events(since_hours=72)
        if event.source == "injection"
    }
    safe_event = injections["strict-safe"]
    assert safe_event.item_ids == (item.id,)
    assert safe_event.summary == "记录 1 条 injection cohort 记忆"
    assert safe_event.metadata["pack_metrics"] == {
        "context_pack_chars": 88,
        "items": [
            {
                "id": item.id,
                "selected_view": "overview",
                "packed_tokens": 7,
            }
        ],
        "candidate_count": 2,
        "excluded_count": 1,
        "included_count": 1,
        "selected_views": {"overview": 1},
        "compressed_count": 0,
        "excluded_reasons": {"missing_source": 1},
        "gateway_candidate_count": 2,
        "hydrate_error_count": 0,
        "raw_candidate_count": 2,
        "retrieval_trace": {item.id: {"final_rank": 1}},
    }
    assert "SECRET" not in json.dumps(safe_event.to_dict(), ensure_ascii=False)

    historical = injections["mismatched-history"]
    assert historical.item_ids == ()
    assert historical.metadata["pack_metrics"] == {"context_pack_chars": 55}

    report = build_memory_lineage_report(tmp_brain, hours=72).to_dict()
    serialized = json.dumps(report, ensure_ascii=False)
    safe_lineage = next(
        event
        for event in report["events"]
        if event["kind"] == "load" and event["session_id"] == "strict-safe"
    )
    assert safe_lineage["item_ids"] == [item.id]
    assert safe_lineage["metrics"]["pack_metrics"] == safe_event.metadata["pack_metrics"]
    assert "SECRET" not in serialized


def test_data_flow_and_lineage_close_dirty_injection_metadata_from_raw_jsonl(
    tmp_brain: Path,
) -> None:
    from agent_brain.memory.context.injection_cohorts import (
        injection_cohorts_path,
        iter_injection_cohorts,
    )
    from agent_brain.observability.data_flow import DataFlowLedger
    from agent_brain.product.chain_log import build_chain_log_detail, build_chain_log_report
    from agent_brain.product.memory_lineage import build_memory_lineage_report

    item = _write_item(tmp_brain, "mem-20260711-030405-dirty-metadata-safe-item")
    raw = {
        "cohort_id": "SECRET_COHORT_ID",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "item_ids": [item.id],
        "adapter": "SECRET_ADAPTER",
        "session_id": "sess-dirty-metadata",
        "cwd": "/repo",
        "source": "SECRET_SOURCE",
        "query_sha256": "SECRET_QUERY_HASH",
        "pack_metrics": {
            "candidate_count": 1,
            "included_count": 1,
            "excluded_count": 0,
            "selected_views": {"overview": 1},
        },
    }
    path = injection_cohorts_path(tmp_brain)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw) + "\n", encoding="utf-8")
    raw_ledger = path.read_text(encoding="utf-8")
    assert all(
        sentinel in raw_ledger
        for sentinel in (
            "SECRET_COHORT_ID",
            "SECRET_ADAPTER",
            "SECRET_SOURCE",
            "SECRET_QUERY_HASH",
        )
    )

    cohort = next(iter_injection_cohorts(tmp_brain))
    assert cohort.cohort_id.startswith("inj-invalid-")
    assert cohort.adapter == "unknown"
    assert cohort.source == "unknown"
    assert cohort.query_sha256 is None

    event = next(
        event
        for event in DataFlowLedger(tmp_brain).list_events(source="injection")
        if event.session_id == "sess-dirty-metadata"
    )
    payload = event.to_dict()
    assert event.event_id.startswith("inj-invalid-")
    assert event.adapter == "unknown"
    assert event.metadata["source"] == "unknown"
    assert event.metadata["query_sha256"] is None
    assert "SECRET" not in json.dumps(payload, ensure_ascii=False)

    report = build_memory_lineage_report(tmp_brain, hours=72).to_dict()
    lineage = next(
        row
        for row in report["events"]
        if row["session_id"] == "sess-dirty-metadata"
    )
    assert lineage["agent"] == "unknown"
    assert lineage["metrics"]["source"] == "unknown"
    assert lineage["metrics"]["query_sha256"] is None
    assert "SECRET" not in json.dumps(report, ensure_ascii=False)

    chain_report = build_chain_log_report(tmp_brain, hours=72).to_dict()
    assert "SECRET" not in json.dumps(chain_report, ensure_ascii=False)
    chain_id = chain_report["chains"][0]["chain_id"]
    chain = build_chain_log_detail(tmp_brain, chain_id, hours=72).to_dict()
    assert chain["adapter"] == "unknown"
    assert "SECRET" not in json.dumps(chain, ensure_ascii=False)


def test_data_flow_known_ids_are_lightweight_and_stable_after_archive(
    tmp_brain: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.memory.store.items_store import ItemsStore
    from agent_brain.observability.data_flow import DataFlowLedger

    item = _write_item(tmp_brain, "mem-20260711-040506-archive-stable")
    noncanonical_id = "not-a-memory-id"
    newline_id = "mem-20260711-040507-dirty\n"
    record_injection_cohort(
        tmp_brain,
        item_ids=[item.id, noncanonical_id, newline_id],
        adapter="codex",
        session_id="archive-stable",
        source="search",
        pack_metrics={
            "candidate_count": 1,
            "included_count": 1,
            "excluded_count": 0,
            "selected_views": {"overview": 1},
        },
    )
    (tmp_brain / "items" / "bad.md").write_text("malformed", encoding="utf-8")

    def fail_body_scan(*args, **kwargs):
        raise AssertionError("DataFlow must not parse item bodies/frontmatter")

    monkeypatch.setattr(ItemsStore, "iter_all", fail_body_scan)
    ledger = DataFlowLedger(tmp_brain)
    before = next(
        event
        for event in ledger.list_events(source="injection")
        if event.session_id == "archive-stable"
    )
    before_payload = before.to_dict()

    archive_dir = tmp_brain / "items" / "archived"
    archive_dir.mkdir(parents=True)
    shutil.move(
        str(tmp_brain / "items" / f"{item.id}.md"),
        str(archive_dir / f"{item.id}.md"),
    )
    after = next(
        event
        for event in ledger.list_events(source="injection")
        if event.session_id == "archive-stable"
    )

    assert before.item_ids == after.item_ids == (item.id,)
    assert before.metadata["pack_metrics"] == after.metadata["pack_metrics"]
    assert after.to_dict() == before_payload
    assert noncanonical_id not in repr(after.to_dict())
    assert "dirty" not in repr(after.to_dict())


def test_data_flow_ordered_trace_binds_original_ids_before_public_filtering(
    tmp_brain: Path,
) -> None:
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.observability.data_flow import DataFlowLedger

    first = _write_item(tmp_brain, "mem-20260711-050607-trace-first")
    second = _write_item(tmp_brain, "mem-20260711-050608-trace-second")
    dirty_id = "mem-20260711-050609-dirty\nSECRET_TRACE_ID"
    aggregate = {
        "candidate_count": 2,
        "included_count": 2,
        "excluded_count": 0,
        "selected_views": {"overview": 2},
    }
    record_injection_cohort(
        tmp_brain,
        item_ids=[first.id, dirty_id, second.id],
        adapter="codex",
        session_id="ordered-trace-full",
        source="search",
        pack_metrics={
            **aggregate,
            "retrieval_trace": [
                {"final_rank": 1},
                {"final_rank": 999, "query": "SECRET_DIRTY_TRACE"},
                {"final_rank": 3},
            ],
        },
    )
    record_injection_cohort(
        tmp_brain,
        item_ids=[first.id, dirty_id, second.id],
        adapter="codex",
        session_id="ordered-trace-short",
        source="search",
        pack_metrics={
            **aggregate,
            "retrieval_trace": [
                {"final_rank": 1},
                {"final_rank": 999, "query": "SECRET_REBOUND_TRACE"},
            ],
        },
    )

    events = {
        event.session_id: event
        for event in DataFlowLedger(tmp_brain).list_events(source="injection")
    }
    full_metrics = events["ordered-trace-full"].metadata["pack_metrics"]
    short_metrics = events["ordered-trace-short"].metadata["pack_metrics"]

    assert events["ordered-trace-full"].item_ids == (first.id, second.id)
    assert full_metrics["retrieval_trace"] == [
        {"final_rank": 1},
        {"final_rank": 3},
    ]
    assert "retrieval_trace" not in short_metrics
    assert "SECRET" not in json.dumps(
        [event.to_dict() for event in events.values()],
        ensure_ascii=False,
    )


def test_data_flow_source_filter_does_not_construct_unrequested_sources(
    tmp_brain: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent_brain.observability.data_flow as data_flow
    from agent_brain.agent_integrations.runtime_events import record_runtime_event
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort

    record_runtime_event(
        tmp_brain,
        adapter="codex",
        event_name="UserPromptSubmit",
        session_id="source-aware",
    )
    record_injection_cohort(
        tmp_brain,
        item_ids=["mem-20260711-060708-unrequested"],
        adapter="codex",
        session_id="unrequested-injection",
    )
    ledger = data_flow.DataFlowLedger(tmp_brain)
    calls = {"injections": 0, "gaps": 0, "ids": 0}

    def fail_injections(*args, **kwargs):
        calls["injections"] += 1
        raise AssertionError("unrequested injection source was constructed")

    def fail_gaps(*args, **kwargs):
        calls["gaps"] += 1
        raise AssertionError("unrequested gap source was constructed")

    def fail_ids():
        calls["ids"] += 1
        raise AssertionError("unrequested item ID scan ran")

    monkeypatch.setattr(data_flow, "iter_injection_cohorts", fail_injections)
    monkeypatch.setattr(data_flow, "iter_gap_records", fail_gaps)
    monkeypatch.setattr(ledger, "_known_item_ids", fail_ids)

    events = ledger.list_events(source="adapter_runtime")

    assert [event.source for event in events] == ["adapter_runtime"]
    assert calls == {"injections": 0, "gaps": 0, "ids": 0}


def test_data_flow_skips_hostile_jsonl_rows_and_keeps_following_valid_events(
    tmp_brain: Path,
) -> None:
    from agent_brain.agent_integrations.runtime_events import (
        record_runtime_event,
        runtime_events_path,
    )
    from agent_brain.agent_integrations.verifications import (
        adapter_verifications_path,
        record_adapter_verification,
    )
    from agent_brain.memory.context.injection_cohorts import (
        injection_cohorts_path,
        record_injection_cohort,
    )
    from agent_brain.memory.governance.recall_events import (
        recall_gaps_path,
        record_gap,
        record_task_outcome,
        task_outcomes_path,
    )
    from agent_brain.memory.loops.loop_events import append_loop_event, loop_events_path
    from agent_brain.memory.loops.loop_types import LoopEvent
    from agent_brain.observability.data_flow import DataFlowLedger

    item = _write_item(tmp_brain, "mem-20260711-070809-jsonl-safe")
    record_runtime_event(
        tmp_brain,
        adapter="codex",
        event_name="UserPromptSubmit",
        session_id="jsonl-safe",
    )
    record_adapter_verification(
        tmp_brain,
        adapter="codex",
        status="passed",
        verifier="pytest",
        evidence=["offline"],
    )
    append_loop_event(
        tmp_brain,
        LoopEvent(
            event_id="loop-jsonl-safe",
            loop_id="loop-jsonl-safe",
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_type="checkpoint_added",
            actor="pytest",
            summary="safe",
        ),
    )
    record_gap(
        tmp_brain,
        query="sha256:fingerprint",
        reason="empty_recall",
        adapter="codex",
        session_id="jsonl-safe",
    )
    record_task_outcome(
        tmp_brain,
        task_id="jsonl-safe",
        question="safe",
        outcome="accepted",
        injected_ids=[item.id],
        adapter="codex",
        session_id="jsonl-safe",
    )
    record_injection_cohort(
        tmp_brain,
        item_ids=[item.id],
        adapter="codex",
        session_id="jsonl-safe",
    )
    hostile = b'{"count":' + (b"9" * 5000) + b"}\n"
    paths = (
        runtime_events_path(tmp_brain),
        adapter_verifications_path(tmp_brain),
        loop_events_path(tmp_brain),
        recall_gaps_path(tmp_brain),
        task_outcomes_path(tmp_brain),
        injection_cohorts_path(tmp_brain),
    )
    for path in paths:
        path.write_bytes(hostile + path.read_bytes())

    events = DataFlowLedger(tmp_brain).list_events(since_hours=72)

    assert {event.source for event in events} == {
        "adapter_runtime",
        "adapter_verification",
        "loop",
        "recall_gap",
        "task_outcome",
        "injection",
    }
