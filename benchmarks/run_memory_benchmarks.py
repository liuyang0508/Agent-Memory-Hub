#!/usr/bin/env python3
"""One-click AMH + external memory benchmark report runner."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from agent_brain.evaluation.external_memory_benchmark import (  # noqa: E402
    ExternalBenchmarkOptions,
    build_external_benchmark_report,
    inspect_memorydata_prerequisites,
    write_external_benchmark_report,
)
from agent_brain.evaluation.longmemeval_retrieval import (  # noqa: E402
    run_longmemeval_amh_ranking,
    run_longmemeval_retrieval_smoke,
)
from agent_brain.evaluation.memorydata_runner import (  # noqa: E402
    FAMILY_CONFIGS,
    MemoryDataRunOptions,
    run_memorydata,
)
from agent_brain.evaluation.professional_report import (  # noqa: E402
    load_adapter_capability_records,
    write_professional_evaluation_report,
)
from agent_brain.evaluation.system_benchmark import (  # noqa: E402
    build_synthetic_system_cases,
    load_items,
    run_system_benchmark_on_items,
)
from agent_brain.interfaces.cli._shared import _brain_dir  # noqa: E402


MEMORYDATA_FULL_QUERY_COUNTS = {
    "MemoryAgentBench": 500,
    "MemoryAgentBenchTTL": 100,
    "MemoryAgentBenchLRU": 71,
    "MemoryAgentBenchCR": 100,
    "LoCoMo": 1540,
    "LoCoMoCategory5": 446,
    "LongBench": 150,
    "LongBenchV2Full": 503,
    "MemBench": 100,
    "MemBenchNoisy": 100,
    "MemBenchKnowledgeUpdate": 100,
    "MemBenchHighlevel": 150,
    "MemBenchRecMultiSession": 50,
}

MEMORYDATA_FULL_ARTIFACT_SUBDIRS = {
    "MemoryAgentBench": Path("full/memoryagentbench-ar-eventqa"),
    "MemoryAgentBenchTTL": Path("full/memoryagentbench-ttl-icl-banking77"),
    "MemoryAgentBenchLRU": Path("full/memoryagentbench-lru-detectiveqa"),
    "MemoryAgentBenchCR": Path("full/memoryagentbench-cr-fact-mh-6k"),
    "LoCoMo": Path("full-family/locomo-4cat"),
    "LoCoMoCategory5": Path("full-family/locomo-category5-adversarial"),
    "LongBench": Path("full-family/longbench-rep150"),
    "LongBenchV2Full": Path("full-family/longbench-v2-503"),
    "MemBench": Path("full-family/membench-simple"),
    "MemBenchNoisy": Path("full-family/membench-noisy"),
    "MemBenchKnowledgeUpdate": Path("full-family/membench-knowledge-update"),
    "MemBenchHighlevel": Path("full-family/membench-highlevel"),
    "MemBenchRecMultiSession": Path("full-family/membench-recmultisession"),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run AMH local benchmark and write latest external-memory benchmark reports."
    )
    parser.add_argument("--output-dir", type=Path, default=Path("docs/evaluation"))
    parser.add_argument("--memorydata-repo", type=Path, default=Path(".cache/external/MemoryData"))
    parser.add_argument("--dataset-cache-root", type=Path, default=Path(".cache/external"))
    parser.add_argument("--max-items", type=int, default=None)
    parser.add_argument("--max-cases", type=int, default=240)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--min-block-accuracy", type=float, default=1.0)
    parser.add_argument("--min-inject-accuracy", type=float, default=0.95)
    parser.add_argument("--min-recall-at-k", type=float, default=0.85)
    parser.add_argument("--min-firewall-include-rate", type=float, default=0.85)
    parser.add_argument("--min-pack-reversible-rate", type=float, default=1.0)
    parser.add_argument(
        "--run-external-smoke",
        action="store_true",
        help="Deprecated alias for --run-memorydata-smoke.",
    )
    parser.add_argument(
        "--run-memorydata-smoke",
        action="store_true",
        help="Run MemoryData smoke when prereqs are ready.",
    )
    parser.add_argument(
        "--run-memorydata-full",
        action="store_true",
        help="Run MemoryData full family matrix when prereqs are ready.",
    )
    parser.add_argument(
        "--memorydata-family",
        action="append",
        dest="memorydata_families",
        default=None,
        help="MemoryData family to run; repeat for multiple families.",
    )
    parser.add_argument(
        "--memorydata-agent-config",
        default="config/reference_simple_rag_bm25.yaml",
        help="MemoryData agent config path passed to upstream main.py.",
    )
    parser.add_argument(
        "--memorydata-timeout-s",
        type=int,
        default=1800,
        help="Timeout in seconds for each MemoryData family run.",
    )
    parser.add_argument("--skip-external-run", action="store_true")
    parser.add_argument(
        "--run-longmemeval-smoke",
        action="store_true",
        help="Run LongMemEval lexical and AMH ranking smoke reports before writing the latest report.",
    )
    parser.add_argument(
        "--longmemeval-dataset-file",
        type=Path,
        default=Path(".cache/external/LongMemEval/data/longmemeval_s_cleaned.json"),
    )
    parser.add_argument("--longmemeval-max-cases", type=int, default=5)
    parser.add_argument("--longmemeval-workspace-dir", type=Path, default=None)
    parser.add_argument(
        "--longmemeval-generation-report",
        type=Path,
        default=None,
        help="Existing LongMemEval-S generation result JSON to include in the unified report.",
    )
    parser.add_argument(
        "--longmemeval-judge-report",
        type=Path,
        default=None,
        help="Existing LongMemEval-S LLM-as-judge sidecar JSON to include in the unified report.",
    )
    parser.add_argument(
        "--no-clone-memorydata",
        action="store_true",
        help="Do not clone MemoryData automatically; useful for offline tests and hermetic CI.",
    )
    parser.add_argument("--max-test-queries-ablation", type=int, default=None)
    parser.add_argument(
        "--memorydata-query-start-index",
        type=int,
        default=0,
        help="Global MemoryData query index to start from; use with --max-test-queries-ablation as shard end.",
    )
    parser.add_argument("--format", choices=["summary", "json"], default="summary")
    args = parser.parse_args(argv)

    if not args.no_clone_memorydata:
        ensure_memorydata_repo(args.memorydata_repo)

    os.environ.setdefault("MEMORY_HUB_TEST_EMBEDDING", "1")
    brain_dir = _brain_dir()
    items = load_items(brain_dir, max_items=args.max_items)
    cases = build_synthetic_system_cases(items, max_cases=args.max_cases)
    system_report = run_system_benchmark_on_items(
        brain_dir,
        items,
        cases,
        top_k=args.top_k,
        min_block_accuracy=args.min_block_accuracy,
        min_inject_accuracy=args.min_inject_accuracy,
        min_recall_at_k=args.min_recall_at_k,
        min_firewall_include_rate=args.min_firewall_include_rate,
        min_pack_reversible_rate=args.min_pack_reversible_rate,
    )

    adapter_records, adapter_error = load_adapter_capability_records(brain_dir)
    local_written = write_professional_evaluation_report(
        args.output_dir,
        system_report,
        adapter_capabilities=adapter_records,
        adapter_error=adapter_error,
    )

    longmemeval_smoke_path = args.output_dir / "longmemeval-retrieval-smoke.json"
    longmemeval_amh_path = args.output_dir / "longmemeval-amh-ranking-smoke.json"
    if args.run_longmemeval_smoke:
        longmemeval_workspace = (
            args.longmemeval_workspace_dir
            or args.dataset_cache_root / "LongMemEval" / "amh-ranking-workspace-smoke"
        )
        try:
            lexical_report = run_longmemeval_retrieval_smoke(
                dataset_file=args.longmemeval_dataset_file,
                max_cases=args.longmemeval_max_cases,
                top_ks=(5, 10),
            )
            amh_report = run_longmemeval_amh_ranking(
                dataset_file=args.longmemeval_dataset_file,
                max_cases=args.longmemeval_max_cases,
                top_ks=(5, 10),
                workspace_dir=longmemeval_workspace,
            )
        except Exception as exc:
            print(f"LongMemEval smoke failed: {exc}", file=sys.stderr)
            return 1
        args.output_dir.mkdir(parents=True, exist_ok=True)
        longmemeval_smoke_path.write_text(
            json.dumps(lexical_report, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        longmemeval_amh_path.write_text(
            json.dumps(amh_report, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    run_memorydata_smoke_requested = args.run_memorydata_smoke or args.run_external_smoke
    run_mode = "source-lock"
    memorydata_runs = None
    if not args.skip_external_run:
        if args.run_memorydata_full:
            run_mode = "full"
        elif run_memorydata_smoke_requested:
            run_mode = "smoke"
    external_options = ExternalBenchmarkOptions(
        memorydata_repo=args.memorydata_repo,
        dataset_cache_root=args.dataset_cache_root,
        memorydata_agent_config=args.memorydata_agent_config,
        longmemeval_smoke_report=longmemeval_smoke_path,
        longmemeval_amh_report=longmemeval_amh_path,
        longmemeval_rk_report=args.output_dir / "longmemeval-retrieval-rk-full.json",
        longmemeval_amh_rk_report=args.output_dir / "longmemeval-amh-ranking-rk-full.json",
        longmemeval_generation_report=args.longmemeval_generation_report,
        longmemeval_judge_report=args.longmemeval_judge_report,
        run_mode=run_mode,
        artifact_root=args.output_dir / "memorydata-artifacts",
        max_test_queries=args.max_test_queries_ablation or 1,
    )
    if not args.skip_external_run and (run_memorydata_smoke_requested or args.run_memorydata_full):
        prereqs = inspect_memorydata_prerequisites(
            args.memorydata_repo,
            agent_config=args.memorydata_agent_config,
            env=os.environ,
        )
        memorydata_runs = []
        if run_memorydata_smoke_requested:
            smoke_families = args.memorydata_families or ["MemoryAgentBench"]
            memorydata_runs.extend(
                _run_memorydata_families(
                    memorydata_repo=args.memorydata_repo,
                    families=smoke_families,
                    run_level="smoke",
                    agent_config=args.memorydata_agent_config,
                    artifact_root=args.output_dir / "memorydata-artifacts",
                    max_test_queries=args.max_test_queries_ablation,
                    query_start_index=args.memorydata_query_start_index,
                    timeout_s=args.memorydata_timeout_s,
                    prereqs=prereqs,
                )
            )
        if args.run_memorydata_full:
            full_families = args.memorydata_families or list(FAMILY_CONFIGS)
            memorydata_runs.extend(
                _run_memorydata_families(
                    memorydata_repo=args.memorydata_repo,
                    families=full_families,
                    run_level="full",
                    agent_config=args.memorydata_agent_config,
                    artifact_root=args.output_dir / "memorydata-artifacts",
                    max_test_queries=args.max_test_queries_ablation,
                    query_start_index=args.memorydata_query_start_index,
                    timeout_s=args.memorydata_timeout_s,
                    prereqs=prereqs,
                )
            )

    external_report = build_external_benchmark_report(
        system_report,
        external_options,
        memorydata_runs=memorydata_runs,
    )
    external_written = write_external_benchmark_report(args.output_dir, external_report)

    status = external_report.to_dict()["status"]
    if args.format == "json":
        print(json.dumps({
            "status": status,
            "paths": {
                "local_json": str(local_written.json_path),
                "local_markdown": str(local_written.markdown_path),
                "local_html": str(local_written.html_path),
                "external_json": str(external_written["json"]),
                "external_markdown": str(external_written["markdown"]),
                "external_latest_markdown": str(external_written["latest_markdown"]),
            },
            "external_report": external_report.to_dict(),
        }, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        metrics = system_report.metrics
        query_gate = metrics["query_gate"]
        retrieval = metrics["retrieval"]
        context = metrics["context"]
        print(
            f"Memory benchmark report: {status} "
            f"cases={metrics['case_count']} items={metrics['items_indexed']} "
            f"block={query_gate['block_accuracy']:.3f} "
            f"inject={query_gate['inject_accuracy']:.3f} "
            f"recall@{metrics['top_k']}={retrieval['recall_at_k']:.3f} "
            f"mrr={retrieval['mrr']:.3f} "
            f"firewall_include={context['firewall_include_rate']:.3f} "
            f"firewall_exclude={context.get('firewall_exclude_rate', 0.0):.3f} "
            f"pack={context['pack_reversible_rate']:.3f}"
        )
        print(f"- local markdown: {local_written.markdown_path}")
        print(f"- external latest markdown: {external_written['latest_markdown']}")
        if adapter_error:
            print(f"- adapter capability warning: {adapter_error}")

    if not system_report.passed:
        return 1
    if any(run["status"] == "failed" for run in external_report.to_dict()["memorydata_runs"]):
        return 1
    return 0


def ensure_memorydata_repo(path: Path, *, url: str = "https://github.com/OpenDataBox/MemoryData.git") -> bool:
    """Clone MemoryData into path when absent. Kept separate for manual callers."""

    path = Path(path)
    if (path / "main.py").is_file():
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", url, str(path)],
            capture_output=True,
            text=True,
            timeout=60 * 5,
            env=dict(os.environ, GIT_TERMINAL_PROMPT="0"),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _run_memorydata_families(
    *,
    memorydata_repo: Path,
    families: list[str],
    run_level: str,
    agent_config: str,
    artifact_root: Path,
    max_test_queries: int | None,
    query_start_index: int,
    timeout_s: int,
    prereqs: dict[str, object],
) -> list[dict[str, object]]:
    runs: list[dict[str, object]] = []
    for family in families:
        family_prereqs = _memorydata_family_prereqs(prereqs, family)
        family_max_queries = _memorydata_max_queries_for_run(
            family,
            run_level,
            max_test_queries,
        )
        options = MemoryDataRunOptions(
            memorydata_repo=memorydata_repo,
            family=family,
            run_level=run_level,
            agent_config=agent_config,
            artifact_root=_memorydata_artifact_root_for_run(
                artifact_root,
                family,
                run_level,
            ),
            max_test_queries=family_max_queries,
            query_start_index=query_start_index,
            timeout_s=timeout_s,
        )
        runs.append(run_memorydata(options, prereqs=family_prereqs))
    return runs


def _memorydata_artifact_root_for_run(
    artifact_root: Path,
    family: str,
    run_level: str,
) -> Path:
    if run_level == "full":
        return artifact_root / MEMORYDATA_FULL_ARTIFACT_SUBDIRS.get(
            family,
            Path("full") / family.lower(),
        )
    return artifact_root / run_level / family.lower()


def _memorydata_max_queries_for_run(
    family: str,
    run_level: str,
    requested_max_queries: int | None,
) -> int:
    if requested_max_queries is not None:
        return requested_max_queries
    if run_level == "full":
        return MEMORYDATA_FULL_QUERY_COUNTS.get(family, 1)
    return 1


def _memorydata_family_prereqs(
    prereqs: dict[str, object],
    family: str,
) -> dict[str, object]:
    prereq_family = _memorydata_prereq_family_name(family)
    family_checks = [
        row
        for row in prereqs.get("dataset_checks", [])
        if isinstance(row, dict) and row.get("name") == prereq_family
    ]
    if not family_checks:
        return {
            **prereqs,
            "datasets_ready": False,
            "dataset_checks": [],
        }
    return {
        **prereqs,
        "datasets_ready": all(bool(row.get("ready")) for row in family_checks),
        "dataset_checks": family_checks,
    }


def _memorydata_prereq_family_name(family: str) -> str:
    if family in {
        "MemoryAgentBenchTTL",
        "MemoryAgentBenchLRU",
        "MemoryAgentBenchCR",
    }:
        return "MemoryAgentBench"
    if family in {
        "MemBenchNoisy",
        "MemBenchKnowledgeUpdate",
        "MemBenchHighlevel",
        "MemBenchRecMultiSession",
    }:
        return "MemBench"
    return family


if __name__ == "__main__":
    raise SystemExit(main())
