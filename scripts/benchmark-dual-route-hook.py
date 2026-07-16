#!/usr/bin/env python3
"""Compare legacy and routed hook latency without emitting recall content."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time
from typing import Callable, NamedTuple, Sequence, TextIO


class BenchmarkStats(NamedTuple):
    p50_ms: float
    p95_ms: float
    max_ms: float
    timeouts: int
    errors: int
    samples: int


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def summarize(
    durations: Sequence[float],
    *,
    timeouts: int,
    errors: int = 0,
) -> BenchmarkStats:
    milliseconds = [duration * 1000.0 for duration in durations]
    return BenchmarkStats(
        p50_ms=round(_percentile(milliseconds, 0.50), 3),
        p95_ms=round(_percentile(milliseconds, 0.95), 3),
        max_ms=round(max(milliseconds, default=0.0), 3),
        timeouts=timeouts,
        errors=errors,
        samples=len(milliseconds),
    )


def exit_code(
    old: BenchmarkStats,
    new: BenchmarkStats,
    *,
    max_new_ms: float = 2000.0,
    max_p95_delta_ms: float = 150.0,
) -> int:
    delta = new.p95_ms - old.p95_ms
    return int(
        old.timeouts > 0
        or new.timeouts > 0
        or old.errors > 0
        or new.errors > 0
        or new.max_ms > max_new_ms
        or delta > max_p95_delta_ms
    )


def _measure(
    command: list[str],
    payload: bytes,
    *,
    repeats: int,
    warmup: int,
    runner: Callable[..., object],
    clock: Callable[[], float],
    timeout_seconds: float,
) -> BenchmarkStats:
    durations: list[float] = []
    timeouts = 0
    errors = 0
    for iteration in range(warmup + repeats):
        started = clock()
        timed_out = False
        returncode = 0
        try:
            completed = runner(
                command,
                input=payload,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout_seconds,
                check=False,
            )
            returncode = int(getattr(completed, "returncode", 0))
        except subprocess.TimeoutExpired:
            timed_out = True
        elapsed = clock() - started
        if timed_out:
            timeouts += 1
        elif returncode != 0:
            errors += 1
        if iteration < warmup:
            continue
        if timed_out:
            elapsed = max(elapsed, timeout_seconds + 0.001)
        durations.append(elapsed)
    return summarize(durations, timeouts=timeouts, errors=errors)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-command", required=True)
    parser.add_argument("--new-command", required=True)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--timeout-seconds", type=float, default=2.0)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: Callable[..., object] = subprocess.run,
    clock: Callable[[], float] = time.perf_counter,
    stdout: TextIO = sys.stdout,
) -> int:
    args = _parser().parse_args(argv)
    if args.repeats <= 0 or args.warmup < 0 or args.timeout_seconds <= 0:
        raise SystemExit("repeats/timeout must be positive and warmup non-negative")
    payload = args.payload.read_bytes()
    old = _measure(
        shlex.split(args.old_command),
        payload,
        repeats=args.repeats,
        warmup=args.warmup,
        runner=runner,
        clock=clock,
        timeout_seconds=args.timeout_seconds,
    )
    new = _measure(
        shlex.split(args.new_command),
        payload,
        repeats=args.repeats,
        warmup=args.warmup,
        runner=runner,
        clock=clock,
        timeout_seconds=args.timeout_seconds,
    )
    status = exit_code(old, new)
    report = {
        "old": old._asdict(),
        "new": new._asdict(),
        "p95_delta_ms": round(new.p95_ms - old.p95_ms, 3),
        "limits": {"max_new_ms": 2000.0, "max_p95_delta_ms": 150.0},
        "passed": status == 0,
    }
    json.dump(report, stdout, sort_keys=True)
    stdout.write("\n")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
