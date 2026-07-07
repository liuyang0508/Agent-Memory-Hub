"""Explainable memory lineage read model for Web.

This module is intentionally observational. It joins existing source ledgers,
runtime sidecars, and item metadata into a human-readable view of how memory is
written, retrieved, scored, loaded, and injected. It never exposes raw prompt,
query, or memory body text.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from agent_brain.contracts.memory_item import DECAY_HALF_LIFE_DAYS
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.observability.data_flow import DataFlowEvent, DataFlowLedger


MAX_WINDOW_HOURS = 72
MAX_EVENTS = 500
LINEAGE_MODES = ("maintain", "recall", "evolve")


@dataclass(frozen=True)
class MemoryLineageEvent:
    event_id: str
    timestamp: str
    agent: str
    kind: str
    mode: str
    stage: str
    moment: str
    method: str
    summary: str
    status: str = "observed"
    session_id: str | None = None
    item_ids: tuple[str, ...] = ()
    storage_targets: tuple[str, ...] = ()
    storage_reads: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    trace_steps: tuple[str, ...] = ()
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["item_ids"] = list(self.item_ids)
        data["storage_targets"] = list(self.storage_targets)
        data["storage_reads"] = list(self.storage_reads)
        data["evidence"] = list(self.evidence)
        data["trace_steps"] = list(self.trace_steps)
        return data


@dataclass(frozen=True)
class MemoryLineageFormula:
    key: str
    name: str
    formula: str
    where: str
    meaning: str
    variables: tuple[str, ...]
    caveat: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["variables"] = list(self.variables)
        return data


@dataclass(frozen=True)
class MemoryLineageReport:
    filters: dict[str, Any]
    summary: dict[str, Any]
    agent_activity: tuple[dict[str, Any], ...]
    memory_activity: tuple[dict[str, Any], ...]
    storage_media: tuple[dict[str, Any], ...]
    lifecycle: tuple[dict[str, Any], ...]
    retrieval_pipeline: tuple[dict[str, Any], ...]
    formulas: tuple[MemoryLineageFormula, ...]
    events: tuple[MemoryLineageEvent, ...]
    boundaries: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "filters": self.filters,
            "summary": self.summary,
            "agent_activity": list(self.agent_activity),
            "memory_activity": list(self.memory_activity),
            "storage_media": list(self.storage_media),
            "lifecycle": list(self.lifecycle),
            "retrieval_pipeline": list(self.retrieval_pipeline),
            "formulas": [formula.to_dict() for formula in self.formulas],
            "events": [event.to_dict() for event in self.events],
            "boundaries": list(self.boundaries),
        }


def build_memory_lineage_report(
    brain_dir: Path,
    *,
    hours: int = MAX_WINDOW_HOURS,
    agent: str | None = None,
    mode: str | None = None,
    item_id: str | None = None,
    limit: int = 200,
) -> MemoryLineageReport:
    """Build an explainable, Web-safe memory lineage report."""

    brain = Path(brain_dir)
    window_hours = _bounded_hours(hours)
    max_events = _bounded_limit(limit)
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=window_hours)
    write_events = _write_events(brain, start=start, end=now)
    data_events = _runtime_events(brain, since_hours=window_hours)
    events = [*write_events, *data_events]
    if agent:
        events = [event for event in events if event.agent == agent]
    selected_mode = _bounded_mode(mode)
    if selected_mode:
        events = [event for event in events if event.mode == selected_mode]
    if item_id:
        events = [event for event in events if item_id in event.item_ids]
    events.sort(key=lambda event: _parse_timestamp(event.timestamp) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    events = events[:max_events]
    storage_counts = _storage_counts(brain)
    items_by_id = _items_by_id(brain)
    agents = _agent_summary(events)
    return MemoryLineageReport(
        filters={"hours": window_hours, "agent": agent, "mode": selected_mode, "item_id": item_id, "limit": max_events},
        summary={
            "window_hours": window_hours,
            "total_events": len(events),
            "agents": agents,
            "by_kind": dict(Counter(event.kind for event in events)),
            "by_mode": _mode_counts(events),
            "by_stage": dict(Counter(event.stage for event in events)),
            "last_event_at": events[0].timestamp if events else None,
            "storage_counts": storage_counts,
            "item_counts": _item_counts(brain),
        },
        agent_activity=tuple(_agent_activity(events)),
        memory_activity=tuple(_memory_activity(events, items_by_id)),
        storage_media=tuple(_storage_media(storage_counts)),
        lifecycle=tuple(_lifecycle()),
        retrieval_pipeline=tuple(_retrieval_pipeline()),
        formulas=tuple(_formulas()),
        events=tuple(events),
        boundaries=(
            "Web 只展示脱敏 query hash、item id、标题/摘要级元数据和 sidecar 路径，不展示原始 prompt、query 或 body。",
            "items/*.md 是长期记忆事实源；index.db、resources、extractions、runtime jsonl 都是可重建或派生证据视图。",
            "Hopfield、MMR、graph expansion 是可选增强；默认链路是否启用取决于 Retriever 配置和质量门禁。",
            "locator/overview/detail 是 MemoryItem schema 的加载视图；写入时补默认值，召回注入时按成熟度、证据形状、防火墙动作和预算选择。",
        ),
    )


def _write_events(brain: Path, *, start: datetime, end: datetime) -> list[MemoryLineageEvent]:
    events: list[MemoryLineageEvent] = []
    for path in sorted((brain / "sources" / "writes").glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        timestamp = str(data.get("created_at") or "")
        parsed = _parse_timestamp(timestamp)
        if parsed is None or not (start <= parsed <= end):
            continue
        refs = data.get("refs") if isinstance(data.get("refs"), dict) else {}
        resources = list(refs.get("resources") or [])
        extractions = list(refs.get("extractions") or [])
        files = list(refs.get("files") or [])
        targets = ["items/*.md", "sources/writes/*.json"]
        if resources:
            targets.append("resources/*.json")
        if extractions:
            targets.append("extractions/*.json")
        targets.append("index.db(meta/FTS/vector/refs_graph) best-effort")
        events.append(
            MemoryLineageEvent(
                event_id=f"write-{data.get('item_id') or path.stem}",
                timestamp=timestamp,
                agent=str(data.get("agent") or "unknown"),
                session_id=data.get("session"),
                kind="write",
                mode="maintain",
                stage="写入维护",
                moment="主动写入 / hook 候选批准 / MCP 或 SDK 写入",
                method=str(data.get("writer") or "WriteService"),
                summary=f"{data.get('type', 'memory')} · {data.get('title', data.get('item_id', path.stem))}",
                status="written",
                item_ids=(str(data.get("item_id") or path.stem),),
                storage_targets=tuple(targets),
                evidence=(str(path),),
                trace_steps=(
                    "入口归一为 MemoryItem + Markdown body。",
                    "audit_memory_text fail-close；field_enrichment / quality_warnings 补治理字段。",
                    "ItemsStore.write 写入 items/<id>.md，作为唯一长期事实源。",
                    "WriteService 写 sources/writes/<id>.json，记录 writer、agent、session、refs、validity、body_sha256。",
                    "refs.files 或 write-input 生成 resources/extractions sidecar；多模态 placeholder 不被伪装成文本证据。",
                    "HubIndex upsert 投影 meta/FTS/vector/refs_graph；失败只标 dirty，不撤销 Markdown 写入。",
                ),
                metrics={
                    "body_size_bytes": data.get("body_size_bytes"),
                    "has_body_sha256": bool(data.get("body_sha256")),
                    "refs": {
                        "files": len(files),
                        "resources": len(resources),
                        "extractions": len(extractions),
                        "mems": len(refs.get("mems") or []),
                        "urls": len(refs.get("urls") or []),
                        "commits": len(refs.get("commits") or []),
                    },
                    "sensitivity": data.get("sensitivity"),
                    "source_kind": data.get("source_kind"),
                },
            )
        )
    return events


def _runtime_events(brain: Path, *, since_hours: int) -> list[MemoryLineageEvent]:
    ledger = DataFlowLedger(brain)
    events = ledger.list_events(since_hours=since_hours, limit=MAX_EVENTS)
    return [_from_data_flow(event) for event in events]


def _from_data_flow(event: DataFlowEvent) -> MemoryLineageEvent:
    kind = _kind_for_data_flow(event)
    mode = _mode_for_kind(kind)
    return MemoryLineageEvent(
        event_id=event.event_id,
        timestamp=event.timestamp,
        agent=event.adapter or str(event.metadata.get("actor") or "unknown"),
        session_id=event.session_id,
        kind=kind,
        mode=mode,
        stage=event.stage,
        moment=_moment_for_data_flow(event),
        method=_method_for_data_flow(event),
        summary=event.summary,
        status=event.status,
        item_ids=tuple(event.item_ids),
        storage_reads=_reads_for_data_flow(event),
        evidence=tuple(event.evidence),
        trace_steps=_steps_for_data_flow(event),
        metrics=event.metadata,
    )


def _kind_for_data_flow(event: DataFlowEvent) -> str:
    return {
        "adapter_runtime": "trigger",
        "adapter_verification": "verification",
        "loop": "governance",
        "recall_gap": "load",
        "task_outcome": "feedback",
        "injection": "load",
    }.get(event.source, event.source)


def _mode_for_kind(kind: str) -> str:
    if kind == "load":
        return "recall"
    if kind in {"governance", "feedback", "verification"}:
        return "evolve"
    return "maintain"


def _moment_for_data_flow(event: DataFlowEvent) -> str:
    if event.source == "adapter_runtime":
        return "SessionStart / UserPromptSubmit / Stop / Compact / 子智能体钩子"
    if event.source == "injection":
        return "查询后、上下文注入前"
    if event.source == "recall_gap":
        return "候选召回后、防火墙/人工反馈发现缺口时"
    if event.source == "task_outcome":
        return "任务结束或用户反馈后"
    if event.source == "adapter_verification":
        return "install / doctor / runtime evidence 验证后"
    if event.source == "loop":
        return "Loop checkpoint / 自进化治理事件"
    return "运行时观测"


def _method_for_data_flow(event: DataFlowEvent) -> str:
    if event.source == "injection":
        return "Retriever.search -> ContextFirewall -> context_pack"
    if event.source == "recall_gap":
        return "record_gap"
    if event.source == "task_outcome":
        return "record_task_outcome"
    if event.source == "adapter_runtime":
        return str(event.metadata.get("source") or "hook")
    if event.source == "adapter_verification":
        return str(event.metadata.get("verifier") or "adapter verifier")
    return event.source


def _reads_for_data_flow(event: DataFlowEvent) -> tuple[str, ...]:
    if event.source in {"injection", "recall_gap", "task_outcome"}:
        return (
            "index.db items_meta",
            "index.db items_fts",
            "index.db items_vec",
            "items/*.md context_views",
            "runtime/injection-cohorts.jsonl",
        )
    if event.source == "adapter_runtime":
        return ("runtime/adapter-events.jsonl",)
    if event.source == "adapter_verification":
        return ("runtime/adapter-verifications.jsonl",)
    return ("runtime/*.jsonl",)


def _steps_for_data_flow(event: DataFlowEvent) -> tuple[str, ...]:
    if event.source == "injection":
        return (
            "用户问题进入 SearchFilter，按 type/project/tags/tenant/since_days 先做 metadata 过滤。",
            "BM25 与向量检索各取候选，RRF 融合初始分。",
            "候选经过 rerank、遗忘曲线衰减、反馈价值、runtime evidence、stale/supersession 过滤。",
            "可选 MMR / Hopfield / refs_graph 扩展相关但第一跳没命中的记忆。",
            "ContextFirewall 按主题、时间、置信度、证据边界和预算形成 context_pack。",
            "context_loading 按 maturity/abstraction、类型、refs、validity 和 firewall 决策选择 locator/overview/detail。",
            "注入时默认给 locator/overview 和 detail_uri；正文按 detail_uri 延迟读取。",
        )
    if event.source == "recall_gap":
        return (
            "记录被拒绝/未注入候选 id 和缺口原因。",
            "不保存原始 query 到 Web read model；只暴露 has_query 或 query_sha256。",
            "后续可转化为 benchmark case、候选记忆或防火墙调优建议。",
        )
    if event.source == "adapter_runtime":
        return (
            "adapter hook 只记录事件名、adapter、cwd、session。",
            "不记录 prompt/body/tool arguments。",
        )
    return ("读取对应 runtime sidecar，汇总为脱敏事件。",)


def _storage_counts(brain: Path) -> dict[str, int]:
    return {
        "items": _count_files(brain / "items", "*.md"),
        "sources_conversations": _count_files(brain / "sources" / "conversations", "messages.jsonl"),
        "sources_writes": _count_files(brain / "sources" / "writes", "*.json"),
        "resources": _count_files(brain / "resources", "*.json"),
        "extractions": _count_files(brain / "extractions", "*.json"),
        "runtime_jsonl": _count_files(brain / "runtime", "*.jsonl"),
        "index_db": 1 if (brain / "index.db").exists() else 0,
    }


def _item_counts(brain: Path) -> dict[str, dict[str, int]]:
    store = ItemsStore(brain / "items")
    by_type: Counter[str] = Counter()
    by_maturity: Counter[str] = Counter()
    by_agent: Counter[str] = Counter()
    for item, _body in store.iter_all():
        by_type[str(item.type)] += 1
        by_maturity[str(item.maturity)] += 1
        by_agent[str(item.agent or "unknown")] += 1
    return {
        "by_type": dict(by_type),
        "by_maturity": dict(by_maturity),
        "by_agent": dict(by_agent),
        "skipped": {"count": store.last_scan.skipped_count},
    }


def _items_by_id(brain: Path) -> dict[str, Any]:
    store = ItemsStore(brain / "items")
    return {item.id: item for item, _body in store.iter_all()}


def _agent_summary(events: Iterable[MemoryLineageEvent]) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = defaultdict(
        lambda: {"writes": 0, "loads": 0, "events": 0, "maintain": 0, "recall": 0, "evolve": 0}
    )
    for event in events:
        row = summary[event.agent]
        row["events"] += 1
        row[event.mode] += 1
        if event.kind == "write":
            row["writes"] += 1
        if event.kind == "load":
            row["loads"] += 1
    return dict(summary)


def _agent_activity(events: list[MemoryLineageEvent]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for event in events:
        row = grouped.setdefault(
            event.agent,
            {
                "agent": event.agent,
                "events": 0,
                "writes": 0,
                "loads": 0,
                "mode_counts": _empty_mode_counts(),
                "memory_ids": set(),
                "last_seen_at": None,
            },
        )
        row["events"] += 1
        row["mode_counts"][event.mode] += 1
        if event.kind == "write":
            row["writes"] += 1
        if event.kind == "load":
            row["loads"] += 1
        row["memory_ids"].update(event.item_ids)
        if row["last_seen_at"] is None or event.timestamp > row["last_seen_at"]:
            row["last_seen_at"] = event.timestamp

    rows: list[dict[str, Any]] = []
    for row in grouped.values():
        memory_ids = sorted(row.pop("memory_ids"))
        row["memory_count"] = len(memory_ids)
        row["sample_memory_ids"] = memory_ids[:5]
        rows.append(row)
    rows.sort(key=lambda row: (row["events"], row["last_seen_at"] or ""), reverse=True)
    return rows


def _memory_activity(events: list[MemoryLineageEvent], items_by_id: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for event in events:
        for current_id in event.item_ids:
            item = items_by_id.get(current_id)
            row = grouped.setdefault(
                current_id,
                {
                    "item_id": current_id,
                    "title": _item_attr(item, "title", current_id),
                    "summary": _item_attr(item, "summary", ""),
                    "type": str(_item_attr(item, "type", "unknown")),
                    "maturity": str(_item_attr(item, "maturity", "unknown")),
                    "owning_agent": _item_attr(item, "agent", None),
                    "project": _item_attr(item, "project", None),
                    "tags": list(_item_attr(item, "tags", []) or []),
                    "mode_counts": _empty_mode_counts(),
                    "total_events": 0,
                    "first_seen_at": None,
                    "last_seen_at": None,
                    "storage_targets": set(),
                    "storage_reads": set(),
                    "evidence": set(),
                    "timeline": [],
                    "_agents": {},
                },
            )
            row["total_events"] += 1
            row["mode_counts"][event.mode] += 1
            row["storage_targets"].update(event.storage_targets)
            row["storage_reads"].update(event.storage_reads)
            row["evidence"].update(event.evidence)
            row["timeline"].append(event.to_dict())
            if row["first_seen_at"] is None or event.timestamp < row["first_seen_at"]:
                row["first_seen_at"] = event.timestamp
            if row["last_seen_at"] is None or event.timestamp > row["last_seen_at"]:
                row["last_seen_at"] = event.timestamp
            agent_row = row["_agents"].setdefault(
                event.agent,
                {"agent": event.agent, "maintain": 0, "recall": 0, "evolve": 0, "events": 0, "last_seen_at": None},
            )
            agent_row[event.mode] += 1
            agent_row["events"] += 1
            if agent_row["last_seen_at"] is None or event.timestamp > agent_row["last_seen_at"]:
                agent_row["last_seen_at"] = event.timestamp

    rows: list[dict[str, Any]] = []
    for row in grouped.values():
        touched = list(row.pop("_agents").values())
        touched.sort(key=lambda agent_row: (agent_row["events"], agent_row["last_seen_at"] or ""), reverse=True)
        row["touched_by_agents"] = touched
        row["storage_targets"] = sorted(row["storage_targets"])
        row["storage_reads"] = sorted(row["storage_reads"])
        row["evidence"] = sorted(row["evidence"])[:12]
        row["timeline"].sort(key=lambda event: event["timestamp"], reverse=True)
        rows.append(row)
    rows.sort(key=lambda row: (row["total_events"], row["last_seen_at"] or ""), reverse=True)
    return rows


def _item_attr(item: Any, name: str, default: Any) -> Any:
    if item is None:
        return default
    return getattr(item, name, default)


def _empty_mode_counts() -> dict[str, int]:
    return {mode: 0 for mode in LINEAGE_MODES}


def _mode_counts(events: Iterable[MemoryLineageEvent]) -> dict[str, int]:
    counts = _empty_mode_counts()
    for event in events:
        counts[event.mode] += 1
    return counts


def _storage_media(counts: dict[str, int]) -> list[dict[str, Any]]:
    return [
        {
            "name": "items/*.md",
            "role": "长期记忆事实源",
            "stores": "MemoryItem frontmatter + Markdown body + context_views(locator/overview/detail_uri)",
            "written_by": "WriteService -> ItemsStore.write",
            "read_by": "ItemsStore / reindex / context loading / detail_uri",
            "count": counts["items"],
        },
        {
            "name": "sources/conversations/*/messages.jsonl",
            "role": "原始对话证据源",
            "stores": "按 conversation/session 分片的原始消息流；不等于长期记忆",
            "written_by": "conversation capture / import / hook capture",
            "read_by": "harvester / audit / evidence review",
            "count": counts["sources_conversations"],
        },
        {
            "name": "sources/writes/*.json",
            "role": "写入账本",
            "stores": "writer、agent、session、item_path、body_sha256、refs、validity",
            "written_by": "WriteService 成功写 items 后同步写",
            "read_by": "记忆链路追踪 / provenance diagnosis",
            "count": counts["sources_writes"],
        },
        {
            "name": "resources/*.json + extractions/*.json",
            "role": "证据 sidecar",
            "stores": "文件/网页/写入输入资源登记，以及文本/OCR/ASR/摘要抽取",
            "written_by": "ResourceStore / WriteService ref-file/write-input sidecar",
            "read_by": "证据追溯 / SDK include_resources / Web 诊断",
            "count": counts["resources"] + counts["extractions"],
        },
        {
            "name": "index.db",
            "role": "派生检索投影",
            "stores": "items_meta、items_fts、items_vec、refs_graph",
            "written_by": "HubIndex.upsert / reindex",
            "read_by": "Retriever BM25/vector/filter/graph lookup",
            "count": counts["index_db"],
        },
        {
            "name": "runtime/*.jsonl",
            "role": "运行时观测账本",
            "stores": "adapter events、injection cohorts、recall gaps、task outcomes、loop events",
            "written_by": "hooks / query injection / feedback / loop commands",
            "read_by": "DataFlowLedger / evolution_control / memory_lineage",
            "count": counts["runtime_jsonl"],
        },
    ]


def _lifecycle() -> list[dict[str, str]]:
    return [
        {"phase": "触发", "detail": "SessionStart/UserPromptSubmit/Stop/SubagentStart 等 hook、CLI、MCP、SDK 或 Web 主动写入。"},
        {"phase": "写入漏斗", "detail": "入口统一为 MemoryItem，过 audit gate、字段增强、质量 warning、边界隔离。"},
        {"phase": "事实落盘", "detail": "items/*.md 是唯一长期事实源；sources/writes 记录写入账本；resources/extractions 记录证据 sidecar。"},
        {"phase": "派生索引", "detail": "index.db 投影 meta/FTS/vector/refs_graph；失败进 dirty log，可重建。"},
        {"phase": "召回", "detail": "SearchFilter -> BM25/vector -> RRF -> rerank/decay/value/runtime/status -> MMR/Hopfield/graph。"},
        {"phase": "注入", "detail": "ContextFirewall 按 topic/temporal/confidence/evidence/budget 形成 context_pack；context_loading 选择 locator/overview/detail_uri。"},
        {"phase": "反馈与自进化", "detail": "task outcome、recall gap、loop/evolve 只生成建议或候选；成熟度升级、合并、归档仍需治理门禁。"},
    ]


def _retrieval_pipeline() -> list[dict[str, str]]:
    return [
        {"step": "1. query intent", "code": "user question -> SearchFilter", "detail": "用户问题不会直接倒灌上下文；先形成 type/project/tags/exclude_tags/since_days/tenant/superseded 等可解释过滤条件。"},
        {"step": "2. first-hop retrieval", "code": "HubIndex.bm25_search + vector_search", "detail": "全文检索和向量相似度并行；degraded embedder 时自动 BM25-only，避免伪语义污染。"},
        {"step": "3. fusion", "code": "rrf_fusion", "detail": "BM25 rank 和 vector rank 通过 Reciprocal Rank Fusion 合并，得到第一版候选分。"},
        {"step": "4. policy stages", "code": "Retriever._candidate_stages", "detail": "handoff supplement、cross encoder rerank、遗忘曲线衰减、feedback value、status/runtime boost、temporal/supersession filter。"},
        {"step": "5. maturity and firewall", "code": "ContextFirewall + context_loading", "detail": "成熟度不是预过滤主轴；raw/L0 无证据项会被防火墙降权，raw+直接证据可加载 detail，consolidated/skill 更适合 overview/detail。"},
        {"step": "6. associative expansion", "code": "MMR / Hopfield / refs_graph", "detail": "可选去冗余、连续 Hopfield attractor 联想、显式图谱扩展，必须受基准门禁约束。"},
        {"step": "7. context pack", "code": "ContextFirewall + context_views", "detail": "候选转成 locator/overview/detail_uri，按预算注入；正文按 detail_uri 延迟加载。"},
    ]


def _formulas() -> list[MemoryLineageFormula]:
    half_life = ", ".join(f"{k}={v}d" for k, v in sorted(DECAY_HALF_LIFE_DAYS.items()))
    return [
        MemoryLineageFormula(
            key="rrf",
            name="RRF 融合",
            formula="score = Σ_source weight_source / (rrf_k + rank_source)",
            where="agent_brain/memory/recall/retrieval_fusion.py",
            meaning="把全文检索和向量检索的排名融合成一个初始候选分。",
            variables=("rrf_k 默认 60", "bm25_weight", "vector_weight", "rank_source 从 1 开始"),
            caveat="它解释初始召回，不是最终注入分；后续还会 rerank/decay/filter。",
        ),
        MemoryLineageFormula(
            key="retention",
            name="遗忘曲线",
            formula="retention = 0.5 ** (days_since_reference / half_life(decay_class))",
            where="agent_brain/memory/recall/retrieval_decay.py",
            meaning=f"按记忆类型半衰期计算时间保留率；当前半衰期：{half_life}。",
            variables=("days_since_reference", "decay_class", "half_life"),
            caveat="last_accessed 优先，否则用 created_at；小于等于 0 天时 retention=1。",
        ),
        MemoryLineageFormula(
            key="decay_coefficient",
            name="衰减系数",
            formula="coef = retention * access_mul * support_mul * gain_mul * contradiction_mul, bounded [0.01, 1.35]",
            where="agent_brain/memory/recall/retrieval_decay.py",
            meaning="时间衰减是底座，访问、支持反馈、gain 可强化，矛盾反馈会削弱。",
            variables=("access_mul=1+min(0.35,log1p(access_count)*0.08)", "support_mul=1+min(0.18,support_count*0.03)", "gain_mul=1+clamp(gain_score*0.12,-0.15,0.15)", "contradiction_mul=1-min(0.45,contradict_count*0.08)"),
            caveat="最终候选分还会乘 confidence；衰减不能压过所有相关性信号。",
        ),
        MemoryLineageFormula(
            key="maturity_score",
            name="成熟度评分",
            formula="maturity_score = 0.28*source + 0.22*confidence + support + reuse + graph + validation + overview + gain - contradiction - stale",
            where="agent_brain/memory/governance/maturity_scoring.py",
            meaning="把证据完整度、置信度、复用、反馈、图谱引用、验证证据和 stale/矛盾惩罚合成治理建议。",
            variables=("raw/L0: score < 0.65", "consolidated/L1: score >= 0.65", "skill/L2: score >= 0.80 且 skill 或 L2", "l0_evidence_only_penalty=0.2"),
            caveat="这是自进化治理建议，不会在召回前硬过滤；注入阶段会结合防火墙与 context view 使用。",
        ),
        MemoryLineageFormula(
            key="hopfield",
            name="Hopfield 联想扩展",
            formula="weights=softmax(score_i); query=Σ weights_i·embedding_i; attractor=Hopfield.recall(query)",
            where="agent_brain/memory/recall/retrieval_hopfield.py",
            meaning="把第一跳候选向量当连续联想记忆，形成 attractor 后再找近邻补相关记忆。",
            variables=("candidate scores", "candidate embeddings", "beta 默认 8.0", "hopfield_top"),
            caveat="只有 Retriever(hopfield_expand=True) 时启用，且新增候选分受 max_score*0.85*similarity 约束。",
        ),
        MemoryLineageFormula(
            key="context_views",
            name="分层加载",
            formula="locator -> overview -> detail_uri(body)",
            where="agent_brain/contracts/memory_item.py + context loading",
            meaning="召回先给短 locator/overview，正文通过 detail_uri 延迟读取，减少 token 污染。",
            variables=("locator 默认 summary", "overview 可写入时显式提供", "detail_uri=memory://items/<id>/body"),
            caveat="不是写入时生成三份正文；是一个 schema 里的三层加载视图。",
        ),
    ]


def _count_files(root: Path, pattern: str) -> int:
    if not root.exists():
        return 0
    return sum(1 for _ in root.rglob(pattern))


def _bounded_hours(value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = MAX_WINDOW_HOURS
    return max(1, min(MAX_WINDOW_HOURS, parsed))


def _bounded_limit(value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 200
    return max(1, min(MAX_EVENTS, parsed))


def _bounded_mode(value: str | None) -> str | None:
    if not value:
        return None
    return value if value in LINEAGE_MODES else None


def _parse_timestamp(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


__all__ = [
    "MemoryLineageEvent",
    "MemoryLineageFormula",
    "MemoryLineageReport",
    "build_memory_lineage_report",
]
