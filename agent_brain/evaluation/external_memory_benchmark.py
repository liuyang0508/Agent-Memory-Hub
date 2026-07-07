"""External memory benchmark source-lock and report helpers.

This module deliberately separates AMH's local system benchmark from external
MemoryData execution. A report may be PASS for AMH while MemoryData is still
blocked by missing datasets, dependencies, or model endpoints.
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from agent_brain.evaluation.dataset_provenance_audit import (
    build_dataset_provenance_audit,
    render_dataset_provenance_markdown,
)
from agent_brain.evaluation.memory_eval_datasets import build_memory_eval_dataset_plan
from agent_brain.evaluation.public_hygiene import public_path, redact_public_text
from agent_brain.evaluation.system_benchmark import SystemBenchmarkReport


MEMORYDATA_URL = "https://github.com/OpenDataBox/MemoryData"
MEMTRON_AGENTMEMORY_BENCH_URL = "https://github.com/MEMTRON/AgentMemory-Bench"
AGENTMEMORY_COMPARISON_URL = "https://github.com/rohitg00/agentmemory/blob/main/benchmark/COMPARISON.md"
STATE_BENCH_URL = (
    "https://opensource.microsoft.com/blog/2026/05/19/"
    "introducing-state-bench-a-benchmark-for-ai-agent-memory/"
)
MEMORYAGENTBENCH_URL = "https://github.com/HUST-AI-HYZ/MemoryAgentBench"
OPENVIKING_URL = "https://openviking.ai/"
OPENVIKING_GITHUB_URL = "https://github.com/volcengine/OpenViking"
ARXIV_MEMORYDATA_URL = "https://arxiv.org/abs/2606.24775"

DEFAULT_JSON_NAME = "memorydata-external-benchmark-report.json"
DEFAULT_MARKDOWN_NAME = "memorydata-external-benchmark-report.zh.md"
DEFAULT_LATEST_MARKDOWN_NAME = "latest-memory-benchmark-report.zh.md"

MEMORYDATA_FAMILIES = (
    {
        "name": "MemoryAgentBench",
        "config": "benchmark/memoryagentbench/Accurate_Retrieval/config/EventQA/Eventqa_full.yaml",
        "dataset_path": "datasets/MemoryAgentBench/eval_dataset_collection",
        "kind": "dir",
    },
    {
        "name": "LoCoMo",
        "config": "benchmark/locomo/config/Locomo_qa_4cat_600_dist.yaml",
        "dataset_path": "datasets/LoCoMo/rq1_4cat_600_dist/locomo_4cat_600_dist.json",
        "kind": "file",
    },
    {
        "name": "LoCoMoCategory5",
        "config": "benchmark/locomo/config/Locomo_qa_category5_adversarial.yaml",
        "dataset_path": "datasets/LoCoMo/locomo10.json",
        "kind": "file",
    },
    {
        "name": "LongBench",
        "config": "benchmark/longbench/config/LongBench_rep150_proportional.yaml",
        "dataset_path": "datasets/longBench_rep150_proportional/datasets",
        "kind": "dir",
    },
    {
        "name": "LongBenchV2Full",
        "config": "benchmark/longbench/config/LongBench_v2_503_full.yaml",
        "dataset_path": "datasets/longBench_v2_503_full/datasets",
        "kind": "dir",
    },
    {
        "name": "MemBench",
        "config": "benchmark/membench/config/MemBench_simple.yaml",
        "dataset_path": "datasets/MemBench/MemData/FirstAgent",
        "kind": "dir",
    },
)

MEMORYAGENTBENCH_FULL_ARTIFACTS = (
    {
        "id": "memoryagentbench-ar-eventqa",
        "dimension": "准确召回 AR",
        "config": "Accurate_Retrieval / EventQA full",
        "expected_rows": 500,
        "metrics": [
            ("EM", "exact_match"),
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
            ("EM", "exact_match"),
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
            ("EM", "exact_match"),
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
            ("EM", "exact_match"),
            ("Answer Hit", "answer_hit"),
            ("Concise Response", "concise_response"),
        ],
    },
)

MEMORYDATA_FULL_FAMILY_ARTIFACTS = (
    {
        "id": "memorydata-locomo-4cat-full",
        "name": "LoCoMo 4cat QA full",
        "family": "LoCoMo",
        "artifact_subdir": "full-family/locomo-4cat",
        "expected_rows": 1540,
        "sample_unit": "QA",
        "tier": "B",
        "limitations": "LoCoMo official locomo10 derived category 1-4 QA; excludes adversarial/category 5.",
        "metrics": [
            ("EM", "exact_match"),
            ("F1", "f1"),
            ("ROUGE-L F1", "rougeL_f1"),
            ("ROUGE-L Recall", "rougeL_recall"),
            ("Recall@10", "locomo_recall@10"),
        ],
    },
    {
        "id": "memorydata-locomo-category5-adversarial-full",
        "name": "LoCoMo category5 adversarial full",
        "family": "LoCoMoCategory5",
        "artifact_subdir": "full-family/locomo-category5-adversarial",
        "expected_rows": 446,
        "sample_unit": "QA",
        "tier": "B",
        "limitations": (
            "LoCoMo official locomo10 category 5 adversarial questions, scored separately "
            "against adversarial_answer; not mixed into the category 1-4 QA benchmark."
        ),
        "metrics": [
            ("EM", "exact_match"),
            ("F1", "f1"),
            ("ROUGE-L F1", "rougeL_f1"),
            ("ROUGE-L Recall", "rougeL_recall"),
        ],
    },
    {
        "id": "memorydata-longbench-rep150-full",
        "name": "LongBench rep150 proportional full",
        "family": "LongBench",
        "artifact_subdir": "full-family/longbench-rep150",
        "expected_rows": 150,
        "sample_unit": "rows",
        "tier": "B",
        "limitations": "MemoryData deterministic 150-row proportional subset; not THUDM LongBench-v2 503-question full.",
        "metrics": [
            ("EM", "exact_match"),
            ("F1", "f1"),
            ("ROUGE-L F1", "rougeL_f1"),
            ("ROUGE-L Recall", "rougeL_recall"),
        ],
    },
    {
        "id": "memorydata-longbench-v2-503-full",
        "name": "LongBench-v2 503-question full",
        "family": "LongBenchV2Full",
        "artifact_subdir": "full-family/longbench-v2-503",
        "expected_rows": 503,
        "sample_unit": "rows",
        "tier": "A",
        "limitations": "Official THUDM LongBench-v2 503-question full set through MemoryData-compatible loader.",
        "metrics": [
            ("EM", "exact_match"),
            ("F1", "f1"),
            ("ROUGE-L F1", "rougeL_f1"),
            ("ROUGE-L Recall", "rougeL_recall"),
        ],
    },
    {
        "id": "memorydata-membench-simple-full",
        "name": "MemBench simple full",
        "family": "MemBench",
        "artifact_subdir": "full-family/membench-simple",
        "expected_rows": 100,
        "sample_unit": "rows",
        "tier": "B",
        "limitations": "MemBench public FirstAgent simple slice full.",
        "metrics": [("EM", "exact_match"), ("F1", "f1"), ("Recall@10", "membench_recall@10")],
    },
    {
        "id": "memorydata-membench-noisy-full",
        "name": "MemBench noisy full",
        "family": "MemBench",
        "artifact_subdir": "full-family/membench-noisy",
        "expected_rows": 100,
        "sample_unit": "rows",
        "tier": "B",
        "limitations": "MemBench public FirstAgent noisy slice full.",
        "metrics": [("EM", "exact_match"), ("F1", "f1"), ("Recall@10", "membench_recall@10")],
    },
    {
        "id": "memorydata-membench-knowledge-update-full",
        "name": "MemBench knowledge_update full",
        "family": "MemBench",
        "artifact_subdir": "full-family/membench-knowledge-update",
        "expected_rows": 100,
        "sample_unit": "rows",
        "tier": "B",
        "limitations": "MemBench public FirstAgent knowledge_update slice full.",
        "metrics": [("EM", "exact_match"), ("F1", "f1"), ("Recall@10", "membench_recall@10")],
    },
    {
        "id": "memorydata-membench-highlevel-full",
        "name": "MemBench highlevel full",
        "family": "MemBench",
        "artifact_subdir": "full-family/membench-highlevel",
        "expected_rows": 150,
        "sample_unit": "rows",
        "tier": "B",
        "limitations": "MemBench public FirstAgent highlevel slice full.",
        "metrics": [("EM", "exact_match"), ("F1", "f1"), ("Recall@10", "membench_recall@10")],
    },
    {
        "id": "memorydata-membench-recmultisession-full",
        "name": "MemBench RecMultiSession full",
        "family": "MemBench",
        "artifact_subdir": "full-family/membench-recmultisession",
        "expected_rows": 50,
        "sample_unit": "rows",
        "tier": "B",
        "limitations": "MemBench public FirstAgent RecMultiSession slice full.",
        "metrics": [("EM", "exact_match"), ("F1", "f1"), ("Recall@10", "membench_recall@10")],
    },
)

REFERENCE_SOURCES = (
    {
        "id": "agentmemory_comparison",
        "name": "agentmemory COMPARISON",
        "url": AGENTMEMORY_COMPARISON_URL,
        "role": "横向对照口径：LongMemEval、质量、规模、成本，不把第三方表格数字冒充 AMH 结果。",
        "signals": "LongMemEval / quality benchmark / scale benchmark / cost",
    },
    {
        "id": "state_bench",
        "name": "State-Bench",
        "url": STATE_BENCH_URL,
        "role": "有状态任务闭环口径：任务完成率、可靠性、效率、用户体验。",
        "signals": "task completion / pass^5 / efficiency / user experience",
    },
    {
        "id": "memoryagentbench",
        "name": "MemoryAgentBench",
        "url": MEMORYAGENTBENCH_URL,
        "role": "能力分型口径：准确召回、测试时学习、长程理解、冲突解决。",
        "signals": "准确召回 / 测试时学习 / 长程理解 / 冲突解决",
    },
    {
        "id": "openviking",
        "name": "OpenViking",
        "url": OPENVIKING_URL,
        "role": "公开评测体系参考：LoCoMo、tau2-bench、HotpotQA / KB QA、延迟和 token 成本。",
        "signals": "LoCoMo / tau2-bench / HotpotQA / KB QA / latency / token cost",
    },
)

METRIC_MATRIX = (
    {
        "dimension": "准确召回",
        "external_metric": "MemoryAgentBench AR、LoCoMo QA、LongMemEval-S、Recall@K / MRR",
        "amh_local_metric": "Recall@10、MRR、词频/BM25、向量召回、RRF 融合",
        "gate": "候选必须可追溯到 MemoryItem 和 source evidence",
    },
    {
        "dimension": "测试时学习",
        "external_metric": "MemoryAgentBench TTL、State-Bench state update tasks",
        "amh_local_metric": "WriteService、MemoryItem 写入审计、runtime ledger、feedback ledger",
        "gate": "新事实必须落到本地事实层，不能只停在 prompt",
    },
    {
        "dimension": "长程理解",
        "external_metric": "MemoryAgentBench LRU、LoCoMo long conversation、多跳/时序问题",
        "amh_local_metric": "locator / overview / detail 分层注入、ContextPack 可逆、token budget",
        "gate": "长上下文只允许分层装载，detail 需要按需取证",
    },
    {
        "dimension": "冲突解决",
        "external_metric": "MemoryAgentBench CR、知识更新、过期/冲突状态处理",
        "amh_local_metric": "supersession、stale filter、用户/Agent 反馈、成熟度和废止过滤",
        "gate": "旧事实不得覆盖新证据；冲突必须保留来源边界",
    },
    {
        "dimension": "有状态任务闭环",
        "external_metric": "State-Bench task completion、pass^5、reliability、user experience",
        "amh_local_metric": "弱意图阻断、可注入识别、防火墙 include/exclude、ContextPack 可逆",
        "gate": "能完成任务，也要能拒绝不该注入的上下文",
    },
    {
        "dimension": "成本与规模",
        "external_metric": "token / latency / storage / indexed items / scale benchmark",
        "amh_local_metric": "indexed items、运行耗时、top_k、pack reversible、报告生成耗时",
        "gate": "报告必须同时给准确率和成本边界",
    },
)

PUBLICATION_RULES = (
    "AMH 本地指标可以直接发布，但必须带用例数、indexed items、top_k 和运行耗时。",
    "外部 source-lock 只证明来源和入口可复核，不等于跑完外部榜单。",
    "smoke 只证明 adapter / dataset / endpoint 最小链路可跑，不能外推到 full matrix。",
    "full matrix 必须按来源维度写清 benchmark family、样本范围、指标和失败类型。",
    "OpenViking、agentmemory COMPARISON、State-Bench、MemoryAgentBench 都是评估口径来源；没有真实运行就不写外部成绩。",
)


@dataclass(frozen=True)
class ExternalBenchmarkOptions:
    memorydata_repo: Path = Path(".cache/external/MemoryData")
    dataset_cache_root: Path = Path(".cache/external")
    memorydata_agent_config: str | Path = Path("config/reference_simple_rag_bm25.yaml")
    longmemeval_smoke_report: Path | None = None
    longmemeval_amh_report: Path | None = None
    longmemeval_rk_report: Path | None = None
    longmemeval_amh_rk_report: Path | None = None
    longmemeval_generation_report: Path | None = None
    longmemeval_judge_report: Path | None = None
    run_mode: str = "source-lock"
    generated_at: datetime | None = None
    artifact_root: Path | None = None
    max_test_queries: int = 1
    check_endpoint: bool = True
    env: dict[str, str] | None = None


@dataclass(frozen=True)
class ExternalBenchmarkReport:
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return self.payload

    def to_markdown(self) -> str:
        payload = self.payload
        amh = payload["amh_system_benchmark"]
        prereq = payload["memorydata_prerequisites"]
        memorydata_agent_config = payload.get(
            "memorydata_agent_config", "config/reference_simple_rag_bm25.yaml"
        )
        lines = [
            "# MemoryData 外部横评与 AMH 本地指标",
            "",
            f"**总状态**：`{payload['status']}`",
            "",
            "这份报告把 AMH 本地 system benchmark 的核心指标和 MemoryData 外部横评 loop 放在同一个门禁视图里。AMH 本地指标可以直接复核；MemoryData / AgentMemory-Bench 外部结果只有在源码、依赖、数据集和模型 endpoint 都满足并完成运行后才写入，不填充无法复核的外部榜单数字。",
            "",
            "## AMH 核心指标",
            "",
            _markdown_table(
                ["指标", "结果"],
                [
                    ["总用例", str(amh["case_count"])],
                    ["失败数", str(amh["failure_count"])],
                    ["弱意图阻断", _fmt_pct(amh["block_accuracy"])],
                    ["可注入问题识别", _fmt_pct(amh["inject_accuracy"])],
                    [f"Recall@{amh['top_k']}", _fmt_pct(amh["recall_at_k"])],
                    ["MRR", _fmt_pct(amh["mrr"])],
                    ["Firewall include", _fmt_pct(amh["firewall_include_rate"])],
                    ["Firewall exclude", _fmt_pct(amh["firewall_exclude_rate"])],
                    ["ContextPack 可逆", _fmt_pct(amh["pack_reversible_rate"])],
                    ["top_k", str(amh["top_k"])],
                    ["indexed items", str(amh["items_indexed"])],
                    ["运行耗时", f"{amh['total_time_s']:.3f}s"],
                ],
            ),
            "",
            "## 外部 Source Lock",
            "",
            _markdown_table(
                ["来源", "状态", "URL / 路径", "commit / 说明"],
                [
                    _source_row("MemoryData", payload["external_sources"]["memorydata"]),
                    _source_row(
                        "MEMTRON/AgentMemory-Bench",
                        payload["external_sources"]["memtron_agentmemory_bench"],
                    ),
                    _source_row("OpenViking", payload["external_sources"]["openviking"]),
                    _source_row("arXiv 2606.24775", payload["external_sources"]["arxiv_2606_24775"]),
                ],
            ),
            "",
            "## 记忆评估 Loop（四源融合）",
            "",
            "这条 loop 把四类外部口径合并成 AMH 的评估合同：先锁来源，再准备数据和 adapter，再跑 smoke / full matrix，最后只发布可复核结果。",
            "",
            _markdown_table(
                ["来源", "作用", "对齐信号"],
                [
                    [row["name"], row["role"], row["signals"]]
                    for row in payload["memory_evaluation_loop"]["reference_sources"]
                ],
            ),
            "",
            _markdown_table(
                ["阶段", "状态", "门禁"],
                [
                    [row["name"], row["status"], row["gate"]]
                    for row in payload["memory_evaluation_loop"]["stages"]
                ],
            ),
            "",
            "流程：`source lock -> dataset materialize -> adapter mapping -> smoke run -> full matrix -> result normalize -> report publish`",
            "",
            "## 能力与指标矩阵",
            "",
            _markdown_table(
                ["维度", "外部指标", "AMH 本地指标", "门禁"],
                [
                    [row["dimension"], row["external_metric"], row["amh_local_metric"], row["gate"]]
                    for row in payload["memory_evaluation_loop"]["metric_matrix"]
                ],
            ),
            "",
            "## 发布规则",
            "",
            "\n".join(
                f"- {rule}" for rule in payload["memory_evaluation_loop"]["publication_rules"]
            ),
            "",
            _provenance_markdown_section(payload["dataset_provenance_audit"]),
            "",
            "## LongMemEval-S Retrieval Loop",
            "",
            "这条子 loop 先对齐公开工具常用的 LongMemEval-S R@K 口径：先下载 cleaned 数据，再做 retrieval-only smoke，最后再接 AMH ranking run。它不依赖 full generation / judge endpoint。",
            "",
            _markdown_table(
                ["阶段", "状态", "门禁"],
                [
                    [row["name"], row["status"], row["gate"]]
                    for row in payload["longmemeval_retrieval_loop"]["stages"]
                ],
            ),
            "",
            _markdown_table(
                ["数据", "状态", "来源", "本地路径"],
                [
                    [
                        payload["longmemeval_retrieval_loop"]["primary_dataset"]["name"],
                        "ready" if payload["longmemeval_retrieval_loop"]["primary_dataset"]["ready"] else "missing",
                        payload["longmemeval_retrieval_loop"]["primary_dataset"]["source_url"],
                        f"`{payload['longmemeval_retrieval_loop']['primary_dataset']['target_path']}`",
                    ]
                ],
            ),
            "",
            "一键 materialize：",
            "",
            "```bash",
            payload["longmemeval_retrieval_loop"]["one_click_command"],
            "```",
            "",
            *(
                [
                    "Lexical smoke 结果：",
                    "",
                    _markdown_table(
                        ["指标", "结果"],
                        [
                            ["status", payload["longmemeval_retrieval_loop"]["smoke_report"]["status"]],
                            ["cases", str(payload["longmemeval_retrieval_loop"]["smoke_report"]["case_count"])],
                            ["R@5", _fmt_pct(payload["longmemeval_retrieval_loop"]["smoke_report"]["metrics"].get("recall_at_5", 0.0))],
                            ["R@10", _fmt_pct(payload["longmemeval_retrieval_loop"]["smoke_report"]["metrics"].get("recall_at_10", 0.0))],
                            ["MRR", _fmt_pct(payload["longmemeval_retrieval_loop"]["smoke_report"]["metrics"].get("mrr", 0.0))],
                        ],
                    ),
                    "",
                ]
                if payload["longmemeval_retrieval_loop"].get("smoke_report")
                else []
            ),
            *(
                [
                    "AMH ranking 结果：",
                    "",
                    _markdown_table(
                        ["指标", "结果"],
                        [
                            ["status", payload["longmemeval_retrieval_loop"]["amh_ranking_report"]["status"]],
                            ["cases", str(payload["longmemeval_retrieval_loop"]["amh_ranking_report"]["case_count"])],
                            ["R@5", _fmt_pct(payload["longmemeval_retrieval_loop"]["amh_ranking_report"]["metrics"].get("recall_at_5", 0.0))],
                            ["R@10", _fmt_pct(payload["longmemeval_retrieval_loop"]["amh_ranking_report"]["metrics"].get("recall_at_10", 0.0))],
                            ["MRR", _fmt_pct(payload["longmemeval_retrieval_loop"]["amh_ranking_report"]["metrics"].get("mrr", 0.0))],
                            ["backend", payload["longmemeval_retrieval_loop"]["amh_ranking_report"].get("ranking_backend", "-")],
                        ],
                    ),
                    "",
                ]
                if payload["longmemeval_retrieval_loop"].get("amh_ranking_report")
                else []
            ),
            *(
                [
                    "R@K full 结果：",
                    "",
                    _markdown_table(
                        ["结果", "status", "cases", "R@5", "R@10", "MRR", "边界"],
                        [
                            _longmemeval_rk_markdown_row(
                                "lexical",
                                payload["longmemeval_retrieval_loop"]["rk_full_report"],
                            ),
                            _longmemeval_rk_markdown_row(
                                "AMH ranking",
                                payload["longmemeval_retrieval_loop"]["amh_rk_full_report"],
                            ),
                        ],
                    ),
                    "",
                ]
                if payload["longmemeval_retrieval_loop"].get("rk_full_report")
                or payload["longmemeval_retrieval_loop"].get("amh_rk_full_report")
                else []
            ),
            "## LongMemEval-S QA / Judge Loop",
            "",
            "这条子 loop 单独追踪 answer generation 与 LLM-as-judge。它不能和 R@K-only retrieval 分数混写；只有 generation result 和 judge sidecar 都覆盖 full case 时，才可称为 LongMemEval QA/Judge full。",
            "",
            _markdown_table(
                ["阶段", "状态", "门禁"],
                [
                    [row["name"], row["status"], row["gate"]]
                    for row in payload["longmemeval_qa_judge_loop"]["stages"]
                ],
            ),
            "",
            _markdown_table(
                ["项", "状态", "样本", "指标", "产物"],
                _longmemeval_qa_judge_markdown_rows(payload["longmemeval_qa_judge_loop"]),
            ),
            "",
            "## MemoryData 外部横评",
            "",
            f"**执行模式**：`{payload['run_mode']}`",
            "",
            _markdown_table(
                ["前置项", "状态", "说明"],
                [
                    [
                        "源码",
                        "ready" if payload["external_sources"]["memorydata"]["available"] else "blocked",
                        payload["external_sources"]["memorydata"].get("reason") or payload["external_sources"]["memorydata"].get("path", "-"),
                    ],
                    ["Python 依赖", "ready" if prereq["dependencies_ready"] else "blocked", "; ".join(prereq["missing_dependencies"]) or "required modules importable"],
                    ["数据集", _dataset_prereq_status(prereq), "; ".join(_blocked_dataset_labels(prereq)) or "all family datasets present"],
                    ["模型 endpoint", "ready" if prereq["endpoint_ready"] else "blocked", prereq["endpoint_note"]],
                ],
            ),
            "",
            _markdown_table(
                ["Benchmark family", "配置", "数据集状态"],
                [
                    [row["name"], f"`{row['config']}`", row["dataset_status"]]
                    for row in payload["memorydata_plan"]["families_detail"]
                ],
            ),
            "",
            "## MemoryAgentBench 四维 Full 结果",
            "",
            "这部分只统计 MemoryAgentBench 核心四维：AR / TTL / LRU / CR。LRU 使用 Detective_QA exact_match 路径；InfBench summarization 需要按 HELMET 口径做 LLM-as-judge，未混入这里的四维 full 结果。",
            "",
            _markdown_table(
                ["维度", "状态", "样本行数", "关键指标", "产物"],
                [
                    [
                        row["dimension"],
                        row["status"],
                        f"{row['rows']} / {row['expected_rows']}",
                        row["metrics_summary"],
                        row.get("result_path") or "-",
                    ]
                    for row in payload["memoryagentbench_full_runs"]
                ],
            ),
            "",
            "## MemoryData Full-family 结果",
            "",
            "这部分统计 MemoryData 其他 family 的 full-family 本地 artifact：LoCoMo 4-category QA、LongBench 150-row proportional subset、MemBench FirstAgent 五个 slice。它们与论文 full / 第三方榜单的边界由 Dataset Provenance Audit 单独标注。",
            "",
            _markdown_table(
                ["结果", "Benchmark", "状态", "样本范围", "关键指标", "边界", "产物"],
                [
                    [
                        row["name"],
                        row["family"],
                        row["status"],
                        row["sample_scope"],
                        row["metrics_summary"],
                        row["limitations"],
                        row.get("result_path") or "-",
                    ]
                    for row in payload.get("memorydata_full_family_runs", [])
                ],
            ),
            "",
            "## 运行记录",
            "",
            _markdown_table(
                ["名称", "状态", "命令 / 原因", "产物"],
                [
                    [
                        run["name"],
                        run["status"],
                        "`" + " ".join(run["command"]) + "`" if run.get("command") else run.get("reason", "-"),
                        run.get("artifact", "-"),
                    ]
                    for run in payload["memorydata_runs"]
                ],
            ),
            "",
            "## 一键命令",
            "",
            "```bash",
            "python benchmarks/run_memory_benchmarks.py --run-longmemeval-smoke "
            f"--memorydata-agent-config {memorydata_agent_config} --output-dir docs/evaluation",
            "```",
            "",
            "如果外部数据集和 endpoint 已准备好，可以同时打开 MemoryData 外部 smoke：",
            "",
            "```bash",
            "python benchmarks/run_memory_benchmarks.py --run-longmemeval-smoke "
            f"--run-memorydata-smoke --memorydata-agent-config {memorydata_agent_config} "
            "--output-dir docs/evaluation",
            "```",
        ]
        return "\n".join(lines).rstrip() + "\n"


def build_external_benchmark_report(
    system_report: SystemBenchmarkReport,
    options: ExternalBenchmarkOptions,
    *,
    memorydata_runs: list[dict[str, Any]] | None = None,
) -> ExternalBenchmarkReport:
    generated_at = options.generated_at or datetime.now(timezone.utc)
    env = options.env or os.environ
    memorydata = _inspect_memorydata_source(options.memorydata_repo)
    dataset_materialization = build_memory_eval_dataset_plan(
        memorydata_repo=options.memorydata_repo,
        cache_root=options.dataset_cache_root,
    )
    prereqs = _memorydata_prerequisites(
        options.memorydata_repo,
        agent_config=options.memorydata_agent_config,
        env=env,
        check_endpoint=options.check_endpoint,
    )
    plan = _memorydata_plan(
        options.memorydata_repo,
        prereqs,
        agent_config=options.memorydata_agent_config,
    )
    raw_runs = memorydata_runs or [
        _blocked_or_planned_run(
            options,
            memorydata_available=memorydata["available"],
            prereqs=prereqs,
        )
    ]
    runs = [_public_run_payload(run) for run in raw_runs]
    memoryagentbench_full_runs = _memoryagentbench_full_runs(options.artifact_root)
    memorydata_full_family_runs = _memorydata_full_family_runs(options.artifact_root)
    status = _overall_status(
        system_report,
        memorydata,
        prereqs,
        runs,
        memoryagentbench_full_runs=memoryagentbench_full_runs,
        memorydata_full_family_runs=memorydata_full_family_runs,
    )
    payload: dict[str, Any] = {
        "status": status,
        "generated_at": generated_at.astimezone(timezone.utc).isoformat(),
        "run_mode": options.run_mode,
        "memorydata_agent_config": str(options.memorydata_agent_config),
        "amh_system_benchmark": _amh_metrics(system_report),
        "external_sources": {
            "memorydata": memorydata,
            "memtron_agentmemory_bench": {
                "available": False,
                "url": MEMTRON_AGENTMEMORY_BENCH_URL,
                "reason": "Current public source lock uses OpenDataBox/MemoryData; MEMTRON/AgentMemory-Bench is not treated as an anonymously readable canonical repo.",
            },
            "openviking": {
                "available": True,
                "url": OPENVIKING_URL,
                "github": OPENVIKING_GITHUB_URL,
                "reason": "Design/reference source, not an AMH result source.",
            },
            "arxiv_2606_24775": {
                "available": True,
                "url": ARXIV_MEMORYDATA_URL,
                "reason": "Paper reference for agent-native memory evaluation taxonomy.",
            },
        },
        "memory_evaluation_loop": _memory_evaluation_loop(
            memorydata=memorydata,
            prereqs=prereqs,
            runs=runs,
            memoryagentbench_full_runs=memoryagentbench_full_runs,
            memorydata_full_family_runs=memorydata_full_family_runs,
        ),
        "dataset_materialization": dataset_materialization,
        "longmemeval_retrieval_loop": _longmemeval_retrieval_loop(
            dataset_materialization,
            smoke_report_path=options.longmemeval_smoke_report,
            amh_report_path=options.longmemeval_amh_report,
            rk_full_report_path=options.longmemeval_rk_report,
            amh_rk_full_report_path=options.longmemeval_amh_rk_report,
        ),
        "longmemeval_qa_judge_loop": _longmemeval_qa_judge_loop(
            dataset_materialization,
            generation_report_path=options.longmemeval_generation_report,
            judge_report_path=options.longmemeval_judge_report,
        ),
        "memorydata_prerequisites": prereqs,
        "memorydata_plan": plan,
        "memorydata_runs": runs,
        "memoryagentbench_full_runs": memoryagentbench_full_runs,
        "memorydata_full_family_runs": memorydata_full_family_runs,
    }
    payload["dataset_provenance_audit"] = build_dataset_provenance_audit(payload)
    return ExternalBenchmarkReport(payload)


def _longmemeval_retrieval_loop(
    dataset_materialization: dict[str, Any],
    *,
    smoke_report_path: Path | None,
    amh_report_path: Path | None,
    rk_full_report_path: Path | None = None,
    amh_rk_full_report_path: Path | None = None,
) -> dict[str, Any]:
    primary = _dataset_artifact_by_id(dataset_materialization["artifacts"], "longmemeval_s_cleaned")
    dataset_ready = bool(primary["ready"])
    smoke_report = _read_smoke_report(smoke_report_path)
    amh_report = _read_smoke_report(amh_report_path)
    rk_full_report = _read_smoke_report(rk_full_report_path)
    amh_rk_full_report = _read_smoke_report(amh_rk_full_report_path)
    effective_retrieval_report = rk_full_report or smoke_report
    effective_amh_report = amh_rk_full_report or amh_report
    smoke_passed = bool(effective_retrieval_report and effective_retrieval_report.get("status") == "passed")
    amh_passed = bool(effective_amh_report and effective_amh_report.get("status") == "passed")
    full_rk_published = _is_longmemeval_full_rk(rk_full_report) or _is_longmemeval_full_rk(
        amh_rk_full_report
    )
    command = " ".join(str(part) for part in primary["materialize_command"])
    return {
        "primary_dataset": primary,
        "one_click_command": command,
        "smoke_report_path": str(smoke_report_path) if smoke_report_path else "",
        "smoke_report": smoke_report,
        "amh_ranking_report_path": str(amh_report_path) if amh_report_path else "",
        "amh_ranking_report": amh_report,
        "rk_full_report_path": str(rk_full_report_path) if rk_full_report_path else "",
        "rk_full_report": rk_full_report,
        "amh_rk_full_report_path": str(amh_rk_full_report_path) if amh_rk_full_report_path else "",
        "amh_rk_full_report": amh_rk_full_report,
        "stages": [
            {
                "id": "source_lock",
                "name": "source lock",
                "status": "done",
                "gate": "LongMemEval cleaned 数据源锁定到 Hugging Face xiaowu0162/longmemeval-cleaned。",
            },
            {
                "id": "dataset_materialize",
                "name": "dataset materialize",
                "status": "done" if dataset_ready else "blocked",
                "gate": "本地存在 longmemeval_s_cleaned.json，且文件非空。",
            },
            {
                "id": "retrieval_only_smoke",
                "name": "retrieval-only smoke",
                "status": "done" if smoke_passed else ("planned" if dataset_ready else "blocked"),
                "gate": "先跑小样本 R@5/R@10，不依赖外部 LLM judge。",
            },
            {
                "id": "amh_ranking_run",
                "name": "AMH ranking run",
                "status": "done"
                if amh_passed
                else ("planned" if smoke_passed else ("blocked" if not dataset_ready else "waiting-smoke")),
                "gate": "把 session evidence 写成 MemoryItem，再用 AMH retriever 计算 R@K。",
            },
            {
                "id": "report_publish",
                "name": "report publish",
                "status": "rk-full-published"
                if full_rk_published
                else (
                    "amh-ranking-published"
                    if amh_passed
                    else ("smoke-published" if smoke_passed else ("planned" if dataset_ready else "blocked"))
                ),
                "gate": "只有跑出本地可复现指标后，才能写 LongMemEval-S R@K 数字。",
            },
        ],
    }


def _longmemeval_qa_judge_loop(
    dataset_materialization: dict[str, Any],
    *,
    generation_report_path: Path | None,
    judge_report_path: Path | None,
) -> dict[str, Any]:
    primary = _dataset_artifact_by_id(dataset_materialization["artifacts"], "longmemeval_s_cleaned")
    dataset_ready = bool(primary["ready"])
    generation_report = _read_longmemeval_generation_report(generation_report_path)
    judge_report = _read_longmemeval_judge_report(judge_report_path)
    generation_done = bool(generation_report and generation_report.get("rows", 0) > 0)
    judge_summary = (judge_report or {}).get("summary") or {}
    supported_rows = int(judge_summary.get("supported_rows") or 0)
    judged_rows = int(judge_summary.get("judged_rows") or 0)
    judge_done = bool(judge_report and supported_rows > 0 and judged_rows == supported_rows)
    return {
        "primary_dataset": primary,
        "generation_report_path": str(generation_report_path) if generation_report_path else "",
        "generation_report": generation_report,
        "judge_report_path": str(judge_report_path) if judge_report_path else "",
        "judge_report": judge_report,
        "stages": [
            {
                "id": "source_lock",
                "name": "source lock",
                "status": "done",
                "gate": "LongMemEval-S cleaned 数据源锁定；QA/Judge 不复用 MemoryAgentBench 300-QA 派生口径冒充 500 full。",
            },
            {
                "id": "dataset_materialize",
                "name": "dataset materialize",
                "status": "done" if dataset_ready else "blocked",
                "gate": "本地存在 LongMemEval-S cleaned 数据。",
            },
            {
                "id": "generation_run",
                "name": "answer generation",
                "status": "done" if generation_done else ("planned" if dataset_ready else "blocked"),
                "gate": "必须保存逐题 output 的 generation *_results.json。",
            },
            {
                "id": "judge_run",
                "name": "LLM-as-judge",
                "status": "done" if judge_done else ("planned" if generation_done else "blocked"),
                "gate": "必须保存 sidecar judge JSON，且 judged_rows 覆盖 supported_rows。",
            },
            {
                "id": "report_publish",
                "name": "report publish",
                "status": "qa-judge-published" if generation_done and judge_done else "planned",
                "gate": "generation 与 judge 分数单独发布，不替代 retrieval R@K。",
            },
        ],
    }


def _read_smoke_report(path: Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _is_longmemeval_full_rk(payload: dict[str, Any] | None) -> bool:
    return bool(
        payload
        and payload.get("status") == "passed"
        and payload.get("run_scope") == "full-rk"
        and payload.get("case_count") == payload.get("total_available_cases")
    )


def _dataset_artifact_by_id(artifacts: list[dict[str, Any]], artifact_id: str) -> dict[str, Any]:
    for artifact in artifacts:
        if artifact["id"] == artifact_id:
            return artifact
    raise KeyError(artifact_id)


def _provenance_markdown_section(audit: dict[str, Any]) -> str:
    markdown = render_dataset_provenance_markdown(audit).strip()
    return markdown.replace("# Dataset Provenance Audit", "## Dataset Provenance Audit", 1)


def _longmemeval_rk_markdown_row(label: str, payload: dict[str, Any] | None) -> list[str]:
    if not payload:
        return [label, "missing", "-", "-", "-", "-", "未运行"]
    metrics = payload.get("metrics") or {}
    cases = f"{payload.get('case_count', 0)} / {payload.get('total_available_cases', 0)}"
    return [
        label,
        str(payload.get("status", "-")),
        cases,
        _fmt_pct(metrics.get("recall_at_5", 0.0)),
        _fmt_pct(metrics.get("recall_at_10", 0.0)),
        _fmt_pct(metrics.get("mrr", 0.0)),
        "R@K-only full；不包含 answer generation / judge。",
    ]


def _read_longmemeval_generation_report(path: Path | None) -> dict[str, Any] | None:
    payload = _read_smoke_report(path)
    if not payload:
        return None
    rows = payload.get("data") or []
    failed_rows = sum(
        1 for row in rows
        if isinstance(row, dict) and row.get("status") == "failed"
    )
    return {
        "path": str(path) if path else "",
        "status": "passed" if rows and failed_rows == 0 else ("failed" if failed_rows else "missing"),
        "rows": len(rows),
        "failed_rows": failed_rows,
        "dataset_config": payload.get("dataset_config") or {},
        "metrics": payload.get("averaged_metrics") or {},
    }


def _read_longmemeval_judge_report(path: Path | None) -> dict[str, Any] | None:
    payload = _read_smoke_report(path)
    if not payload:
        return None
    return {
        "path": str(path) if path else "",
        "judge_model": payload.get("judge_model", ""),
        "num_runs": payload.get("num_runs"),
        "summary": payload.get("summary") or {},
    }


def _longmemeval_qa_judge_markdown_rows(loop: dict[str, Any]) -> list[list[str]]:
    generation = loop.get("generation_report") or {}
    judge = loop.get("judge_report") or {}
    judge_summary = judge.get("summary") or {}
    return [
        [
            "Generation",
            str(generation.get("status", "missing")),
            str(generation.get("rows", 0) or "-"),
            _longmemeval_generation_metric_summary(generation.get("metrics") or {}),
            generation.get("path") or "-",
        ],
        [
            "Judge",
            "passed" if judge_summary.get("judged_rows") else "missing",
            f"{judge_summary.get('judged_rows', 0)} / {judge_summary.get('supported_rows', 0)}",
            _longmemeval_judge_metric_summary(judge_summary),
            judge.get("path") or "-",
        ],
    ]


def _longmemeval_generation_metric_summary(metrics: dict[str, Any]) -> str:
    if not metrics:
        return "-"
    parts = []
    for label, key in [
        ("Exact EM", "exact_match"),
        ("Substring EM", "substring_exact_match"),
        ("F1", "f1"),
        ("ROUGE-L F1", "rougeL_f1"),
        ("ROUGE-L Recall", "rougeL_recall"),
    ]:
        if key in metrics:
            parts.append(f"{label}={float(metrics[key]):.2f}")
    return "; ".join(parts) or "-"


def _longmemeval_judge_metric_summary(summary: dict[str, Any]) -> str:
    accuracy = summary.get("judge_accuracy")
    if accuracy is None:
        return "-"
    return f"Judge Accuracy={_fmt_pct(float(accuracy))}"


def _memory_evaluation_loop(
    *,
    memorydata: dict[str, Any],
    prereqs: dict[str, Any],
    runs: list[dict[str, Any]],
    memoryagentbench_full_runs: list[dict[str, Any]] | None = None,
    memorydata_full_family_runs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    passed_runs = [run for run in runs if run.get("status") == "passed"]
    smoke_passed = bool(passed_runs)
    full_passed_families = {
        run.get("family")
        for run in passed_runs
        if run.get("run_level") == "full" and run.get("family")
    }
    expected_full_families = {family["name"] for family in MEMORYDATA_FAMILIES}
    full_complete = expected_full_families <= full_passed_families
    full_partial = bool(full_passed_families) and not full_complete
    mab_full_runs = memoryagentbench_full_runs or []
    mab_full_complete = bool(mab_full_runs) and all(
        run.get("status") == "passed" for run in mab_full_runs
    )
    mab_full_partial = bool(mab_full_runs) and any(
        run.get("status") in {"passed", "partial"} for run in mab_full_runs
    )
    full_family_runs = memorydata_full_family_runs or []
    full_family_complete = _memorydata_full_family_complete(full_family_runs)
    full_family_partial = _memorydata_full_family_partial(full_family_runs)
    dataset_status = _dataset_stage_status(prereqs)
    artifact_smoke_satisfied = mab_full_partial or full_family_partial
    smoke_status = "done" if smoke_passed or artifact_smoke_satisfied else "blocked"
    if runs and all(run["status"] == "planned" for run in runs):
        smoke_status = "ready"
    full_status = "blocked"
    if full_complete or (mab_full_complete and full_family_complete):
        full_status = "done"
    elif full_partial or full_family_partial:
        full_status = "partial"
    elif mab_full_complete:
        full_status = "memoryagentbench-full"
    elif mab_full_partial:
        full_status = "partial"
    elif smoke_passed:
        full_status = "planned"
    return {
        "reference_sources": [dict(row) for row in REFERENCE_SOURCES],
        "stages": [
            {
                "id": "source_lock",
                "name": "source lock",
                "status": "done" if memorydata.get("available") else "blocked",
                "gate": "四份外部资料有固定 URL；MemoryData 本地 repo 有 commit SHA。",
            },
            {
                "id": "dataset_materialize",
                "name": "dataset materialize",
                "status": dataset_status,
                "gate": "MemoryAgentBench / LoCoMo / LongBench / MemBench 数据集本地可读。",
            },
            {
                "id": "adapter_mapping",
                "name": "adapter mapping",
                "status": "planned" if memorydata.get("available") else "blocked",
                "gate": "AMH write / retrieve / update / context pack 映射到外部 runner。",
            },
            {
                "id": "smoke_run",
                "name": "smoke run",
                "status": smoke_status,
                "gate": "最小样本在依赖、数据集和 OpenAI-compatible endpoint 全部 ready 后执行。",
            },
            {
                "id": "full_matrix",
                "name": "full matrix",
                "status": full_status,
                "gate": "smoke pass 后才跑 AR / TTL / LRU / CR、LoCoMo、State-Bench 类任务。",
            },
            {
                "id": "result_normalize",
                "name": "result normalize",
                "status": "done"
                if (full_complete or full_partial or mab_full_complete or mab_full_partial)
                else ("planned" if smoke_passed else "blocked"),
                "gate": "统一 Recall@K、MRR、accuracy、pass^5、latency、token、storage 和失败类型。",
            },
            {
                "id": "report_publish",
                "name": "report publish",
                "status": "done",
                "gate": "本地指标已发布；外部指标必须区分 source-lock / smoke / full matrix。",
            },
        ],
        "metric_matrix": [dict(row) for row in METRIC_MATRIX],
        "publication_rules": list(PUBLICATION_RULES),
    }


def write_external_benchmark_report(
    output_dir: Path,
    report: ExternalBenchmarkReport,
) -> dict[str, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / DEFAULT_JSON_NAME
    markdown_path = output_dir / DEFAULT_MARKDOWN_NAME
    latest_markdown_path = output_dir / DEFAULT_LATEST_MARKDOWN_NAME
    json_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    markdown = report.to_markdown()
    markdown_path.write_text(markdown, encoding="utf-8")
    latest_markdown_path.write_text(markdown, encoding="utf-8")
    return {
        "json": json_path,
        "markdown": markdown_path,
        "latest_markdown": latest_markdown_path,
    }


def run_memorydata_smoke(options: ExternalBenchmarkOptions) -> dict[str, Any]:
    prereqs = _memorydata_prerequisites(
        options.memorydata_repo,
        env=options.env or os.environ,
        check_endpoint=options.check_endpoint,
    )
    memorydata = _inspect_memorydata_source(options.memorydata_repo)
    planned = _blocked_or_planned_run(
        ExternalBenchmarkOptions(
            memorydata_repo=options.memorydata_repo,
            run_mode="smoke",
            generated_at=options.generated_at,
            artifact_root=options.artifact_root,
            max_test_queries=options.max_test_queries,
            check_endpoint=options.check_endpoint,
            env=options.env,
        ),
        memorydata_available=memorydata["available"],
        prereqs=prereqs,
    )
    if planned["status"] == "blocked":
        return _public_run_payload(planned)

    started_at = datetime.now(timezone.utc)
    completed = subprocess.run(
        planned["_execution_command"],
        cwd=options.memorydata_repo,
        env=dict(os.environ, **(options.env or {})),
        capture_output=True,
        text=True,
        timeout=60 * 30,
    )
    return _public_run_payload({
        **planned,
        "status": "passed" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "started_at": started_at.isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "stdout_tail": redact_public_text(completed.stdout[-4000:]),
        "stderr_tail": redact_public_text(completed.stderr[-4000:]),
    })


def inspect_memorydata_prerequisites(
    memorydata_repo: Path,
    *,
    agent_config: str | Path = Path("config/reference_simple_rag_bm25.yaml"),
    env: dict[str, str] | None = None,
    check_endpoint: bool = True,
) -> dict[str, Any]:
    return _memorydata_prerequisites(
        memorydata_repo,
        agent_config=agent_config,
        env=env or os.environ,
        check_endpoint=check_endpoint,
    )


def _inspect_memorydata_source(path: Path) -> dict[str, Any]:
    path = Path(path)
    commit = _git_output(path, ["rev-parse", "HEAD"]) if path.exists() else ""
    available = bool(commit or (path / "README.md").is_file())
    payload: dict[str, Any] = {
        "available": available,
        "url": MEMORYDATA_URL,
        "path": public_path(path),
        "launcher_ready": (path / "main.py").is_file(),
    }
    if not available:
        payload["reason"] = "MemoryData repo is not present at this path."
        return payload

    payload["commit"] = commit
    payload["origin"] = _git_output(path, ["remote", "get-url", "origin"]) or MEMORYDATA_URL
    payload["commit_date"] = _git_output(path, ["log", "-1", "--format=%ci"])
    payload["commit_subject"] = _git_output(path, ["log", "-1", "--format=%s"])
    if not payload["launcher_ready"]:
        payload["reason"] = "source commit locked; launcher main.py not present in this fixture/path"
    return payload


def _memorydata_prerequisites(
    memorydata_repo: Path,
    *,
    agent_config: str | Path = Path("config/reference_simple_rag_bm25.yaml"),
    env: dict[str, str],
    check_endpoint: bool,
) -> dict[str, Any]:
    repo = Path(memorydata_repo)
    dataset_checks = []
    for family in MEMORYDATA_FAMILIES:
        target = repo / family["dataset_path"]
        if family["kind"] == "file":
            exists = target.is_file()
        else:
            exists = target.is_dir() and any(target.iterdir())
        dataset_checks.append({
            "name": family["name"],
            "path": public_path(target),
            "ready": exists,
            "config": family["config"],
        })

    missing_deps = [
        module
        for module in ("datasets", "rank_bm25", "openai", "yaml", "dotenv", "tqdm", "numpy")
        if importlib.util.find_spec(module) is None
    ]
    endpoint_env = _env_with_agent_endpoint(repo, agent_config=agent_config, env=env)
    endpoint_ready, endpoint_note = _endpoint_ready(endpoint_env, check_endpoint=check_endpoint)
    return {
        "dependencies_ready": not missing_deps,
        "missing_dependencies": missing_deps,
        "datasets_ready": all(row["ready"] for row in dataset_checks),
        "dataset_checks": dataset_checks,
        "endpoint_ready": endpoint_ready,
        "endpoint_note": endpoint_note,
    }


def _memorydata_plan(
    memorydata_repo: Path,
    prereqs: dict[str, Any],
    *,
    agent_config: str | Path = Path("config/reference_simple_rag_bm25.yaml"),
) -> dict[str, Any]:
    details = []
    dataset_by_name = {row["name"]: row for row in prereqs["dataset_checks"]}
    for family in MEMORYDATA_FAMILIES:
        dataset = dataset_by_name[family["name"]]
        details.append({
            "name": family["name"],
            "config": family["config"],
            "dataset_path": family["dataset_path"],
            "dataset_status": "ready" if dataset["ready"] else "missing",
        })
    smoke_command = _memorydata_command(
        Path(memorydata_repo),
        agent_config=agent_config,
        dataset_config=MEMORYDATA_FAMILIES[0]["config"],
        max_test_queries=1,
        artifact_root=Path("results/amh-memorydata-smoke"),
    )
    return {
        "families": [family["name"] for family in MEMORYDATA_FAMILIES],
        "families_detail": details,
        "smoke_command": smoke_command,
    }


def _blocked_or_planned_run(
    options: ExternalBenchmarkOptions,
    *,
    memorydata_available: bool,
    prereqs: dict[str, Any],
) -> dict[str, Any]:
    execution_command = _memorydata_command(
        options.memorydata_repo,
        agent_config=options.memorydata_agent_config,
        dataset_config=MEMORYDATA_FAMILIES[0]["config"],
        max_test_queries=options.max_test_queries,
        artifact_root=options.artifact_root or Path("docs/evaluation/memorydata-artifacts"),
    )
    command = _memorydata_command(
        options.memorydata_repo,
        agent_config=options.memorydata_agent_config,
        dataset_config=MEMORYDATA_FAMILIES[0]["config"],
        max_test_queries=options.max_test_queries,
        artifact_root=options.artifact_root or Path("docs/evaluation/memorydata-artifacts"),
        public=True,
    )
    if options.run_mode in {"source-lock", "skip"}:
        return {
            "name": "memorydata-smoke",
            "status": "blocked",
            "reason": "external execution skipped; source lock and AMH local metrics were generated",
            "command": command,
            "_execution_command": execution_command,
            "artifact": "-",
        }
    blockers: list[str] = []
    if not memorydata_available:
        blockers.append("MemoryData repo missing")
    if not prereqs["dependencies_ready"]:
        blockers.append("missing dependencies: " + ", ".join(prereqs["missing_dependencies"]))
    if not prereqs["datasets_ready"]:
        blockers.append("missing datasets")
    if not prereqs["endpoint_ready"]:
        blockers.append("model endpoint not ready")
    if blockers:
        return {
            "name": "memorydata-smoke",
            "status": "blocked",
            "reason": "; ".join(blockers),
            "command": command,
            "_execution_command": execution_command,
            "artifact": "-",
        }
    return {
        "name": "memorydata-smoke",
        "status": "planned",
        "reason": "ready to execute",
        "command": command,
        "_execution_command": execution_command,
        "artifact": public_path((options.artifact_root or Path("docs/evaluation/memorydata-artifacts"))),
    }


def _memorydata_command(
    memorydata_repo: Path,
    *,
    agent_config: str | Path = Path("config/reference_simple_rag_bm25.yaml"),
    dataset_config: str,
    max_test_queries: int,
    artifact_root: Path,
    public: bool = False,
) -> list[str]:
    artifact_arg = public_path(artifact_root) if public else str(artifact_root)
    return [
        "python",
        "main.py",
        "--agent_config",
        str(agent_config),
        "--dataset_config",
        dataset_config,
        "--max_test_queries_ablation",
        str(max_test_queries),
        "--artifact_root",
        artifact_arg,
    ]


def _public_run_payload(run: dict[str, Any]) -> dict[str, Any]:
    public: dict[str, Any] = {}
    for key, value in run.items():
        if key.startswith("_"):
            continue
        if key in {"artifact", "artifact_root", "run_record"} and value not in {None, "-"}:
            public[key] = public_path(str(value))
        elif key in {"stdout_tail", "stderr_tail", "reason"} and isinstance(value, str):
            public[key] = redact_public_text(value)
        elif key == "command" and isinstance(value, list):
            public[key] = [redact_public_text(str(part)) for part in value]
        else:
            public[key] = value
    return public


def _amh_metrics(system_report: SystemBenchmarkReport) -> dict[str, Any]:
    metrics = system_report.metrics
    query_gate = metrics["query_gate"]
    retrieval = metrics["retrieval"]
    context = metrics["context"]
    return {
        "passed": system_report.passed,
        "case_count": metrics["case_count"],
        "failure_count": len(system_report.failures),
        "items_indexed": metrics["items_indexed"],
        "top_k": metrics["top_k"],
        "total_time_s": metrics.get("total_time_s", 0.0),
        "block_accuracy": query_gate["block_accuracy"],
        "inject_accuracy": query_gate["inject_accuracy"],
        "recall_at_k": retrieval["recall_at_k"],
        "mrr": retrieval["mrr"],
        "firewall_include_rate": context["firewall_include_rate"],
        "firewall_exclude_rate": context.get("firewall_exclude_rate", 0.0),
        "pack_reversible_rate": context["pack_reversible_rate"],
    }


def _overall_status(
    system_report: SystemBenchmarkReport,
    memorydata: dict[str, Any],
    prereqs: dict[str, Any],
    runs: list[dict[str, Any]],
    *,
    memoryagentbench_full_runs: list[dict[str, Any]] | None = None,
    memorydata_full_family_runs: list[dict[str, Any]] | None = None,
) -> str:
    if not system_report.passed:
        return "FAIL_AMH_SYSTEM_BENCHMARK"
    if runs and any(run["status"] == "failed" for run in runs):
        return "FAIL_EXTERNAL_MEMORYDATA"
    mab_full_runs = memoryagentbench_full_runs or []
    mab_full_complete = bool(mab_full_runs) and all(
        run.get("status") == "passed" for run in mab_full_runs
    )
    mab_full_partial = bool(mab_full_runs) and any(
        run.get("status") in {"passed", "partial"} for run in mab_full_runs
    )
    full_family_runs = memorydata_full_family_runs or []
    full_family_complete = _memorydata_full_family_complete(full_family_runs)
    full_family_partial = _memorydata_full_family_partial(full_family_runs)
    if mab_full_complete and full_family_complete:
        return "PASS_WITH_MEMORYDATA_FULL"
    passed_runs = [run for run in runs if run.get("status") == "passed"]
    if passed_runs:
        levels = {run.get("run_level", "smoke") for run in passed_runs}
        if "full" in levels:
            expected = {family["name"] for family in MEMORYDATA_FAMILIES}
            passed_families = {
                run.get("family")
                for run in passed_runs
                if run.get("run_level") == "full" and run.get("family")
            }
            if expected <= passed_families:
                return "PASS_WITH_MEMORYDATA_FULL"
            return "PASS_WITH_MEMORYDATA_FULL_PARTIAL"
        if mab_full_complete:
            return "PASS_WITH_MEMORYAGENTBENCH_FULL"
        if mab_full_partial:
            return "PASS_WITH_MEMORYDATA_SMOKE_AND_MEMORYAGENTBENCH_PARTIAL"
        return "PASS_WITH_MEMORYDATA_SMOKE"
    if full_family_complete or full_family_partial:
        return "PASS_WITH_MEMORYDATA_FULL_PARTIAL"
    if mab_full_complete:
        return "PASS_WITH_MEMORYAGENTBENCH_FULL"
    if mab_full_partial:
        return "PASS_WITH_MEMORYAGENTBENCH_FULL_PARTIAL"
    if memorydata["available"] or prereqs["datasets_ready"] or prereqs["dependencies_ready"]:
        return "PASS_WITH_EXTERNAL_SOURCE_LOCK"
    return "PASS_LOCAL_ONLY_EXTERNAL_BLOCKED"


def _endpoint_ready(env: dict[str, str], *, check_endpoint: bool) -> tuple[bool, str]:
    if env.get("OPENAI_API_KEY") and env.get("OPENAI_API_KEY") not in {"EMPTY", "dummy"}:
        return True, "OPENAI_API_KEY is present"
    base_url = env.get("OPENAI_API_BASE") or env.get("OPENAI_BASE_URL") or "http://127.0.0.1:9908/v1"
    if not check_endpoint:
        return False, f"endpoint check skipped; no API key found; default base_url={base_url}"
    parsed = urlparse(base_url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host:
        return False, f"invalid base_url={base_url}"
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True, f"TCP reachable: {host}:{port}"
    except OSError as exc:
        return False, f"no API key and endpoint unreachable: {host}:{port} ({type(exc).__name__})"


def _env_with_agent_endpoint(
    memorydata_repo: Path,
    *,
    agent_config: str | Path,
    env: dict[str, str],
) -> dict[str, str]:
    if env.get("OPENAI_API_BASE") or env.get("OPENAI_BASE_URL"):
        return env

    config = _read_memorydata_agent_config(Path(memorydata_repo) / Path(agent_config))
    base_url_env = str(config.get("base_url_env") or "").strip()
    base_url = str(config.get("base_url") or "").strip()
    if base_url_env and env.get(base_url_env):
        return {**env, "OPENAI_API_BASE": env[base_url_env]}
    if base_url:
        return {**env, "OPENAI_API_BASE": base_url}
    return env


def _read_memorydata_agent_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}

    text = path.read_text(encoding="utf-8")
    try:
        yaml = importlib.import_module("yaml")
        data = yaml.safe_load(text) or {}
    except Exception:
        data = {}
        for line in text.splitlines():
            content = line.split("#", 1)[0]
            if ":" not in content:
                continue
            key, value = content.split(":", 1)
            key = key.strip()
            if key in {"base_url", "base_url_env"}:
                data[key] = value.strip()
    return data if isinstance(data, dict) else {}


def _git_output(path: Path, args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip()


def _source_row(name: str, source: dict[str, Any]) -> list[str]:
    status = "ready" if source.get("available") else "blocked"
    url = source.get("url") or source.get("path") or "-"
    detail = source.get("commit") or source.get("reason") or source.get("commit_subject") or "-"
    return [name, status, str(url), str(detail)]


def _memoryagentbench_full_runs(artifact_root: Path | None) -> list[dict[str, Any]]:
    root = Path(artifact_root) if artifact_root is not None else None
    rows = []
    for spec in MEMORYAGENTBENCH_FULL_ARTIFACTS:
        result_path = _first_result_json(root / "full" / str(spec["id"])) if root else None
        metrics: dict[str, Any] = {}
        row_count = 0
        if result_path is not None:
            try:
                payload = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            metrics = payload.get("averaged_metrics") or {}
            row_count = len(payload.get("data") or [])
        expected_rows = int(spec["expected_rows"])
        if row_count == expected_rows:
            status = "passed"
        elif row_count:
            status = "partial"
        else:
            status = "missing"
        rows.append(
            {
                "id": spec["id"],
                "dimension": spec["dimension"],
                "config": spec["config"],
                "status": status,
                "rows": row_count,
                "expected_rows": expected_rows,
                "metrics": metrics,
                "metrics_summary": _memoryagentbench_metrics_summary(metrics, spec["metrics"]),
                "result_path": str(result_path) if result_path else "",
            }
        )
    return rows


def _memorydata_full_family_runs(artifact_root: Path | None) -> list[dict[str, Any]]:
    root = Path(artifact_root) if artifact_root is not None else None
    rows = []
    for spec in MEMORYDATA_FULL_FAMILY_ARTIFACTS:
        result_path = _first_result_json(root / str(spec["artifact_subdir"])) if root else None
        metrics: dict[str, Any] = {}
        row_count = 0
        if result_path is not None:
            try:
                payload = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            metrics = payload.get("averaged_metrics") or {}
            row_count = len(payload.get("data") or [])
        expected_rows = int(spec["expected_rows"])
        if row_count == expected_rows:
            status = "passed"
        elif row_count:
            status = "partial"
        else:
            status = "missing"
        sample_unit = str(spec["sample_unit"])
        rows.append(
            {
                "id": spec["id"],
                "name": spec["name"],
                "family": spec["family"],
                "status": status,
                "rows": row_count,
                "expected_rows": expected_rows,
                "sample_scope": f"{row_count} / {expected_rows} {sample_unit}",
                "tier": spec["tier"],
                "metrics": metrics,
                "metrics_summary": _memoryagentbench_metrics_summary(metrics, spec["metrics"]),
                "limitations": spec["limitations"],
                "result_path": str(result_path) if result_path else "",
            }
        )
    return rows


def _memorydata_full_family_complete(runs: list[dict[str, Any]]) -> bool:
    return len(runs) == len(MEMORYDATA_FULL_FAMILY_ARTIFACTS) and all(
        run.get("status") == "passed" for run in runs
    )


def _memorydata_full_family_partial(runs: list[dict[str, Any]]) -> bool:
    return any(run.get("status") in {"passed", "partial"} for run in runs)


def _first_result_json(root: Path) -> Path | None:
    if not root.exists():
        return None
    matches = sorted(root.rglob("*_results.json"))
    return matches[0] if matches else None


def _memoryagentbench_metrics_summary(
    metrics: dict[str, Any],
    metric_specs: list[tuple[str, str]],
) -> str:
    if not metrics:
        return "-"
    parts = []
    for label, key in metric_specs:
        if key in metrics:
            parts.append(f"{label} {_fmt_metric_pct(metrics[key])}")
    return "; ".join(parts) or "-"


def _fmt_metric_pct(value: Any) -> str:
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return "-"


def _dataset_stage_status(prereq: dict[str, Any]) -> str:
    checks = prereq.get("dataset_checks", [])
    ready_count = sum(1 for row in checks if row.get("ready"))
    if checks and ready_count == len(checks):
        return "done"
    if ready_count:
        return "partial"
    return "blocked"


def _dataset_prereq_status(prereq: dict[str, Any]) -> str:
    stage_status = _dataset_stage_status(prereq)
    if stage_status == "done":
        return "ready"
    return stage_status


def _blocked_dataset_labels(prereq: dict[str, Any]) -> list[str]:
    return [row["name"] for row in prereq["dataset_checks"] if not row["ready"]]


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def _markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


__all__ = [
    "ExternalBenchmarkOptions",
    "ExternalBenchmarkReport",
    "build_external_benchmark_report",
    "inspect_memorydata_prerequisites",
    "run_memorydata_smoke",
    "write_external_benchmark_report",
]
