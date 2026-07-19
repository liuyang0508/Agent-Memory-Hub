#!/usr/bin/env python3
"""Generate a terminal real-hook recall evidence manifest."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import uuid


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_brain.evaluation.hook_recall_evidence import write_manifest_atomic  # noqa: E402
from agent_brain.evaluation.hook_recall_runner import (  # noqa: E402
    run_hook_recall_evidence,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        type=Path,
        default=ROOT / "tests/fixtures/recall_quality_production_replay_v1.json",
    )
    parser.add_argument(
        "--hook",
        type=Path,
        default=ROOT / "agent_runtime_kit/hooks/inject-context.sh",
    )
    parser.add_argument("--adapter", default="codex")
    parser.add_argument("--timeout-seconds", type=float, default=8.0)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        with tempfile.TemporaryDirectory(prefix="amh-hook-recall-evidence-") as raw:
            manifest = run_hook_recall_evidence(
                root=ROOT,
                corpus_path=args.corpus,
                hook_path=args.hook,
                adapter=args.adapter,
                timeout_seconds=args.timeout_seconds,
                workspace=Path(raw),
            )
    except Exception as exc:  # noqa: BLE001 - persist a bounded blocked artifact
        now = datetime.now(timezone.utc).isoformat()
        manifest = {
            "schema_version": 1,
            "run_id": str(uuid.uuid4()),
            "started_at": now,
            "completed_at": now,
            "status": "blocked",
            "provenance": {},
            "counts": {
                "planned": 0,
                "applicable": 0,
                "not_applicable": 0,
                "executed": 0,
            },
            "planned_case_ids": [],
            "results": [],
            "failed_gates": ["G0:runner_blocked"],
            "block_reason": type(exc).__name__,
        }
    try:
        write_manifest_atomic(args.output, manifest)
    except OSError as exc:
        print(f"hook recall evidence could not be written: {type(exc).__name__}")
        return 2
    counts = manifest["counts"]
    assert isinstance(counts, dict)
    print(
        "hook recall evidence generated: "
        f"status={manifest['status']} "
        f"applicable={counts['applicable']} executed={counts['executed']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

