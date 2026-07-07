from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.memory.store.items_store import ItemsStore


def _long_summary() -> str:
    return (
        "This summary is intentionally long and starts with the important locator. "
        "It then keeps adding operational detail about commands, validation, "
        "handoff, and historical context so governance can suggest a concise "
        "summary rewrite candidate without changing the body."
    )


def _write_long_summary_item(store: ItemsStore, item_id: str) -> MemoryItem:
    item = MemoryItem(
        id=item_id,
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="Long summary apply",
        summary=_long_summary(),
        tags=["quality"],
    )
    store.write(item, "body")
    return item


def test_summary_rewrite_apply_dry_run_does_not_mutate(tmp_brain_dir: Path) -> None:
    from agent_brain.memory.governance.summary_rewrite_apply import apply_summary_rewrites

    store = ItemsStore(tmp_brain_dir / "items")
    item = _write_long_summary_item(store, "mem-20260618-180000-summary-dry-run")

    result = apply_summary_rewrites(
        brain_dir=tmp_brain_dir,
        items_store=store,
        limit=10,
        dry_run=True,
        snapshot=False,
    )

    assert result.candidate_count == 1
    assert result.applied_count == 0
    assert result.dry_run is True
    assert result.changes[0].item_id == item.id
    assert result.changes[0].candidate_length <= 200
    unchanged, _ = store.get(item.id)
    assert unchanged.summary == _long_summary()


def test_summary_rewrite_apply_updates_summary_and_records_snapshot(
    tmp_brain_dir: Path,
) -> None:
    from agent_brain.memory.governance.summary_rewrite_apply import apply_summary_rewrites

    store = ItemsStore(tmp_brain_dir / "items")
    item = _write_long_summary_item(store, "mem-20260618-180001-summary-apply")

    result = apply_summary_rewrites(
        brain_dir=tmp_brain_dir,
        items_store=store,
        limit=10,
        dry_run=False,
        snapshot=True,
    )

    assert result.candidate_count == 1
    assert result.applied_count == 1
    assert result.snapshot_sha
    assert (tmp_brain_dir / ".summary-rewrite-rollback").read_text().strip() == result.snapshot_sha
    updated, _ = store.get(item.id)
    assert updated.summary == result.changes[0].candidate_summary
    assert len(updated.summary) <= 200


def test_summary_rewrite_apply_respects_limit(tmp_brain_dir: Path) -> None:
    from agent_brain.memory.governance.summary_rewrite_apply import apply_summary_rewrites

    store = ItemsStore(tmp_brain_dir / "items")
    _write_long_summary_item(store, "mem-20260618-180002-summary-limit-a")
    _write_long_summary_item(store, "mem-20260618-180003-summary-limit-b")

    result = apply_summary_rewrites(
        brain_dir=tmp_brain_dir,
        items_store=store,
        limit=1,
        dry_run=False,
        snapshot=False,
    )

    assert result.candidate_count == 2
    assert result.returned_count == 1
    assert result.applied_count == 1
