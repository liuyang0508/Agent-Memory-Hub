#!/usr/bin/env python3
"""Run LongMemEval-S retrieval smoke benchmarks."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from agent_brain.evaluation.longmemeval_retrieval import (  # noqa: E402
    run_longmemeval_amh_ranking,
    run_longmemeval_retrieval_smoke,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a lightweight LongMemEval-S retrieval smoke benchmark."
    )
    parser.add_argument(
        "--mode",
        choices=["lexical-smoke", "amh-ranking"],
        default="lexical-smoke",
        help="lexical-smoke is a baseline sanity check; amh-ranking uses AMH MemoryItem + Retriever.",
    )
    parser.add_argument(
        "--dataset-file",
        type=Path,
        default=Path(".cache/external/LongMemEval/data/longmemeval_s_cleaned.json"),
    )
    parser.add_argument("--max-cases", type=int, default=5)
    parser.add_argument("--top-k", type=int, action="append", dest="top_ks")
    parser.add_argument("--workspace-dir", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--format", choices=["summary", "json"], default="summary")
    args = parser.parse_args(argv)

    top_ks = tuple(args.top_ks or [5, 10])
    try:
        if args.mode == "amh-ranking":
            report = run_longmemeval_amh_ranking(
                dataset_file=args.dataset_file,
                max_cases=args.max_cases,
                top_ks=top_ks,
                workspace_dir=args.workspace_dir,
            )
        else:
            report = run_longmemeval_retrieval_smoke(
                dataset_file=args.dataset_file,
                max_cases=args.max_cases,
                top_ks=top_ks,
            )
    except Exception as exc:
        if args.format == "json":
            print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        else:
            print(f"LongMemEval retrieval smoke failed: {exc}", file=sys.stderr)
        return 1

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        metrics = report["metrics"]
        top_parts = " ".join(
            f"R@{top_k}={metrics[f'recall_at_{top_k}']:.3f}"
            for top_k in top_ks
        )
        print(
            f"LongMemEval {report['mode']}: {report['status']} "
            f"cases={report['case_count']} {top_parts} MRR={metrics['mrr']:.3f}"
        )
        if args.output:
            print(f"- report: {args.output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
