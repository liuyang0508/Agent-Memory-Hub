#!/usr/bin/env python3
"""Run LongMemEval-S answer generation with an OpenAI-compatible endpoint."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from agent_brain.evaluation.longmemeval_generation import run_longmemeval_generation  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run LongMemEval-S generation.")
    parser.add_argument(
        "--dataset-file",
        type=Path,
        default=Path(".cache/external/LongMemEval/data/longmemeval_s_cleaned.json"),
    )
    parser.add_argument("--ranking-report", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument("--top-k-context", type=int, default=5)
    parser.add_argument("--max-concurrency", type=int, default=1)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--format", choices=["summary", "json"], default="summary")
    args = parser.parse_args(argv)

    try:
        report = run_longmemeval_generation(
            dataset_file=args.dataset_file,
            output_file=args.output,
            ranking_report=args.ranking_report,
            start_index=args.start_index,
            max_cases=args.max_cases,
            top_k_context=args.top_k_context,
            max_concurrency=args.max_concurrency,
            model=args.model,
            base_url=args.base_url,
            api_key_env=args.api_key_env,
        )
    except Exception as exc:
        print(f"LongMemEval generation failed: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        metrics = report.get("averaged_metrics") or {}
        print(
            "LongMemEval generation: "
            f"{report['status']} cases={report['case_count']}/{report['total_available_cases']} "
            f"EM={metrics.get('exact_match', 0.0):.2f} F1={metrics.get('f1', 0.0):.2f} "
            f"output={args.output}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
