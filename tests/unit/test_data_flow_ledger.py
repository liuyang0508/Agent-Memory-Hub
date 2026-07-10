"""Tests for the three-day data-flow observability read model."""

from __future__ import annotations

import json
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
