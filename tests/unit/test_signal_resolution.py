from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from agent_brain.contracts.memory_enums import MemoryType
from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.memory.governance.signal_resolution import (
    read_signal_receipt_health,
    transition_signal_state,
)
from agent_brain.memory.governance.signal_state import assess_signal_state
from agent_brain.memory.store.items_store import ItemsStore


NOW = datetime(2026, 7, 30, 3, 0, tzinfo=timezone.utc)


def _item(
    item_id: str,
    *,
    type_: MemoryType = MemoryType.signal,
    tags: list[str] | None = None,
) -> MemoryItem:
    return MemoryItem(
        id=item_id,
        type=type_,
        created_at=NOW,
        project="agent-memory-hub",
        tenant_id="tenant-a",
        title="等待生产接线",
        summary="尚待处理",
        tags=tags or ["blocked", "pending"],
    )


def test_signal_state_is_scoped_to_signal_memories() -> None:
    with pytest.raises(ValidationError, match="only allowed on signal"):
        MemoryItem(
            id="mem-20260730-030000-not-signal",
            type=MemoryType.fact,
            created_at=NOW,
            title="fact",
            summary="fact",
            signal_state={
                "status": "resolved",
                "changed_at": NOW,
            },
        )


def test_resolve_signal_is_digest_bound_receipted_and_links_evidence(
    tmp_path,
) -> None:
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    signal = _item("mem-20260730-030001-signal")
    resolution = _item(
        "mem-20260730-030002-resolution",
        type_=MemoryType.decision,
        tags=["governance"],
    )
    store.write(signal, "**当前状态**\n\n等待处理")
    store.write(resolution, "**决策**\n\n问题已经修复")

    preview = transition_signal_state(
        brain_dir=brain,
        store=store,
        item_id=signal.id,
        action="resolve",
        resolution_item_id=resolution.id,
        reason="verified by regression suite",
        now=NOW,
    )
    result = transition_signal_state(
        brain_dir=brain,
        store=store,
        item_id=signal.id,
        action="resolve",
        resolution_item_id=resolution.id,
        reason="verified by regression suite",
        apply=True,
        expected_intent_sha256=preview.intent_sha256,
        now=NOW,
    )
    updated, _body = store.get(signal.id)
    health = read_signal_receipt_health(brain)

    assert preview.status == "ready"
    assert result.status == "applied"
    assert result.signal_state == "resolved"
    assert updated.signal_state is not None
    assert updated.signal_state.status == "resolved"
    assert updated.signal_state.resolution_item_id == resolution.id
    assert updated.tags == ["signal-resolved"]
    assert resolution.id in updated.refs.mems
    assert assess_signal_state(updated, now=NOW).state == "resolved"
    assert health.status == "healthy"
    assert health.record_count == 2
    assert health.completed_count == 1


def test_changed_signal_blocks_stale_preview(tmp_path) -> None:
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    signal = _item("mem-20260730-030003-stale-preview")
    store.write(signal, "waiting")
    preview = transition_signal_state(
        brain_dir=brain,
        store=store,
        item_id=signal.id,
        action="obsolete",
        now=NOW,
    )
    store.update_frontmatter(signal.id, summary="changed after preview")

    result = transition_signal_state(
        brain_dir=brain,
        store=store,
        item_id=signal.id,
        action="obsolete",
        apply=True,
        expected_intent_sha256=preview.intent_sha256,
        now=NOW,
    )

    assert result.status == "blocked"
    assert result.reason == "SIGNAL_TRANSITION_CHANGED"


def test_deferred_signal_reopens_for_governance_after_deadline(tmp_path) -> None:
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    signal = _item("mem-20260730-030004-deferred")
    store.write(signal, "waiting")
    preview = transition_signal_state(
        brain_dir=brain,
        store=store,
        item_id=signal.id,
        action="defer",
        defer_days=7,
        now=NOW,
    )
    applied = transition_signal_state(
        brain_dir=brain,
        store=store,
        item_id=signal.id,
        action="defer",
        defer_days=7,
        apply=True,
        expected_intent_sha256=preview.intent_sha256,
        now=NOW,
    )
    updated, _body = store.get(signal.id)

    assert applied.status == "applied"
    assert assess_signal_state(updated, now=NOW + timedelta(days=6)).state == "deferred"
    expired = assess_signal_state(updated, now=NOW + timedelta(days=8))
    assert expired.state == "open"
    assert "expired_deferral" in expired.issues


def test_failed_completion_receipt_rolls_signal_back(tmp_path, monkeypatch) -> None:
    from agent_brain.memory.governance import signal_resolution

    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    signal = _item("mem-20260730-030005-rollback")
    store.write(signal, "waiting")
    before = (brain / "items" / f"{signal.id}.md").read_bytes()
    preview = transition_signal_state(
        brain_dir=brain,
        store=store,
        item_id=signal.id,
        action="obsolete",
        now=NOW,
    )
    original = signal_resolution._append_receipt

    def fail_completed(*args, **kwargs):
        if kwargs["state"] == "completed":
            raise OSError("injected completion failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(signal_resolution, "_append_receipt", fail_completed)
    result = transition_signal_state(
        brain_dir=brain,
        store=store,
        item_id=signal.id,
        action="obsolete",
        apply=True,
        expected_intent_sha256=preview.intent_sha256,
        now=NOW,
    )

    assert result.status == "blocked"
    assert result.reason == "SIGNAL_TRANSITION_ROLLED_BACK"
    assert (brain / "items" / f"{signal.id}.md").read_bytes() == before
    health = read_signal_receipt_health(brain)
    assert health.status == "healthy"
    assert health.rolled_back_count == 1
