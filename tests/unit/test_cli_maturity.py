from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from typer.testing import CliRunner

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.interfaces.cli import app
from agent_brain.memory.store.items_store import ItemsStore

runner = CliRunner()


def test_govern_maturity_reports_recommendations_without_apply(tmp_brain_dir: Path, monkeypatch) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    store = ItemsStore(tmp_brain_dir / "items")
    item = MemoryItem(
        id="mem-20260618-140000-cli-maturity",
        type=MemoryType.decision,
        created_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
        title="CLI maturity",
        summary="CLI maturity locator",
        confidence=0.85,
        refs={"files": ["docs/architecture.md"], "mems": ["mem-20260618-010101-source"]},
        support_count=4,
        gain_score=0.3,
        context_views={
            "locator": "CLI maturity locator",
            "overview": "CLI maturity overview",
        },
    )
    store.write(item, "body")

    result = runner.invoke(app, ["govern", "maturity", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["changed_items"] == 1
    assert payload["items"][0]["recommended_maturity"] == "consolidated"
    assert payload["items"][0]["recommended_abstraction"] == "L1"
    unchanged, _ = store.get(item.id)
    assert unchanged.maturity == "raw"
    assert unchanged.abstraction == "L0"


def test_govern_maturity_apply_persists_recommendations(tmp_brain_dir: Path, monkeypatch) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    store = ItemsStore(tmp_brain_dir / "items")
    item = MemoryItem(
        id="mem-20260618-140001-cli-maturity",
        type=MemoryType.decision,
        created_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
        title="CLI maturity apply",
        summary="CLI maturity apply locator",
        confidence=0.85,
        refs={"files": ["docs/architecture.md"], "mems": ["mem-20260618-010101-source"]},
        support_count=4,
        gain_score=0.3,
        context_views={
            "locator": "CLI maturity apply locator",
            "overview": "CLI maturity apply overview",
        },
    )
    store.write(item, "body")

    result = runner.invoke(app, ["govern", "maturity", "--format", "json", "--apply"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["applied_items"] == 1
    updated, _ = store.get(item.id)
    assert updated.maturity == "consolidated"
    assert updated.abstraction == "L1"


def test_govern_maturity_ignores_default_enum_noop(
    tmp_brain_dir: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    store = ItemsStore(tmp_brain_dir / "items")
    (store.items_dir / "mem-20260618-140002-cli-default-enum.md").write_text(
        """---
id: mem-20260618-140002-cli-default-enum
type: fact
created_at: '2026-06-18T14:00:02+00:00'
title: CLI default enum no-op
summary: Low evidence item remains raw L0
confidence: 0.2
tags: []
sensitivity: internal
refs:
  files: []
  urls: []
  mems: []
  commits: []
retention:
  access_count: 0
  decay_class: fact
---

body
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["govern", "maturity", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["changed_items"] == 0
    assert payload["items"] == []
