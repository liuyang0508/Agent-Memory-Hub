#!/usr/bin/env python3
"""Fail closed when the committed dual-route calibration report has open gaps."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


DEFAULT_REPORT = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "evaluation"
    / "dual-route-calibration-report.json"
)


def _bounded_rate(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    rate = float(value)
    if not 0.0 <= rate <= 1.0:
        raise ValueError(f"{field} must be between zero and one")
    return rate


def _validated_summary(report: dict[str, Any]) -> dict[str, Any]:
    if report.get("schema_version") != 1:
        raise ValueError("unsupported calibration report schema version")
    if type(report.get("calibration_passed")) is not bool:
        raise ValueError("calibration_passed must be a boolean")
    gap_count = report.get("unresolved_gap_count")
    if type(gap_count) is not int or gap_count < 0:
        raise ValueError("unresolved_gap_count must be a non-negative integer")
    gaps = report.get("gaps")
    if not isinstance(gaps, list) or len(gaps) != gap_count:
        raise ValueError("gap count does not match gaps")

    model = report.get("model")
    if not isinstance(model, dict) or not model.get("revision"):
        raise ValueError("model revision is required")
    splits = report.get("splits")
    if not isinstance(splits, dict) or set(splits) != {"calibration", "heldout"}:
        raise ValueError("calibration and heldout splits are required")
    for split_name, metrics in splits.items():
        if not isinstance(metrics, dict):
            raise ValueError(f"{split_name} metrics must be an object")
        _bounded_rate(metrics.get("precision"), f"{split_name}.precision")
        _bounded_rate(metrics.get("recall"), f"{split_name}.recall")

    passed = report["calibration_passed"] and gap_count == 0
    return {
        "calibration_passed": report["calibration_passed"],
        "release_gate": "passed" if passed else "blocked",
        "report_schema_version": report["schema_version"],
        "unresolved_gap_count": gap_count,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.report.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("calibration report must be a JSON object")
        summary = _validated_summary(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"invalid calibration report: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0 if summary["release_gate"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
