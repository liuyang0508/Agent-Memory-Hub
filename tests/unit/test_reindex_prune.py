"""P3-2: reindex --prune drops orphan index rows; verify diffs md vs index.

Before the fix `reindex` only upserts md items and never removes index rows
whose md file is gone, so deleted/archived items linger as ghost search hits
and there is no reconcile/verify capability.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_brain.interfaces.cli import app
from agent_brain.platform.indexing.index import HubIndex
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.contracts.memory_item import MemoryItem, MemoryType

runner = CliRunner()

LIVE_ID = "mem-20260528-100000-live"
GHOST_ID = "mem-20260528-100000-ghost"


def _make_item(item_id: str, title: str) -> tuple[MemoryItem, str]:
    item = MemoryItem(
        id=item_id,
        type=MemoryType.fact,
        created_at=datetime(2026, 5, 28, tzinfo=timezone.utc),
        title=title,
        summary=f"{title} summary mypy",
    )
    return item, f"{title} body"


@pytest.fixture
def brain_with_ghost(tmp_brain_dir: Path, monkeypatch):
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")
    store = ItemsStore(tmp_brain_dir / "items")
    live, live_body = _make_item(LIVE_ID, "live one")
    store.write(live, live_body)
    idx = HubIndex(db_path=tmp_brain_dir / "index.db")
    idx.upsert(live, live_body, embedding=None)
    ghost, ghost_body = _make_item(GHOST_ID, "ghost one")
    idx.upsert(ghost, ghost_body, embedding=None)
    idx.connection.close()
    return tmp_brain_dir


def _all_ids(brain: Path) -> set[str]:
    idx = HubIndex(db_path=brain / "index.db")
    try:
        return idx.all_ids()
    finally:
        idx.connection.close()


def test_index_maintenance_helpers_are_split():
    from agent_brain.interfaces.cli.commands import maintenance
    from agent_brain.interfaces.cli.commands.index_maintenance import inspect_index_drift

    assert maintenance.inspect_index_drift is inspect_index_drift


def test_reindex_without_prune_keeps_ghost(brain_with_ghost: Path):
    result = runner.invoke(app, ["reindex"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "reindexed 1 items"
    assert GHOST_ID in _all_ids(brain_with_ghost)


def test_reindex_prune_drops_ghost(brain_with_ghost: Path):
    result = runner.invoke(app, ["reindex", "--prune"])
    assert result.exit_code == 0, result.output
    assert "pruned 1" in result.output
    ids = _all_ids(brain_with_ghost)
    assert GHOST_ID not in ids
    assert LIVE_ID in ids


def test_verify_reports_orphan_and_exits_nonzero(brain_with_ghost: Path):
    result = runner.invoke(app, ["verify"])
    assert result.exit_code == 1
    assert "orphan index rows: 1" in result.output
    assert GHOST_ID in result.output


def test_verify_repair_then_clean(brain_with_ghost: Path):
    repair = runner.invoke(app, ["verify", "--repair"])
    assert repair.exit_code == 0, repair.output
    assert "pruned 1 orphans" in repair.output
    check = runner.invoke(app, ["verify"])
    assert check.exit_code == 0, check.output
    assert "index in sync" in check.output
