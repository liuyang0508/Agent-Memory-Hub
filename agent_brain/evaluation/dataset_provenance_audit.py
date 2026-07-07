"""Dataset provenance audit helpers for external memory evaluations."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


TIER_LABELS = {
    "A": "A：论文/官方同源 full 可比",
    "B": "B：官方同源但有派生/子集边界",
    "C": "C：smoke / adapter 验证，不可当 benchmark 成绩",
}


_PROVENANCE_RULES: dict[str, dict[str, Any]] = {
    "memoryagentbench_hf": {
        "tier": "A",
        "paper_or_primary_source": "MemoryAgentBench official dataset and configs",
        "primary_urls": [
            "https://github.com/HUST-AI-HYZ/MemoryAgentBench",
            "https://huggingface.co/datasets/ai-hyz/MemoryAgentBench",
        ],
        "industry_usage": "MemoryData unifies MemoryAgentBench as one of its four benchmark families.",
        "scope": "Four core capability tracks: AR / TTL / LRU / CR.",
        "limitations": (
            "Core four-dimensional representative configs are complete locally; "
            "InfBench summarization LLM-as-judge remains a separate track."
        ),
    },
    "locomo_4cat_dist": {
        "tier": "B",
        "paper_or_primary_source": "LoCoMo official locomo10 release",
        "primary_urls": [
            "https://github.com/snap-research/locomo",
            "https://snap-research.github.io/locomo/",
        ],
        "industry_usage": "Common memory-tool reports use LoCoMo QA with 10 conversations and 1540 category 1-4 questions.",
        "scope": "Question-answering categories 1-4 from official LoCoMo raw data.",
        "limitations": "官方原始数据派生：保留 category 1-4，排除 adversarial/category 5；不是重新下载的第三方私有副本。",
    },
    "locomo_raw": {
        "tier": "B",
        "paper_or_primary_source": "LoCoMo official locomo10 release",
        "primary_urls": [
            "https://github.com/snap-research/locomo",
            "https://snap-research.github.io/locomo/",
        ],
        "industry_usage": "LoCoMo is widely used for long-term conversational memory QA evaluation.",
        "scope": "Official raw source for downstream LoCoMo 4-category artifact.",
        "limitations": "Raw source only; benchmark scoring uses the derived MemoryData-compatible 4-category file.",
    },
    "locomo_category5_adversarial": {
        "tier": "B",
        "paper_or_primary_source": "LoCoMo official locomo10 release",
        "primary_urls": [
            "https://github.com/snap-research/locomo",
            "https://snap-research.github.io/locomo/",
        ],
        "industry_usage": "LoCoMo category 5 is an adversarial supplement and is commonly separated from the 1540 category 1-4 QA score.",
        "scope": "Official LoCoMo category 5 adversarial questions from locomo10.json.",
        "limitations": "补充 adversarial 轨道；使用 adversarial_answer 评分，不能并入 LoCoMo category 1-4 QA 主分数。",
    },
    "longmemeval_s_cleaned": {
        "tier": "C",
        "paper_or_primary_source": "LongMemEval official benchmark",
        "primary_urls": [
            "https://github.com/xiaowu0162/longmemeval",
            "https://arxiv.org/abs/2410.10813",
        ],
        "industry_usage": "Used by public memory systems for LongMemEval-S retrieval and QA comparisons.",
        "scope": "Retrieval smoke / AMH ranking smoke only in the current report.",
        "limitations": "当前只证明 retrieval loop；未跑 full answer generation 和 judge，不可写成 LongMemEval full 成绩。",
    },
    "longmemeval_oracle": {
        "tier": "C",
        "paper_or_primary_source": "LongMemEval official benchmark",
        "primary_urls": [
            "https://github.com/xiaowu0162/longmemeval",
            "https://arxiv.org/abs/2410.10813",
        ],
        "industry_usage": "Oracle-style LongMemEval splits are often used for retrieval sanity checks.",
        "scope": "Not materialized in the current local run.",
        "limitations": "本地未就绪；不能参与下一阶段 full 结论。",
    },
    "longbench_rep150_proportional": {
        "tier": "B",
        "paper_or_primary_source": "LongBench-v2 official Hugging Face dataset",
        "primary_urls": [
            "https://huggingface.co/datasets/THUDM/LongBench-v2",
            "https://arxiv.org/abs/2412.15204",
        ],
        "industry_usage": "MemoryData includes LongBench as a benchmark family for long-context reasoning.",
        "scope": "Deterministic MemoryData-compatible 150-row proportional subset.",
        "limitations": "150-row proportional subset；不是 LongBench-v2 503-question full set。",
    },
    "longbench_v2_503_full": {
        "tier": "A",
        "paper_or_primary_source": "LongBench-v2 official Hugging Face dataset",
        "primary_urls": [
            "https://huggingface.co/datasets/THUDM/LongBench-v2",
            "https://arxiv.org/abs/2412.15204",
        ],
        "industry_usage": "MemoryData includes LongBench as a benchmark family for long-context reasoning.",
        "scope": "Official 503-question LongBench-v2 full set through a MemoryData-compatible save_to_disk path.",
        "limitations": "Full dataset source is official; score comparability still depends on the same model and judging/evaluation harness.",
    },
    "membench_firstagent": {
        "tier": "B",
        "paper_or_primary_source": "MemBench public FirstAgent JSON slices",
        "primary_urls": [
            "https://github.com/import-myself/Membench",
            "https://github.com/OpenDataBox/MemoryData",
        ],
        "industry_usage": "MemoryData includes MemBench as a benchmark family; Letta issue trackers also reference MemBench as a standardized memory benchmark candidate.",
        "scope": "FirstAgent simple/noisy/knowledge_update/highlevel/RecMultiSession slices are present locally.",
        "limitations": "FirstAgent 五个 public slice 是 MemoryData-compatible full-family 口径；仍需和其他私有/扩展 MemBench 口径区分。",
    },
}


def build_dataset_provenance_audit(report: dict[str, Any]) -> dict[str, Any]:
    """Build a provenance and comparability audit from the benchmark report payload."""

    artifacts = report.get("dataset_materialization", {}).get("artifacts", [])
    entries = [_audit_entry(artifact, report=report) for artifact in artifacts]
    entries = [entry for entry in entries if entry is not None]
    result_entries = _result_entries(report)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tier_definitions": [
            {"tier": tier, "label": label}
            for tier, label in TIER_LABELS.items()
        ],
        "entries": entries,
        "result_entries": result_entries,
        "next_stage_gate": _next_stage_gate(entries, result_entries),
    }


def render_dataset_provenance_markdown(audit: dict[str, Any]) -> str:
    """Render the provenance audit as a Chinese Markdown report."""

    lines = [
        "# Dataset Provenance Audit",
        "",
        "这份审计只回答一个问题：当前评测结果依赖的数据集是否与论文 / 官方 benchmark / 常见记忆工具评测口径同源，以及哪些结果还不能当作 full benchmark 成绩。",
        "",
        "## 分档定义",
        "",
        _markdown_table(
            ["档位", "含义"],
            [[row["tier"], row["label"]] for row in audit["tier_definitions"]],
        ),
        "",
        "## 数据集与结果分档",
        "",
        _markdown_table(
            ["ID", "Benchmark", "档位", "就绪", "范围", "边界"],
            [
                [
                    row["id"],
                    row["benchmark"],
                    row["tier_label"],
                    "ready" if row["ready"] else "missing",
                    row["scope"],
                    row["limitations"],
                ]
                for row in audit["entries"]
            ],
        ),
        "",
        "## 已发布结果分档",
        "",
        _markdown_table(
            ["结果", "Benchmark", "档位", "样本范围", "边界"],
            [
                [
                    row["id"],
                    row["benchmark"],
                    row["tier_label"],
                    row["sample_scope"],
                    row["limitations"],
                ]
                for row in audit["result_entries"]
            ],
        ),
        "",
        "## 来源与行业使用证据",
        "",
    ]
    for row in audit["entries"]:
        lines.extend(
            [
                f"### {row['benchmark']} / {row['id']}",
                "",
                f"- 官方来源：{row['paper_or_primary_source']}",
                f"- URL：{'; '.join(row['primary_urls'])}",
                f"- 常见使用口径：{row['industry_usage']}",
                f"- 本地路径：`{row['target_path']}`",
                f"- 本地结果：{row['local_result_status']}",
                "",
            ]
        )
    gate = audit["next_stage_gate"]
    lines.extend(
        [
            "## 下一阶段门禁",
            "",
            f"**是否允许直接进入 full-family 跑分**：{'是' if gate['allowed'] else '否'}",
            "",
            "必须先完成：",
            "",
            *[f"- {action}" for action in gate["required_actions"]],
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _audit_entry(artifact: dict[str, Any], *, report: dict[str, Any]) -> dict[str, Any] | None:
    artifact_id = str(artifact.get("id") or "")
    rule = _PROVENANCE_RULES.get(artifact_id)
    if rule is None:
        return None
    tier = str(rule["tier"])
    return {
        "id": artifact_id,
        "benchmark": artifact.get("benchmark", ""),
        "tier": tier,
        "tier_label": TIER_LABELS[tier],
        "ready": bool(artifact.get("ready")),
        "source_url": artifact.get("source_url", ""),
        "target_path": artifact.get("target_path", ""),
        "paper_or_primary_source": rule["paper_or_primary_source"],
        "primary_urls": list(rule["primary_urls"]),
        "industry_usage": rule["industry_usage"],
        "scope": rule["scope"],
        "limitations": rule["limitations"],
        "local_result_status": _local_result_status(artifact_id, report),
    }


def _local_result_status(artifact_id: str, report: dict[str, Any]) -> str:
    if artifact_id == "memoryagentbench_hf":
        runs = report.get("memoryagentbench_full_runs") or []
        passed = [run for run in runs if run.get("status") == "passed"]
        if len(passed) == 4:
            return "MemoryAgentBench AR / TTL / LRU / CR four-dimensional full completed."
        if passed:
            return f"MemoryAgentBench partial full completed: {len(passed)} / 4 dimensions."
        return "No MemoryAgentBench full result in current report."
    if artifact_id == "longmemeval_s_cleaned":
        loop = report.get("longmemeval_retrieval_loop") or {}
        qa_loop = report.get("longmemeval_qa_judge_loop") or {}
        if _longmemeval_qa_judge_complete(qa_loop):
            return "LongMemEval-S 500-case R@K full and QA/Judge full completed."
        if _longmemeval_full_rk_complete(loop):
            return "LongMemEval-S 500-case R@K-only full completed; generation/judge not run."
        smoke = loop.get("smoke_report") or {}
        amh = loop.get("amh_ranking_report") or {}
        if smoke.get("status") == "passed" and amh.get("status") == "passed":
            return "Retrieval smoke and AMH ranking smoke completed; generation/judge full not run."
    if artifact_id == "locomo_4cat_dist":
        run = _result_by_id(report, "memorydata-locomo-4cat-full")
        if run and run.get("status") == "passed":
            return f"LoCoMo category 1-4 QA full completed: {run.get('sample_scope', '')}."
        return "Dataset ready; only MemoryData smoke is completed in current report."
    if artifact_id == "locomo_category5_adversarial":
        run = _result_by_id(report, "memorydata-locomo-category5-adversarial-full")
        if run and run.get("status") == "passed":
            return f"LoCoMo category 5 adversarial completed separately: {run.get('sample_scope', '')}."
        return "Dataset ready as supplemental adversarial track; no completed category 5 result in current report."
    if artifact_id == "longbench_rep150_proportional":
        run = _result_by_id(report, "memorydata-longbench-rep150-full")
        if run and run.get("status") == "passed":
            return f"LongBench rep150 proportional full completed: {run.get('sample_scope', '')}."
        return "Dataset ready as 150-row subset; only MemoryData smoke is completed in current report."
    if artifact_id == "longbench_v2_503_full":
        run = _result_by_id(report, "memorydata-longbench-v2-503-full")
        if run and run.get("status") == "passed":
            return f"LongBench-v2 503 full completed: {run.get('sample_scope', '')}."
        return "LongBench-v2 503 full dataset may be ready, but no completed 503 full result is in the current report."
    if artifact_id == "membench_firstagent":
        if _membench_full_complete(report):
            return "MemBench FirstAgent five-slice full completed."
        return "FirstAgent slices ready; only MemBench simple smoke is completed in current report."
    return "No completed benchmark result in current report."


def _result_entries(report: dict[str, Any]) -> list[dict[str, Any]]:
    entries = []
    for run in report.get("memoryagentbench_full_runs") or []:
        tier = "A" if run.get("status") == "passed" else "C"
        entries.append(
            {
                "id": run.get("id", ""),
                "benchmark": "MemoryAgentBench",
                "tier": tier,
                "tier_label": TIER_LABELS[tier],
                "sample_scope": f"{run.get('rows', 0)} / {run.get('expected_rows', 0)}",
                "limitations": (
                    "MemoryAgentBench four-dimensional full artifact."
                    if tier == "A"
                    else "未达到预期行数，不能作为 full 成绩。"
                ),
            }
        )
    for run in report.get("memorydata_runs") or []:
        if run.get("status") != "passed":
            continue
        run_level = run.get("run_level") or ("smoke" if "smoke" in str(run.get("name", "")) else "")
        if run_level == "smoke":
            entries.append(
                {
                    "id": run.get("name", ""),
                    "benchmark": run.get("family", "MemoryData"),
                    "tier": "C",
                    "tier_label": TIER_LABELS["C"],
                    "sample_scope": "smoke",
                    "limitations": "只证明 dataset / runner / endpoint / artifact 链路可跑，不可当 full benchmark 成绩。",
                }
            )
    for run in report.get("memorydata_full_family_runs") or []:
        status = run.get("status")
        if status not in {"passed", "partial"}:
            continue
        tier = str(run.get("tier") or "B") if status == "passed" else "C"
        entries.append(
            {
                "id": run.get("id", ""),
                "benchmark": run.get("family", "MemoryData"),
                "tier": tier,
                "tier_label": TIER_LABELS[tier],
                "sample_scope": run.get("sample_scope", ""),
                "limitations": (
                    run.get("limitations", "")
                    if status == "passed"
                    else "未达到预期行数，不能作为 full-family 成绩。"
                ),
            }
        )
    longmemeval = report.get("longmemeval_retrieval_loop") or {}
    for key, result_id, label in [
        ("rk_full_report", "longmemeval-lexical-rk-full", "LongMemEval lexical R@K full"),
        ("amh_rk_full_report", "longmemeval-amh-ranking-rk-full", "LongMemEval AMH ranking R@K full"),
    ]:
        payload = longmemeval.get(key) or {}
        if _is_full_rk_payload(payload):
            entries.append(
                {
                    "id": result_id,
                    "benchmark": "LongMemEval",
                    "tier": "B",
                    "tier_label": TIER_LABELS["B"],
                    "sample_scope": f"{payload.get('case_count', 0)} / {payload.get('total_available_cases', 0)} cases",
                    "limitations": f"{label}；只比较 retrieval R@K/MRR，不包含 answer generation / judge。",
                }
            )
    for key, result_id, label in [
        ("smoke_report", "longmemeval-lexical-retrieval-smoke", "LongMemEval lexical retrieval smoke"),
        ("amh_ranking_report", "longmemeval-amh-ranking-smoke", "LongMemEval AMH ranking smoke"),
    ]:
        payload = longmemeval.get(key) or {}
        if payload.get("status") == "passed":
            entries.append(
                {
                    "id": result_id,
                    "benchmark": "LongMemEval",
                    "tier": "C",
                    "tier_label": TIER_LABELS["C"],
                    "sample_scope": f"{payload.get('case_count', 0)} cases",
                    "limitations": f"{label}；不包含 answer generation / judge。",
                }
            )
    qa_loop = report.get("longmemeval_qa_judge_loop") or {}
    if _longmemeval_qa_judge_complete(qa_loop):
        generation = qa_loop.get("generation_report") or {}
        judge = qa_loop.get("judge_report") or {}
        summary = judge.get("summary") or {}
        entries.append(
            {
                "id": "longmemeval-qa-judge-full",
                "benchmark": "LongMemEval",
                "tier": "B",
                "tier_label": TIER_LABELS["B"],
                "sample_scope": (
                    f"{summary.get('judged_rows', 0)} / "
                    f"{summary.get('supported_rows', generation.get('rows', 0))} judged rows"
                ),
                "limitations": "LongMemEval-S answer generation + LLM-as-judge full；单独发布，不替代 retrieval R@K。",
            }
        )
    return entries


def _next_stage_gate(
    entries: list[dict[str, Any]],
    result_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    del entries
    result_by_id = {entry["id"]: entry for entry in result_entries}
    required_actions = []
    if result_by_id.get("memorydata-locomo-4cat-full", {}).get("tier") not in {"A", "B"}:
        required_actions.append(
            "LoCoMo：确认 MemoryData full preset 是否接受 4-category derived file；full 结果必须标注为 category 1-4 QA，不含 adversarial/category 5。"
        )
    if result_by_id.get("memorydata-longbench-v2-503-full", {}).get("tier") != "A":
        required_actions.append(
            "LongBench：补跑 THUDM/LongBench-v2 503-question full；rep150 只能作为 B 档子集结果。"
        )
    if not _membench_result_entries_complete(result_by_id):
        required_actions.append(
            "MemBench：从 simple smoke 扩展到 FirstAgent 五个 slice full，并逐 slice 发布样本数和指标。"
        )
    if not _longmemeval_result_entries_complete(result_by_id):
        required_actions.append(
            "LongMemEval：retrieval smoke 不能冒充 full QA；若要横向对比工具榜单，需要补 answer generation / judge 或明确只比较 R@K。"
        )
    return {
        "allowed": not required_actions,
        "required_actions": required_actions,
    }


def _result_by_id(report: dict[str, Any], result_id: str) -> dict[str, Any] | None:
    for run in report.get("memorydata_full_family_runs") or []:
        if run.get("id") == result_id:
            return run
    return None


def _membench_full_complete(report: dict[str, Any]) -> bool:
    result_by_id = {
        str(run.get("id")): run
        for run in report.get("memorydata_full_family_runs") or []
    }
    return _membench_result_entries_complete(result_by_id)


def _membench_result_entries_complete(result_by_id: dict[str, dict[str, Any]]) -> bool:
    required_ids = {
        "memorydata-membench-simple-full",
        "memorydata-membench-noisy-full",
        "memorydata-membench-knowledge-update-full",
        "memorydata-membench-highlevel-full",
        "memorydata-membench-recmultisession-full",
    }
    return all(
        result_by_id.get(result_id, {}).get("tier") in {"A", "B"}
        or result_by_id.get(result_id, {}).get("status") == "passed"
        for result_id in required_ids
    )


def _longmemeval_full_rk_complete(loop: dict[str, Any]) -> bool:
    return _is_full_rk_payload(loop.get("rk_full_report") or {}) or _is_full_rk_payload(
        loop.get("amh_rk_full_report") or {}
    )


def _longmemeval_qa_judge_complete(loop: dict[str, Any]) -> bool:
    generation = loop.get("generation_report") or {}
    judge = loop.get("judge_report") or {}
    summary = judge.get("summary") or {}
    rows = int(generation.get("rows") or 0)
    supported_rows = int(summary.get("supported_rows") or 0)
    judged_rows = int(summary.get("judged_rows") or 0)
    return rows > 0 and supported_rows > 0 and judged_rows == supported_rows


def _longmemeval_result_entries_complete(result_by_id: dict[str, dict[str, Any]]) -> bool:
    return any(
        result_by_id.get(result_id, {}).get("tier") in {"A", "B"}
        for result_id in {
            "longmemeval-lexical-rk-full",
            "longmemeval-amh-ranking-rk-full",
        }
    )


def _is_full_rk_payload(payload: dict[str, Any]) -> bool:
    return bool(
        payload
        and payload.get("status") == "passed"
        and payload.get("run_scope") == "full-rk"
        and payload.get("case_count") == payload.get("total_available_cases")
    )


def _markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


__all__ = [
    "build_dataset_provenance_audit",
    "render_dataset_provenance_markdown",
]
