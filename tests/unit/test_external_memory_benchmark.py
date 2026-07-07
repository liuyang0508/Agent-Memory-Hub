from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from agent_brain.evaluation.system_benchmark import SystemBenchmarkReport


def _init_git_repo(path: Path) -> str:
    path.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "README.md").write_text("MemoryData fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=path, check=True, capture_output=True, text=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path, text=True).strip()


def _fake_memorydata_repo(path: Path) -> str:
    commit = _init_git_repo(path)
    (path / "main.py").write_text("print('MemoryData fixture')\n", encoding="utf-8")
    for config in [
        "benchmark/memoryagentbench/Accurate_Retrieval/config/EventQA/Eventqa_full.yaml",
        "benchmark/locomo/config/Locomo_qa_4cat_600_dist.yaml",
        "benchmark/locomo/config/Locomo_qa_category5_adversarial.yaml",
        "benchmark/longbench/config/LongBench_rep150_proportional.yaml",
        "benchmark/longbench/config/LongBench_v2_503_full.yaml",
        "benchmark/membench/config/MemBench_simple.yaml",
    ]:
        target = path / config
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("dataset: fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "configs"], cwd=path, check=True, capture_output=True, text=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path, text=True).strip() or commit


def _system_report() -> SystemBenchmarkReport:
    return SystemBenchmarkReport(
        passed=True,
        metrics={
            "case_count": 240,
            "items_indexed": 1232,
            "top_k": 10,
            "total_time_s": 43.998149,
            "query_gate": {
                "block_accuracy": 1.0,
                "inject_accuracy": 1.0,
                "weak_block_cases": 12,
                "inject_cases": 228,
            },
            "retrieval": {
                "recall_at_k": 1.0,
                "mrr": 0.997807,
                "retrieval_cases": 228,
            },
            "context": {
                "firewall_include_rate": 1.0,
                "firewall_exclude_rate": 1.0,
                "firewall_include_expected_cases": 4,
                "firewall_exclude_expected_cases": 62,
                "pack_reversible_rate": 1.0,
                "packed_cases": 4,
            },
        },
        cases=[],
        failures=[],
    )


def test_memorydata_prerequisites_reads_endpoint_from_agent_config(tmp_path: Path) -> None:
    from agent_brain.evaluation.external_memory_benchmark import inspect_memorydata_prerequisites

    memorydata = tmp_path / "MemoryData"
    _fake_memorydata_repo(memorydata)
    config = memorydata / "config" / "local_ollama.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "provider: openai_compatible\n"
        "base_url: http://127.0.0.1:11434/v1\n"
        "base_url_env:\n",
        encoding="utf-8",
    )

    prereqs = inspect_memorydata_prerequisites(
        memorydata,
        agent_config="config/local_ollama.yaml",
        env={},
        check_endpoint=False,
    )

    assert prereqs["endpoint_ready"] is False
    assert "base_url=http://127.0.0.1:11434/v1" in prereqs["endpoint_note"]


def test_external_memory_benchmark_report_merges_source_lock_prereqs_and_amh_metrics(tmp_path: Path) -> None:
    from agent_brain.evaluation.external_memory_benchmark import (
        ExternalBenchmarkOptions,
        build_external_benchmark_report,
        write_external_benchmark_report,
    )

    memorydata = tmp_path / "MemoryData"
    memorydata_commit = _fake_memorydata_repo(memorydata)

    report = build_external_benchmark_report(
        _system_report(),
            ExternalBenchmarkOptions(
                memorydata_repo=memorydata,
                dataset_cache_root=tmp_path / "external",
                run_mode="source-lock",
                generated_at=datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc),
            ),
    )

    payload = report.to_dict()
    assert payload["status"] == "PASS_WITH_EXTERNAL_SOURCE_LOCK"
    assert payload["amh_system_benchmark"]["items_indexed"] == 1232
    assert payload["amh_system_benchmark"]["mrr"] == 0.997807
    assert payload["external_sources"]["memorydata"]["available"] is True
    assert payload["external_sources"]["memorydata"]["commit"] == memorydata_commit
    assert payload["external_sources"]["memtron_agentmemory_bench"]["available"] is False
    evaluation_loop = payload["memory_evaluation_loop"]
    assert [source["id"] for source in evaluation_loop["reference_sources"]] == [
        "agentmemory_comparison",
        "state_bench",
        "memoryagentbench",
        "openviking",
    ]
    assert [stage["id"] for stage in evaluation_loop["stages"]] == [
        "source_lock",
        "dataset_materialize",
        "adapter_mapping",
        "smoke_run",
        "full_matrix",
        "result_normalize",
        "report_publish",
    ]
    assert {
        row["dimension"]: row["amh_local_metric"]
        for row in evaluation_loop["metric_matrix"]
    }["有状态任务闭环"] == "弱意图阻断、可注入识别、防火墙 include/exclude、ContextPack 可逆"
    assert {
        row["dimension"]: row["external_metric"]
        for row in evaluation_loop["metric_matrix"]
    }["成本与规模"] == "token / latency / storage / indexed items / scale benchmark"
    longmemeval_loop = payload["longmemeval_retrieval_loop"]
    assert [stage["id"] for stage in longmemeval_loop["stages"]] == [
        "source_lock",
        "dataset_materialize",
        "retrieval_only_smoke",
        "amh_ranking_run",
        "report_publish",
    ]
    assert longmemeval_loop["primary_dataset"]["id"] == "longmemeval_s_cleaned"
    assert longmemeval_loop["primary_dataset"]["ready"] is False
    assert longmemeval_loop["primary_dataset"]["source_url"].startswith(
        "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/"
    )
    assert "materialize_memory_eval_datasets.py --dataset longmemeval-s" in longmemeval_loop["one_click_command"]
    assert payload["memorydata_plan"]["families"] == [
        "MemoryAgentBench",
        "LoCoMo",
        "LoCoMoCategory5",
        "LongBench",
        "LongBenchV2Full",
        "MemBench",
    ]
    assert payload["dataset_provenance_audit"]["next_stage_gate"]["allowed"] is False
    assert "LoCoMo" in " ".join(
        payload["dataset_provenance_audit"]["next_stage_gate"]["required_actions"]
    )
    assert payload["memorydata_prerequisites"]["datasets_ready"] is False
    assert payload["memorydata_prerequisites"]["endpoint_ready"] is False
    assert payload["memorydata_runs"][0]["status"] == "blocked"
    assert "不填充无法复核的外部榜单数字" in report.to_markdown()
    assert "## 记忆评估 Loop（四源融合）" in report.to_markdown()
    assert "State-Bench" in report.to_markdown()
    assert "pass^5" in report.to_markdown()
    assert "准确召回 / 测试时学习 / 长程理解 / 冲突解决" in report.to_markdown()
    assert "OpenViking" in report.to_markdown()
    assert "## LongMemEval-S Retrieval Loop" in report.to_markdown()
    assert "longmemeval_s_cleaned.json" in report.to_markdown()
    assert "## Dataset Provenance Audit" in report.to_markdown()
    assert "A：论文/官方同源 full 可比" in report.to_markdown()
    assert "下一阶段门禁" in report.to_markdown()
    assert "materialize_memory_eval_datasets.py --dataset longmemeval-s" in report.to_markdown()
    assert (
        "run_memory_benchmarks.py --run-longmemeval-smoke "
        "--memorydata-agent-config config/reference_simple_rag_bm25.yaml --output-dir docs/evaluation"
        in report.to_markdown()
    )
    assert "总用例 | 240" in report.to_markdown()
    assert "indexed items | 1232" in report.to_markdown()

    written = write_external_benchmark_report(tmp_path / "out", report)
    assert written["json"].is_file()
    assert written["markdown"].is_file()
    assert written["latest_markdown"].is_file()
    assert json.loads(written["json"].read_text(encoding="utf-8"))["status"] == payload["status"]
    assert "MemoryData 外部横评" in written["latest_markdown"].read_text(encoding="utf-8")


def test_external_memory_benchmark_report_publishes_longmemeval_amh_ranking(tmp_path: Path) -> None:
    from agent_brain.evaluation.external_memory_benchmark import (
        ExternalBenchmarkOptions,
        build_external_benchmark_report,
    )

    memorydata = tmp_path / "MemoryData"
    _fake_memorydata_repo(memorydata)
    cache_root = tmp_path / "external"
    longmemeval_file = cache_root / "LongMemEval" / "data" / "longmemeval_s_cleaned.json"
    longmemeval_file.parent.mkdir(parents=True)
    longmemeval_file.write_text("[]", encoding="utf-8")
    smoke_report = tmp_path / "longmemeval-retrieval-smoke.json"
    smoke_report.write_text(
        json.dumps(
            {
                "status": "passed",
                "case_count": 5,
                "mode": "retrieval-only lexical smoke",
                "metrics": {"recall_at_5": 1.0, "recall_at_10": 1.0, "mrr": 0.64},
            }
        ),
        encoding="utf-8",
    )
    amh_report = tmp_path / "longmemeval-amh-ranking-smoke.json"
    amh_report.write_text(
        json.dumps(
            {
                "status": "passed",
                "case_count": 5,
                "mode": "amh-ranking",
                "ranking_backend": "AMH HubIndex + Retriever BM25/RRF pipeline",
                "metrics": {"recall_at_5": 1.0, "recall_at_10": 1.0, "mrr": 0.9},
            }
        ),
        encoding="utf-8",
    )

    report = build_external_benchmark_report(
        _system_report(),
        ExternalBenchmarkOptions(
            memorydata_repo=memorydata,
            dataset_cache_root=cache_root,
            longmemeval_smoke_report=smoke_report,
            longmemeval_amh_report=amh_report,
            generated_at=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
        ),
    )

    longmemeval_loop = report.to_dict()["longmemeval_retrieval_loop"]
    stages = {stage["id"]: stage["status"] for stage in longmemeval_loop["stages"]}
    assert longmemeval_loop["primary_dataset"]["ready"] is True
    assert stages["retrieval_only_smoke"] == "done"
    assert stages["amh_ranking_run"] == "done"
    assert stages["report_publish"] == "amh-ranking-published"
    assert longmemeval_loop["amh_ranking_report"]["metrics"]["mrr"] == 0.9
    markdown = report.to_markdown()
    assert "AMH ranking 结果" in markdown
    assert "AMH HubIndex + Retriever BM25/RRF pipeline" in markdown
    assert "MRR | 90.00%" in markdown


def test_external_memory_benchmark_report_publishes_longmemeval_full_rk_scope(
    tmp_path: Path,
) -> None:
    from agent_brain.evaluation.external_memory_benchmark import (
        ExternalBenchmarkOptions,
        build_external_benchmark_report,
    )

    memorydata = tmp_path / "MemoryData"
    _fake_memorydata_repo(memorydata)
    cache_root = tmp_path / "external"
    longmemeval_file = cache_root / "LongMemEval" / "data" / "longmemeval_s_cleaned.json"
    longmemeval_file.parent.mkdir(parents=True)
    longmemeval_file.write_text("[]", encoding="utf-8")
    lexical_full = tmp_path / "longmemeval-retrieval-rk-full.json"
    lexical_full.write_text(
        json.dumps(
            {
                "status": "passed",
                "case_count": 500,
                "total_available_cases": 500,
                "run_scope": "full-rk",
                "mode": "retrieval-only lexical R@K full",
                "metrics": {"recall_at_5": 0.89, "recall_at_10": 0.936, "mrr": 0.7874},
            }
        ),
        encoding="utf-8",
    )
    amh_full = tmp_path / "longmemeval-amh-ranking-rk-full.json"
    amh_full.write_text(
        json.dumps(
            {
                "status": "passed",
                "case_count": 500,
                "total_available_cases": 500,
                "run_scope": "full-rk",
                "mode": "amh-ranking",
                "ranking_backend": "AMH HubIndex + Retriever BM25/RRF pipeline",
                "metrics": {"recall_at_5": 0.974, "recall_at_10": 0.984, "mrr": 0.9129},
            }
        ),
        encoding="utf-8",
    )

    report = build_external_benchmark_report(
        _system_report(),
        ExternalBenchmarkOptions(
            memorydata_repo=memorydata,
            dataset_cache_root=cache_root,
            longmemeval_rk_report=lexical_full,
            longmemeval_amh_rk_report=amh_full,
            generated_at=datetime(2026, 7, 2, 13, 0, tzinfo=timezone.utc),
        ),
    )

    payload = report.to_dict()
    loop = payload["longmemeval_retrieval_loop"]
    assert loop["rk_full_report"]["case_count"] == 500
    assert loop["amh_rk_full_report"]["metrics"]["recall_at_10"] == 0.984
    result_entries = {
        row["id"]: row for row in payload["dataset_provenance_audit"]["result_entries"]
    }
    assert result_entries["longmemeval-amh-ranking-rk-full"]["tier"] == "B"
    required_actions = " ".join(
        payload["dataset_provenance_audit"]["next_stage_gate"]["required_actions"]
    )
    assert "LongMemEval：retrieval smoke 不能冒充 full QA" not in required_actions
    markdown = report.to_markdown()
    assert "R@K full 结果" in markdown
    assert "500 / 500" in markdown
    assert "R@K-only full" in markdown


def test_external_memory_benchmark_report_publishes_longmemeval_qa_judge_scope(
    tmp_path: Path,
) -> None:
    from agent_brain.evaluation.external_memory_benchmark import (
        ExternalBenchmarkOptions,
        build_external_benchmark_report,
    )

    memorydata = tmp_path / "MemoryData"
    _fake_memorydata_repo(memorydata)
    cache_root = tmp_path / "external"
    longmemeval_file = cache_root / "LongMemEval" / "data" / "longmemeval_s_cleaned.json"
    longmemeval_file.parent.mkdir(parents=True)
    longmemeval_file.write_text("[]", encoding="utf-8")
    generation = tmp_path / "longmemeval-generation-full-results.json"
    generation.write_text(
        json.dumps(
            {
                "dataset_config": {"sub_dataset": "longmemeval_s"},
                "data": [
                    {"query": f"q{i}", "answer": "a", "output": "a"}
                    for i in range(500)
                ],
                "averaged_metrics": {
                    "exact_match": 12.5,
                    "substring_exact_match": 22.0,
                    "f1": 18.0,
                    "rougeL_f1": 19.0,
                    "rougeL_recall": 20.0,
                },
            }
        ),
        encoding="utf-8",
    )
    judge = tmp_path / "longmemeval-generation-full.longmemeval_judge.json"
    judge.write_text(
        json.dumps(
            {
                "judge_model": "gpt-4o",
                "summary": {
                    "total_rows": 500,
                    "supported_rows": 500,
                    "judged_rows": 500,
                    "judge_accuracy": 0.42,
                    "judge_accuracy_std": 0.0,
                    "judge_accuracy_by_type": {
                        "single-session-user": {
                            "supported_count": 300,
                            "judged_count": 300,
                            "accuracy": 0.4,
                        }
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    report = build_external_benchmark_report(
        _system_report(),
        ExternalBenchmarkOptions(
            memorydata_repo=memorydata,
            dataset_cache_root=cache_root,
            longmemeval_generation_report=generation,
            longmemeval_judge_report=judge,
            generated_at=datetime(2026, 7, 2, 13, 30, tzinfo=timezone.utc),
        ),
    )

    loop = report.to_dict()["longmemeval_qa_judge_loop"]
    assert loop["generation_report"]["rows"] == 500
    assert loop["generation_report"]["metrics"]["substring_exact_match"] == 22.0
    assert loop["generation_report"]["metrics"]["rougeL_recall"] == 20.0
    assert loop["judge_report"]["summary"]["judged_rows"] == 500
    assert loop["judge_report"]["summary"]["judge_accuracy"] == 0.42
    stages = {stage["id"]: stage["status"] for stage in loop["stages"]}
    assert stages["generation_run"] == "done"
    assert stages["judge_run"] == "done"
    result_entries = {
        row["id"]: row
        for row in report.to_dict()["dataset_provenance_audit"]["result_entries"]
    }
    assert result_entries["longmemeval-qa-judge-full"]["sample_scope"] == "500 / 500 judged rows"
    longmemeval_entry = next(
        row
        for row in report.to_dict()["dataset_provenance_audit"]["entries"]
        if row["id"] == "longmemeval_s_cleaned"
    )
    assert "QA/Judge full completed" in longmemeval_entry["local_result_status"]
    markdown = report.to_markdown()
    assert "## LongMemEval-S QA / Judge Loop" in markdown
    assert "Judge Accuracy" in markdown
    assert "42.00%" in markdown
    assert "longmemeval-qa-judge-full" in markdown


def test_external_memory_benchmark_report_includes_memoryagentbench_full_artifacts(
    tmp_path: Path,
) -> None:
    from agent_brain.evaluation.external_memory_benchmark import (
        ExternalBenchmarkOptions,
        build_external_benchmark_report,
    )

    memorydata = tmp_path / "MemoryData"
    _fake_memorydata_repo(memorydata)
    artifact_root = tmp_path / "memorydata-artifacts"
    result_path = (
        artifact_root
        / "full/memoryagentbench-ar-eventqa/outputs/gui-owl-bm25/Accurate_Retrieval/ar_results.json"
    )
    result_path.parent.mkdir(parents=True)
    result_path.write_text(
        json.dumps(
            {
                "data": [{"query": f"q{i}", "answer": "a"} for i in range(500)],
                "averaged_metrics": {
                    "exact_match": 37.0,
                    "f1": 59.17,
                    "eventqa_recall": 37.0,
                },
            }
        ),
        encoding="utf-8",
    )

    report = build_external_benchmark_report(
        _system_report(),
        ExternalBenchmarkOptions(
            memorydata_repo=memorydata,
            dataset_cache_root=tmp_path / "external",
            artifact_root=artifact_root,
            generated_at=datetime(2026, 7, 1, 12, 30, tzinfo=timezone.utc),
        ),
    )

    payload = report.to_dict()
    ar = payload["memoryagentbench_full_runs"][0]
    assert ar["dimension"] == "准确召回 AR"
    assert ar["status"] == "passed"
    assert ar["rows"] == 500
    markdown = report.to_markdown()
    assert "## MemoryAgentBench 四维 Full 结果" in markdown
    assert "准确召回 AR" in markdown
    assert "500 / 500" in markdown
    assert "InfBench summarization" in markdown


def test_external_memory_benchmark_report_includes_memorydata_full_family_artifacts(
    tmp_path: Path,
) -> None:
    from agent_brain.evaluation.external_memory_benchmark import (
        ExternalBenchmarkOptions,
        build_external_benchmark_report,
    )

    memorydata = tmp_path / "MemoryData"
    _fake_memorydata_repo(memorydata)
    artifact_root = tmp_path / "memorydata-artifacts"

    def write_result(relative_root: str, rows: int, metrics: dict[str, float]) -> None:
        result_path = artifact_root / relative_root / "outputs/gui-owl-bm25/result_results.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps(
                {
                    "data": [{"query": f"q{i}", "answer": "a"} for i in range(rows)],
                    "averaged_metrics": metrics,
                }
            ),
            encoding="utf-8",
        )

    write_result(
        "full-family/locomo-4cat",
        1540,
        {
            "exact_match": 3.5,
            "f1": 10.2,
            "rougeL_f1": 9.8,
            "rougeL_recall": 16.4,
            "locomo_recall@10": 7.1,
        },
    )
    write_result(
        "full-family/longbench-rep150",
        150,
        {
            "exact_match": 23.33,
            "f1": 15.33,
            "rougeL_f1": 15.33,
            "rougeL_recall": 15.33,
        },
    )
    for relative_root, rows in [
        ("full-family/membench-simple", 100),
        ("full-family/membench-noisy", 100),
        ("full-family/membench-knowledge-update", 100),
        ("full-family/membench-highlevel", 150),
        ("full-family/membench-recmultisession", 50),
    ]:
        write_result(
            relative_root,
            rows,
            {
                "exact_match": 50.0,
                "f1": 30.0,
                "membench_recall@10": 2.0,
            },
        )

    report = build_external_benchmark_report(
        _system_report(),
        ExternalBenchmarkOptions(
            memorydata_repo=memorydata,
            dataset_cache_root=tmp_path / "external",
            artifact_root=artifact_root,
            generated_at=datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc),
        ),
    )

    payload = report.to_dict()
    stages = {stage["id"]: stage["status"] for stage in payload["memory_evaluation_loop"]["stages"]}
    assert stages["smoke_run"] == "done"
    assert stages["full_matrix"] == "partial"
    full_runs = {row["id"]: row for row in payload["memorydata_full_family_runs"]}
    assert full_runs["memorydata-locomo-4cat-full"]["status"] == "passed"
    assert full_runs["memorydata-locomo-4cat-full"]["sample_scope"] == "1540 / 1540 QA"
    assert full_runs["memorydata-locomo-category5-adversarial-full"]["status"] == "missing"
    assert full_runs["memorydata-locomo-category5-adversarial-full"]["tier"] == "B"
    assert full_runs["memorydata-longbench-rep150-full"]["tier"] == "B"
    assert full_runs["memorydata-longbench-v2-503-full"]["status"] == "missing"
    assert full_runs["memorydata-longbench-v2-503-full"]["tier"] == "A"
    assert full_runs["memorydata-membench-highlevel-full"]["status"] == "passed"
    assert len(full_runs) == 9

    audit_results = {
        row["id"]: row for row in payload["dataset_provenance_audit"]["result_entries"]
    }
    assert audit_results["memorydata-locomo-4cat-full"]["tier"] == "B"
    assert audit_results["memorydata-longbench-rep150-full"]["tier"] == "B"
    assert audit_results["memorydata-membench-recmultisession-full"]["tier"] == "B"
    required_actions = " ".join(
        payload["dataset_provenance_audit"]["next_stage_gate"]["required_actions"]
    )
    assert "MemBench：从 simple smoke 扩展" not in required_actions
    assert "LongBench：补跑 THUDM/LongBench-v2 503-question full" in required_actions
    assert "LongMemEval" in required_actions

    markdown = report.to_markdown()
    assert "## MemoryData Full-family 结果" in markdown
    assert "LoCoMo 4cat QA full" in markdown
    assert "LongBench-v2 503-question full" in markdown
    assert "MemBench highlevel full" in markdown


def test_external_report_full_artifacts_are_not_downgraded_by_selected_full_run(
    tmp_path: Path,
) -> None:
    from agent_brain.evaluation import external_memory_benchmark as emb
    from agent_brain.evaluation.external_memory_benchmark import (
        ExternalBenchmarkOptions,
        build_external_benchmark_report,
    )

    memorydata = tmp_path / "MemoryData"
    _fake_memorydata_repo(memorydata)
    artifact_root = tmp_path / "memorydata-artifacts"

    def write_result(relative_root: str, rows: int) -> None:
        result_path = artifact_root / relative_root / "outputs/gui-owl-bm25/result_results.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps(
                {
                    "data": [{"query": f"q{i}", "answer": "a"} for i in range(rows)],
                    "averaged_metrics": {"exact_match": 1.0},
                }
            ),
            encoding="utf-8",
        )

    for spec in emb.MEMORYAGENTBENCH_FULL_ARTIFACTS:
        write_result(f"full/{spec['id']}", int(spec["expected_rows"]))
    for spec in emb.MEMORYDATA_FULL_FAMILY_ARTIFACTS:
        write_result(str(spec["artifact_subdir"]), int(spec["expected_rows"]))

    report = build_external_benchmark_report(
        _system_report(),
        ExternalBenchmarkOptions(
            memorydata_repo=memorydata,
            dataset_cache_root=tmp_path / "external",
            artifact_root=artifact_root,
            generated_at=datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc),
        ),
        memorydata_runs=[
            {
                "name": "memorydata-longbenchv2full-full",
                "family": "LongBenchV2Full",
                "status": "passed",
                "run_level": "full",
                "command": ["python", "main.py"],
                "artifact": str(artifact_root / "full-family" / "longbench-v2-503"),
            }
        ],
    )

    payload = report.to_dict()
    assert payload["status"] == "PASS_WITH_MEMORYDATA_FULL"
    stages = {stage["id"]: stage["status"] for stage in payload["memory_evaluation_loop"]["stages"]}
    assert stages["full_matrix"] == "done"


def test_dataset_provenance_audit_classifies_benchmark_inputs(tmp_path: Path) -> None:
    from agent_brain.evaluation.dataset_provenance_audit import (
        build_dataset_provenance_audit,
        render_dataset_provenance_markdown,
    )

    report = {
        "dataset_materialization": {
            "artifacts": [
                {
                    "id": "memoryagentbench_hf",
                    "benchmark": "MemoryAgentBench",
                    "ready": True,
                    "source_url": "https://huggingface.co/datasets/ai-hyz/MemoryAgentBench",
                    "target_path": ".cache/external/MemoryData/datasets/MemoryAgentBench/eval_dataset_collection",
                },
                {
                    "id": "locomo_4cat_dist",
                    "benchmark": "LoCoMo",
                    "ready": True,
                    "source_url": "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json",
                    "target_path": ".cache/external/MemoryData/datasets/LoCoMo/rq1_4cat_600_dist/locomo_4cat_600_dist.json",
                },
                {
                    "id": "longbench_rep150_proportional",
                    "benchmark": "LongBench",
                    "ready": True,
                    "source_url": "https://huggingface.co/datasets/THUDM/LongBench-v2",
                    "target_path": ".cache/external/MemoryData/datasets/longBench_rep150_proportional/datasets",
                },
            ]
        },
        "memoryagentbench_full_runs": [
            {
                "id": "memoryagentbench-ar-eventqa",
                "status": "passed",
                "rows": 500,
                "expected_rows": 500,
            }
        ],
    }

    audit = build_dataset_provenance_audit(report)

    by_id = {entry["id"]: entry for entry in audit["entries"]}
    assert by_id["memoryagentbench_hf"]["tier"] == "A"
    assert by_id["memoryagentbench_hf"]["tier_label"] == "A：论文/官方同源 full 可比"
    assert by_id["locomo_4cat_dist"]["tier"] == "B"
    assert "官方原始数据派生" in by_id["locomo_4cat_dist"]["limitations"]
    assert by_id["longbench_rep150_proportional"]["tier"] == "B"
    assert "150-row" in by_id["longbench_rep150_proportional"]["limitations"]
    assert audit["next_stage_gate"]["allowed"] is False
    assert "LoCoMo" in " ".join(audit["next_stage_gate"]["required_actions"])
    result_by_id = {entry["id"]: entry for entry in audit["result_entries"]}
    assert result_by_id["memoryagentbench-ar-eventqa"]["tier"] == "A"
    assert result_by_id["memoryagentbench-ar-eventqa"]["sample_scope"] == "500 / 500"

    markdown = render_dataset_provenance_markdown(audit)
    assert "# Dataset Provenance Audit" in markdown
    assert "A：论文/官方同源 full 可比" in markdown
    assert "B：官方同源但有派生/子集边界" in markdown
    assert "C：smoke / adapter 验证，不可当 benchmark 成绩" in markdown
    assert "## 已发布结果分档" in markdown
    assert "memoryagentbench-ar-eventqa" in markdown
    assert "下一阶段门禁" in markdown


def test_memory_eval_dataset_plan_includes_longbench_v2_full(tmp_path: Path) -> None:
    from agent_brain.evaluation.memory_eval_datasets import build_memory_eval_dataset_plan

    memorydata = tmp_path / "MemoryData"
    _fake_memorydata_repo(memorydata)

    plan = build_memory_eval_dataset_plan(
        memorydata_repo=memorydata,
        cache_root=tmp_path / "external",
    )

    artifacts = {artifact["id"]: artifact for artifact in plan["artifacts"]}
    artifact = artifacts["longbench_v2_503_full"]
    assert artifact["alias"] == "longbench-v2-full"
    assert artifact["benchmark"] == "LongBench"
    assert artifact["source_url"] == "https://huggingface.co/datasets/THUDM/LongBench-v2"
    assert artifact["source_type"] == "huggingface_save_to_disk"
    assert artifact["target_path"].endswith("datasets/longBench_v2_503_full/datasets")
    assert artifact["materialize_command"][3] == "longbench-v2-full"

    manifest_families = {
        family["id"]: family for family in plan["memorydata_manifest"]["memorydata_families"]
    }
    assert manifest_families["longbench_v2_503_full"]["ready"] is False
    assert manifest_families["longbench_v2_503_full"]["config_paths"] == [
        "benchmark/longbench/config/LongBench_v2_503_full.yaml"
    ]


def test_memorydata_runner_maps_longbench_v2_full_to_distinct_config(tmp_path: Path) -> None:
    from agent_brain.evaluation.memorydata_runner import MemoryDataRunOptions, memorydata_command

    command = memorydata_command(
        MemoryDataRunOptions(
            memorydata_repo=tmp_path / "MemoryData",
            family="LongBenchV2Full",
            max_test_queries=503,
            artifact_root=tmp_path / "artifacts",
        )
    )

    assert command[command.index("--dataset_config") + 1] == (
        "benchmark/longbench/config/LongBench_v2_503_full.yaml"
    )
    assert command[command.index("--max_test_queries_ablation") + 1] == "503"


def test_memorydata_runner_maps_locomo_category5_to_distinct_config(tmp_path: Path) -> None:
    from agent_brain.evaluation.memorydata_runner import MemoryDataRunOptions, memorydata_command

    command = memorydata_command(
        MemoryDataRunOptions(
            memorydata_repo=tmp_path / "MemoryData",
            family="LoCoMoCategory5",
            max_test_queries=446,
            artifact_root=tmp_path / "artifacts",
        )
    )

    assert command[command.index("--dataset_config") + 1] == (
        "benchmark/locomo/config/Locomo_qa_category5_adversarial.yaml"
    )
    assert command[command.index("--max_test_queries_ablation") + 1] == "446"


def test_memorydata_runner_blocks_until_prereqs_are_ready(tmp_path: Path) -> None:
    from agent_brain.evaluation.memorydata_runner import (
        MemoryDataRunOptions,
        plan_memorydata_run,
    )

    options = MemoryDataRunOptions(
        memorydata_repo=tmp_path / "MemoryData",
        family="MemoryAgentBench",
        agent_config="config/reference_simple_rag_bm25.yaml",
        artifact_root=tmp_path / "artifacts",
        max_test_queries=1,
    )

    planned = plan_memorydata_run(
        options,
        prereqs={
            "dependencies_ready": False,
            "missing_dependencies": ["datasets"],
            "datasets_ready": False,
            "endpoint_ready": False,
            "endpoint_note": "endpoint missing",
        },
    )

    assert planned["status"] == "blocked"
    assert "missing dependencies: datasets" in planned["reason"]
    assert "missing datasets" in planned["reason"]
    assert "model endpoint not ready" in planned["reason"]
    assert planned["command"][:2] == ["python", "main.py"]


def test_memorydata_runner_marks_zero_exit_run_failed_when_result_contains_failed_query(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from subprocess import CompletedProcess

    from agent_brain.evaluation import memorydata_runner
    from agent_brain.evaluation.memorydata_runner import MemoryDataRunOptions, run_memorydata

    memorydata_repo = tmp_path / "MemoryData"
    memorydata_repo.mkdir()
    (memorydata_repo / "main.py").write_text("print('fixture')\n", encoding="utf-8")
    artifact_root = tmp_path / "artifacts"

    def fake_run(*args, **kwargs):
        del args, kwargs
        results_path = artifact_root / "outputs" / "fixture_results.json"
        results_path.parent.mkdir(parents=True)
        results_path.write_text(
            json.dumps({"data": [{"status": "failed", "error": "missing module"}]}),
            encoding="utf-8",
        )
        return CompletedProcess(["python", "main.py"], 0, stdout="ok", stderr="")

    monkeypatch.setattr(memorydata_runner.subprocess, "run", fake_run)

    run = run_memorydata(
        MemoryDataRunOptions(
            memorydata_repo=memorydata_repo,
            artifact_root=artifact_root,
        ),
        prereqs={
            "dependencies_ready": True,
            "missing_dependencies": [],
            "datasets_ready": True,
            "endpoint_ready": True,
        },
    )

    assert run["status"] == "failed"
    assert run["memorydata_failed_query_count"] == 1
    assert "failed query" in run["reason"]


def test_memorydata_runner_passes_absolute_artifact_root_to_upstream_process(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from subprocess import CompletedProcess

    from agent_brain.evaluation import memorydata_runner
    from agent_brain.evaluation.memorydata_runner import MemoryDataRunOptions, run_memorydata

    memorydata_repo = tmp_path / "MemoryData"
    memorydata_repo.mkdir()
    (memorydata_repo / "main.py").write_text("print('fixture')\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    relative_artifacts = Path("relative-memorydata-artifacts")
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["cwd"] = kwargs["cwd"]
        artifact_root = Path(command[command.index("--artifact_root") + 1])
        (artifact_root / "outputs").mkdir(parents=True, exist_ok=True)
        (artifact_root / "outputs" / "fixture_results.json").write_text(
            json.dumps({"data": [{"status": "passed"}]}),
            encoding="utf-8",
        )
        return CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(memorydata_runner.subprocess, "run", fake_run)

    run = run_memorydata(
        MemoryDataRunOptions(
            memorydata_repo=memorydata_repo,
            artifact_root=relative_artifacts,
        ),
        prereqs={
            "dependencies_ready": True,
            "missing_dependencies": [],
            "datasets_ready": True,
            "endpoint_ready": True,
        },
    )

    command = captured["command"]
    artifact_root_arg = Path(command[command.index("--artifact_root") + 1])
    assert artifact_root_arg.is_absolute()
    assert artifact_root_arg == relative_artifacts.resolve()
    assert captured["cwd"] == memorydata_repo
    assert run["status"] == "passed"
    assert run["artifact_root"] == "relative-memorydata-artifacts"
    assert run["run_record"] == "relative-memorydata-artifacts/run-record.json"


def test_memorydata_runner_redacts_public_run_record(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from subprocess import CompletedProcess

    from agent_brain.evaluation import memorydata_runner
    from agent_brain.evaluation.memorydata_runner import MemoryDataRunOptions, run_memorydata

    memorydata_repo = tmp_path / "MemoryData"
    memorydata_repo.mkdir()
    (memorydata_repo / "main.py").write_text("print('fixture')\n", encoding="utf-8")
    artifact_root = tmp_path / "artifacts"

    def fake_run(command, **kwargs):
        artifact_root_arg = Path(command[command.index("--artifact_root") + 1])
        (artifact_root_arg / "outputs").mkdir(parents=True, exist_ok=True)
        (artifact_root_arg / "outputs" / "fixture_results.json").write_text(
            json.dumps({
                "config": {
                    "output_dir": str(Path.home() / "private-cache" / "outputs"),
                    "artifact_root": str(artifact_root_arg),
                },
                "data": [{"status": "passed"}],
            }),
            encoding="utf-8",
        )
        return CompletedProcess(
            command,
            0,
            stdout=f"saved at <example-cache> and {Path.home()}/private-cache",
            stderr="",
        )

    monkeypatch.setattr(memorydata_runner.subprocess, "run", fake_run)

    run = run_memorydata(
        MemoryDataRunOptions(
            memorydata_repo=memorydata_repo,
            artifact_root=artifact_root,
        ),
        prereqs={
            "dependencies_ready": True,
            "missing_dependencies": [],
            "datasets_ready": True,
            "endpoint_ready": True,
        },
    )

    serialized = json.dumps(run, ensure_ascii=False)
    run_record = json.loads((artifact_root / "run-record.json").read_text(encoding="utf-8"))
    record_serialized = json.dumps(run_record, ensure_ascii=False)
    assert str(Path.home()) not in serialized
    assert str(Path.home()) not in record_serialized
    assert "~" in run["stdout_tail"]
    artifact_text = (artifact_root / "outputs" / "fixture_results.json").read_text(encoding="utf-8")
    assert str(Path.home()) not in artifact_text
    assert str(artifact_root) not in artifact_text


def test_external_report_distinguishes_memorydata_smoke_from_full(tmp_path: Path) -> None:
    from agent_brain.evaluation.external_memory_benchmark import (
        ExternalBenchmarkOptions,
        build_external_benchmark_report,
    )

    memorydata = tmp_path / "MemoryData"
    _fake_memorydata_repo(memorydata)
    run = {
        "name": "memorydata-memoryagentbench-smoke",
        "family": "MemoryAgentBench",
        "status": "passed",
        "run_level": "smoke",
        "command": ["python", "main.py"],
        "artifact": str(tmp_path / "artifact"),
    }

    report = build_external_benchmark_report(
        _system_report(),
        ExternalBenchmarkOptions(
            memorydata_repo=memorydata,
            generated_at=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
        ),
        memorydata_runs=[run],
    )

    payload = report.to_dict()
    assert payload["status"] == "PASS_WITH_MEMORYDATA_SMOKE"
    stages = {stage["id"]: stage["status"] for stage in payload["memory_evaluation_loop"]["stages"]}
    assert stages["smoke_run"] == "done"
    assert stages["full_matrix"] == "planned"
    assert "PASS_WITH_MEMORYDATA_SMOKE" in report.to_markdown()


def test_external_report_marks_memorydata_dataset_materialization_partial(
    tmp_path: Path,
) -> None:
    from agent_brain.evaluation.external_memory_benchmark import (
        ExternalBenchmarkOptions,
        build_external_benchmark_report,
    )

    memorydata = tmp_path / "MemoryData"
    _fake_memorydata_repo(memorydata)
    for dataset_dir in [
        memorydata / "datasets" / "MemoryAgentBench" / "eval_dataset_collection",
        memorydata / "datasets" / "MemBench" / "MemData" / "FirstAgent",
    ]:
        dataset_dir.mkdir(parents=True)
        (dataset_dir / "sentinel.json").write_text("{}", encoding="utf-8")

    report = build_external_benchmark_report(
        _system_report(),
        ExternalBenchmarkOptions(
            memorydata_repo=memorydata,
            generated_at=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            check_endpoint=False,
        ),
    )

    payload = report.to_dict()
    stages = {stage["id"]: stage["status"] for stage in payload["memory_evaluation_loop"]["stages"]}
    assert stages["dataset_materialize"] == "partial"
    assert {
        row["name"]: row["ready"]
        for row in payload["memorydata_prerequisites"]["dataset_checks"]
        } == {
            "MemoryAgentBench": True,
            "LoCoMo": False,
            "LoCoMoCategory5": False,
            "LongBench": False,
            "LongBenchV2Full": False,
            "MemBench": True,
        }
    markdown = report.to_markdown()
    assert "| 数据集 | partial | LoCoMo; LoCoMoCategory5; LongBench; LongBenchV2Full |" in markdown
    assert "| MemBench | `benchmark/membench/config/MemBench_simple.yaml` | ready |" in markdown
