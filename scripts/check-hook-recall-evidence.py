#!/usr/bin/env python3
"""Independently verify a fresh real-hook recall evidence manifest."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_brain.evaluation.hook_recall_evidence import (  # noqa: E402
    load_hook_recall_manifest,
    validate_hook_recall_manifest,
)
from agent_brain.evaluation.hook_recall_runner import (  # noqa: E402
    collect_expected_provenance,
)
from agent_brain.evaluation.recall_quality_corpus import (  # noqa: E402
    load_recall_quality_corpus,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
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
    parser.add_argument("--require-clean", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        manifest = load_hook_recall_manifest(args.manifest)
        corpus = load_recall_quality_corpus(args.corpus)
        expected, _ = collect_expected_provenance(
            root=ROOT,
            corpus=corpus,
            hook_path=args.hook,
            adapter=args.adapter,
            timeout_seconds=args.timeout_seconds,
            require_clean=args.require_clean,
        )
        failures = validate_hook_recall_manifest(manifest, expected=expected)
    except (OSError, ValueError) as exc:
        print(f"hook recall evidence invalid: {type(exc).__name__}")
        return 1
    if failures:
        for failure in failures:
            print(failure)
        return 1
    counts = manifest["counts"]
    assert isinstance(counts, dict)
    print(
        "hook recall evidence verified: "
        f"status={manifest['status']} "
        f"applicable={counts['applicable']} executed={counts['executed']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
