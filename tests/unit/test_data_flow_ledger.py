"""Tests for the three-day data-flow observability read model."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


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
        injected_ids=["mem-a"],
        rejected_ids=["mem-b"],
        adapter="codex",
        session_id="sess-1",
        now=now - timedelta(minutes=3),
    )
    record_task_outcome(
        tmp_brain,
        task_id="task-1",
        question="secret question",
        outcome="accepted",
        injected_ids=["mem-a"],
        adopted_ids=["mem-a"],
        adapter="codex",
        session_id="sess-1",
        now=now - timedelta(minutes=2),
    )
    record_injection_cohort(
        tmp_brain,
        item_ids=["mem-a", "mem-b"],
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
    assert payload[0]["summary"] == "注入 2 条已授权记忆"
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
            "retrieved_count=2",
            "included_count=1",
            "hydrate_error_count=0",
            "excluded_count=1",
            "excluded_reason.missing_source=1",
            "source_evidence_count=3",
            "mem-secret-id:missing_source",
            "SECRET_RAW_RECALL_EVIDENCE",
            "excluded_reason.unknown_private_reason=1",
            "excluded_reason.missing_source=0",
            "retrieved_count=SECRET",
            "retrieved_count=１２",
            "excluded_reason.missing_source=²",
            "excluded_count=" + ("9" * 64),
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
