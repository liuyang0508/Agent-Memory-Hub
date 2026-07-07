"""Tests for the Web memory-lineage read model."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Refs


def _item(item_id: str = "mem-20260623-010203-lineage-demo") -> MemoryItem:
    return MemoryItem(
        id=item_id,
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        agent="codex",
        session="sess-lineage",
        project="agent-memory-hub",
        tags=["lineage"],
        title="Lineage demo",
        summary="Used to verify memory lineage reporting",
        refs=Refs(),
        confidence=0.82,
    )


def test_memory_lineage_report_connects_writes_loads_storage_and_formulas(tmp_path: Path):
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.memory.governance.recall_events import record_gap
    from agent_brain.memory.store.items_store import ItemsStore
    from agent_brain.memory.store.write_service import WriteService
    from agent_brain.product.memory_lineage import build_memory_lineage_report

    item = _item()
    WriteService(ItemsStore(tmp_path / "items"), brain_dir=tmp_path).write(
        item=item,
        body="secret raw body should not leak into lineage report",
        allow_unsafe=True,
    )
    record_injection_cohort(
        tmp_path,
        item_ids=[item.id],
        adapter="codex",
        session_id="sess-lineage",
        cwd="/repo",
        query="secret user query should not leak",
        source="search",
        pack_metrics={"context_pack_chars": 120, "detail_refs": 1},
    )
    record_injection_cohort(
        tmp_path,
        item_ids=[item.id],
        adapter="claude_code",
        session_id="sess-claude",
        cwd="/repo",
        query="same memory should be visible as cross-agent usage",
        source="search",
        pack_metrics={"context_pack_chars": 96, "detail_refs": 0},
    )
    record_gap(
        tmp_path,
        query="another raw query should not leak",
        reason="partial_candidates_rejected",
        injected_ids=[item.id],
        adapter="codex",
        session_id="sess-lineage",
        cwd="/repo",
    )

    report = build_memory_lineage_report(tmp_path, hours=72).to_dict()
    serialized = str(report)

    assert report["summary"]["storage_counts"]["items"] == 1
    assert report["summary"]["storage_counts"]["sources_writes"] == 1
    assert report["summary"]["storage_counts"]["resources"] == 1
    assert report["summary"]["storage_counts"]["extractions"] == 1
    assert report["summary"]["agents"]["codex"]["writes"] == 1
    assert report["summary"]["agents"]["codex"]["loads"] == 2
    assert report["summary"]["agents"]["codex"]["maintain"] == 1
    assert report["summary"]["agents"]["codex"]["recall"] == 2
    assert report["summary"]["agents"]["claude_code"]["recall"] == 1
    assert report["summary"]["by_mode"]["maintain"] == 1
    assert report["summary"]["by_mode"]["recall"] == 3
    assert any(event["kind"] == "write" for event in report["events"])
    assert any(event["kind"] == "load" and event["item_ids"] == [item.id] for event in report["events"])
    assert {event["mode"] for event in report["events"]}.issuperset({"maintain", "recall"})

    memory_rows = report["memory_activity"]
    assert len(memory_rows) == 1
    memory = memory_rows[0]
    assert memory["item_id"] == item.id
    assert memory["title"] == "Lineage demo"
    assert memory["mode_counts"] == {"maintain": 1, "recall": 3, "evolve": 0}
    assert {row["agent"] for row in memory["touched_by_agents"]} == {"codex", "claude_code"}
    assert next(row for row in memory["touched_by_agents"] if row["agent"] == "codex")["recall"] == 2
    assert len(memory["timeline"]) == 4
    assert "items/*.md" in memory["storage_targets"]
    assert "index.db items_fts" in memory["storage_reads"]

    agent_rows = report["agent_activity"]
    codex = next(row for row in agent_rows if row["agent"] == "codex")
    assert codex["memory_count"] == 1
    assert codex["mode_counts"]["maintain"] == 1
    assert codex["mode_counts"]["recall"] == 2
    assert {"items/*.md", "sources/writes/*.json", "resources/*.json", "extractions/*.json"}.issubset(
        set(next(event["storage_targets"] for event in report["events"] if event["kind"] == "write"))
    )
    formula_keys = {formula["key"] for formula in report["formulas"]}
    assert {
        "rrf",
        "retention",
        "decay_coefficient",
        "maturity_score",
        "hopfield",
        "context_views",
    }.issubset(formula_keys)
    assert "secret raw body" not in serialized
    assert "secret user query" not in serialized
    assert "another raw query" not in serialized


def test_memory_lineage_report_can_filter_by_agent(tmp_path: Path):
    from agent_brain.agent_integrations.runtime_events import record_runtime_event
    from agent_brain.product.memory_lineage import build_memory_lineage_report

    record_runtime_event(
        tmp_path,
        adapter="codex",
        event_name="UserPromptSubmit",
        session_id="sess-1",
        cwd="/repo",
    )
    record_runtime_event(
        tmp_path,
        adapter="claude_code",
        event_name="SessionStart",
        session_id="sess-2",
        cwd="/repo",
    )

    report = build_memory_lineage_report(tmp_path, hours=72, agent="codex").to_dict()

    assert report["filters"]["agent"] == "codex"
    assert report["summary"]["agents"] == {"codex": {"writes": 0, "loads": 0, "events": 1, "maintain": 1, "recall": 0, "evolve": 0}}
    assert {event["agent"] for event in report["events"]} == {"codex"}


def test_memory_lineage_report_can_filter_by_mode_and_item(tmp_path: Path):
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.memory.store.items_store import ItemsStore
    from agent_brain.memory.store.write_service import WriteService
    from agent_brain.product.memory_lineage import build_memory_lineage_report

    item = _item("mem-20260623-010203-focused")
    WriteService(ItemsStore(tmp_path / "items"), brain_dir=tmp_path).write(
        item=item,
        body="focused memory body",
        allow_unsafe=True,
    )
    record_injection_cohort(
        tmp_path,
        item_ids=[item.id],
        adapter="codex",
        session_id="sess-focused",
        cwd="/repo",
        query="focused query",
        source="search",
        pack_metrics={"context_pack_chars": 88},
    )

    recall_report = build_memory_lineage_report(tmp_path, hours=72, mode="recall").to_dict()
    assert recall_report["filters"]["mode"] == "recall"
    assert {event["mode"] for event in recall_report["events"]} == {"recall"}
    assert recall_report["memory_activity"][0]["mode_counts"] == {"maintain": 0, "recall": 1, "evolve": 0}

    item_report = build_memory_lineage_report(tmp_path, hours=72, item_id=item.id).to_dict()
    assert item_report["filters"]["item_id"] == item.id
    assert {event["item_ids"][0] for event in item_report["events"] if event["item_ids"]} == {item.id}
