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
        evidence=["retrieved_count=1", "SECRET_LINEAGE_GAP_EVIDENCE"],
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
    assert report["summary"]["agents"]["codex"]["loads"] == 1
    assert report["summary"]["agents"]["codex"]["maintain"] == 1
    assert report["summary"]["agents"]["codex"]["recall"] == 2
    assert report["summary"]["agents"]["claude_code"]["recall"] == 1
    assert report["summary"]["by_mode"]["maintain"] == 1
    assert report["summary"]["by_mode"]["recall"] == 3
    assert report["summary"]["by_kind"]["gap"] == 1
    assert any(event["kind"] == "write" for event in report["events"])
    assert any(event["kind"] == "load" and event["item_ids"] == [item.id] for event in report["events"])
    gap_event = next(event for event in report["events"] if event["kind"] == "gap")
    assert gap_event["evidence"] == ["retrieved_count=1"]
    assert "runtime/recall-gaps.jsonl" in gap_event["storage_reads"]
    assert "runtime/injection-cohorts.jsonl" not in gap_event["storage_reads"]
    injection_event = next(event for event in report["events"] if event["kind"] == "load")
    assert injection_event["method"] == (
        "Retriever.search -> InjectionGateway -> ContextFirewall -> ContextPack"
    )
    assert any("prompt-facing surface" in step for step in injection_event["trace_steps"])
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
    assert "SECRET_LINEAGE_GAP_EVIDENCE" not in serialized


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


def test_memory_lineage_names_the_mandatory_injection_gateway():
    from agent_brain.observability.data_flow import DataFlowEvent
    from agent_brain.product.memory_lineage import _method_for_data_flow

    event = DataFlowEvent(
        event_id="injection-gateway-contract",
        timestamp="2026-07-11T03:00:00+00:00",
        source="injection",
        stage="上下文注入",
        summary="inject one safe item",
    )

    assert _method_for_data_flow(event) == (
        "Retriever.search -> InjectionGateway -> ContextFirewall -> ContextPack"
    )


def test_memory_lineage_explains_gateway_steps_and_final_access_accounting():
    from agent_brain.observability.data_flow import DataFlowEvent
    from agent_brain.product.memory_lineage import (
        _lifecycle,
        _retrieval_pipeline,
        _steps_for_data_flow,
    )

    event = DataFlowEvent(
        event_id="injection-gateway-steps",
        timestamp="2026-07-11T03:00:00+00:00",
        source="injection",
        stage="上下文注入",
        summary="inject one safe item",
    )
    steps = _steps_for_data_flow(event)

    assert (
        "候选统一进入 InjectionGateway；任何 prompt-facing surface 都不得直接打包 raw hit。"
        in steps
    )
    assert (
        "InjectionGateway 先调用 ContextFirewall 做主题、时间、敏感度、审核、废止、证据和 scope 门禁。"
        in steps
    )
    assert (
        "通过项再由 ContextPack 按预算选择 locator/overview/detail，正文按 detail_uri 延迟读取。"
        in steps
    )
    assert (
        "raw overfetch 不计访问；只有 Gateway 最终 included hits 在 prompt surface 输出前统一 record_accesses 一次。"
        in steps
    )
    assert "InjectionGateway" in next(
        row["detail"] for row in _lifecycle() if row["phase"] == "注入"
    )
    pipeline = _retrieval_pipeline()
    assert any(row["code"] == "InjectionGateway -> ContextFirewall" for row in pipeline)
    assert any(
        row["code"] == "ContextPack -> record_accesses -> prompt surface"
        for row in pipeline
    )


def test_memory_lineage_distinguishes_prompt_gap_privacy_from_explicit_diagnostics():
    from agent_brain.observability.data_flow import DataFlowEvent
    from agent_brain.product.memory_lineage import _steps_for_data_flow

    event = DataFlowEvent(
        event_id="recall-gap-privacy-contract",
        timestamp="2026-07-11T03:00:00+00:00",
        source="recall_gap",
        stage="召回缺口",
        summary="aggregate prompt gap",
    )
    steps = _steps_for_data_flow(event)

    assert (
        "Prompt-facing recall-gap 只记录 query fingerprint 与 aggregate counts，不记录 rejected ID 或 id:reason。"
        in steps
    )
    assert (
        "底层显式 record_gap 仍可保留 rejected_ids/evidence 供诊断调用；这不是 prompt-facing 默认行为。"
        in steps
    )
    assert "记录被拒绝/未注入候选 id 和缺口原因。" not in steps


def test_memory_lineage_classifies_query_gate_and_post_retrieval_gaps_truthfully():
    from agent_brain.observability.data_flow import DataFlowEvent
    from agent_brain.product.memory_lineage import (
        _kind_for_data_flow,
        _method_for_data_flow,
        _mode_for_kind,
        _moment_for_data_flow,
        _reads_for_data_flow,
    )

    query_gate = DataFlowEvent(
        event_id="query-gate-gap",
        timestamp="2026-07-11T03:00:00+00:00",
        source="recall_gap",
        stage="召回诊断",
        summary="query gate blocked",
        metadata={"reason": "query_not_injectable"},
    )
    post_retrieval = DataFlowEvent(
        event_id="post-retrieval-gap",
        timestamp="2026-07-11T03:00:01+00:00",
        source="recall_gap",
        stage="召回诊断",
        summary="all candidates rejected",
        metadata={"reason": "all_candidates_rejected"},
    )

    assert _kind_for_data_flow(query_gate) == "gap"
    assert _mode_for_kind("gap") == "recall"
    assert _method_for_data_flow(query_gate) == "record_gap"
    assert _moment_for_data_flow(query_gate) == "检索前 query gate 判定不可注入时"
    assert _reads_for_data_flow(query_gate) == ("runtime/recall-gaps.jsonl",)
    assert _moment_for_data_flow(post_retrieval) == "检索与 InjectionGateway 后记录聚合诊断时"
    assert "runtime/recall-gaps.jsonl" in _reads_for_data_flow(post_retrieval)
    assert "runtime/injection-cohorts.jsonl" not in _reads_for_data_flow(post_retrieval)
    assert "index.db items_fts" in _reads_for_data_flow(post_retrieval)
