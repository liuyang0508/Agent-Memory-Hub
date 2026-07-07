"""Public evaluation report renderer for AMH benchmark results."""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_brain.evaluation.system_benchmark import SystemBenchmarkReport


DEFAULT_TITLE = "Agent Memory Hub Evaluation Report"
DEFAULT_JSON_NAME = "amh-evaluation-report.json"
DEFAULT_MARKDOWN_NAME = "amh-evaluation-report.zh.md"
DEFAULT_HTML_NAME = "amh-evaluation-report.html"


@dataclass(frozen=True)
class ProfessionalEvaluationReport:
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return self.payload

    def to_markdown(self) -> str:
        report = self.payload["report"]
        system = self.payload["system_benchmark"]
        charts = {chart["id"]: chart for chart in self.payload["charts"]}
        adapter_matrix = self.payload["adapter_matrix"]
        benchmark_layers = self.payload["benchmark_layers"]
        lines = [
            "# 评测结果",
            "",
            f"**报告名称**：{report['title']}",
            f"**生成时间**：{report['generated_at']}",
            f"**数据来源**：`{report['source']}`",
            f"**结论**：{'PASS' if system['passed'] else 'FAIL'}，cases={system['metrics']['case_count']}，items={system['metrics']['items_indexed']}，top_k={system['metrics']['top_k']}。",
            "",
            "这份报告使用 AMH 自己的系统级 benchmark 数据生成，不是 OpenViking LOCOMO 横评，也不填充无法复核的第三方数字。它回答的是：当前这套 AMH 链路在本地样本上，能不能正确阻断弱意图、召回目标记忆、通过防火墙，并把上下文可逆地装进 ContextPack。",
            "",
            "## 核心指标快照",
            "",
            _markdown_table(["指标", "结果"], _core_metric_rows(system)),
            "",
            "## 完成率指标对比",
            "",
            _markdown_table(["指标", "完成率", "样本/口径", "来源"], [
                [
                    row["label"],
                    _fmt_pct(row["value"]),
                    row.get("count_label", "-"),
                    f"`{row['source']}`",
                ]
                for row in charts["completion_rate"]["data"]
            ]),
            "",
            "## Token 消耗对比",
            "",
            _markdown_table(["指标", "Token", "说明"], [
                [row["label"], str(row["value"]), row["description"]]
                for row in charts["token_cost"]["data"]
            ]),
            "",
            f"ContextPack 节省率：**{_fmt_pct(charts['token_cost']['summary']['savings_rate'])}**；压缩比：**{charts['token_cost']['summary']['compression_ratio']:.3f}**。",
            "",
            "## 治理与防火墙",
            "",
            _markdown_table(["指标", "值", "说明"], [
                [row["label"], str(row["value"]), row["description"]]
                for row in charts["governance"]["data"]
            ]),
            "",
            "## 评测口径分层",
            "",
            "报告 PASS 不等于 release gate PASS。公开报告只展示当前本地 system benchmark 的可复核结果；发布前仍要单独跑 release gate 和开放式相关性基准。",
            "",
            _markdown_table(["层", "作用", "状态来源"], [
                [row["name"], row["purpose"], f"`{row['status_source']}`"]
                for row in benchmark_layers
            ]),
            "",
            "## 外部横评状态",
            "",
            "AMH 已完成本地 system benchmark 和 MemoryData source-lock；MemoryData smoke/full 需要 `datasets`、`rank_bm25`、四类数据集和 OpenAI-compatible endpoint 就绪后才能写入外部横评结果。OpenViking 仍是设计参考，不作为 AMH 指标来源。",
            "",
            _markdown_table(["参考", "用途", "当前状态"], [
                ["OpenViking", "参考 context database、文件系统范式、L0/L1/L2 tiered context loading 和 retrieval trajectory 叙事。", "设计参考，不作为 AMH 评测结果来源。"],
                ["arXiv 2606.24775", "参考 agent-native memory evaluation 的系统拆分：表示/存储、抽取、检索/路由、维护。", "论文口径已引用，不替代 AMH 实测。"],
                ["OpenDataBox/MemoryData", "统一 MemoryAgentBench、LoCoMo、LongBench、MemBench 的外部 benchmark harness。", "source-lock 已完成；最新状态见 `docs/evaluation/latest-memory-benchmark-report.zh.md`。"],
            ]),
            "",
            "## 多 Agent 适配矩阵",
            "",
            f"当前矩阵读取的是 adapter capability 记录：total={adapter_matrix['summary']['total']}，ready={adapter_matrix['summary']['ready']}，verified={adapter_matrix['summary']['verified']}，runtime_observed={adapter_matrix['summary']['runtime_observed']}。",
            "",
            _markdown_table(["Agent", "状态", "证据等级", "运行观测", "接入模式", "阻塞项"], [
                [
                    row["name"],
                    row["status"],
                    row["support_level"],
                    "yes" if row["runtime_observed"] else "no",
                    ", ".join(row["integration_modes"]) or "-",
                    "; ".join(row["verification_blockers"]) or "-",
                ]
                for row in adapter_matrix["rows"]
            ]) if adapter_matrix["rows"] else "_本次未加载 adapter capability。_",
            "",
            "## 数据口径",
            "",
            "- Query Gate：只判断该不该进入搜索/注入，不代表最终一定注入。",
            "- Retrieval：使用当前 AMH system benchmark 的 BM25/vector/RRF/MMR/graph 配置和 deterministic hashing embedding。",
            "- Context Firewall：统计应注入样本的 include rate、应排除样本的 exclude rate，以及 ContextPack 可逆性。",
            "- Token：来自 benchmark case 里的 `full_tokens` 和 `packed_tokens`，是 AMH 本地打包预算口径，不是模型供应商计费账单。",
            "- Adapter：来自 `agent_brain.agent_integrations.capabilities`，表示安装/doctor/runtime evidence 状态，不等价于公开任务完成率。",
        ]
        if system["failures"]:
            lines.extend(["", "## 失败样本", "", *[f"- {failure}" for failure in system["failures"]]])
        return "\n".join(lines).rstrip() + "\n"

    def to_html(self) -> str:
        report = self.payload["report"]
        system = self.payload["system_benchmark"]
        charts = {chart["id"]: chart for chart in self.payload["charts"]}
        adapter_matrix = self.payload["adapter_matrix"]
        benchmark_layers = self.payload["benchmark_layers"]
        status = "PASS" if system["passed"] else "FAIL"
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_esc(report['title'])}</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #172033;
      --muted: #5e6a7d;
      --line: #d8dee8;
      --panel: #ffffff;
      --wash: #f6f8fb;
      --brand: #1f6feb;
      --good: #178a52;
      --warn: #b86b00;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      color: var(--ink);
      background: #eef2f7;
      line-height: 1.62;
    }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 48px 24px 72px; }}
    header {{
      border: 1px solid var(--line);
      background: linear-gradient(180deg, #fff 0%, #f8fbff 100%);
      padding: 34px;
      border-radius: 8px;
    }}
    h1 {{ margin: 0 0 12px; font-size: clamp(30px, 4vw, 52px); letter-spacing: 0; }}
    h2 {{ margin: 38px 0 16px; font-size: 24px; letter-spacing: 0; }}
    p {{ margin: 0 0 14px; color: var(--muted); }}
    .meta {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 22px; }}
    .chip {{
      border: 1px solid var(--line);
      background: #fff;
      border-radius: 999px;
      padding: 7px 12px;
      font-size: 13px;
      color: var(--muted);
    }}
    .chip strong {{ color: var(--ink); }}
    .grid {{ display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }}
    .card {{
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 8px;
      padding: 22px;
      min-width: 0;
    }}
    .metric {{ display: grid; gap: 10px; margin: 16px 0 18px; }}
    .metric-head {{ display: flex; justify-content: space-between; gap: 12px; align-items: baseline; }}
    .metric-label {{ font-weight: 650; }}
    .metric-value {{ font-variant-numeric: tabular-nums; color: var(--brand); font-weight: 700; }}
    .bar {{ height: 10px; background: #e7ecf4; border-radius: 999px; overflow: hidden; }}
    .bar > span {{ display: block; height: 100%; background: linear-gradient(90deg, #1f6feb, #178a52); border-radius: 999px; }}
    table {{ width: 100%; table-layout: fixed; border-collapse: collapse; background: #fff; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }}
    th, td {{ padding: 12px 14px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; overflow-wrap: anywhere; word-break: break-word; }}
    th {{ background: #f6f8fb; font-size: 13px; color: var(--muted); }}
    tr:last-child td {{ border-bottom: 0; }}
    code {{ background: #eef2f7; padding: 2px 5px; border-radius: 4px; }}
    ul {{ margin: 0; padding-left: 20px; color: var(--muted); }}
    .note {{ border-left: 4px solid var(--brand); padding-left: 14px; color: var(--muted); }}
    @media (max-width: 680px) {{
      main {{ padding: 24px 14px 48px; }}
      header, .card {{ padding: 18px; }}
      table {{ font-size: 12px; }}
      th, td {{ padding: 8px 6px; }}
    }}
  </style>
</head>
<body>
<main>
  <header>
    <h1>评测结果</h1>
    <p>{_esc(report['title'])} 使用 AMH 本地 system benchmark 生成。这里不复用 OpenViking 的 LOCOMO 数字，也不制造无法复核的横评结论。</p>
    <div class="meta">
      <span class="chip"><strong>{status}</strong></span>
      <span class="chip">cases <strong>{system['metrics']['case_count']}</strong></span>
      <span class="chip">items <strong>{system['metrics']['items_indexed']}</strong></span>
      <span class="chip">top_k <strong>{system['metrics']['top_k']}</strong></span>
      <span class="chip">generated <strong>{_esc(report['generated_at'])}</strong></span>
    </div>
  </header>

  <h2>核心指标快照</h2>
  <div class="card">
    {_simple_table(["指标", "结果"], _core_metric_rows(system))}
  </div>

  <h2>完成率指标对比</h2>
  <section class="grid">
    {_completion_cards(charts['completion_rate'])}
  </section>

  <h2>Token 消耗对比</h2>
  <section class="grid">
    {_token_cards(charts['token_cost'])}
  </section>

  <h2>治理与防火墙</h2>
  <section class="grid">
    {_governance_cards(charts['governance'])}
  </section>

  <h2>评测口径分层</h2>
  <div class="card">
    <p class="note">报告 PASS 不等于 release gate PASS。公开报告只展示当前本地 system benchmark 的可复核结果；发布前仍要单独跑 release gate 和开放式相关性基准。</p>
    {_benchmark_layer_table(benchmark_layers)}
  </div>

  <h2>外部横评状态</h2>
  <div class="card">
    <p class="note">AMH 已完成本地 system benchmark 和 MemoryData source-lock；MemoryData smoke/full 需要依赖、数据集和 OpenAI-compatible endpoint 就绪后才能写入外部横评结果。OpenViking 仍是设计参考，不作为 AMH 指标来源。</p>
    <ul>
      <li>OpenViking：参考 context database、文件系统范式、L0/L1/L2 tiered context loading 和 retrieval trajectory。</li>
      <li>arXiv 2606.24775：参考 agent-native memory evaluation 的表示/存储、抽取、检索/路由、维护四模块拆分。</li>
      <li>OpenDataBox/MemoryData：source-lock 已完成；最新状态见 docs/evaluation/latest-memory-benchmark-report.zh.md。</li>
    </ul>
  </div>

  <h2>多 Agent 适配矩阵</h2>
  {_adapter_table(adapter_matrix)}

  <h2>数据口径</h2>
  <div class="card">
    <p class="note">这份报告是 AMH 当前本地链路的可复核结果：Query Gate、Retrieval、Context Firewall、ContextPack 和 Adapter Capability 分开统计。</p>
    <ul>
      <li>完成率来自 system benchmark 的 block / inject / recall / firewall / pack 指标。</li>
      <li>Token 来自每个 case 的 full_tokens 与 packed_tokens，不是模型账单。</li>
      <li>Adapter 矩阵来自本机 capability evidence，不等价于公开 benchmark 完成率。</li>
      <li>新增数据类型、适配器或召回策略后必须重新生成报告。</li>
    </ul>
  </div>
</main>
</body>
</html>
"""


@dataclass(frozen=True)
class WrittenEvaluationReport:
    json_path: Path
    markdown_path: Path
    html_path: Path
    report: ProfessionalEvaluationReport

    def to_dict(self) -> dict[str, Any]:
        return {
            "paths": {
                "json": str(self.json_path),
                "markdown": str(self.markdown_path),
                "html": str(self.html_path),
            },
            "report": self.report.to_dict(),
        }


def build_professional_evaluation_report(
    system_report: SystemBenchmarkReport,
    *,
    generated_at: datetime | None = None,
    adapter_capabilities: list[object] | None = None,
    adapter_error: str | None = None,
    title: str = DEFAULT_TITLE,
) -> ProfessionalEvaluationReport:
    generated_at = generated_at or datetime.now(timezone.utc)
    metrics = system_report.metrics
    adapter_rows = [_normalize_adapter_row(row) for row in adapter_capabilities or []]
    payload: dict[str, Any] = {
        "report": {
            "title": title,
            "generated_at": generated_at.astimezone(timezone.utc).isoformat(),
            "source": "memory benchmark system",
            "source_note": "Generated from AMH local system benchmark and adapter capability records.",
        },
        "system_benchmark": _system_summary(system_report),
        "benchmark_layers": _benchmark_layers(),
        "charts": [
            _completion_chart(metrics),
            _token_cost_chart(system_report.cases),
            _governance_chart(metrics),
        ],
        "adapter_matrix": {
            "summary": _adapter_summary(adapter_rows),
            "rows": adapter_rows,
            "error": adapter_error,
        },
    }
    return ProfessionalEvaluationReport(payload)


def _benchmark_layers() -> list[dict[str, str]]:
    return [
        {
            "name": "public_report",
            "purpose": "开源展示当前本地可复核结果",
            "status_source": "system_benchmark.passed",
        },
        {
            "name": "system_benchmark",
            "purpose": "验证 query gate、retrieval、firewall、context pack 的端到端链路",
            "status_source": "memory benchmark system",
        },
        {
            "name": "release_gate",
            "purpose": "发布前质量门禁，必须单独运行 benchmarks/release_gate.py",
            "status_source": "benchmarks/release_gate.py",
        },
        {
            "name": "relevance_benchmark",
            "purpose": "开放式相关性基准，衡量非精确标题/locator 查询表现",
            "status_source": "benchmarks/benchmark_relevance.py",
        },
        {
            "name": "memorydata_external_loop",
            "purpose": "外部横评 source-lock 与可执行入口；MemoryData smoke/full 需依赖、数据集和 endpoint 就绪",
            "status_source": "docs/evaluation/latest-memory-benchmark-report.zh.md",
        },
    ]


def write_professional_evaluation_report(
    output_dir: Path,
    system_report: SystemBenchmarkReport,
    *,
    generated_at: datetime | None = None,
    adapter_capabilities: list[object] | None = None,
    adapter_error: str | None = None,
    title: str = DEFAULT_TITLE,
) -> WrittenEvaluationReport:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report = build_professional_evaluation_report(
        system_report,
        generated_at=generated_at,
        adapter_capabilities=adapter_capabilities,
        adapter_error=adapter_error,
        title=title,
    )
    json_path = output_dir / DEFAULT_JSON_NAME
    markdown_path = output_dir / DEFAULT_MARKDOWN_NAME
    html_path = output_dir / DEFAULT_HTML_NAME
    json_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    markdown_path.write_text(report.to_markdown(), encoding="utf-8")
    html_path.write_text(report.to_html(), encoding="utf-8")
    return WrittenEvaluationReport(
        json_path=json_path,
        markdown_path=markdown_path,
        html_path=html_path,
        report=report,
    )


def load_adapter_capability_records(brain_dir: Path) -> tuple[list[dict[str, Any]], str | None]:
    try:
        from agent_brain.agent_integrations.capabilities import capabilities_for_all

        return [cap.to_dict() for cap in capabilities_for_all(brain_dir)], None
    except Exception as exc:  # pragma: no cover - defensive for partial adapter installs
        return [], f"{type(exc).__name__}: {exc}"


def _completion_chart(metrics: dict[str, Any]) -> dict[str, Any]:
    query_gate = metrics["query_gate"]
    retrieval = metrics["retrieval"]
    context = metrics["context"]
    top_k = metrics["top_k"]
    return {
        "id": "completion_rate",
        "title": "完成率指标对比",
        "unit": "%",
        "data": [
            {
                "label": "弱意图阻断",
                "value": _pct(query_gate["block_accuracy"]),
                "count_label": f"{query_gate['weak_block_cases']} weak prompts",
                "source": "metrics.query_gate.block_accuracy",
            },
            {
                "label": "可注入问题识别",
                "value": _pct(query_gate["inject_accuracy"]),
                "count_label": f"{query_gate['inject_cases']} injectable prompts",
                "source": "metrics.query_gate.inject_accuracy",
            },
            {
                "label": f"Recall@{top_k}",
                "value": _pct(retrieval["recall_at_k"]),
                "count_label": f"{retrieval['retrieval_cases']} retrieval cases",
                "source": "metrics.retrieval.recall_at_k",
            },
            {
                "label": "MRR",
                "value": _pct(retrieval["mrr"]),
                "count_label": "mean reciprocal rank",
                "source": "metrics.retrieval.mrr",
            },
            {
                "label": "Firewall include",
                "value": _pct(context["firewall_include_rate"]),
                "count_label": f"{context['firewall_include_expected_cases']} expected include",
                "source": "metrics.context.firewall_include_rate",
            },
            {
                "label": "ContextPack 可逆",
                "value": _pct(context["pack_reversible_rate"]),
                "count_label": f"{context['packed_cases']} packed cases",
                "source": "metrics.context.pack_reversible_rate",
            },
        ],
    }


def _system_summary(system_report: SystemBenchmarkReport) -> dict[str, Any]:
    category_counts: dict[str, int] = {}
    passed_by_category: dict[str, int] = {}
    for case in system_report.cases:
        category = str(case.get("category", "unknown"))
        category_counts[category] = category_counts.get(category, 0) + 1
        if case.get("passed"):
            passed_by_category[category] = passed_by_category.get(category, 0) + 1
    return {
        "passed": system_report.passed,
        "metrics": system_report.metrics,
        "failure_count": len(system_report.failures),
        "failures": list(system_report.failures),
        "case_summary": {
            "total": len(system_report.cases),
            "by_category": category_counts,
            "passed_by_category": passed_by_category,
        },
        "detail_note": (
            "Public report omits raw case traces and hit payloads. "
            "Use `memory benchmark system --output <path>` for full local diagnostics."
        ),
    }


def _token_cost_chart(cases: list[dict[str, Any]]) -> dict[str, Any]:
    full_tokens = 0
    packed_tokens = 0
    packed_cases = 0
    for case in cases:
        context_pack = case.get("stages", {}).get("context_pack", {})
        if context_pack.get("skipped"):
            continue
        full = int(context_pack.get("full_tokens") or 0)
        packed = int(context_pack.get("packed_tokens") or 0)
        if full <= 0 and packed <= 0:
            continue
        full_tokens += full
        packed_tokens += packed
        packed_cases += 1
    saved = max(0, full_tokens - packed_tokens)
    compression_ratio = round(packed_tokens / max(1, full_tokens), 6)
    savings_rate = _pct(saved / max(1, full_tokens))
    return {
        "id": "token_cost",
        "title": "Token 消耗对比",
        "unit": "tokens",
        "summary": {
            "packed_cases": packed_cases,
            "full_tokens": full_tokens,
            "packed_tokens": packed_tokens,
            "saved_tokens": saved,
            "compression_ratio": compression_ratio,
            "savings_rate": savings_rate,
        },
        "data": [
            {
                "label": "全文详情预算",
                "value": full_tokens,
                "description": "如果把命中 item 的详情全部放入上下文，benchmark 记录的 token 预算。",
            },
            {
                "label": "ContextPack 注入",
                "value": packed_tokens,
                "description": "经过 locator / overview / detail_uri 分层装载后的实际注入预算。",
            },
            {
                "label": "节省 Token",
                "value": saved,
                "description": "full_tokens - packed_tokens，负数按 0 处理。",
            },
        ],
    }


def _governance_chart(metrics: dict[str, Any]) -> dict[str, Any]:
    query_gate = metrics["query_gate"]
    context = metrics["context"]
    return {
        "id": "governance",
        "title": "治理与防火墙",
        "unit": "cases",
        "data": [
            {
                "label": "弱意图阻断样本",
                "value": query_gate["weak_block_cases"],
                "description": "继续、好的、确认等不应自动注入的 prompt。",
            },
            {
                "label": "Firewall 覆盖样本",
                "value": context["firewall_cases"],
                "description": "进入检索后接受防火墙决策的样本。",
            },
            {
                "label": "应注入样本",
                "value": context["firewall_include_expected_cases"],
                "description": "目标 item 应该进入 ContextPack 的样本。",
            },
            {
                "label": "应排除样本",
                "value": context["firewall_exclude_expected_cases"],
                "description": "低置信、缺来源、过期或敏感 item 应被挡下的样本。",
            },
            {
                "label": "排除正确率",
                "value": _pct(context["firewall_exclude_rate"]),
                "description": "应排除样本中，防火墙确实挡下目标 item 的比例。",
            },
        ],
    }


def _normalize_adapter_row(row: object) -> dict[str, Any]:
    if hasattr(row, "to_dict"):
        row = row.to_dict()
    data = dict(row)  # type: ignore[arg-type]
    return {
        "name": str(data.get("name", "")),
        "display_names": list(data.get("display_names", []) or []),
        "status": str(data.get("status", "")),
        "support_level": str(data.get("support_level", "")),
        "verified": bool(data.get("verified", False)),
        "runtime_observed": bool(data.get("runtime_observed", False)),
        "integration_modes": [str(value) for value in data.get("integration_modes", []) or []],
        "verification_blockers": [str(value) for value in data.get("verification_blockers", []) or []],
    }


def _adapter_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(rows),
        "ready": sum(1 for row in rows if row["status"] == "ready"),
        "verified": sum(1 for row in rows if row["verified"]),
        "runtime_observed": sum(1 for row in rows if row["runtime_observed"]),
    }


def _core_metric_rows(system: dict[str, Any]) -> list[list[str]]:
    metrics = system["metrics"]
    query_gate = metrics["query_gate"]
    retrieval = metrics["retrieval"]
    context = metrics["context"]
    return [
        ["总用例", str(metrics["case_count"])],
        ["失败数", str(system["failure_count"])],
        ["弱意图阻断", _fmt_pct(_pct(query_gate["block_accuracy"]))],
        ["可注入问题识别", _fmt_pct(_pct(query_gate["inject_accuracy"]))],
        [f"Recall@{metrics['top_k']}", _fmt_pct(_pct(retrieval["recall_at_k"]))],
        ["MRR", _fmt_pct(_pct(retrieval["mrr"]))],
        ["Firewall include", _fmt_pct(_pct(context["firewall_include_rate"]))],
        ["Firewall exclude", _fmt_pct(_pct(context.get("firewall_exclude_rate", 0.0)))],
        ["ContextPack 可逆", _fmt_pct(_pct(context["pack_reversible_rate"]))],
        ["top_k", str(metrics["top_k"])],
        ["indexed items", str(metrics["items_indexed"])],
        ["运行耗时", f"{float(metrics.get('total_time_s', 0.0)):.3f}s"],
    ]


def _simple_table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{_esc(header)}</th>" for header in headers)
    body = "\n".join(
        "<tr>" + "".join(f"<td>{_esc(cell)}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f"""<table>
    <thead><tr>{head}</tr></thead>
    <tbody>{body}</tbody>
  </table>"""


def _completion_cards(chart: dict[str, Any]) -> str:
    return "\n".join(
        f"""<article class="card metric">
      <div class="metric-head"><span class="metric-label">{_esc(row['label'])}</span><span class="metric-value">{_fmt_pct(row['value'])}</span></div>
      <div class="bar"><span style="width:{min(100, max(0, float(row['value']))):.2f}%"></span></div>
      <p>{_esc(row.get('count_label', ''))} · <code>{_esc(row['source'])}</code></p>
    </article>"""
        for row in chart["data"]
    )


def _token_cards(chart: dict[str, Any]) -> str:
    max_value = max([1, *[int(row["value"]) for row in chart["data"]]])
    cards = []
    for row in chart["data"]:
        value = int(row["value"])
        width = value / max_value * 100
        cards.append(
            f"""<article class="card metric">
      <div class="metric-head"><span class="metric-label">{_esc(row['label'])}</span><span class="metric-value">{value}</span></div>
      <div class="bar"><span style="width:{width:.2f}%"></span></div>
      <p>{_esc(row['description'])}</p>
    </article>"""
        )
    return "\n".join(cards)


def _governance_cards(chart: dict[str, Any]) -> str:
    return "\n".join(
        f"""<article class="card">
      <div class="metric-head"><span class="metric-label">{_esc(row['label'])}</span><span class="metric-value">{_esc(str(row['value']))}</span></div>
      <p>{_esc(row['description'])}</p>
    </article>"""
        for row in chart["data"]
    )


def _benchmark_layer_table(rows: list[dict[str, str]]) -> str:
    body = "\n".join(
        "<tr>"
        f"<td><code>{_esc(row['name'])}</code></td>"
        f"<td>{_esc(row['purpose'])}</td>"
        f"<td><code>{_esc(row['status_source'])}</code></td>"
        "</tr>"
        for row in rows
    )
    return f"""<table>
    <thead><tr><th>层</th><th>作用</th><th>状态来源</th></tr></thead>
    <tbody>{body}</tbody>
  </table>"""


def _adapter_table(adapter_matrix: dict[str, Any]) -> str:
    rows = adapter_matrix["rows"]
    if not rows:
        error = adapter_matrix.get("error")
        suffix = f" adapter load error: {_esc(error)}" if error else ""
        return f'<div class="card"><p>本次未加载 adapter capability。{suffix}</p></div>'
    body = "\n".join(
        "<tr>"
        f"<td>{_esc(row['name'])}</td>"
        f"<td>{_esc(row['status'])}</td>"
        f"<td>{_esc(row['support_level'])}</td>"
        f"<td>{'yes' if row['runtime_observed'] else 'no'}</td>"
        f"<td>{_esc(', '.join(row['integration_modes']) or '-')}</td>"
        f"<td>{_esc('; '.join(row['verification_blockers']) or '-')}</td>"
        "</tr>"
        for row in rows
    )
    return f"""<table>
    <thead><tr><th>Agent</th><th>状态</th><th>证据等级</th><th>运行观测</th><th>接入模式</th><th>阻塞项</th></tr></thead>
    <tbody>{body}</tbody>
  </table>"""


def _markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    header = "| " + " | ".join(headers) + " |"
    sep = "| " + " | ".join("---" for _ in headers) + " |"
    body = ["| " + " | ".join(_md_cell(cell) for cell in row) + " |" for row in rows]
    return "\n".join([header, sep, *body])


def _md_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _pct(value: object) -> float:
    return round(float(value) * 100.0, 2)


def _fmt_pct(value: object) -> str:
    return f"{float(value):.2f}%"


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


__all__ = [
    "ProfessionalEvaluationReport",
    "WrittenEvaluationReport",
    "build_professional_evaluation_report",
    "load_adapter_capability_records",
    "write_professional_evaluation_report",
]
