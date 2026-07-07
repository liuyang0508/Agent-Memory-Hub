from __future__ import annotations

import json
from datetime import datetime, timezone

from typer.testing import CliRunner

from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.interfaces.cli import app
from agent_brain.memory.store.items_store import ItemsStore


runner = CliRunner()
NOW = datetime(2026, 6, 28, 9, 0, tzinfo=timezone.utc)


def _item(
    suffix: str,
    type_: str,
    title: str,
    summary: str,
    *,
    project: str | None = "agent-memory-hub",
    tags: list[str] | None = None,
    refs: dict | None = None,
    validity: dict | None = None,
) -> MemoryItem:
    return MemoryItem.model_validate({
        "id": f"mem-20260628-100000-{suffix}",
        "type": type_,
        "created_at": NOW.isoformat(),
        "title": title,
        "summary": summary,
        "project": project,
        "tags": tags or [],
        "refs": refs or {},
        "validity": validity or {},
    })


def _store(tmp_brain_dir) -> ItemsStore:
    return ItemsStore(tmp_brain_dir / "items")


def test_lint_json_outputs_report(tmp_brain_dir, monkeypatch) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    store = _store(tmp_brain_dir)
    store.write(_item("json", "fact", "Unsourced fact", "No refs"), "body")

    result = runner.invoke(app, ["lint", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["total_items"] == 1
    assert payload["total_findings"] == 1
    assert payload["counts_by_type"] == {"source_missing": 1}
    assert payload["findings"][0]["issue_type"] == "source_missing"


def test_lint_table_outputs_issue_columns(tmp_brain_dir, monkeypatch) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    store = _store(tmp_brain_dir)
    store.write(_item("table", "fact", "Table fact", "No refs"), "body")

    result = runner.invoke(app, ["lint"])

    assert result.exit_code == 0, result.output
    assert "Knowledge Lint" in result.output
    assert "source_missing" in result.output
    assert "warning" in result.output
    assert "Table fact" in result.output


def test_lint_project_type_and_limit_filters(tmp_brain_dir, monkeypatch) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    store = _store(tmp_brain_dir)
    store.write(_item("alpha", "fact", "Alpha fact", "No refs", project="alpha"), "body")
    store.write(_item("beta", "fact", "Beta fact", "No refs", project="beta"), "body")
    store.write(
        _item(
            "orphan",
            "episode",
            "Alpha orphan",
            "Broken link",
            project="alpha",
            refs={"mems": ["mem-20260628-100000-missing"]},
        ),
        "body",
    )

    result = runner.invoke(app, [
        "lint",
        "--format",
        "json",
        "--project",
        "alpha",
        "--type",
        "source_missing",
        "--limit",
        "1",
    ])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["total_items"] == 2
    assert payload["total_findings"] == 1
    assert payload["findings"][0]["item_id"] == "mem-20260628-100000-alpha"
    assert payload["findings"][0]["issue_type"] == "source_missing"


def test_lint_scope_options_can_report_scope_mismatch(tmp_brain_dir, monkeypatch) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    store = _store(tmp_brain_dir)
    store.write(
        _item(
            "scope",
            "signal",
            "Browser unavailable",
            "Browser unavailable in another repo",
            tags=["browser", "runtime"],
            validity={"cwd": "/repo/other", "adapter": "codex"},
        ),
        "body",
    )

    result = runner.invoke(app, [
        "lint",
        "--format",
        "json",
        "--cwd",
        "/repo/current",
        "--adapter",
        "codex",
    ])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["findings"][0]["issue_type"] == "scope_mismatch"
    assert "scope_mismatch:cwd" in payload["findings"][0]["reasons"]


def test_lint_invalid_format_exits_cleanly(tmp_brain_dir, monkeypatch) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))

    result = runner.invoke(app, ["lint", "--format", "xml"])

    assert result.exit_code == 2
    assert "format must be table or json" in result.output


def test_lint_negative_limit_exits_cleanly(tmp_brain_dir, monkeypatch) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))

    result = runner.invoke(app, ["lint", "--limit", "-1"])

    assert result.exit_code == 2
    assert "limit must be non-negative" in result.output
