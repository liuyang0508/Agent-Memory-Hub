"""Tests for garbage collection (gc) — CLI and Hermes."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_brain.interfaces.cli import app
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.contracts.memory_item import MemoryItem, MemoryType

runner = CliRunner()


def _make_item(suffix: str, tags: list[str], days_old: int = 0) -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260528-100000-{suffix}",
        type=MemoryType.signal,
        created_at=datetime.now(timezone.utc) - timedelta(days=days_old),
        title=f"Signal {suffix}",
        summary=f"auto signal {suffix}",
        tags=tags,
    )


@pytest.fixture
def gc_brain(tmp_brain_dir: Path):
    os.environ["BRAIN_DIR"] = str(tmp_brain_dir)
    store = ItemsStore(tmp_brain_dir / "items")
    store.write(_make_item("old-auto", ["session-end", "auto-captured"], days_old=10), "old body")
    store.write(_make_item("old-review", ["needs-review"], days_old=8), "old review body")
    store.write(_make_item("recent-auto", ["auto-captured"], days_old=2), "recent body")
    store.write(_make_item("old-real", ["infra"], days_old=30), "real item")
    yield tmp_brain_dir, store
    os.environ.pop("BRAIN_DIR", None)


class TestGcCLI:
    def test_gc_command_is_split_and_reexported(self):
        from agent_brain.interfaces.cli.commands import maintenance
        from agent_brain.interfaces.cli.commands.gc import gc

        assert maintenance.gc is gc

    def test_gc_dry_run(self, gc_brain):
        tmp, store = gc_brain
        result = runner.invoke(app, ["gc", "--dry-run"])
        assert result.exit_code == 0
        assert "would delete" in result.output
        assert "dry run" in result.output
        assert (store.items_dir / "mem-20260528-100000-old-auto.md").exists()

    def test_gc_deletes_old_auto(self, gc_brain):
        tmp, store = gc_brain
        result = runner.invoke(app, ["gc", "--max-age", "7"])
        assert result.exit_code == 0
        assert "deleted 2" in result.output
        assert not (store.items_dir / "mem-20260528-100000-old-auto.md").exists()
        assert not (store.items_dir / "mem-20260528-100000-old-review.md").exists()
        assert (store.items_dir / "mem-20260528-100000-recent-auto.md").exists()
        assert (store.items_dir / "mem-20260528-100000-old-real.md").exists()

    def test_gc_preserves_recent(self, gc_brain):
        tmp, store = gc_brain
        result = runner.invoke(app, ["gc", "--max-age", "5"])
        assert result.exit_code == 0
        assert (store.items_dir / "mem-20260528-100000-recent-auto.md").exists()

    def test_gc_custom_tags(self, gc_brain):
        tmp, store = gc_brain
        result = runner.invoke(app, ["gc", "--max-age", "7", "--tags", "infra"])
        assert result.exit_code == 0
        assert not (store.items_dir / "mem-20260528-100000-old-real.md").exists()
        assert (store.items_dir / "mem-20260528-100000-old-auto.md").exists()


class TestGcHermes:
    def test_hub_gc_dry_run(self, gc_brain):
        from agent_brain.agent_integrations.hermes.provider import hub_gc
        result = hub_gc(max_age_days=7, dry_run=True)
        assert result["dry_run"] is True
        assert result["deleted"] == 0
        assert len(result["candidates"]) == 2

    def test_hub_gc_deletes(self, gc_brain):
        tmp, store = gc_brain
        from agent_brain.agent_integrations.hermes.provider import hub_gc
        result = hub_gc(max_age_days=7)
        assert result["deleted"] == 2
        assert not (store.items_dir / "mem-20260528-100000-old-auto.md").exists()
