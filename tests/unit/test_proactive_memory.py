from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.memory.store.items_store import ItemsStore


def _write_signal(brain_dir: Path) -> MemoryItem:
    store = ItemsStore(brain_dir / "items")
    item = MemoryItem(
        id="mem-20260621-010000-signal-handoff",
        type=MemoryType.signal,
        created_at=datetime(2026, 6, 21, tzinfo=timezone.utc),
        title="Handoff: adapter verify is blocked",
        summary="Need remember adapter verify blocker",
        tags=["blocker"],
        confidence=0.7,
    )
    store.write(item, "**当前状态** blocked on runtime event\n**期望操作** verify after hook fires")
    return item


def test_generate_candidates_from_high_signal_items(tmp_path: Path) -> None:
    from agent_brain.product.proactive_memory import generate_candidates, list_candidates

    source = _write_signal(tmp_path)
    result = generate_candidates(tmp_path, limit=20)
    queue = list_candidates(tmp_path)

    assert result["created"] == 1
    assert queue["pending"] == 1
    assert queue["items"][0]["source_item_ids"] == [source.id]
    assert "needs-review" in queue["items"][0]["tags"]


def test_generate_candidates_is_idempotent(tmp_path: Path) -> None:
    from agent_brain.product.proactive_memory import generate_candidates, list_candidates

    _write_signal(tmp_path)
    first = generate_candidates(tmp_path, limit=20)
    second = generate_candidates(tmp_path, limit=20)

    assert first["created"] == 1
    assert second["created"] == 0
    assert list_candidates(tmp_path)["pending"] == 1


def test_approve_candidate_writes_via_write_service(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")
    from agent_brain.product.proactive_memory import (
        approve_candidate,
        generate_candidates,
        list_candidates,
    )

    _write_signal(tmp_path)
    generate_candidates(tmp_path, limit=20)
    candidate_id = list_candidates(tmp_path)["items"][0]["candidate_id"]

    result = approve_candidate(tmp_path, candidate_id, reviewer="pytest")

    assert result["status"] == "approved"
    assert result["write_result"]["status"] == "written"
    written = ItemsStore(tmp_path / "items").get(result["item_id"])[0]
    assert "proactive" in written.tags
    assert "review-approved" in written.tags


def test_reject_candidate_does_not_write_item(tmp_path: Path) -> None:
    from agent_brain.product.proactive_memory import (
        generate_candidates,
        list_candidates,
        reject_candidate,
    )

    _write_signal(tmp_path)
    generate_candidates(tmp_path, limit=20)
    candidate_id = list_candidates(tmp_path)["items"][0]["candidate_id"]

    result = reject_candidate(tmp_path, candidate_id, reviewer="pytest")

    assert result["status"] == "rejected"
    assert list_candidates(tmp_path)["pending"] == 0
