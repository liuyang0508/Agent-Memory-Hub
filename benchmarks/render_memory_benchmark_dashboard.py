#!/usr/bin/env python3
"""Render a visual phase-one memory benchmark dashboard from report JSON."""

from __future__ import annotations

import argparse
import base64
import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any


_MEMORYAGENTBENCH_FULL_RUNS = [
    {
        "id": "memoryagentbench-ar-eventqa",
        "dimension": "准确召回 AR",
        "config": "Accurate_Retrieval / EventQA full",
        "expected_rows": 500,
        "metrics": [
            ("精确匹配", "exact_match"),
            ("F1", "f1"),
            ("EventQA Recall", "eventqa_recall"),
        ],
    },
    {
        "id": "memoryagentbench-ttl-icl-banking77",
        "dimension": "测试时学习 TTL",
        "config": "Test_Time_Learning / ICL banking77",
        "expected_rows": 100,
        "metrics": [
            ("Exact Match", "exact_match"),
            ("Label Accuracy", "label_accuracy"),
            ("Label Format", "label_format_valid"),
        ],
    },
    {
        "id": "memoryagentbench-lru-detectiveqa",
        "dimension": "长程理解 LRU",
        "config": "Long_Range_Understanding / Detective_QA",
        "expected_rows": 71,
        "metrics": [
            ("Exact Match", "exact_match"),
            ("F1", "f1"),
            ("ROUGE-L Recall", "rougeL_recall"),
        ],
    },
    {
        "id": "memoryagentbench-cr-fact-mh-6k",
        "dimension": "冲突解决 CR",
        "config": "Conflict_Resolution / Factconsolidation mh 6k",
        "expected_rows": 100,
        "metrics": [
            ("Exact Match", "exact_match"),
            ("Answer Hit", "answer_hit"),
            ("Concise Response", "concise_response"),
        ],
    },
]

_AMH_BAR_COLOR = "#00a8a8"

_PAPER_METHOD_GROUPS = {
    "Long Context": "Reference Baselines",
    "Embed. RAG": "Reference Baselines",
    "MemAgent": "Sequential Context",
    "Mem0": "Sequential Context",
    "MemoChat": "Sequential Context",
    "Cognee": "Structural Topological",
    "Zep Local": "Structural Topological",
    "MemTree": "Structural Topological",
    "Letta": "Multi-Paradigm Hybrid",
    "LightMem": "Multi-Paradigm Hybrid",
    "SimpleMem": "Multi-Paradigm Hybrid",
    "MemOS": "Multi-Paradigm Hybrid",
    "MemoryOS": "Multi-Paradigm Hybrid",
    "A-MEM": "Multi-Paradigm Hybrid",
}

_PAPER_GROUP_COLORS = {
    "Reference Baselines": "#e46f52",
    "Sequential Context": "#2f3558",
    "Structural Topological": "#8dbda6",
    "Multi-Paradigm Hybrid": "#efc372",
    "AMH": _AMH_BAR_COLOR,
}

_PAPER_METHOD_ORDER = list(_PAPER_METHOD_GROUPS)

_PAPER_FIGURE_SERIES = {
    "longmemeval_substring_em": {
        "title": "(a) LongMemEval: Substring EM",
        "max_y": 35.0,
        "values": [7.7, 7.0, 11.1, 8.7, 7.7, 27.7, 29.7, 20.7, 14.7, 12.3, 7.3, 19.7, 28.3, 20.7],
    },
    "longmemeval_rouge_l_f1": {
        "title": "(b) LongMemEval: ROUGE-L F1",
        "max_y": 45.0,
        "values": [14.5, 13.7, 6.2, 15.5, 16.7, 35.3, 35.0, 29.0, 7.8, 21.9, 15.6, 28.6, 33.7, 22.8],
    },
    "longmemeval_rouge_l_recall": {
        "title": "(c) LongMemEval: ROUGE-L Recall",
        "max_y": 50.0,
        "values": [20.2, 19.3, 33.1, 18.8, 20.1, 39.8, 44.1, 33.5, 27.6, 26.1, 17.6, 34.9, 40.8, 35.9],
    },
    "longmemeval_judge_acc": {
        "title": "(d) LongMemEval: LLM Judge Acc.",
        "max_y": 60.0,
        "values": [19.0, 16.0, 3.7, 16.7, 14.7, 40.7, 48.0, 33.3, 23.0, 18.7, 17.3, 33.0, 39.3, 34.7],
    },
    "locomo_em": {
        "title": "(e) LoCoMo: EM",
        "max_y": 15.0,
        "values": [9.7, 3.0, 4.1, 5.1, 0.0, 9.8, 9.4, 8.6, 0.0, 9.3, 4.6, 11.6, 10.1, 5.7],
    },
    "locomo_answer_f1": {
        "title": "(f) LoCoMo: Answer F1",
        "max_y": 40.0,
        "values": [32.8, 13.0, 12.8, 21.5, 32.8, 26.2, 24.5, 5.3, 28.1, 14.7, 32.2, 29.2, 23.5, 23.5],
    },
    "db_bench_em": {
        "title": "(g) DB-Bench: EM",
        "max_y": 70.0,
        "values": [48.2, 45.4, 22.8, 36.8, 41.6, 27.6, 25.8, 34.4, 61.6, 34.4, 28.0, 42.0, 44.0, 43.8],
    },
    "db_bench_task_success": {
        "title": "(h) DB-Bench: Task Success Rate",
        "max_y": 70.0,
        "values": [48.2, 45.4, 22.9, 55.4, 0.0, 41.6, 27.6, 25.8, 61.6, 25.8, 28.1, 42.0, 44.0, 43.8],
    },
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render a Chinese visual HTML dashboard for the AMH memory benchmark report."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("docs/evaluation/memorydata-external-benchmark-report.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/evaluation/memory-benchmark-phase1-dashboard.html"),
    )
    args = parser.parse_args(argv)

    report = json.loads(args.input.read_text(encoding="utf-8"))
    html_text = render_dashboard(report, base_dir=args.input.resolve().parents[2])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html_text, encoding="utf-8")
    print(args.output)
    return 0


def render_dashboard(report: dict[str, Any], *, base_dir: Path) -> str:
    amh = report["amh_system_benchmark"]
    longmemeval = report["longmemeval_retrieval_loop"]
    prereqs = report["memorydata_prerequisites"]
    runs = report.get("memorydata_runs", [])
    run_metrics = [_run_card_payload(run) for run in runs]
    family_rows = _family_rows(report)

    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="zh-CN">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            "<title>AMH 记忆评测审计报告</title>",
            f"<style>{_css()}</style>",
            "</head>",
            "<body>",
            '<main class="page">',
            _hero(report),
            _executive_summary_section(report),
            _comparability_map_section(),
            _section(
                "评测门禁总览",
                "AMH 本地门禁、MemoryAgentBench 四维 full、MemoryData full-family 与 LongMemEval-S R@K full 均已落盘；B/C 档边界由 Dataset Provenance Audit 标注。",
                _metric_grid(
                    [
                        ("AMH 用例数", str(amh["case_count"]), "本地 system benchmark"),
                        ("Recall@10", _pct(amh["recall_at_k"]), "AMH 本地召回"),
                        ("MRR", _pct(amh["mrr"]), "AMH 本地召回"),
                        ("索引条目数", str(amh["items_indexed"]), "brain pool 快照"),
                    ]
                ),
            ),
            _longmemeval_section(longmemeval),
            _longmemeval_qa_judge_section(report.get("longmemeval_qa_judge_loop") or {}),
            _competitor_comparison_section(report, base_dir=base_dir),
            _memorydata_smoke_section(run_metrics, base_dir=base_dir),
            _memoryagentbench_full_section(report, base_dir=base_dir),
            _memorydata_full_family_section(report, base_dir=base_dir),
            _locomo_scope_section(report),
            _cost_stability_section(report),
            _dataset_provenance_section(report),
            _family_matrix_section(family_rows, prereqs),
            _section(
                "论文矩阵复现范围",
                "LoCoMo、LongBench、MemBench full-family 已有本机结果；派生数据集、子集和 InfBench judge 单独标注适用范围。",
                _boundary_panel(report),
            ),
            _data_boundary_next_steps_section(),
            "</main>",
            "</body>",
            "</html>",
        ]
    )


def _hero(report: dict[str, Any]) -> str:
    status = _esc(report["status"])
    return f"""
<section class="hero">
  <div>
    <p class="eyebrow">Agent Memory Hub / Benchmark Audit</p>
    <h1>AMH 记忆评测审计报告</h1>
    <p class="lede">面向复现、竞品对比和发布决策的专业评测报告。报告只展示已落盘 artifact、可追溯来源和明确缺口，不混用不同 benchmark 或未公开 runner 的数字。</p>
  </div>
  <div class="status-card">
    <span class="status-pill">{status}</span>
    <dl>
      <dt>运行模式</dt>
      <dd>{_esc(report["run_mode"])}</dd>
    </dl>
  </div>
</section>
"""


def _format_generated_at(value: object) -> str:
    raw = str(value or "")
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    suffix = " UTC" if dt.utcoffset() is not None and dt.utcoffset().total_seconds() == 0 else ""
    return dt.strftime("%Y-%m-%d %H:%M") + suffix


def _executive_summary_section(report: dict[str, Any]) -> str:
    longmemeval = report.get("longmemeval_retrieval_loop") or {}
    amh_report = longmemeval.get("amh_rk_full_report") or longmemeval.get("amh_ranking_report") or {}
    amh_metrics = amh_report.get("metrics") or {}
    full_family_count = len(report.get("memorydata_full_family_runs") or [])
    mab_count = len(report.get("memoryagentbench_full_runs") or [])
    body = f"""
<div class="executive-grid">
  <article class="finding finding-primary">
    <span>核心结论</span>
    <strong>AMH 已完成本机复现主线；跨来源竞品数字按证据级别分层呈现。</strong>
    <p>LongMemEval-S R@K、LongMemEval-S QA/Judge、LoCoMo、LongBench、MemBench、MemoryAgentBench 均有本机 artifact；DB-Bench 当前缺少开源 runner/data，标记为待补充项。</p>
  </article>
  <article class="finding">
    <span>本机复现范围</span>
    <strong>{full_family_count + mab_count} 组 full artifact</strong>
    <p>MemoryData full-family {full_family_count} 组，MemoryAgentBench 四维 full {mab_count} 组。</p>
  </article>
  <article class="finding">
    <span>LongMemEval Retrieval</span>
    <strong>R@5 {_pct1(_float(amh_metrics.get("recall_at_5")) * 100)}</strong>
    <p>同一 LongMemEval-S retrieval 口径；R@10 {_pct1(_float(amh_metrics.get("recall_at_10")) * 100)}，MRR {_pct1(_float(amh_metrics.get("mrr")) * 100)}。</p>
  </article>
  <article class="finding">
    <span>风险与缺口</span>
    <strong>DB-Bench 缺 runner/data</strong>
    <p>论文原图包含 DB-Bench；MemoryData 开源 cache 未提供 loader/config/dataset。</p>
  </article>
</div>
<div class="evidence-strip" aria-label="证据分层">
  <span class="strip-title">证据分层</span>
  <span><b>A</b> 本机复现</span>
  <span><b>B</b> 公开报告</span>
  <span><b>C</b> Vendor / self-reported</span>
  <span><b>D</b> 缺 runner/data</span>
</div>
"""
    return _section(
        "执行摘要",
        "先给结论，再给证据边界：哪些分数可以发布、哪些只能参考、哪些必须等 runner/data。",
        body,
    )


def _comparability_map_section() -> str:
    body = """
<div class="comparability-grid">
  <article>
    <span class="rank-mark rank-a">严格可比</span>
    <h3>同 runner / 同 dataset / 同 metric</h3>
    <p>AMH 接入 MemoryData 入口后的本机结果；可以和同 workload 下的论文方法做同口径横比。</p>
  </article>
  <article>
    <span class="rank-mark rank-b">参考横比</span>
    <h3>同 benchmark / 同 metric / 不同来源</h3>
    <p>例如 agentmemory LongMemEval R@5 comparison；可横向看，但证据级别低于本机统一 runner 复跑。</p>
  </article>
  <article>
    <span class="rank-mark rank-c">仅作背景</span>
    <h3>不同 benchmark 或 published score</h3>
    <p>例如 LoCoMo published score，仅作为背景分数，不纳入 LongMemEval-S R@5 排名。</p>
  </article>
  <article>
    <span class="rank-mark rank-d">缺 runner/data</span>
    <h3>论文有图，本地不可运行</h3>
    <p>DB-Bench 当前没有开源 loader/config/dataset；保留缺口说明，标记为待补充项。</p>
  </article>
</div>
"""
    return _section(
        "可比性地图",
        "把评测口径和证据来源分开，避免把同一 metric、同一 runner、不同 benchmark 混成一张榜。",
        body,
    )


def _data_boundary_next_steps_section() -> str:
    body = """
<div class="next-grid">
  <article>
    <h3>已复现</h3>
    <p>LongMemEval Retrieval、LongMemEval QA/Judge、LoCoMo、LongBench、MemBench、MemoryAgentBench 已有本机 artifact 和 HTML 汇总。</p>
  </article>
  <article>
    <h3>需要继续补齐</h3>
    <p>DB-Bench 需要官方或等价 runner/data/scorer；MemoryData 论文原图的竞品分数若要结构化排名，需要可机读来源或统一复跑产物。</p>
  </article>
  <article>
    <h3>发布边界</h3>
    <p>AMH 分数可发布为本机复现结果；竞品公开数字必须保留来源等级；DB-Bench 继续标缺口，不使用其他 benchmark 分数代替。</p>
  </article>
</div>
"""
    return _section(
        "数据边界与下一步",
        "最终报告应同时讲清楚成绩、可比性和剩余缺口，便于对外发布或继续补跑。",
        body,
    )


def _dataset_provenance_section(report: dict[str, Any]) -> str:
    audit = report.get("dataset_provenance_audit") or {}
    entries = audit.get("entries") or []
    counts = {tier: 0 for tier in ("A", "B", "C")}
    for entry in entries:
        tier = str(entry.get("tier", ""))
        if tier in counts:
            counts[tier] += 1
    gate = audit.get("next_stage_gate") or {"allowed": False, "required_actions": []}
    actions = gate.get("required_actions") or []
    body = _metric_grid(
        [
            ("A 档", str(counts["A"]), "论文/官方同源 full 可比"),
            ("B 档", str(counts["B"]), "官方同源但有派生/子集边界"),
            ("C 档", str(counts["C"]), "smoke / adapter 验证"),
            ("下一阶段门禁", "允许" if gate.get("allowed") else "暂缓", "先补齐 provenance 边界"),
        ]
    )
    if actions:
        body += (
            '<div class="audit-list">'
            + "".join(f"<p>{_esc(action)}</p>" for action in actions[:4])
            + "</div>"
        )
    return _section(
        "Dataset Provenance Audit",
        "把每个数据集和当前结果按 A/B/C 三档标注，避免把派生子集或 smoke 结果写成论文 full 成绩。",
        body,
    )


def _memoryagentbench_full_section(report: dict[str, Any], *, base_dir: Path) -> str:
    cards = ['<div class="chart-grid">']
    for spec in _MEMORYAGENTBENCH_FULL_RUNS:
        root = base_dir / "docs" / "evaluation" / "memorydata-artifacts" / "full" / spec["id"]
        result_path = _first_result_json(root)
        declared = _memoryagentbench_declared_run(report, str(spec["id"]))
        metrics: dict[str, Any] = {}
        rows = 0
        display_path = result_path
        if result_path is not None:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            metrics = payload.get("averaged_metrics") or {}
            rows = len(payload.get("data") or [])
        elif declared:
            metrics = declared.get("metrics") or {}
            rows = int(declared.get("rows") or 0)
            display_path = _path_or_none(declared.get("result_path"))
        status = "full 已完成" if rows == spec["expected_rows"] else "未完成"
        bars = "\n".join(
            _metric_bar(label, _float(metrics.get(metric_key)))
            for label, metric_key in spec["metrics"]
        )
        cards.append(
            '<article class="chart-card">'
            f'<div class="card-topline"><h3>{_esc(spec["dimension"])}</h3>'
            f'<span class="mini-pill">{_esc(status)}</span></div>'
            f'<p class="muted">{_esc(spec["config"])} / {rows} 条 query 记录'
            f'，预期 {spec["expected_rows"]} 条</p>'
            f"{bars}"
            f'<p class="path">{_esc(_display_path(display_path, base_dir=base_dir))}</p>'
            "</article>"
        )
    cards.append("</div>")
    callout = (
        '<p class="callout">适用范围：LRU 使用 Detective_QA exact_match 路径；'
        "InfBench summarization 需要按 HELMET 口径做 LLM-as-judge，未混入这四个核心 full 结果。</p>"
    )
    return _section(
        "MemoryAgentBench 四维 Full 结果",
        "使用本地 Ollama OpenAI-compatible endpoint 执行，不带 max_test_queries_ablation；每个结果都保留 raw JSON artifact。",
        "\n".join(cards) + callout,
    )


def _memoryagentbench_declared_run(report: dict[str, Any], run_id: str) -> dict[str, Any]:
    for row in report.get("memoryagentbench_full_runs") or []:
        if row.get("id") == run_id:
            return row
    return {}


def _memorydata_full_family_section(report: dict[str, Any], *, base_dir: Path) -> str:
    full_runs = report.get("memorydata_full_family_runs") or []
    cards = ['<div class="chart-grid">']
    for run in full_runs:
        metrics = run.get("metrics") or {}
        preferred = _preferred_metrics(str(run.get("family", "")), metrics)
        bars = "\n".join(_metric_bar(label, value) for label, value in preferred)
        cards.append(
            '<article class="chart-card">'
            f'<div class="card-topline"><h3>{_esc(run.get("name", ""))}</h3>'
            f'<span class="mini-pill">{_esc(_status_label(run.get("status")))}</span></div>'
            f'<p class="muted">{_esc(run.get("sample_scope", ""))} / { _esc(run.get("limitations", ""))}</p>'
            f"{bars}"
            f'<p class="path">{_esc(_display_path(_path_or_none(run.get("result_path")), base_dir=base_dir))}</p>'
            "</article>"
        )
    cards.append("</div>")
    return _section(
        "MemoryData Full-family 结果",
        "LoCoMo 4cat QA、LongBench rep150 proportional、MemBench FirstAgent 五个 slice 的本地 full-family artifact；可比性边界见 Dataset Provenance Audit。",
        "\n".join(cards),
    )


def _locomo_scope_section(report: dict[str, Any]) -> str:
    rows = [
        run for run in report.get("memorydata_full_family_runs") or []
        if str(run.get("family", "")).startswith("LoCoMo")
    ]
    table_rows = []
    for run in rows:
        metrics = run.get("metrics") or {}
        table_rows.append(
            "<tr>"
            f'<td><strong>{_esc(run.get("name", ""))}</strong></td>'
            f'<td>{_esc(run.get("sample_scope", ""))}</td>'
            f'<td>{_float(metrics.get("exact_match")):.2f}%</td>'
            f'<td>{_float(metrics.get("f1")):.2f}%</td>'
            f'<td>{_float(metrics.get("rougeL_recall")):.2f}%</td>'
            f'<td>{_esc(run.get("limitations", ""))}</td>'
            "</tr>"
        )
    table = (
        '<table class="matrix">'
        "<thead><tr><th>结果</th><th>样本范围</th><th>EM</th><th>F1</th><th>ROUGE-L Recall</th><th>口径边界</th></tr></thead>"
        f"<tbody>{''.join(table_rows)}</tbody></table>"
        '<p class="callout">LoCoMo 本节按 MemoryData 统一 runner 的 EM/F1/ROUGE 口径展示；'
        "Letta / Mem0 外部表格中的 judge accuracy 属于不同口径，单独作为参考。</p>"
    )
    return _section(
        "LoCoMo 分类口径",
        "把 category 1-4 QA 和 category 5 adversarial 分开发布，避免把不同问题类型合成一个不可解释百分比。",
        table,
    )


def _cost_stability_section(report: dict[str, Any]) -> str:
    rows = []
    for run in (report.get("memoryagentbench_full_runs") or []) + (report.get("memorydata_full_family_runs") or []):
        metrics = run.get("metrics") or {}
        name = run.get("dimension") or run.get("name") or run.get("id") or "-"
        sample_scope = run.get("sample_scope") or f"{run.get('rows', 0)} / {run.get('expected_rows', 0)}"
        rows.append(
            "<tr>"
            f"<td><strong>{_esc(name)}</strong></td>"
            f"<td>{_esc(sample_scope)}</td>"
            f"<td>{_float(metrics.get('query_time_len')):.2f}s</td>"
            f"<td>{_float(metrics.get('memory_construction_time')):.4f}s</td>"
            f"<td>{_float(metrics.get('input_len')):.0f}</td>"
            f"<td>{_float(metrics.get('output_len')):.1f}</td>"
            "</tr>"
        )
    table = (
        '<table class="matrix">'
        "<thead><tr><th>评测</th><th>样本</th><th>平均查询耗时</th><th>平均写入/构建耗时</th><th>平均输入 tokens</th><th>平均输出 tokens</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )
    return _section(
        "成本与稳定性",
        "同一批 artifact 中记录的平均耗时和 token 边界；用于解释准确率之外的工程成本。",
        table,
    )


def _longmemeval_section(loop: dict[str, Any]) -> str:
    smoke = loop.get("rk_full_report") or loop.get("smoke_report") or {}
    amh = loop.get("amh_rk_full_report") or loop.get("amh_ranking_report") or {}
    smoke_label = "词法 R@K full" if smoke.get("run_scope") == "full-rk" else "词法召回 smoke"
    amh_label = "AMH R@K full" if amh.get("run_scope") == "full-rk" else "AMH 排序 smoke"
    rows = [
        (smoke_label, smoke.get("metrics", {}), _case_count_label(smoke)),
        (amh_label, amh.get("metrics", {}), _case_count_label(amh)),
    ]
    body = [
        '<div class="chart-grid">',
    ]
    for label, metrics, case_count in rows:
        body.append(
            '<article class="chart-card">'
            f"<h3>{_esc(label)}</h3>"
            f'<p class="muted">{_esc(case_count)} / LongMemEval-S</p>'
            f'{_metric_bar("R@5", _float(metrics.get("recall_at_5")) * 100)}'
            f'{_metric_bar("R@10", _float(metrics.get("recall_at_10")) * 100)}'
            f'{_metric_bar("MRR", _float(metrics.get("mrr")) * 100)}'
            "</article>"
        )
    body.append("</div>")
    return _section(
        "LongMemEval-S 召回准确率",
        "覆盖 retrieval R@K；即使是 500-case full-rk，也不代表 generation / judge 全链路。",
        "\n".join(body),
    )


def _longmemeval_qa_judge_section(loop: dict[str, Any]) -> str:
    generation = loop.get("generation_report") or {}
    judge = loop.get("judge_report") or {}
    judge_summary = judge.get("summary") or {}
    generation_metrics = generation.get("metrics") or {}
    body = [
        '<div class="chart-grid">',
        '<article class="chart-card">',
        '<div class="card-topline"><h3>Answer Generation</h3>'
        f'<span class="mini-pill">{_esc(_status_label(generation.get("status", "missing")))}</span></div>',
        f'<p class="muted">{_esc(generation.get("rows", 0))} rows / LongMemEval-S QA</p>',
        _metric_bar("Exact Match", _float(generation_metrics.get("exact_match"))),
        _metric_bar("F1", _float(generation_metrics.get("f1"))),
        f'<p class="path">{_esc(generation.get("path") or "-")}</p>',
        "</article>",
        '<article class="chart-card">',
        '<div class="card-topline"><h3>LLM-as-Judge</h3>'
        f'<span class="mini-pill">{_esc("已通过" if judge_summary.get("judged_rows") else "缺失")}</span></div>',
        (
            f'<p class="muted">{_esc(judge_summary.get("judged_rows", 0))} / '
            f'{_esc(judge_summary.get("supported_rows", 0))} judged rows</p>'
        ),
        _metric_bar("Judge Accuracy", _float(judge_summary.get("judge_accuracy")) * 100),
        f'<p class="path">{_esc(judge.get("path") or "-")}</p>',
        "</article>",
        "</div>",
    ]
    return _section(
        "LongMemEval-S QA / Judge",
        "单独展示 answer generation 与 LLM-as-judge；不与 R@K-only retrieval 分数混写。",
        "\n".join(body),
    )


def _competitor_comparison_section(report: dict[str, Any], *, base_dir: Path) -> str:
    longmemeval = report.get("longmemeval_retrieval_loop") or {}
    amh_report = longmemeval.get("amh_rk_full_report") or longmemeval.get("amh_ranking_report") or {}
    amh_metrics = amh_report.get("metrics") or {}
    amh_r5 = _pct1(_float(amh_metrics.get("recall_at_5")) * 100)
    amh_r10 = _pct1(_float(amh_metrics.get("recall_at_10")) * 100)
    amh_mrr = _pct1(_float(amh_metrics.get("mrr")) * 100)
    amh_cases = _case_count_label(amh_report)

    longmemeval_rows = [
        {
            "system": "Agent Memory Hub (AMH, BM25/RRF)",
            "dataset": "LongMemEval-S",
            "score": amh_r5,
            "evidence": "A 本机复现",
            "evidence_class": "full",
            "source": (
                f"本机 {amh_cases} R@K-only run；R@10 {amh_r10}，MRR {amh_mrr}。"
                "Retrieval-only。"
            ),
        },
        {
            "system": "agentmemory (BM25 + Vector)",
            "dataset": "LongMemEval-S",
            "score": "95.2%",
            "evidence": "B 竞品公开报告",
            "evidence_class": "ready",
            "source": "agentmemory COMPARISON 发布值；all-MiniLM-L6-v2 embeddings，无 API key。",
        },
        {
            "system": "agentmemory (BM25-only)",
            "dataset": "LongMemEval-S",
            "score": "86.2%",
            "evidence": "B 竞品公开报告",
            "evidence_class": "ready",
            "source": "agentmemory COMPARISON 发布值；无 embedding provider 时的 fallback。",
        },
        {
            "system": "MemPalace",
            "dataset": "LongMemEval-S",
            "score": "~96.6%",
            "evidence": "C 竞品自报",
            "evidence_class": "smoke",
            "source": "Vendor-published / self-reported；未本机独立复现。",
        },
        {
            "system": "oracleagentmemory",
            "dataset": "LongMemEval",
            "score": "94.4%",
            "evidence": "C 竞品自报",
            "evidence_class": "smoke",
            "source": "Vendor-published / self-reported；使用 Oracle AI Database；未本机独立复现。",
        },
    ]
    locomo_rows = [
        {
            "system": "Letta / MemGPT",
            "benchmark": "LoCoMo",
            "score": "83.2%",
            "notes": "LoCoMo 公开分数；不同于 LongMemEval R@5。",
        },
        {
            "system": "Mem0",
            "benchmark": "LoCoMo",
            "score": "68.5%",
            "notes": "LoCoMo 公开分数；未在本轮 AMH runner 中复跑。",
        },
    ]

    def render_paper_methods_table() -> str:
        rendered_rows = []
        for row in _memorydata_paper_method_rows():
            rendered_rows.append(
                "<tr>"
                f'<td>{_esc(row["group"])}</td>'
                f'<td><strong>{_esc(row["method"])}</strong></td>'
                f'<td>{_esc(_paper_method_type(row["method"], row["group"]))}</td>'
                f'<td>{_esc(row.get("plain") or _paper_method_plain_text(row["method"], row["group"]))}</td>'
                f'<td><code>{_esc(row["preset"])}</code></td>'
                f'<td>{_esc(row["runtime"])}</td>'
                "</tr>"
            )
        return (
            '<table class="matrix">'
            "<thead><tr><th>论文分组</th><th>方法 / 竞品</th><th>类型</th><th>白话解释</th><th>Representative preset</th><th>Runtime entry</th></tr></thead>"
            f"<tbody>{''.join(rendered_rows)}</tbody></table>"
        )

    def render_longmemeval_table(table_rows: list[dict[str, str]]) -> str:
        rendered_rows = []
        for row in table_rows:
            rendered_rows.append(
                "<tr>"
                f'<td><strong>{_esc(row["system"])}</strong></td>'
                f'<td>{_esc(row["dataset"])}</td>'
                f'<td><strong>{_esc(row["score"])}</strong></td>'
                f'<td><span class="state state-{_esc(row["evidence_class"])}">{_esc(row["evidence"])}</span></td>'
                f'<td>{_esc(row["source"])}</td>'
                "</tr>"
            )
        return (
            '<table class="matrix">'
            "<thead><tr><th>System</th><th>Dataset</th><th>R@5</th><th>证据级别</th><th>数字来源</th></tr></thead>"
            f"<tbody>{''.join(rendered_rows)}</tbody></table>"
        )

    def render_reference_table(table_rows: list[dict[str, str]]) -> str:
        rendered_rows = []
        for row in table_rows:
            rendered_rows.append(
                "<tr>"
                f'<td><strong>{_esc(row["system"])}</strong></td>'
                f'<td>{_esc(row["benchmark"])}</td>'
                f'<td><strong>{_esc(row["score"])}</strong></td>'
                f'<td>{_esc(row["notes"])}</td>'
                "</tr>"
            )
        return (
            '<table class="matrix">'
            "<thead><tr><th>System</th><th>Benchmark</th><th>Published score</th><th>说明</th></tr></thead>"
            f"<tbody>{''.join(rendered_rows)}</tbody></table>"
        )

    table = "".join(
        [
            '<p class="callout"><strong>AMH 接入 MemoryData 的同一评测入口、数据配置和指标。</strong> '
            "AMH 是追加评测对象；agentmemory 的 LongMemEval COMPARISON 单独列为公开来源参考。</p>",
            '<h3 class="subhead">论文统一评测标准：MemoryData 方法表</h3>',
            '<p class="callout">这 22 个方法属于 MemoryData 论文/代码发布的同一套 launcher 和 benchmark family；'
            "AMH 行表示 AMH 接入同一个 MemoryData 评测入口；同一 workload 下可以使用同一 runner / dataset config / metric 比较。"
            "表中名称包含产品、框架、baseline、研究方法和设计范式；类型列用于区分层级。</p>",
            render_paper_methods_table(),
            '<p class="callout">AMH 是 MemoryData 追加评测对象，未列入论文原始 22 个方法。'
            "AMH 如需进入论文式排名，需要在同一 MemoryData 评测入口下复跑对应 workload。</p>",
            _memorydata_paper_score_figure(base_dir),
            _memorydata_paper_style_amh_bars(report),
            _memorydata_paper_metric_available_section(report),
            _amh_score_summary_section(report),
            '<h3 class="subhead">另一个来源：agentmemory LongMemEval R@5 comparison</h3>',
            '<p class="callout"><strong>LongMemEval R@5 表使用同一 retrieval 指标。</strong> ',
            "表格按来源拆分：LongMemEval R@5 可横向比较；LoCoMo published score 作为不同 benchmark 的公开分数参考。</p>",
            '<h3 class="subhead">同一套标准：LongMemEval R@5 横评</h3>',
            '<p class="callout">这一表里的标准固定为 retrieval R@5。证据级别用于说明数字来源强弱，评测标准仍为 retrieval R@5。</p>',
            render_longmemeval_table(longmemeval_rows),
            '<p class="callout">LongMemEval / LongMemEval-S 行是同一 R@5 口径，可以放在同一组横向比较。'
            "统一 runner 独立复跑属于更高证据级别；同一评测口径由 dataset 和 metric 决定。</p>",
            '<h3 class="subhead">不同 benchmark：LoCoMo 公开分数</h3>',
            render_reference_table(locomo_rows),
            '<p class="callout">LoCoMo 行属于不同 benchmark，只作为 published score 参考，',
            "不纳入 LongMemEval-S R@5 排名。</p>",
        ]
    )
    return _section(
        "竞品对比与可比性边界",
        "先列 MemoryData 论文统一 runner 的 22 个方法，再单独列 agentmemory LongMemEval comparison；避免把两个来源混成一张榜。",
        table,
    )


def _memorydata_paper_score_figure(base_dir: Path) -> str:
    image_data_uri = _memorydata_overview_image_data_uri(base_dir)
    if not image_data_uri:
        return (
            '<h3 class="subhead">MemoryData 论文原图评分</h3>'
            '<p class="callout"><strong>MemoryData 论文结果图</strong>：当前本地 cache 未找到 '
            '<code>MemoryData_overview.png</code>；本页保留方法入口和已有本机结果。</p>'
        )
    return (
        '<h3 class="subhead">MemoryData 论文原图评分</h3>'
        '<p class="callout"><strong>MemoryData 论文结果图</strong>：原图包含数值标注，但未提供可机读 CSV/JSON；'
        "本页嵌入原图，并在下方追加 AMH 已复现指标。</p>"
        '<figure class="paper-figure">'
        f'<img src="{image_data_uri}" alt="MemoryData paper overview scores">'
        "</figure>"
    )


def _memorydata_overview_image_data_uri(base_dir: Path) -> str:
    candidates = [
        base_dir / ".cache" / "external" / "MemoryData" / "MemoryData_overview.png",
        base_dir.parent / ".cache" / "external" / "MemoryData" / "MemoryData_overview.png",
        Path.cwd() / ".cache" / "external" / "MemoryData" / "MemoryData_overview.png",
    ]
    for candidate in candidates:
        if candidate.is_file():
            encoded = base64.b64encode(candidate.read_bytes()).decode("ascii")
            return f"data:image/png;base64,{encoded}"
    return ""


def _memorydata_paper_style_amh_bars(report: dict[str, Any]) -> str:
    amh_values = _paper_style_amh_values(report)
    charts = [
        _paper_style_chart_svg(str(key), spec, amh_values.get(str(key)))
        for key, spec in _PAPER_FIGURE_SERIES.items()
    ]
    legend_items = [
        ("Reference Baselines", _PAPER_GROUP_COLORS["Reference Baselines"]),
        ("Sequential Context", _PAPER_GROUP_COLORS["Sequential Context"]),
        ("Structural Topological", _PAPER_GROUP_COLORS["Structural Topological"]),
        ("Multi-Paradigm Hybrid", _PAPER_GROUP_COLORS["Multi-Paradigm Hybrid"]),
        ("AMH", _AMH_BAR_COLOR),
    ]
    legend = "".join(
        '<span class="paper-legend-item">'
        f'<i style="background:{_esc(color)}"></i>{_esc(label)}'
        "</span>"
        for label, color in legend_items
    )
    return (
        '<h3 class="subhead">论文图风格追加 AMH 柱状图</h3>'
        '<p class="callout">下面按论文原图样式重绘 8 个小图，并把 AMH 作为最后一根柱追加；'
        "只有同一 benchmark / 同一 metric 已复现的数据才画 AMH 柱，DB-Bench 暂不补假值。"
        "图例颜色表示方法所属架构类别，不表示独立工具；同色柱重复出现是因为多个方法属于同一类。</p>"
        '<div class="paper-chart-legend">'
        f"{legend}"
        f'<span class="legend-note">AMH appended bar color <code>{_AMH_BAR_COLOR}</code></span>'
        "</div>"
        '<div class="paper-chart-grid">'
        f"{''.join(charts)}"
        "</div>"
    )


def _paper_style_amh_values(report: dict[str, Any]) -> dict[str, float | None]:
    qa_judge = report.get("longmemeval_qa_judge_loop") or {}
    generation = qa_judge.get("generation_report") or {}
    generation_metrics = generation.get("metrics") or {}
    judge_summary = ((qa_judge.get("judge_report") or {}).get("summary") or {})
    locomo_main = _main_locomo_run(report)
    locomo_metrics = (locomo_main or {}).get("metrics") or {}
    return {
        "longmemeval_substring_em": _metric_or_none(generation_metrics, "substring_exact_match"),
        "longmemeval_rouge_l_f1": _metric_or_none(generation_metrics, "rougeL_f1"),
        "longmemeval_rouge_l_recall": _metric_or_none(generation_metrics, "rougeL_recall"),
        "longmemeval_judge_acc": (
            _float(judge_summary.get("judge_accuracy")) * 100
            if "judge_accuracy" in judge_summary else None
        ),
        "locomo_em": _metric_or_none(locomo_metrics, "exact_match"),
        "locomo_answer_f1": _metric_or_none(locomo_metrics, "f1"),
        "db_bench_em": None,
        "db_bench_task_success": None,
    }


def _metric_or_none(metrics: dict[str, Any], key: str) -> float | None:
    if key not in metrics:
        return None
    return _float(metrics.get(key))


def _main_locomo_run(report: dict[str, Any]) -> dict[str, Any] | None:
    locomo_runs = [
        run for run in report.get("memorydata_full_family_runs") or []
        if str(run.get("family") or "") == "LoCoMo"
    ]
    for run in locomo_runs:
        name = str(run.get("name") or "").lower()
        if "4cat" in name:
            return run
    return locomo_runs[0] if locomo_runs else None


def _paper_style_chart_svg(
    chart_id: str,
    spec: dict[str, Any],
    amh_value: float | None,
) -> str:
    width = 420
    height = 282
    left = 38
    top = 18
    plot_height = 148
    right = 12
    bottom = top + plot_height
    max_y = float(spec["max_y"])
    labels = [*_PAPER_METHOD_ORDER, "AMH"]
    values = [float(value) for value in spec["values"]]
    plotted_values: list[float | None] = [*values, amh_value]
    step = (width - left - right) / len(plotted_values)
    bar_width = min(16.0, step * 0.66)

    def y_for(value: float) -> float:
        bounded = max(0.0, min(max_y, value))
        return bottom - (bounded / max_y) * plot_height

    grid_parts = []
    for index in range(6):
        tick_value = max_y * index / 5
        y = y_for(tick_value)
        grid_parts.append(
            f'<line x1="{left}" y1="{y:.2f}" x2="{width - right}" y2="{y:.2f}" class="paper-grid-line" />'
            f'<text x="{left - 7}" y="{y + 3:.2f}" class="paper-y-label">{tick_value:.0f}</text>'
        )

    bar_parts = []
    for index, (label, value) in enumerate(zip(labels, plotted_values, strict=True)):
        x = left + index * step + (step - bar_width) / 2
        is_amh = label == "AMH"
        group = "AMH" if is_amh else _PAPER_METHOD_GROUPS[label]
        fill = _PAPER_GROUP_COLORS[group]
        if value is None:
            bar_parts.append(
                '<g class="paper-chart-amh is-missing" data-label="AMH 未复现">'
                f'<line x1="{x + bar_width / 2:.2f}" y1="{bottom - 18:.2f}" '
                f'x2="{x + bar_width / 2:.2f}" y2="{bottom:.2f}" class="paper-missing-line" />'
                f'<text x="{x + bar_width / 2:.2f}" y="{bottom - 22:.2f}" class="paper-amh-missing">未复现</text>'
                "</g>"
            )
        else:
            y = y_for(value)
            bar_height = bottom - y
            value_label = f"{value:.1f}"
            data_label = f"AMH {value_label}" if is_amh else f"{label} {value_label}"
            extra_class = " paper-chart-amh" if is_amh else ""
            bar_parts.append(
                f'<g class="paper-bar{extra_class}" data-label="{_esc(data_label)}">'
                f'<rect x="{x:.2f}" y="{y:.2f}" width="{bar_width:.2f}" height="{bar_height:.2f}" '
                f'fill="{_esc(fill)}" />'
                f'<text x="{x + bar_width / 2:.2f}" y="{max(9, y - 4):.2f}" class="paper-value-label">{value_label}</text>'
                "</g>"
            )
        label_class = "paper-x-label paper-x-label-amh" if is_amh else "paper-x-label"
        bar_parts.append(
            f'<text x="{x + bar_width / 2:.2f}" y="{bottom + 14:.2f}" '
            f'class="{label_class}" transform="rotate(-50 {x + bar_width / 2:.2f} {bottom + 14:.2f})">{_esc(label)}</text>'
        )

    return (
        '<article class="paper-chart-card">'
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{_esc(str(spec["title"]))} with AMH appended bar">'
        f'<title>{_esc(str(spec["title"]))} - AMH appended</title>'
        f'<rect x="0" y="0" width="{width}" height="{height}" class="paper-chart-bg" />'
        f"{''.join(grid_parts)}"
        f'<line x1="{left}" y1="{bottom}" x2="{width - right}" y2="{bottom}" class="paper-axis" />'
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{bottom}" class="paper-axis" />'
        f"{''.join(bar_parts)}"
        f'<text x="{width / 2:.2f}" y="{height - 8}" class="paper-chart-title">{_esc(str(spec["title"]))}</text>'
        "</svg>"
        "</article>"
    )


def _memorydata_paper_metric_available_section(report: dict[str, Any]) -> str:
    qa_judge = report.get("longmemeval_qa_judge_loop") or {}
    generation = qa_judge.get("generation_report") or {}
    generation_metrics = generation.get("metrics") or {}
    judge = qa_judge.get("judge_report") or {}
    judge_summary = judge.get("summary") or {}
    generation_scope = f'{generation.get("rows", 0)} generated' if generation else ""

    locomo_runs = [
        run for run in report.get("memorydata_full_family_runs") or []
        if str(run.get("family", "")).startswith("LoCoMo")
    ]
    locomo_workload = _join_nonempty(str(run.get("name") or run.get("family") or "") for run in locomo_runs)
    locomo_scope = _join_nonempty(
        str(run.get("sample_scope") or f'{run.get("rows", 0)} / {run.get("expected_rows", 0)} rows')
        for run in locomo_runs
    )
    locomo_boundary = _join_nonempty(str(run.get("limitations") or "") for run in locomo_runs)
    locomo_em = _join_nonempty(
        f'{run.get("name")}: {_pct2(_float((run.get("metrics") or {}).get("exact_match")))}'
        for run in locomo_runs
        if "exact_match" in (run.get("metrics") or {})
    )
    locomo_f1 = _join_nonempty(
        f'{run.get("name")}: {_pct2(_float((run.get("metrics") or {}).get("f1")))}'
        for run in locomo_runs
        if "f1" in (run.get("metrics") or {})
    )
    db_bench_boundary = (
        "DB-Bench 当前没有本机 AMH 结果；MemoryData 开源 cache 未提供 "
        "DB-Bench loader/config/dataset，拿到 DB-Bench runner/data 后可以接 AMH 复跑。"
    )

    def generation_metric_row(label: str, key: str, boundary: str) -> dict[str, str]:
        has_metric = key in generation_metrics
        return {
            "metric": label,
            "workload": "LongMemEval-S QA" if has_metric else "-",
            "score": _pct2(_float(generation_metrics.get(key))) if has_metric else "未落盘",
            "scope": (generation_scope or "-") if has_metric else "-",
            "status": "已复现" if has_metric else "未落盘",
            "boundary": boundary if has_metric else f"当前 LongMemEval-S generation artifact 未保存 {key}。",
        }

    rows: list[dict[str, str]] = [
        generation_metric_row(
            "LongMemEval: Substring EM",
            "substring_exact_match",
            "本机按论文图字段补算：normalized answer 是 normalized output 的子串；strict exact_match 另保留。",
        ),
        generation_metric_row(
            "LongMemEval: ROUGE-L F1",
            "rougeL_f1",
            "本机 LongMemEval-S generation artifact 按 token LCS 补算。",
        ),
        generation_metric_row(
            "LongMemEval: ROUGE-L Recall",
            "rougeL_recall",
            "本机 LongMemEval-S generation artifact 按 token LCS recall 补算。",
        ),
        {
            "metric": "LongMemEval: LLM Judge Acc.",
            "workload": "LongMemEval-S judge" if judge_summary else "-",
            "score": _pct2(_float(judge_summary.get("judge_accuracy")) * 100) if "judge_accuracy" in judge_summary else "未落盘",
            "scope": (
                f'{judge_summary.get("judged_rows", 0)} / {judge_summary.get("supported_rows", 0)} judged'
                if judge_summary else "-"
            ),
            "status": "已复现" if "judge_accuracy" in judge_summary else "未落盘",
            "boundary": "本机 LLM-as-judge sidecar；与 retrieval R@5 分开。",
        },
        {
            "metric": "LoCoMo: EM",
            "workload": locomo_workload or "-",
            "score": locomo_em or "未落盘",
            "scope": locomo_scope or "-",
            "status": "已复现" if locomo_em else "未落盘",
            "boundary": locomo_boundary or "MemoryData LoCoMo runner 本机结果。",
        },
        {
            "metric": "LoCoMo: Answer F1",
            "workload": locomo_workload or "-",
            "score": locomo_f1 or "未落盘",
            "scope": locomo_scope or "-",
            "status": "已复现" if locomo_f1 else "未落盘",
            "boundary": locomo_boundary or "MemoryData LoCoMo runner 本机结果。",
        },
        {
            "metric": "DB-Bench: EM",
            "workload": "-",
            "score": "未复现",
            "scope": "-",
            "status": "未复现",
            "boundary": db_bench_boundary,
        },
        {
            "metric": "DB-Bench: Task Success Rate",
            "workload": "-",
            "score": "未复现",
            "scope": "-",
            "status": "未复现",
            "boundary": db_bench_boundary,
        },
    ]

    rendered_rows = []
    for row in rows:
        rendered_rows.append(
            "<tr>"
            f'<td><strong>{_esc(row["metric"])}</strong></td>'
            f'<td>{_esc(row["workload"])}</td>'
            f'<td><strong>{_esc(row["score"])}</strong></td>'
            f'<td>{_esc(row["scope"])}</td>'
            f'<td><span class="state state-{_esc(_paper_metric_state_class(row["status"]))}">{_esc(row["status"])}</span></td>'
            f'<td>{_esc(row["boundary"])}</td>'
            "</tr>"
        )
    return (
        '<h3 class="subhead">论文原图 8 指标覆盖矩阵</h3>'
        '<p class="callout">按截图里的 8 个指标逐行对齐：已有本机 AMH 数据的指标展示分数；'
        "缺少 runner/data 的指标标为待补充。</p>"
        '<table class="matrix">'
        "<thead><tr><th>论文图指标</th><th>AMH workload</th><th>AMH score</th><th>Scope</th><th>状态</th><th>边界</th></tr></thead>"
        f"<tbody>{''.join(rendered_rows)}</tbody></table>"
        '<p class="callout">DB-Bench 当前没有本机 AMH 结果；'
        "MemoryData 开源 cache 未提供 DB-Bench loader/config/dataset。"
        "LongMemEval QA 指标来自同一 generation artifact，strict exact_match 单独保留。</p>"
    )


def _join_nonempty(values: object) -> str:
    return "; ".join(value for value in values if value)


def _paper_metric_state_class(status: str) -> str:
    classes = {
        "已复现": "full",
        "近邻口径": "ready",
        "未落盘": "missing",
        "未复现": "missing",
    }
    return classes.get(status, "missing")


def _amh_score_summary_section(report: dict[str, Any]) -> str:
    rows = []

    longmemeval = report.get("longmemeval_retrieval_loop") or {}
    amh_report = longmemeval.get("amh_rk_full_report") or longmemeval.get("amh_ranking_report") or {}
    amh_metrics = amh_report.get("metrics") or {}
    if amh_metrics:
        rows.append(
            {
                "workload": "LongMemEval-S R@5",
                "scope": _case_count_label(amh_report),
                "scores": (
                    f'R@5 {_pct1(_float(amh_metrics.get("recall_at_5")) * 100)}; '
                    f'R@10 {_pct1(_float(amh_metrics.get("recall_at_10")) * 100)}; '
                    f'MRR {_pct1(_float(amh_metrics.get("mrr")) * 100)}'
                ),
                "notes": "本机 AMH R@K-only retrieval run；不含 answer generation / judge。",
            }
        )

    qa_judge = report.get("longmemeval_qa_judge_loop") or {}
    generation = qa_judge.get("generation_report") or {}
    generation_metrics = generation.get("metrics") or {}
    judge_summary = ((qa_judge.get("judge_report") or {}).get("summary") or {})
    if generation_metrics or judge_summary:
        score_parts = []
        for label, key in [
            ("Exact EM", "exact_match"),
            ("Substring EM", "substring_exact_match"),
            ("F1", "f1"),
            ("ROUGE-L F1", "rougeL_f1"),
            ("ROUGE-L Recall", "rougeL_recall"),
        ]:
            if key in generation_metrics:
                score_parts.append(f"{label} {_pct1(_float(generation_metrics.get(key)))}")
        if "judge_accuracy" in judge_summary:
            score_parts.append(f'Judge Acc {_pct1(_float(judge_summary.get("judge_accuracy")) * 100)}')
        rows.append(
            {
                "workload": "LongMemEval-S QA / Judge",
                "scope": f'{_esc(generation.get("rows", 0))} generated / {_esc(judge_summary.get("judged_rows", 0))} judged',
                "scores": "; ".join(score_parts) or "-",
                "notes": "Answer-generation 与 LLM judge 单独发布，不替代 R@K retrieval 分数。",
            }
        )

    for run in report.get("memorydata_full_family_runs") or []:
        metrics = run.get("metrics") or {}
        rows.append(
            {
                "workload": str(run.get("name") or run.get("family") or "-"),
                "scope": str(run.get("sample_scope") or f'{run.get("rows", 0)} / {run.get("expected_rows", 0)} rows'),
                "scores": _summary_metric_text(run.get("family"), metrics),
                "notes": "AMH 接入 MemoryData runner 后的本机 full-family artifact。",
            }
        )

    for run in report.get("memoryagentbench_full_runs") or []:
        metrics = run.get("metrics") or {}
        rows.append(
            {
                "workload": f'MemoryAgentBench / {run.get("dimension") or run.get("id") or "-"}',
                "scope": f'{run.get("rows", 0)} / {run.get("expected_rows", 0)} rows',
                "scores": _summary_metric_text(run.get("id"), metrics),
                "notes": "AMH 本机 MemoryAgentBench full artifact。",
            }
        )

    rendered_rows = []
    for row in rows:
        rendered_rows.append(
            "<tr>"
            f'<td><strong>{_esc(row["workload"])}</strong></td>'
            f'<td>{_esc(row["scope"])}</td>'
            f'<td><strong>{_esc(row["scores"])}</strong></td>'
            f'<td>{_esc(row["notes"])}</td>'
            "</tr>"
        )
    return (
        '<h3 class="subhead">AMH 本机已跑评分摘要</h3>'
        '<p class="callout">AMH 追加接入后已落盘的本机分数，适合和同 workload / 同 metric 的竞品分数横向查看；'
        "跨 benchmark 排名需单独定义归一化规则。</p>"
        '<table class="matrix">'
        "<thead><tr><th>Workload</th><th>Scope</th><th>AMH scores</th><th>说明</th></tr></thead>"
        f"<tbody>{''.join(rendered_rows)}</tbody></table>"
    )


def _summary_metric_text(family_or_id: object, metrics: dict[str, Any]) -> str:
    key = str(family_or_id or "").lower()
    metric_keys: list[tuple[str, str]]
    if "locomo" in key:
        metric_keys = [
            ("EM", "exact_match"),
            ("F1", "f1"),
            ("ROUGE-L Recall", "rougeL_recall"),
        ]
    elif "longbench" in key:
        metric_keys = [
            ("EM", "exact_match"),
            ("F1", "f1"),
            ("ROUGE-L Recall", "rougeL_recall"),
        ]
    elif "membench" in key:
        metric_keys = [
            ("EM", "exact_match"),
            ("F1", "f1"),
            ("ROUGE-L Recall", "rougeL_recall"),
        ]
    elif "eventqa" in key:
        metric_keys = [
            ("EM", "exact_match"),
            ("F1", "f1"),
            ("EventQA Recall", "eventqa_recall"),
        ]
    elif "banking77" in key:
        metric_keys = [
            ("EM", "exact_match"),
            ("F1", "f1"),
            ("Label Accuracy", "label_accuracy"),
        ]
    elif "detectiveqa" in key:
        metric_keys = [
            ("EM", "exact_match"),
            ("F1", "f1"),
            ("ROUGE-L Recall", "rougeL_recall"),
        ]
    elif "fact-mh" in key:
        metric_keys = [
            ("EM", "exact_match"),
            ("F1", "f1"),
            ("Answer Hit", "answer_hit"),
        ]
    else:
        metric_keys = [
            ("EM", "exact_match"),
            ("F1", "f1"),
            ("ROUGE-L Recall", "rougeL_recall"),
        ]
    parts = [
        f"{label} {_pct1(_float(metrics.get(metric_key)))}"
        for label, metric_key in metric_keys
        if metric_key in metrics
    ]
    return "; ".join(parts) or "-"


def _paper_method_plain_text(method: object, group: object) -> str:
    method_key = str(method or "").lower()
    method_overrides = {
        "long context": "把全部历史尽量塞进长上下文，不做复杂记忆结构。",
        "embedding rag": "用向量检索召回相关历史片段。",
        "embed. rag": "用向量检索召回相关历史片段。",
        "bm25 rag": "用关键词/BM25 检索召回相关历史片段。",
        "mem0": "开源记忆框架，抽取用户事实并在后续对话中检索。",
        "graphrag": "把信息组织成图结构，用图关系辅助检索和回答。",
        "hipporag": "以海马体式/图式记忆检索为核心的研究方法 preset。",
        "raptor": "把文档或记忆分层聚合成树状摘要再检索。",
        "zep": "面向会话记忆的产品/服务，论文里有远端服务 preset。",
        "zep local": "Zep 的本地化检索/记忆实现 preset。",
        "letta": "原 MemGPT 路线的 agent memory/runtime 框架。",
        "memos": "MemOS 记忆操作系统路线，实现多层记忆管理。",
        "memoryos": "MemoryOS 路线，强调长期记忆读写与管理。",
        "a-mem": "A-MEM 方法 preset，偏混合式记忆写入与检索。",
        "everos": "EverOS 方法 preset，偏 agent 长期状态和环境记忆管理。",
        "self-rag": "Self-RAG 路线，边生成边判断是否检索和引用。",
        "memorag": "MemoRAG 路线，偏长上下文缓存和记忆增强检索。",
        "agent memory hub (amh)": "Agent Memory Hub 的共享长期上下文工具，作为 AMH 扩展接入项。",
    }
    if method_key in method_overrides:
        return method_overrides[method_key]

    group_key = str(group or "").lower()
    if "reference" in group_key:
        return "参考 baseline：不主张是完整记忆产品，用来做对照。"
    if "sequential" in group_key:
        return "按顺序把历史对话压缩/拼接进上下文。"
    if "structural" in group_key or "topological" in group_key:
        return "把记忆组织成图、树或拓扑结构再检索。"
    if "hybrid" in group_key:
        return "混合多种记忆策略：摘要、检索、结构化记忆或工具调用。"
    if "amh" in group_key:
        return "Agent Memory Hub 的共享长期上下文工具，作为 AMH 扩展接入项。"
    return "论文方法 preset；用于统一 runner 下比较。"


def _paper_method_type(method: object, group: object) -> str:
    method_key = str(method or "").lower()
    baseline_methods = {"long context", "embedding rag", "embed. rag", "bm25 rag"}
    product_framework_methods = {
        "cognee",
        "zep",
        "zep local",
        "mem0",
        "letta",
        "memos",
        "memoryos",
        "agent memory hub (amh)",
    }
    design_or_implementation_methods = {"graphrag"}
    research_methods = {
        "memagent",
        "memochat",
        "memtree",
        "hipporag",
        "raptor",
        "lightmem",
        "simplemem",
        "a-mem",
        "everos",
        "self-rag",
        "memorag",
    }
    if method_key in baseline_methods:
        return "参考 baseline"
    if method_key in product_framework_methods:
        return "产品/框架"
    if method_key in design_or_implementation_methods:
        return "设计范式/实现"
    if method_key in research_methods:
        return "研究方法"
    if "amh" in str(group or "").lower():
        return "产品/框架"
    return "方法 preset"


def _memorydata_paper_method_rows() -> list[dict[str, str]]:
    return [
        {
            "group": "Reference Baselines",
            "method": "Long Context",
            "preset": "reference_long_context_agent.yaml",
            "runtime": "utils/agent.py",
        },
        {
            "group": "Reference Baselines",
            "method": "Embedding RAG",
            "preset": "reference_embedding_rag.yaml",
            "runtime": "methods/embedding_rag/embedding_retriever.py",
        },
        {
            "group": "Reference Baselines",
            "method": "BM25 RAG",
            "preset": "reference_simple_rag_bm25.yaml",
            "runtime": "utils/agent.py",
        },
        {
            "group": "Sequential Context Architectures",
            "method": "MemAgent",
            "preset": "sequential_memagent.yaml",
            "runtime": "methods/memagent/",
        },
        {
            "group": "Sequential Context Architectures",
            "method": "Mem0",
            "preset": "sequential_mem0.yaml",
            "runtime": "methods/mem0/source/mem0/",
        },
        {
            "group": "Sequential Context Architectures",
            "method": "MemoChat",
            "preset": "sequential_memochat.yaml",
            "runtime": "methods/memochat/memochat_adapter.py",
        },
        {
            "group": "Structural Topological Architectures",
            "method": "Cognee",
            "preset": "topological_cognee.yaml",
            "runtime": "methods/cognee/source/cognee/",
        },
        {
            "group": "Structural Topological Architectures",
            "method": "Zep Local",
            "preset": "topological_zep_local.yaml",
            "runtime": "methods/zep_local/main.py",
        },
        {
            "group": "Structural Topological Architectures",
            "method": "MemTree",
            "preset": "topological_memtree.yaml",
            "runtime": "methods/memtree/memtree_adapter.py",
        },
        {
            "group": "Structural Topological Architectures",
            "method": "GraphRAG",
            "preset": "topological_graph_rag.yaml",
            "runtime": "methods/graph_rag/graph_rag.py",
        },
        {
            "group": "Structural Topological Architectures",
            "method": "HippoRAG",
            "preset": "topological_hippo_rag_v2_openai.yaml",
            "runtime": "methods/hipporag/",
        },
        {
            "group": "Structural Topological Architectures",
            "method": "RAPTOR",
            "preset": "topological_raptor.yaml",
            "runtime": "methods/raptor/raptor.py",
        },
        {
            "group": "Structural Topological Architectures",
            "method": "Zep",
            "preset": "topological_zep.yaml",
            "runtime": "methods/zep/zep.py",
        },
        {
            "group": "Multi-Paradigm Hybrid Architectures",
            "method": "Letta",
            "preset": "hybrid_letta.yaml",
            "runtime": "utils/agent.py",
        },
        {
            "group": "Multi-Paradigm Hybrid Architectures",
            "method": "LightMem",
            "preset": "hybrid_lightmem.yaml",
            "runtime": "methods/lightmem/lightmem_adapter.py",
        },
        {
            "group": "Multi-Paradigm Hybrid Architectures",
            "method": "SimpleMem",
            "preset": "hybrid_simplemem.yaml",
            "runtime": "methods/simplemem/simplemem_adapter.py",
        },
        {
            "group": "Multi-Paradigm Hybrid Architectures",
            "method": "MemOS",
            "preset": "hybrid_memos.yaml",
            "runtime": "methods/MemOS/source/src/",
        },
        {
            "group": "Multi-Paradigm Hybrid Architectures",
            "method": "MemoryOS",
            "preset": "hybrid_memoryos.yaml",
            "runtime": "methods/memoryos/memoryos_adapter.py",
        },
        {
            "group": "Multi-Paradigm Hybrid Architectures",
            "method": "A-MEM",
            "preset": "hybrid_a_mem.yaml",
            "runtime": "methods/a_mem/a_mem_adapter.py",
        },
        {
            "group": "Multi-Paradigm Hybrid Architectures",
            "method": "EverOS",
            "preset": "hybrid_everos.yaml",
            "runtime": "methods/everos/everos_adapter.py",
        },
        {
            "group": "Multi-Paradigm Hybrid Architectures",
            "method": "Self-RAG",
            "preset": "hybrid_self_rag.yaml",
            "runtime": "methods/self_rag/self_rag.py",
        },
        {
            "group": "Multi-Paradigm Hybrid Architectures",
            "method": "MemoRAG",
            "preset": "hybrid_memo_rag.yaml",
            "runtime": "methods/memorag/",
        },
        {
            "group": "AMH",
            "method": "Agent Memory Hub (AMH)",
            "preset": "AMH 接入同一个 MemoryData 评测入口",
            "runtime": "AMH 扩展接入项",
        },
    ]


def _memorydata_smoke_section(run_metrics: list[dict[str, Any]], *, base_dir: Path) -> str:
    cards = ['<div class="chart-grid">']
    for run in run_metrics:
        result_path = _display_path(run.get("result_path"), base_dir=base_dir)
        metrics = run.get("metrics", {})
        preferred = _preferred_metrics(run.get("family", ""), metrics)
        bars = "\n".join(_metric_bar(label, value) for label, value in preferred)
        cards.append(
            '<article class="chart-card">'
            f'<div class="card-topline"><h3>{_esc(run["family"])}</h3>'
            f'<span class="mini-pill">{_esc(_status_label(run["status"]))}</span></div>'
            f'<p class="muted">{_esc(run["name"])} / {run["rows"]} 条 query 记录</p>'
            f"{bars}"
            f'<p class="path">{_esc(result_path)}</p>'
            "</article>"
        )
    cards.append("</div>")
    return _section(
        "MemoryData Smoke 矩阵",
        "使用本地 Ollama OpenAI-compatible endpoint 执行 upstream MemoryData，并保存 raw artifacts。",
        "\n".join(cards),
    )


def _family_matrix_section(family_rows: list[dict[str, str]], prereqs: dict[str, Any]) -> str:
    rows_html = []
    for row in family_rows:
        rows_html.append(
            "<tr>"
            f'<td><strong>{_esc(row["name"])}</strong></td>'
            f'<td>{_esc(row["config"])}</td>'
            f'<td><span class="state state-{_esc(row["state"])}">{_esc(row["label"])}</span></td>'
            f'<td>{_esc(row["note"])}</td>'
            "</tr>"
        )
    blockers = "; ".join(
        row["name"] for row in prereqs.get("dataset_checks", []) if not row.get("ready")
    )
    table = f"""
<table class="matrix">
  <thead><tr><th>Family</th><th>MemoryData 配置</th><th>状态</th><th>边界</th></tr></thead>
  <tbody>{''.join(rows_html)}</tbody>
</table>
<p class="callout">严格 full-matrix 剩余数据集阻塞项：{ _esc(blockers or "无") }。</p>
"""
    return _section(
        "Full-Family 准备度矩阵",
        "对应 benchmark family matrix：dataset ready、smoke passed、missing 分开呈现。",
        table,
    )


def _boundary_panel(report: dict[str, Any]) -> str:
    sources = report["external_sources"]
    return f"""
<div class="boundary">
  <div>
    <h3>本页哪些是真实跑出来的</h3>
    <p>AMH 本地指标、LongMemEval-S 500-case R@K full、MemoryData full-family、MemoryAgentBench 四维 full 都来自本机产物。</p>
  </div>
  <div>
    <h3>哪些只是参考口径</h3>
    <p>MemoryData 论文 full charts、LongBench-v2 503 full、InfBench LLM judge、OpenViking 框架、MEMTRON/AgentMemory-Bench 不会在未本机复现时写成 AMH 分数。</p>
  </div>
  <div>
    <h3>源码锁定</h3>
    <p>MemoryData commit：<code>{_esc(sources["memorydata"].get("commit", "-"))}</code></p>
  </div>
</div>
"""


def _section(title: str, subtitle: str, body: str) -> str:
    return f"""
<section class="section">
  <div class="section-head">
    <h2>{_esc(title)}</h2>
    <p>{_esc(subtitle)}</p>
  </div>
  {body}
</section>
"""


def _metric_grid(metrics: list[tuple[str, str, str]]) -> str:
    cards = [
        f'<article class="metric"><span>{_esc(label)}</span><strong>{_esc(value)}</strong><em>{_esc(note)}</em></article>'
        for label, value, note in metrics
    ]
    return '<div class="metric-grid">' + "".join(cards) + "</div>"


def _metric_bar(label: str, value: float) -> str:
    bounded = max(0.0, min(100.0, value))
    return (
        '<div class="bar-row">'
        f'<div class="bar-label"><span>{_esc(label)}</span><strong>{bounded:.1f}%</strong></div>'
        '<div class="bar-track">'
        f'<div class="bar-fill" style="width:{bounded:.2f}%"></div>'
        "</div>"
        "</div>"
    )


def _run_card_payload(run: dict[str, Any]) -> dict[str, Any]:
    artifact_root = Path(str(run.get("artifact_root") or run.get("artifact") or ""))
    result_path = _first_result_json(artifact_root)
    metrics: dict[str, Any] = {}
    rows = 0
    if result_path is not None:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        metrics = payload.get("averaged_metrics") or {}
        rows = len(payload.get("data") or [])
    return {
        "name": run.get("name", ""),
        "family": run.get("family", ""),
        "status": run.get("status", ""),
        "metrics": metrics,
        "rows": rows,
        "result_path": result_path,
    }


def _first_result_json(root: Path) -> Path | None:
    if not root.exists():
        return None
    matches = sorted(root.rglob("*_results.json"))
    return matches[0] if matches else None


def _present_metrics(metrics: dict[str, Any], specs: list[tuple[str, str]]) -> list[tuple[str, float]]:
    return [
        (label, _float(metrics.get(metric_key)))
        for label, metric_key in specs
        if metric_key in metrics and metrics.get(metric_key) is not None
    ]


def _preferred_metrics(family: str, metrics: dict[str, Any]) -> list[tuple[str, float]]:
    family_lower = family.lower()
    if family_lower.startswith("locomo"):
        return _present_metrics(
            metrics,
            [
                ("精确匹配", "exact_match"),
                ("F1", "f1"),
                ("ROUGE-L F1", "rougeL_f1"),
                ("ROUGE-L Recall", "rougeL_recall"),
            ],
        )
    if family_lower.startswith("longbench"):
        return _present_metrics(
            metrics,
            [
                ("多选准确率", "exact_match"),
                ("F1", "f1"),
                ("ROUGE-L F1", "rougeL_f1"),
                ("ROUGE-L Recall", "rougeL_recall"),
            ],
        )
    if family_lower.startswith("membench"):
        return _present_metrics(
            metrics,
            [
                ("精确匹配", "exact_match"),
                ("F1", "f1"),
                ("ROUGE-L F1", "rougeL_f1"),
                ("ROUGE-L Recall", "rougeL_recall"),
            ],
        )
    return _present_metrics(
        metrics,
        [
            ("精确匹配", "exact_match"),
            ("F1", "f1"),
            ("ROUGE-L F1", "rougeL_f1"),
            ("ROUGE-L Recall", "rougeL_recall"),
            ("EventQA Recall", "eventqa_recall"),
        ],
    )


def _family_rows(report: dict[str, Any]) -> list[dict[str, str]]:
    run_families = {
        run.get("family")
        for run in report.get("memorydata_runs", [])
        if run.get("status") == "passed"
    }
    full_runs = report.get("memorydata_full_family_runs") or []
    full_by_family: dict[str, list[dict[str, Any]]] = {}
    for run in full_runs:
        full_by_family.setdefault(str(run.get("family", "")), []).append(run)
    mab_full_runs = report.get("memoryagentbench_full_runs") or []
    mab_full_complete = len(mab_full_runs) == 4 and all(
        row.get("status") == "passed" for row in mab_full_runs
    )
    rows = []
    for family in report["memorydata_plan"]["families_detail"]:
        name = family["name"]
        ready = family["dataset_status"] == "ready"
        smoked = name in run_families
        if name == "MemoryAgentBench" and mab_full_complete:
            state, label = "full", "四维 full 已完成"
            note = "AR / TTL / LRU / CR 已有 full artifact；InfBench judge 另跑。"
        elif _family_full_complete(name, full_by_family.get(name, [])):
            state, label = "full", "full-family 已完成"
            note = "本地 raw JSON artifact 已落盘；可比性边界见 provenance audit。"
        elif smoked:
            state, label = "smoke", "smoke 已通过"
            note = "已有 upstream raw artifact；full matrix 覆盖范围单独标注。"
        elif ready:
            state, label = "ready", "数据集已就绪"
            note = "数据集已存在，但尚未发布 smoke artifact。"
        else:
            state, label = "missing", "缺失"
            note = "严格 MemoryData full preset 数据尚不可用。"
        rows.append(
            {
                "name": name,
                "config": family["config"],
                "state": state,
                "label": label,
                "note": note,
            }
        )
    return rows


def _family_full_complete(family: str, runs: list[dict[str, Any]]) -> bool:
    expected_counts = {"LoCoMo": 1, "LongBench": 1, "MemBench": 5}
    expected = expected_counts.get(family, 0)
    return len(runs) == expected and all(run.get("status") == "passed" for run in runs)


def _case_count_label(payload: dict[str, Any]) -> str:
    case_count = payload.get("case_count", 0)
    total = payload.get("total_available_cases")
    if total:
        return f"{case_count} / {total} 个用例"
    return f"{case_count} 个用例"


def _path_or_none(value: object) -> Path | None:
    text = str(value or "")
    return Path(text) if text else None


def _display_path(path: Path | None, *, base_dir: Path) -> str:
    if path is None:
        return "-"
    try:
        return str(path.relative_to(base_dir))
    except ValueError:
        return str(path)


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def _pct1(value: float) -> str:
    return f"{value:.1f}%"


def _pct2(value: float) -> str:
    return f"{value:.2f}%"


def _status_label(status: object) -> str:
    labels = {
        "passed": "已通过",
        "failed": "失败",
        "blocked": "阻塞",
        "planned": "计划中",
    }
    return labels.get(str(status), str(status))


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _css() -> str:
    return """
:root {
  color-scheme: light;
  --ink: #17202a;
  --muted: #68727f;
  --line: #d7dde4;
  --bg: #f6f8fb;
  --panel: #ffffff;
  --blue: #2f6fbd;
  --green: #5d9b83;
  --orange: #d58a3a;
  --red: #c95f4d;
  --navy: #303753;
  --amh: #00a8a8;
  --paper: #fbfcfd;
  --copper: #a9682a;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background:
    linear-gradient(180deg, #f7f9fb 0%, #eef3f6 44%, #f7f8fa 100%);
  color: var(--ink);
  font-family: Aptos, "IBM Plex Sans", "Helvetica Neue", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
.page { max-width: 1360px; margin: 0 auto; padding: 34px 32px 64px; }
.hero {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 400px;
  gap: 28px;
  align-items: stretch;
  border: 1px solid var(--line);
  border-top: 5px solid var(--amh);
  border-radius: 8px;
  padding: 30px;
  background: rgba(255, 255, 255, 0.86);
  box-shadow: 0 20px 54px rgba(23, 32, 42, 0.07);
}
.eyebrow { margin: 0 0 10px; color: var(--amh); font-weight: 900; letter-spacing: 0.02em; text-transform: uppercase; }
h1 { margin: 0; font-size: 48px; line-height: 1.04; letter-spacing: 0; }
.lede { max-width: 820px; color: var(--muted); font-size: 19px; line-height: 1.5; margin: 18px 0 0; }
.status-card, .metric, .chart-card, .boundary > div, .finding, .comparability-grid article, .next-grid article {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  box-shadow: 0 12px 32px rgba(23, 32, 42, 0.06);
}
.status-card { padding: 22px; background: #fbfcfd; }
.status-card dl { margin: 18px 0 0; display: grid; grid-template-columns: 82px minmax(0, 1fr); gap: 10px 14px; }
.status-card dt { color: var(--muted); }
.status-card dd { margin: 0; font-weight: 800; font-size: 14px; overflow-wrap: anywhere; }
.status-pill, .mini-pill {
  display: inline-flex;
  align-items: center;
  min-height: 28px;
  padding: 4px 10px;
  border-radius: 999px;
  background: #e7f1ff;
  color: #174f96;
  font-weight: 800;
}
.section { padding: 34px 0 0; }
.section-head { display: flex; align-items: end; justify-content: space-between; gap: 22px; margin-bottom: 16px; }
.section-head h2 { margin: 0; font-size: 28px; letter-spacing: 0; }
.section-head p { margin: 0; max-width: 660px; color: var(--muted); line-height: 1.45; }
.subhead { margin: 22px 0 10px; font-size: 18px; letter-spacing: 0; }
.executive-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.45fr) repeat(3, minmax(0, 1fr));
  gap: 14px;
}
.finding {
  min-height: 176px;
  padding: 20px;
  border-top: 4px solid #8aa4b2;
}
.finding-primary { border-top-color: var(--amh); background: #f8ffff; }
.finding span {
  display: block;
  color: var(--muted);
  font-size: 13px;
  font-weight: 900;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.finding strong {
  display: block;
  margin: 12px 0 10px;
  font-size: 22px;
  line-height: 1.25;
}
.finding p, .comparability-grid p, .next-grid p { margin: 0; color: var(--muted); line-height: 1.5; }
.evidence-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin-top: 14px;
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #ffffff;
}
.evidence-strip span {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 30px;
  padding: 4px 10px;
  border-radius: 999px;
  background: #eef3f6;
  color: #344250;
  font-weight: 800;
}
.evidence-strip .strip-title {
  background: transparent;
  color: var(--muted);
  padding-left: 0;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.comparability-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
}
.comparability-grid article, .next-grid article {
  padding: 18px;
  min-height: 168px;
}
.comparability-grid h3, .next-grid h3 { margin: 12px 0 8px; font-size: 18px; letter-spacing: 0; }
.rank-mark {
  display: inline-flex;
  padding: 4px 9px;
  border-radius: 999px;
  font-weight: 900;
  font-size: 12px;
}
.rank-a { background: #dff5f1; color: #00645f; }
.rank-b { background: #e7f1ff; color: #174f96; }
.rank-c { background: #fff2d6; color: #8a5a00; }
.rank-d { background: #f8e5df; color: #9a3d2e; }
.next-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}
.metric-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; }
.metric { padding: 18px; min-height: 128px; }
.metric span { display: block; color: var(--muted); font-weight: 700; }
.metric strong { display: block; margin: 10px 0 8px; font-size: 32px; }
.metric em { color: var(--muted); font-style: normal; }
.chart-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }
.chart-card { padding: 20px; }
.card-topline { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.chart-card h3 { margin: 0 0 8px; font-size: 22px; }
.muted { color: var(--muted); margin: 0 0 18px; }
.bar-row { margin: 15px 0; }
.bar-label { display: flex; justify-content: space-between; gap: 14px; margin-bottom: 6px; font-weight: 800; }
.bar-track { height: 16px; background: #e9edf2; border-radius: 999px; overflow: hidden; }
.bar-fill { height: 100%; background: linear-gradient(90deg, var(--blue), var(--green)); }
.path {
  margin: 18px 0 0;
  padding-top: 14px;
  border-top: 1px solid var(--line);
  color: var(--muted);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  overflow-wrap: anywhere;
}
.matrix { width: 100%; border-collapse: collapse; background: var(--panel); border: 1px solid var(--line); }
.matrix th, .matrix td { padding: 14px 16px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
.matrix th { color: var(--muted); font-size: 13px; text-transform: uppercase; letter-spacing: 0.04em; }
.paper-figure {
  margin: 14px 0 0;
  padding: 12px;
  overflow-x: auto;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
}
.paper-figure img {
  display: block;
  width: 100%;
  min-width: 980px;
  height: auto;
}
.paper-chart-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 16px;
  align-items: center;
  margin: 14px 0 12px;
  color: var(--muted);
  font-size: 13px;
  font-weight: 800;
}
.paper-legend-item { display: inline-flex; align-items: center; gap: 7px; }
.paper-legend-item i { width: 22px; height: 10px; border: 1px solid rgba(23, 32, 42, 0.32); display: inline-block; }
.legend-note { margin-left: auto; font-weight: 700; }
.paper-chart-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  padding: 12px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  overflow-x: auto;
}
.paper-chart-card {
  min-width: 292px;
  background: #fff;
  border: 1px solid #dfe4ea;
  border-radius: 6px;
  padding: 8px 7px 4px;
}
.paper-chart-card svg { display: block; width: 100%; height: auto; }
.paper-chart-bg { fill: #fff; }
.paper-grid-line { stroke: #d7dde4; stroke-dasharray: 5 5; stroke-width: 1; }
.paper-axis { stroke: #1e242c; stroke-width: 1.2; }
.paper-y-label {
  fill: #4f5965;
  font-size: 10px;
  text-anchor: end;
  font-weight: 700;
}
.paper-x-label {
  fill: #17202a;
  font-size: 9px;
  text-anchor: end;
  font-weight: 800;
}
.paper-x-label-amh { fill: var(--amh); }
.paper-value-label {
  fill: #17202a;
  font-size: 9px;
  text-anchor: middle;
  font-weight: 800;
}
.paper-chart-title {
  fill: #17202a;
  font-size: 13px;
  text-anchor: middle;
  font-weight: 900;
}
.paper-chart-amh rect {
  stroke: #005f62;
  stroke-width: 1;
}
.paper-missing-line {
  stroke: var(--amh);
  stroke-width: 2;
  stroke-dasharray: 3 3;
}
.paper-amh-missing {
  fill: var(--amh);
  font-size: 10px;
  text-anchor: middle;
  font-weight: 900;
}
.state { display: inline-flex; padding: 4px 9px; border-radius: 999px; font-weight: 800; white-space: nowrap; }
.state-smoke { background: #e8f5ee; color: #1f6d4d; }
.state-full { background: #e1f3f6; color: #0b6570; }
.state-ready { background: #e7f1ff; color: #174f96; }
.state-missing { background: #fff1df; color: #8a4e00; }
.callout { margin: 14px 0 0; color: var(--muted); }
.audit-list { margin-top: 14px; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 12px 16px; }
.audit-list p { margin: 8px 0; color: var(--muted); line-height: 1.45; }
.boundary { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; }
.boundary > div { padding: 18px; }
.boundary h3 { margin: 0 0 10px; }
.boundary p { color: var(--muted); line-height: 1.5; margin: 0; }
code { background: #eef1f5; padding: 2px 5px; border-radius: 4px; }
@media (max-width: 900px) {
  .page { padding: 22px 16px 40px; }
  .hero, .chart-grid, .metric-grid, .boundary, .paper-chart-grid, .executive-grid, .comparability-grid, .next-grid { grid-template-columns: 1fr; }
  h1 { font-size: 36px; }
  .section-head { display: block; }
  .section-head p { margin-top: 8px; }
}
"""


if __name__ == "__main__":
    raise SystemExit(main())
