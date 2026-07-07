#!/usr/bin/env python3
"""Materialize external datasets used by AMH memory evaluation loops."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from agent_brain.evaluation.memory_eval_datasets import (  # noqa: E402
    build_memory_eval_dataset_plan,
    materialize_memory_eval_datasets,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download or inspect datasets for LongMemEval / MemoryAgentBench memory evaluation."
    )
    parser.add_argument(
        "--dataset",
        default="longmemeval-s",
        help=(
            "Dataset alias: longmemeval-s, longmemeval-oracle, memoryagentbench, "
            "locomo-raw, locomo-4cat, longbench-rep150, longbench-v2-full, membench, all"
        ),
    )
    parser.add_argument("--memorydata-repo", type=Path, default=Path(".cache/external/MemoryData"))
    parser.add_argument("--cache-root", type=Path, default=Path(".cache/external"))
    parser.add_argument("--dry-run", action="store_true", help="Only print the materialization plan.")
    parser.add_argument("--format", choices=["summary", "json"], default="summary")
    args = parser.parse_args(argv)

    try:
        if args.dry_run:
            payload = materialize_memory_eval_datasets(
                dataset=args.dataset,
                memorydata_repo=args.memorydata_repo,
                cache_root=args.cache_root,
                dry_run=True,
            )
        else:
            payload = materialize_memory_eval_datasets(
                dataset=args.dataset,
                memorydata_repo=args.memorydata_repo,
                cache_root=args.cache_root,
            )
    except Exception as exc:
        if args.format == "json":
            print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        else:
            print(f"Dataset materialization failed: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_summary(payload)
        plan = build_memory_eval_dataset_plan(
            memorydata_repo=args.memorydata_repo,
            cache_root=args.cache_root,
        )
        print(f"ready={plan['ready_count']}/{plan['total_count']}")

    return 0


def _print_summary(payload: dict) -> None:
    print(f"Dataset materialization: {payload['mode']}")
    for artifact in payload["artifacts"]:
        status = artifact.get("status") or ("ready" if artifact["ready"] else "missing")
        print(f"- {artifact['alias']}: {status} -> {artifact['target_path']}")


if __name__ == "__main__":
    raise SystemExit(main())
