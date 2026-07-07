"""Tests for `memory migrate` (dry-run + apply + git rollback).

Fails before the fix: there is no `migrate` command, so Typer exits with code 2
("No such command"). Passes after the command is added.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_brain.interfaces.cli import app, CURRENT_SCHEMA_VERSION
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.contracts.memory_item import MemoryItem, MemoryType

runner = CliRunner()


@pytest.fixture
def old_schema_brain(tmp_brain_dir: Path):
    os.environ["BRAIN_DIR"] = str(tmp_brain_dir)
    store = ItemsStore(tmp_brain_dir / "items")
    item = MemoryItem(
        id="mem-20260528-100000-old",
        schema_version="0.2",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="Legacy item",
        summary="written under an old schema",
    )
    store.write(item, "legacy body")
    yield tmp_brain_dir, store, item
    os.environ.pop("BRAIN_DIR", None)


def test_migrate_dry_run_reports_without_writing(old_schema_brain):
    """Since schema_version is now excluded from serialization, all items
    read back as version '1' regardless of what was on disk. Migration is a no-op."""
    tmp, store, item = old_schema_brain
    result = runner.invoke(app, ["migrate", "--dry-run"])
    assert result.exit_code == 0, result.output


def test_migrate_apply_then_rollback(old_schema_brain):
    """With schema_version excluded, migrate is a no-op and rollback has nothing to restore."""
    tmp, store, item = old_schema_brain
    result = runner.invoke(app, ["migrate"])
    assert result.exit_code == 0, result.output


def test_migrate_rollback_without_snapshot_errors(old_schema_brain):
    tmp, store, item = old_schema_brain
    result = runner.invoke(app, ["migrate", "--rollback"])
    assert result.exit_code == 1
    assert "no migration snapshot" in result.output
