#!/usr/bin/env python3
"""Fail on new strict-mypy fingerprints while allowing existing debt to shrink."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / ".github" / "mypy-baseline.txt"
COMMAND = [
    sys.executable,
    "-m",
    "mypy",
    "agent_brain",
    "web",
    "benchmarks",
    "--no-error-summary",
    "--no-pretty",
    "--show-error-codes",
]
LOCATION_RE = re.compile(r"^(.*?):\d+(?::\d+)?: (error: .+)$")


def normalize(output: str) -> list[str]:
    fingerprints: list[str] = []
    for raw_line in output.splitlines():
        match = LOCATION_RE.match(raw_line.strip())
        if match:
            fingerprints.append(f"{match.group(1)}: {match.group(2)}")
    return sorted(fingerprints)


def read_baseline() -> list[str]:
    if not BASELINE.is_file():
        raise FileNotFoundError(
            f"missing {BASELINE.relative_to(ROOT)}; run with --write-baseline after review"
        )
    return [line for line in BASELINE.read_text(encoding="utf-8").splitlines() if line]


def expanded_difference(left: Counter[str], right: Counter[str]) -> list[str]:
    return sorted((left - right).elements())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Replace the audited baseline with the current strict-mypy fingerprints",
    )
    args = parser.parse_args()

    completed = subprocess.run(
        COMMAND,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    current = normalize(completed.stdout)
    if completed.returncode not in {0, 1}:
        print(completed.stdout, file=sys.stderr)
        print(f"mypy infrastructure failure: exit {completed.returncode}", file=sys.stderr)
        return 2
    if completed.returncode == 1 and not current:
        print(completed.stdout, file=sys.stderr)
        print("mypy failed without parseable error fingerprints", file=sys.stderr)
        return 2

    if args.write_baseline:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text("".join(f"{line}\n" for line in current), encoding="utf-8")
        print(f"wrote {len(current)} fingerprints to {BASELINE.relative_to(ROOT)}")
        return 0

    try:
        baseline = read_baseline()
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    current_counts = Counter(current)
    baseline_counts = Counter(baseline)
    new_errors = expanded_difference(current_counts, baseline_counts)
    resolved = expanded_difference(baseline_counts, current_counts)

    if resolved:
        print(f"resolved mypy fingerprints: {len(resolved)}")
        for fingerprint in resolved:
            print(f"  - {fingerprint}")
    if new_errors:
        print(f"new mypy fingerprints: {len(new_errors)}", file=sys.stderr)
        for fingerprint in new_errors:
            print(f"  + {fingerprint}", file=sys.stderr)
        print("Refresh requires an intentional audited --write-baseline run.", file=sys.stderr)
        return 1

    print(
        f"mypy baseline gate passed: current={len(current)} "
        f"baseline={len(baseline)} resolved={len(resolved)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
