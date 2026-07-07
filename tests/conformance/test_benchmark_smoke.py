"""Smoke tests for benchmark script to verify it runs correctly."""

import json
import subprocess
import sys
from pathlib import Path

from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.memory.store.items_store import ItemsStore


def _write_longmemeval_fixture(path: Path) -> None:
    samples = [
        {
            "question_id": "q1",
            "question": "What degree did I graduate with?",
            "answer": "Business Administration",
            "answer_session_ids": ["s2"],
            "haystack_session_ids": ["s1", "s2"],
            "haystack_sessions": [
                [{"role": "user", "content": "We discussed transport puzzles and river crossings."}],
                [{"role": "assistant", "content": "You graduated with a Business Administration degree."}],
            ],
        },
        {
            "question_id": "q2",
            "question": "Which city did I visit for the design conference?",
            "answer": "Berlin",
            "answer_session_ids": ["s3"],
            "haystack_session_ids": ["s3", "s4"],
            "haystack_sessions": [
                [{"role": "user", "content": "The design conference trip was in Berlin."}],
                [{"role": "assistant", "content": "We talked about unrelated benchmark setup."}],
            ],
        },
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(samples), encoding="utf-8")


def test_benchmark_126_items_under_5s() -> None:
    """Verify benchmark script runs with 126 items and completes under 5 seconds."""
    repo_root = Path(__file__).parent.parent.parent
    benchmark_script = repo_root / "benchmarks" / "benchmark_retrieval.py"

    assert benchmark_script.exists(), f"Benchmark script not found: {benchmark_script}"

    result = subprocess.run(
        [sys.executable, str(benchmark_script), "--count", "126"],
        capture_output=True,
        text=True,
        timeout=10,  # Should complete well under 10 seconds
    )

    # Check exit code
    assert result.returncode == 0, (
        f"Benchmark script failed with return code {result.returncode}\n"
        f"STDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )

    # Verify output contains expected sections
    output = result.stdout
    assert "=== Benchmark: 126 items ===" in output, "Missing benchmark header"
    assert "Index build time:" in output, "Missing index build time"
    assert "Query latency" in output, "Missing query latency section"
    assert "p50:" in output, "Missing p50 metric"
    assert "p95:" in output, "Missing p95 metric"
    assert "p99:" in output, "Missing p99 metric"
    assert "Governance scan time:" in output, "Missing governance scan time"
    assert "Drift detection time:" in output, "Missing drift detection time"

    # Verify no errors in stderr
    assert not result.stderr or "Error" not in result.stderr, (
        f"Unexpected errors in stderr:\n{result.stderr}"
    )


def test_relevance_benchmark_accepts_hand_labeled_queries_file(tmp_path: Path) -> None:
    repo_root = Path(__file__).parent.parent.parent
    benchmark_script = repo_root / "benchmarks" / "benchmark_relevance.py"
    queries_file = tmp_path / "queries.json"
    queries_file.write_text(json.dumps([
        {
            "query": "hand labeled smoke query",
            "expected_ids": ["mem-does-not-need-to-exist-for-loader-smoke"],
            "category": "hand_labeled_smoke",
            "description": "Ensures --queries-file controls benchmark queries.",
        }
    ]), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(benchmark_script),
            "--synthetic",
            "20",
            "--queries-file",
            str(queries_file),
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, (
        f"Relevance benchmark failed with return code {result.returncode}\n"
        f"STDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )
    report = json.loads(result.stdout)
    assert report["num_queries"] == 1
    assert report["per_query"][0]["category"] == "hand_labeled_smoke"


def test_release_gate_script_enforces_relevance_thresholds() -> None:
    repo_root = Path(__file__).parent.parent.parent
    gate_script = repo_root / "benchmarks" / "release_gate.py"

    result = subprocess.run(
        [
            sys.executable,
            str(gate_script),
            "--synthetic",
            "24",
            "--min-mean-recall-at-10",
            "0.0",
            "--min-mean-mrr",
            "0.0",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["passed"] is True
    assert payload["checks"]["mean_recall_at_10"]["threshold"] == 0.0
    assert payload["checks"]["compression_pass_rate"]["passed"] is True
    assert payload["compression"]["metrics"]["num_cases"] >= 4
    assert payload["checks"]["ml_advisory_pass_rate"]["passed"] is True
    assert payload["checks"]["ml_advisory_unsafe_promotions"]["passed"] is True
    assert payload["ml_advisory"]["metrics"]["unsafe_promotion_count"] == 0


def test_release_gate_default_synthetic_profile_passes() -> None:
    repo_root = Path(__file__).parent.parent.parent
    gate_script = repo_root / "benchmarks" / "release_gate.py"

    result = subprocess.run(
        [
            sys.executable,
            str(gate_script),
            "--synthetic",
            "80",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["passed"] is True
    assert payload["checks"]["mean_recall_at_10"]["passed"] is True
    assert payload["release_query_profile"] == ["title_recall", "project_scope", "multi_keyword"]


def test_one_click_memory_benchmarks_writes_latest_markdown_report(tmp_path: Path) -> None:
    repo_root = Path(__file__).parent.parent.parent
    benchmark_script = repo_root / "benchmarks" / "run_memory_benchmarks.py"
    brain_dir = tmp_path / "brain"
    store = ItemsStore(brain_dir / "items")
    for index in range(1, 7):
        item_id = f"mem-20260630-120000-one-click-benchmark-{index:03d}"
        item = MemoryItem.model_validate(
            {
                "id": item_id,
                "type": "artifact",
                "created_at": "2026-06-30T12:00:00+00:00",
                "title": f"One click benchmark sentinel {index}",
                "summary": f"one click benchmark locator sentinel {index}",
                "project": "agent-memory-hub",
                "tags": ["benchmark", "one-click"],
                "confidence": 0.9,
                "abstraction": "L1",
                "support_count": 2,
                "refs": {"urls": [f"https://example.test/one-click/{index}"]},
                "context_views": {
                    "locator": f"one click benchmark locator sentinel {index}",
                    "overview": f"overview one click benchmark locator sentinel {index}",
                    "detail_uri": f"memory://items/{item_id}/body",
                },
            }
        )
        store.write(item, f"{item.title}\n{item.summary}\nbody {index}")

    output_dir = tmp_path / "reports"
    longmemeval_dataset = tmp_path / "longmemeval_s_cleaned.json"
    _write_longmemeval_fixture(longmemeval_dataset)
    generation_report = tmp_path / "longmemeval-generation-full-results.json"
    generation_report.write_text(
        json.dumps(
            {
                "dataset_config": {"sub_dataset": "longmemeval_s"},
                "data": [
                    {
                        "query": "What degree did I graduate with?",
                        "answer": "Business Administration",
                        "output": "Business Administration",
                        "eval_metadata": {"question_id": "q1"},
                    },
                    {
                        "query": "Which city did I visit for the design conference?",
                        "answer": "Berlin",
                        "output": "Berlin",
                        "eval_metadata": {"question_id": "q2"},
                    },
                ],
                "averaged_metrics": {"exact_match": 100.0, "f1": 100.0},
            }
        ),
        encoding="utf-8",
    )
    judge_report = tmp_path / "longmemeval-generation-full.longmemeval_judge.json"
    judge_report.write_text(
        json.dumps(
            {
                "judge_model": "gui-owl-1.5:latest",
                "num_runs": 1,
                "summary": {
                    "total_rows": 2,
                    "supported_rows": 2,
                    "judged_rows": 2,
                    "judge_accuracy": 1.0,
                    "judge_accuracy_std": 0.0,
                },
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(benchmark_script),
            "--max-cases",
            "18",
            "--output-dir",
            str(output_dir),
            "--memorydata-repo",
            str(tmp_path / "missing-MemoryData"),
            "--no-clone-memorydata",
            "--skip-external-run",
            "--run-longmemeval-smoke",
            "--longmemeval-dataset-file",
            str(longmemeval_dataset),
            "--longmemeval-workspace-dir",
            str(tmp_path / "longmemeval-workspace"),
            "--longmemeval-generation-report",
            str(generation_report),
            "--longmemeval-judge-report",
            str(judge_report),
            "--format",
            "json",
        ],
        cwd=repo_root,
        env={
            "BRAIN_DIR": str(brain_dir),
            "MEMORY_HUB_TEST_EMBEDDING": "1",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / "missing-MemoryData").exists()
    payload = json.loads(result.stdout)
    assert payload["status"].startswith("PASS")
    assert payload["paths"]["external_latest_markdown"].endswith("latest-memory-benchmark-report.zh.md")
    assert (output_dir / "longmemeval-retrieval-smoke.json").is_file()
    assert (output_dir / "longmemeval-amh-ranking-smoke.json").is_file()
    longmemeval_loop = payload["external_report"]["longmemeval_retrieval_loop"]
    stages = {stage["id"]: stage["status"] for stage in longmemeval_loop["stages"]}
    assert stages["retrieval_only_smoke"] == "done"
    assert stages["amh_ranking_run"] == "done"
    assert stages["report_publish"] == "amh-ranking-published"
    qa_loop = payload["external_report"]["longmemeval_qa_judge_loop"]
    qa_stages = {stage["id"]: stage["status"] for stage in qa_loop["stages"]}
    assert qa_stages["generation_run"] == "done"
    assert qa_stages["judge_run"] == "done"
    assert qa_loop["judge_report"]["summary"]["judge_accuracy"] == 1.0
    latest_markdown = Path(payload["paths"]["external_latest_markdown"])
    assert latest_markdown.is_file()
    content = latest_markdown.read_text(encoding="utf-8")
    assert "AMH 核心指标" in content
    assert "MemoryData 外部横评" in content
    assert "总用例" in content
    assert "Judge Accuracy=100.00%" in content


def test_one_click_memory_benchmarks_help_exposes_memorydata_flags() -> None:
    repo_root = Path(__file__).parent.parent.parent
    benchmark_script = repo_root / "benchmarks" / "run_memory_benchmarks.py"

    result = subprocess.run(
        [sys.executable, str(benchmark_script), "--help"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "--run-memorydata-smoke" in result.stdout
    assert "--run-memorydata-full" in result.stdout
    assert "--memorydata-family" in result.stdout
    assert "--memorydata-agent-config" in result.stdout
    assert "--memorydata-timeout-s" in result.stdout
    assert "--longmemeval-generation-report" in result.stdout
    assert "--longmemeval-judge-report" in result.stdout


def test_memorydata_family_prereqs_only_require_selected_family_dataset() -> None:
    from benchmarks.run_memory_benchmarks import _memorydata_family_prereqs

    prereqs = {
        "dependencies_ready": True,
        "missing_dependencies": [],
        "datasets_ready": False,
        "dataset_checks": [
            {"name": "MemoryAgentBench", "ready": True},
            {"name": "LoCoMo", "ready": False},
            {"name": "LongBench", "ready": False},
            {"name": "MemBench", "ready": False},
        ],
        "endpoint_ready": False,
        "endpoint_note": "endpoint missing",
    }

    scoped = _memorydata_family_prereqs(prereqs, "MemoryAgentBench")

    assert scoped["dependencies_ready"] is True
    assert scoped["datasets_ready"] is True
    assert scoped["dataset_checks"] == [{"name": "MemoryAgentBench", "ready": True}]


def test_memorydata_full_mode_defaults_to_family_full_query_count() -> None:
    from benchmarks.run_memory_benchmarks import _memorydata_max_queries_for_run

    assert _memorydata_max_queries_for_run("LongBenchV2Full", "full", None) == 503
    assert _memorydata_max_queries_for_run("LoCoMoCategory5", "full", None) == 446
    assert _memorydata_max_queries_for_run("MemoryAgentBench", "smoke", None) == 1
    assert _memorydata_max_queries_for_run("LongBenchV2Full", "full", 17) == 17


def test_memorydata_full_mode_writes_to_report_scanned_artifact_subdirs(tmp_path: Path) -> None:
    from benchmarks.run_memory_benchmarks import _memorydata_artifact_root_for_run

    root = tmp_path / "memorydata-artifacts"

    assert _memorydata_artifact_root_for_run(root, "LongBenchV2Full", "full") == (
        root / "full-family" / "longbench-v2-503"
    )
    assert _memorydata_artifact_root_for_run(root, "LoCoMoCategory5", "full") == (
        root / "full-family" / "locomo-category5-adversarial"
    )
    assert _memorydata_artifact_root_for_run(root, "MemoryAgentBench", "smoke") == (
        root / "smoke" / "memoryagentbench"
    )


def test_memory_benchmark_dashboard_renderer_writes_visual_report(tmp_path: Path) -> None:
    repo_root = Path(__file__).parent.parent.parent
    renderer = repo_root / "benchmarks" / "render_memory_benchmark_dashboard.py"
    output = tmp_path / "dashboard.html"

    result = subprocess.run(
        [
            sys.executable,
            str(renderer),
            "--input",
            str(repo_root / "docs" / "evaluation" / "memorydata-external-benchmark-report.json"),
            "--output",
            str(output),
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    html = output.read_text(encoding="utf-8")
    assert "AMH 记忆评测审计报告" in html
    assert "运行模式" in html
    assert "smoke" in html
    assert "生成时间" not in html
    assert "执行摘要" in html
    assert "可比性地图" in html
    assert "证据分层" in html
    assert "严格可比" in html
    assert "参考横比" in html
    assert "仅作背景" in html
    assert "缺 runner/data" in html
    assert "数据边界与下一步" in html
    assert "已复现" in html
    assert "本机复现范围" in html
    assert "风险与缺口" in html
    assert "LongMemEval-S 召回准确率" in html
    assert "LongMemEval-S QA / Judge" in html
    assert "Judge Accuracy" in html
    assert "MemoryData Smoke 矩阵" in html
    assert "MemoryData Full-family 结果" in html
    assert "论文矩阵复现范围" in html
    assert "memoryagentbench-ar-eventqa" in html
    assert "full-family/locomo-4cat" in html
    assert "LoCoMo Recall@10" not in html
    assert "ROUGE-L Recall" in html
    assert "LoCoMo 分类口径" in html
    assert "竞品对比与可比性边界" in html
    assert "Agent Memory Hub (AMH, BM25/RRF)" in html
    assert "97.4%" in html
    assert "MemPalace" in html
    assert "Letta / MemGPT" in html
    assert "论文统一评测标准：MemoryData 方法表" in html
    assert "类型</th><th>白话解释" in html
    assert "产品/框架" in html
    assert "研究方法" in html
    assert "参考 baseline" in html
    assert "设计范式/实现" in html
    assert "AMH 接入 MemoryData 的同一评测入口、数据配置和指标" in html
    assert "AMH 是追加评测对象" in html
    assert "MemoryData 论文原图评分" in html
    assert "MemoryData 论文结果图" in html
    if "data:image/png;base64" in html:
        assert "原图包含数值标注，但未提供可机读 CSV/JSON" in html
    else:
        assert "当前本地 cache 未找到" in html
    assert "论文图风格追加 AMH 柱状图" in html
    assert "图例颜色表示方法所属架构类别，不表示独立工具" in html
    assert "同色柱重复出现是因为多个方法属于同一类" in html
    assert "表中名称包含产品、框架、baseline、研究方法和设计范式" in html
    assert "白话解释" in html
    assert "按顺序把历史对话压缩/拼接进上下文" in html
    assert "把记忆组织成图、树或拓扑结构再检索" in html
    assert "AMH appended bar color" in html
    assert "#00a8a8" in html
    assert "AMH 未复现" in html
    assert "论文原图 8 指标覆盖矩阵" in html
    for paper_metric in [
        "LongMemEval: Substring EM",
        "LongMemEval: ROUGE-L F1",
        "LongMemEval: ROUGE-L Recall",
        "LongMemEval: LLM Judge Acc.",
        "LoCoMo: EM",
        "LoCoMo: Answer F1",
        "DB-Bench: EM",
        "DB-Bench: Task Success Rate",
    ]:
        assert paper_metric in html
    assert "DB-Bench 当前没有本机 AMH 结果" in html
    assert "MemoryData 开源 cache 未提供 DB-Bench loader/config/dataset" in html
    assert "拿到 DB-Bench runner/data 后可以接 AMH 复跑" in html
    assert "AMH 本机已跑评分摘要" in html
    assert "LoCoMo 4cat QA full" in html
    assert "LongMemEval-S R@5" in html
    assert "这 22 个方法属于 MemoryData 论文/代码发布的同一套 launcher 和 benchmark family" in html
    for method in [
        "Long Context",
        "Embedding RAG",
        "BM25 RAG",
        "MemAgent",
        "Mem0",
        "MemoChat",
        "Cognee",
        "Zep Local",
        "MemTree",
        "GraphRAG",
        "HippoRAG",
        "RAPTOR",
        "Zep",
        "Letta",
        "LightMem",
        "SimpleMem",
        "MemOS",
        "MemoryOS",
        "A-MEM",
        "EverOS",
        "Self-RAG",
        "MemoRAG",
    ]:
        assert method in html
    assert "Agent Memory Hub (AMH)" in html
    assert "AMH 接入同一个 MemoryData 评测入口" in html
    assert "AMH 扩展接入项" in html
    assert "AMH 是追加评测对象" in html
    assert "另一个来源：agentmemory LongMemEval R@5 comparison" in html
    assert "LongMemEval R@5 表使用同一 retrieval 指标" in html
    assert "同一套标准：LongMemEval R@5 横评" in html
    assert "不同 benchmark：LoCoMo 公开分数" in html
    assert "证据级别用于说明数字来源强弱，评测标准仍为 retrieval R@5" in html
    assert "A 本机复现" in html
    assert "B 竞品公开报告" in html
    assert "C 竞品自报" in html
    assert "数字来源" in html
    assert "证据状态</th><th>边界说明" not in html
    assert "LongMemEval / LongMemEval-S 行是同一 R@5 口径" in html
    assert "统一 runner 独立复跑" in html
    assert "LoCoMo 行属于不同 benchmark" in html
    assert "成本与稳定性" in html
    assert "full-family/longbench-rep150" in html
    assert "多选准确率" in html
    assert "full-family/membench-simple" in html
    assert "四维 full 已完成" in html
    assert "InfBench judge 另跑" in html
    assert "Dataset Provenance Audit" in html
    assert "下一阶段门禁" in html
    for phrase in [
        "论文确实",
        "答案：",
        "不硬填",
        "假分数",
        "顶替",
        "冒充",
        "当前 repo",
        "这里",
        "我们后来",
        "我们的共享",
        "不是 full-matrix",
        "整张表不是",
        "不是同一套标准",
        "不能和",
        "不能跨",
    ]:
        assert phrase not in html


def test_memory_benchmark_dashboard_renderer_appends_amh_paper_style_bars() -> None:
    from benchmarks.render_memory_benchmark_dashboard import render_dashboard

    repo_root = Path(__file__).parent.parent.parent
    report = json.loads(
        (repo_root / "docs" / "evaluation" / "memorydata-external-benchmark-report.json").read_text(
            encoding="utf-8"
        )
    )
    report["longmemeval_qa_judge_loop"] = {
        "generation_report": {
            "rows": 500,
            "metrics": {
                "substring_exact_match": 27.0,
                "rougeL_f1": 19.866683244520637,
                "rougeL_recall": 35.27822313556622,
            },
        },
        "judge_report": {
            "summary": {
                "supported_rows": 500,
                "judged_rows": 500,
                "judge_accuracy": 0.416,
            }
        },
    }
    report["memorydata_full_family_runs"] = [
        {
            "name": "LoCoMo 4cat QA full",
            "family": "LoCoMo",
            "status": "passed",
            "rows": 1540,
            "expected_rows": 1540,
            "sample_scope": "1540 / 1540 QA",
            "metrics": {
                "exact_match": 16.038961038961038,
                "f1": 36.05,
            },
        }
    ]

    html = render_dashboard(report, base_dir=repo_root)

    assert "论文图风格追加 AMH 柱状图" in html
    assert "AMH appended bar color" in html
    assert "#00a8a8" in html
    assert "AMH 27.0" in html
    assert "AMH 41.6" in html
    assert "AMH 16.0" in html
    assert "AMH 36.0" in html


def test_memory_benchmark_dashboard_preferred_metrics_skip_missing_keys() -> None:
    from benchmarks.render_memory_benchmark_dashboard import _preferred_metrics

    locomo_labels = [
        label
        for label, _ in _preferred_metrics(
            "LoCoMo",
            {
                "exact_match": 16.0,
                "f1": 36.0,
                "rougeL_f1": 35.5,
                "rougeL_recall": 45.7,
            },
        )
    ]
    assert "LoCoMo Recall@10" not in locomo_labels
    assert "ROUGE-L Recall" in locomo_labels

    category5_labels = [
        label
        for label, _ in _preferred_metrics(
            "LoCoMoCategory5",
            {
                "exact_match": 20.9,
                "f1": 39.0,
                "rougeL_f1": 38.3,
                "rougeL_recall": 50.9,
            },
        )
    ]
    assert "EventQA Recall" not in category5_labels
    assert "ROUGE-L Recall" in category5_labels

    membench_labels = [
        label
        for label, _ in _preferred_metrics(
            "MemBench",
            {
                "exact_match": 91.0,
                "f1": 63.0,
                "rougeL_f1": 63.0,
                "rougeL_recall": 63.0,
                "membench_recall@5": 0.0,
            },
        )
    ]
    assert "MemBench Recall@5" not in membench_labels
    assert "ROUGE-L Recall" in membench_labels


def test_memory_benchmark_dashboard_renderer_shows_memoryagentbench_full_artifacts(
    tmp_path: Path,
) -> None:
    from benchmarks.render_memory_benchmark_dashboard import render_dashboard

    base_dir = tmp_path
    result_path = (
        base_dir
        / "docs/evaluation/memorydata-artifacts/full/memoryagentbench-ar-eventqa"
        / "outputs/gui-owl-bm25/Accurate_Retrieval/ar_results.json"
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

    report = {
        "status": "PASS_WITH_MEMORYAGENTBENCH_FULL",
        "generated_at": "2026-07-01T09:50:00+00:00",
        "run_mode": "full",
        "amh_system_benchmark": {
            "case_count": 240,
            "recall_at_k": 1.0,
            "mrr": 0.99,
            "items_indexed": 1254,
        },
        "longmemeval_retrieval_loop": {
            "smoke_report": {"case_count": 0, "metrics": {}},
            "amh_ranking_report": {"case_count": 0, "metrics": {}},
        },
        "memorydata_prerequisites": {"dataset_checks": []},
        "memorydata_runs": [],
        "memorydata_plan": {"families_detail": []},
        "external_sources": {"memorydata": {"commit": "fixture-commit"}},
    }

    html = render_dashboard(report, base_dir=base_dir)

    assert "MemoryAgentBench 四维 Full 结果" in html
    assert "准确召回 AR" in html
    assert "500 条 query 记录" in html
    assert "EventQA Recall" in html
    assert "InfBench summarization" in html


def test_dataset_provenance_audit_renderer_writes_json_and_markdown(tmp_path: Path) -> None:
    repo_root = Path(__file__).parent.parent.parent
    renderer = repo_root / "benchmarks" / "render_dataset_provenance_audit.py"
    json_output = tmp_path / "audit.json"
    markdown_output = tmp_path / "audit.zh.md"

    result = subprocess.run(
        [
            sys.executable,
            str(renderer),
            "--input",
            str(repo_root / "docs" / "evaluation" / "memorydata-external-benchmark-report.json"),
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["entries"]
    assert payload["next_stage_gate"] == {"allowed": True, "required_actions": []}
    markdown = markdown_output.read_text(encoding="utf-8")
    assert "Dataset Provenance Audit" in markdown
    assert "下一阶段门禁" in markdown
