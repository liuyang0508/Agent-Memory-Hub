from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.memory.store.items_store import ItemsStore


def _item(
    item_id: str,
    type_: MemoryType,
    title: str,
    *,
    now: datetime,
    days_ago: int = 0,
    summary: str | None = None,
    body: str | None = None,
    confidence: float = 0.8,
    tags: list[str] | None = None,
    support_count: int = 0,
    contradict_count: int = 0,
) -> tuple[MemoryItem, str]:
    created_at = now - timedelta(days=days_ago)
    item = MemoryItem(
        id=item_id,
        type=type_,
        title=title,
        summary=summary or f"Summary for {title}",
        created_at=created_at,
        confidence=confidence,
        tags=tags or [],
        support_count=support_count,
        contradict_count=contradict_count,
    )
    return item, body or f"Body for {title}"


def _write(store: ItemsStore, item: MemoryItem, body: str) -> None:
    store.write(item, body)


def test_cockpit_summary_empty_brain_is_renderable(tmp_path: Path) -> None:
    from agent_brain.product.cockpit import build_cockpit_summary

    now = datetime(2026, 6, 21, tzinfo=timezone.utc)

    payload = build_cockpit_summary(tmp_path, now=now)

    assert payload["generated_at"] == now.isoformat()
    assert payload["brain_dir"] == str(tmp_path)
    assert payload["handoff_pack"] == []
    assert payload["key_decisions"] == []
    assert payload["open_signals"] == []
    assert payload["trust_risks"] == []
    assert payload["cross_agent_timeline"] == []
    assert payload["adapter_health"]["total"] == 16
    assert payload["adapter_health"]["install_ready"] == 15
    assert payload["adapter_health"]["wip"] == 1
    assert payload["adapter_health"]["verified"] == 0
    assert payload["adapter_health"]["status"] == "ok"


def test_cockpit_summary_includes_loop_governance(tmp_path: Path) -> None:
    from agent_brain.memory.loops.loop_feedback import LoopFeedback
    from agent_brain.memory.loops.loop_store import LoopStore
    from agent_brain.product.cockpit import build_cockpit_summary

    store = LoopStore(tmp_path)
    ready = store.create(
        goal="ready contract loop",
        metadata={
            "contract_id": "contract-ready",
            "contract_verifiers": [
                {"id": "unit", "command": "python -m pytest --version", "required": True}
            ],
        },
        start=True,
    )
    store.add_verification_feedback(
        ready.loop_id,
        LoopFeedback(
            feedback_id="lfb-ready",
            timestamp="2026-06-25T00:00:00+00:00",
            command="python -m pytest --version",
            cwd=None,
            status="passed",
            category="successful_execution",
            exit_code=0,
            duration_ms=1,
            stdout_summary="pytest 8",
            stderr_summary="no output",
            stdout_sha256="abc",
            stderr_sha256="def",
            stdout_lines=1,
            stderr_lines=0,
            truncated=False,
            verifier_id="unit",
            contract_id="contract-ready",
        ),
    )
    store.create(
        goal="blocked contract loop",
        metadata={
            "contract_id": "contract-blocked",
            "contract_verifiers": [
                {"id": "unit", "command": "python -m pytest --version", "required": True}
            ],
            "open_human_gates": [{"id": "merge_main", "reason": "review before merge"}],
        },
        start=True,
    )

    payload = build_cockpit_summary(tmp_path)

    assert payload["loop_governance"]["total"] == 2
    assert payload["loop_governance"]["contract_loops"] == 2
    assert payload["loop_governance"]["ready"] == 1
    assert payload["loop_governance"]["blocked"] == 1
    assert payload["loop_governance"]["open_human_gates"] == 1
    assert payload["loop_governance"]["recent"][0]["contract_id"] == "contract-blocked"
    assert payload["loop_governance"]["recent"][1]["completion_readiness"] == "ready"


def test_cockpit_summary_buckets_items_and_keeps_traceability(tmp_path: Path) -> None:
    from agent_brain.product.cockpit import build_cockpit_summary

    now = datetime(2026, 6, 21, 8, 0, tzinfo=timezone.utc)
    store = ItemsStore(tmp_path / "items")
    decision, decision_body = _item(
        "mem-20260620-080000-decision-sse",
        MemoryType.decision,
        "Use SSE for live events",
        now=now,
        days_ago=1,
        support_count=2,
    )
    signal, signal_body = _item(
        "mem-20260621-070000-signal-blocker",
        MemoryType.signal,
        "Adapter verification blocked",
        now=now,
        body="**当前状态** verification still missing\n**影响** cannot mark verified",
        tags=["blocker"],
    )
    stale_fact, stale_body = _item(
        "mem-20260501-080000-stale-fact",
        MemoryType.fact,
        "Old install status",
        now=now,
        days_ago=51,
        confidence=0.35,
        contradict_count=1,
    )
    _write(store, decision, decision_body)
    _write(store, signal, signal_body)
    _write(store, stale_fact, stale_body)

    payload = build_cockpit_summary(tmp_path, now=now)

    assert payload["handoff_pack"][0]["id"] == signal.id
    assert payload["handoff_pack"][0]["detail_uri"] == f"memory://items/{signal.id}/body"
    assert payload["handoff_pack"][0]["retrieve_hint"] == signal.summary
    assert "open_signal" in payload["handoff_pack"][0]["trust_reasons"]
    assert payload["key_decisions"][0]["id"] == decision.id
    assert "supported" in payload["key_decisions"][0]["trust_reasons"]
    assert payload["open_signals"][0]["id"] == signal.id
    assert payload["trust_risks"][0]["id"] == stale_fact.id
    assert {"low_confidence", "contested", "stale"}.issubset(
        set(payload["trust_risks"][0]["risk_reasons"])
    )


def test_cockpit_summary_includes_runtime_timeline(tmp_path: Path) -> None:
    from agent_brain.agent_integrations.runtime_events import record_runtime_event
    from agent_brain.product.cockpit import build_cockpit_summary

    now = datetime(2026, 6, 21, 8, 0, tzinfo=timezone.utc)
    record_runtime_event(
        tmp_path,
        adapter="codex",
        event_name="UserPromptSubmit",
        session_id="s1",
        cwd="/repo",
        now=now,
    )

    payload = build_cockpit_summary(tmp_path, now=now)

    assert payload["cross_agent_timeline"] == [
        {
            "adapter": "codex",
            "event_name": "UserPromptSubmit",
            "timestamp": now.isoformat(),
            "session_id": "s1",
            "cwd": "/repo",
            "source": "hook",
        }
    ]
