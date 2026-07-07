#!/usr/bin/env python3
"""Release gate for retrieval quality benchmarks."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from benchmarks.benchmark_relevance import (  # noqa: E402
    BenchmarkReport,
    generate_queries_from_pool,
    run_relevance_benchmark,
)
from benchmarks.benchmark_retrieval import generate_synthetic_items  # noqa: E402
from agent_brain.evaluation.compression_gate import (  # noqa: E402
    CompressionGateReport,
    evaluate_compression_cases,
    load_builtin_compression_cases,
)
from agent_brain.evaluation.ml_advisory_gate import (  # noqa: E402
    MLAdvisoryGateReport,
    evaluate_ml_advisory_cases,
    load_builtin_ml_advisory_cases,
)

RELEASE_QUERY_PROFILE = ("title_recall", "project_scope", "multi_keyword")


@dataclass(frozen=True)
class ReleaseGateThresholds:
    min_mean_recall_at_10: float = 0.75
    min_mean_mrr: float = 0.35
    min_compression_pass_rate: float = 1.0
    max_compression_mean_ratio: float = 0.8
    min_compression_mean_tokens_saved: float = 1.0
    min_ml_advisory_pass_rate: float = 1.0
    max_ml_advisory_unsafe_promotions: int = 0


def evaluate_relevance_report(
    report: BenchmarkReport,
    thresholds: ReleaseGateThresholds,
    *,
    compression_report: CompressionGateReport | None = None,
    ml_advisory_report: MLAdvisoryGateReport | None = None,
) -> dict[str, object]:
    compression = compression_report or evaluate_compression_cases(
        load_builtin_compression_cases(),
        min_pass_rate=thresholds.min_compression_pass_rate,
        max_mean_compression_ratio=thresholds.max_compression_mean_ratio,
        min_mean_tokens_saved=thresholds.min_compression_mean_tokens_saved,
    )
    ml_advisory = ml_advisory_report or evaluate_ml_advisory_cases(
        load_builtin_ml_advisory_cases(),
        min_pass_rate=thresholds.min_ml_advisory_pass_rate,
        max_unsafe_promotions=thresholds.max_ml_advisory_unsafe_promotions,
    )
    checks = {
        "mean_recall_at_10": {
            "value": report.mean_recall_at_10,
            "threshold": thresholds.min_mean_recall_at_10,
            "passed": report.mean_recall_at_10 >= thresholds.min_mean_recall_at_10,
        },
        "mean_mrr": {
            "value": report.mean_mrr,
            "threshold": thresholds.min_mean_mrr,
            "passed": report.mean_mrr >= thresholds.min_mean_mrr,
        },
        "compression_pass_rate": {
            "value": compression.metrics["pass_rate"],
            "threshold": thresholds.min_compression_pass_rate,
            "passed": compression.metrics["pass_rate"] >= thresholds.min_compression_pass_rate,
        },
        "compression_mean_ratio": {
            "value": compression.metrics["mean_compression_ratio"],
            "threshold": thresholds.max_compression_mean_ratio,
            "passed": compression.metrics["mean_compression_ratio"]
            <= thresholds.max_compression_mean_ratio,
        },
        "compression_mean_tokens_saved": {
            "value": compression.metrics["mean_tokens_saved"],
            "threshold": thresholds.min_compression_mean_tokens_saved,
            "passed": compression.metrics["mean_tokens_saved"]
            >= thresholds.min_compression_mean_tokens_saved,
        },
        "ml_advisory_pass_rate": {
            "value": ml_advisory.metrics["pass_rate"],
            "threshold": thresholds.min_ml_advisory_pass_rate,
            "passed": ml_advisory.metrics["pass_rate"]
            >= thresholds.min_ml_advisory_pass_rate,
        },
        "ml_advisory_unsafe_promotions": {
            "value": ml_advisory.metrics["unsafe_promotion_count"],
            "threshold": thresholds.max_ml_advisory_unsafe_promotions,
            "passed": ml_advisory.metrics["unsafe_promotion_count"]
            <= thresholds.max_ml_advisory_unsafe_promotions,
        },
    }
    return {
        "passed": all(check["passed"] for check in checks.values()),
        "checks": checks,
        "num_queries": report.num_queries,
        "num_items_indexed": report.num_items_indexed,
        "release_query_profile": list(RELEASE_QUERY_PROFILE),
        "compression": compression.to_dict(),
        "ml_advisory": ml_advisory.to_dict(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run AMH release quality gate.")
    parser.add_argument("--synthetic", type=int, default=80)
    parser.add_argument("--min-mean-recall-at-10", type=float, default=0.75)
    parser.add_argument("--min-mean-mrr", type=float, default=0.35)
    parser.add_argument("--min-compression-pass-rate", type=float, default=1.0)
    parser.add_argument("--max-compression-mean-ratio", type=float, default=0.8)
    parser.add_argument("--min-compression-mean-tokens-saved", type=float, default=1.0)
    parser.add_argument("--min-ml-advisory-pass-rate", type=float, default=1.0)
    parser.add_argument("--max-ml-advisory-unsafe-promotions", type=int, default=0)
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args(argv)

    items = generate_synthetic_items(args.synthetic)
    queries = generate_queries_from_pool(
        items,
        target_count=min(24, max(6, args.synthetic // 2)),
        include_categories=set(RELEASE_QUERY_PROFILE),
    )
    report = run_relevance_benchmark(items, queries)
    result = evaluate_relevance_report(
        report,
        ReleaseGateThresholds(
            min_mean_recall_at_10=args.min_mean_recall_at_10,
            min_mean_mrr=args.min_mean_mrr,
            min_compression_pass_rate=args.min_compression_pass_rate,
            max_compression_mean_ratio=args.max_compression_mean_ratio,
            min_compression_mean_tokens_saved=args.min_compression_mean_tokens_saved,
            min_ml_advisory_pass_rate=args.min_ml_advisory_pass_rate,
            max_ml_advisory_unsafe_promotions=args.max_ml_advisory_unsafe_promotions,
        ),
    )

    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        status = "PASS" if result["passed"] else "FAIL"
        print(f"Release gate: {status}")
        for name, check in result["checks"].items():
            op = (
                "<="
                if name in {"compression_mean_ratio", "ml_advisory_unsafe_promotions"}
                else ">="
            )
            print(f"- {name}: {check['value']:.3f} {op} {check['threshold']:.3f}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
