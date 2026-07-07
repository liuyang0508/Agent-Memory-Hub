#!/usr/bin/env python3
"""Render Dataset Provenance Audit artifacts from the memory benchmark report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_brain.evaluation.dataset_provenance_audit import (
    build_dataset_provenance_audit,
    render_dataset_provenance_markdown,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render Dataset Provenance Audit JSON and Chinese Markdown reports."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("docs/evaluation/memorydata-external-benchmark-report.json"),
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=Path("docs/evaluation/dataset-provenance-audit.json"),
    )
    parser.add_argument(
        "--markdown-output",
        type=Path,
        default=Path("docs/evaluation/dataset-provenance-audit.zh.md"),
    )
    args = parser.parse_args(argv)

    report = json.loads(args.input.read_text(encoding="utf-8"))
    audit = build_dataset_provenance_audit(report)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.write_text(
        render_dataset_provenance_markdown(audit),
        encoding="utf-8",
    )
    print(args.json_output)
    print(args.markdown_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
