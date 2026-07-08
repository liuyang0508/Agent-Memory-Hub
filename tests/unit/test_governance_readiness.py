"""Governance readiness report for release, recall admission, and memory lifecycle."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from typer.testing import CliRunner

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.interfaces.cli import app
from agent_brain.memory.store.items_store import ItemsStore

runner = CliRunner()


def _write_item(
    store: ItemsStore,
    item_id: str,
    *,
    item_type: MemoryType,
    days_old: int,
    confidence: float,
    tags: list[str],
    title: str,
) -> None:
    item = MemoryItem(
        id=item_id,
        type=item_type,
        created_at=datetime.now(timezone.utc) - timedelta(days=days_old),
        title=title,
        summary=f"{title} summary",
        confidence=confidence,
        tags=tags,
        project="agent-memory-hub",
    )
    store.write(item, f"{title}\nbody")


def test_govern_readiness_json_reports_release_query_and_lifecycle_lanes(tmp_path, monkeypatch):
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    _write_item(
        store,
        "mem-20260101-000000-stale-signal-aaaa",
        item_type=MemoryType.signal,
        days_old=60,
        confidence=0.3,
        tags=[],
        title="stale hook warning",
    )
    _write_item(
        store,
        "mem-20260101-000000-good-decision-bbbb",
        item_type=MemoryType.decision,
        days_old=3,
        confidence=0.9,
        tags=["release", "doctor"],
        title="doctor fix release decision",
    )
    monkeypatch.setenv("BRAIN_DIR", str(brain))

    result = runner.invoke(app, ["govern", "readiness", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    by_lane = {lane["id"]: lane for lane in payload["lanes"]}
    assert set(by_lane) == {"release", "query_signal", "memory_lifecycle"}
    assert payload["overall_status"] in {"pass", "warn"}
    assert any(check["id"] == "install_sh" for check in by_lane["release"]["checks"])
    assert by_lane["query_signal"]["metrics"]["case_count"] >= 4
    assert by_lane["query_signal"]["metrics"]["under_extracted_cases"] == 0
    lifecycle = by_lane["memory_lifecycle"]["metrics"]
    assert lifecycle["total_items"] == 2
    assert lifecycle["stale_signal_count"] == 1
    assert lifecycle["low_confidence_count"] == 1
    assert lifecycle["untagged_count"] == 1
    assert any("memory govern plan" in action for action in payload["next_actions"])


def test_govern_readiness_markdown_is_user_facing(tmp_path, monkeypatch):
    brain = tmp_path / "brain"
    ItemsStore(brain / "items")
    monkeypatch.setenv("BRAIN_DIR", str(brain))

    result = runner.invoke(app, ["govern", "readiness", "--format", "markdown"])

    assert result.exit_code == 0, result.output
    assert "# Governance Readiness" in result.output
    assert "发布可用性" in result.output
    assert "长任务召回入口" in result.output
    assert "记忆生命周期" in result.output
