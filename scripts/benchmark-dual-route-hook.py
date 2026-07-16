#!/usr/bin/env python3
"""Compare legacy and routed hook latency without emitting recall content."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import selectors
import shlex
import signal
import subprocess
import sys
import time
from typing import Callable, NamedTuple, Sequence, TextIO

_MAX_STDOUT_BYTES = 64 * 1024
_PUBLISHABLE_MIN_SAMPLES = 30
_HOOK_STATUSES = frozenset({"injected", "empty", "timeout", "error"})
_HOOK_REASONS = {
    "injected": frozenset({"included"}),
    "empty": frozenset({"admission_rejected", "no_candidates", "all_rejected"}),
    "timeout": frozenset({"overall_timeout"}),
    "error": frozenset({"internal_error"}),
}
_ROUTE_STATUSES = frozenset({"ok", "skipped", "timeout", "error"})
_ROUTE_NAMES = frozenset({"lexical_terms", "semantic_raw", "lexical_raw_fallback"})
_ROUTE_REASONS = {
    "ok": frozenset({"route_completed"}),
    "skipped": frozenset({
        "admission_rejected",
        "lexical_terms_empty",
        "semantic_not_ready",
    }),
    "timeout": frozenset({"route_timeout"}),
    "error": frozenset({"route_error"}),
}


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


def _valid_hook_result(
    stdout: object,
    *,
    expected_result: str,
    expected_reason: str | None,
) -> bool:
    if not isinstance(stdout, (bytes, bytearray)):
        return False
    raw = bytes(stdout)
    if not raw or len(raw) > _MAX_STDOUT_BYTES:
        return False
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    if set(payload) != {"status", "reason", "context", "routes"}:
        return False
    status = payload.get("status")
    reason = payload.get("reason")
    context = payload.get("context")
    routes = payload.get("routes")
    if (
        not isinstance(status, str)
        or status not in _HOOK_STATUSES
        or not isinstance(reason, str)
        or not isinstance(context, str)
        or not isinstance(routes, list)
    ):
        return False
    if reason not in _HOOK_REASONS[status]:
        return False
    if expected_reason is not None and reason != expected_reason:
        return False
    if any(not _valid_route(route) for route in routes):
        return False
    if status in {"timeout", "error"}:
        return False
    if status == "injected" and not context.strip():
        return False
    if status != "injected" and context:
        return False
    if expected_result == "injected":
        return status == "injected" and bool(context.strip())
    if expected_result == "empty":
        return status == "empty"
    raise ValueError("unsupported expected result")


def _valid_route(route: object) -> bool:
    if not isinstance(route, dict):
        return False
    if set(route) != {"route", "status", "candidate_count", "reason"}:
        return False
    status = route.get("status")
    reason = route.get("reason")
    route_name = route.get("route")
    return (
        isinstance(route_name, str)
        and route_name in _ROUTE_NAMES
        and isinstance(status, str)
        and status in _ROUTE_STATUSES
        and type(route.get("candidate_count")) is int
        and int(route["candidate_count"]) >= 0
        and isinstance(reason, str)
        and reason in _ROUTE_REASONS[status]
    )


def _run_once(
    command: list[str],
    payload: bytes,
    *,
    runner: Callable[..., object] | None,
    clock: Callable[[], float],
    timeout_seconds: float,
    expected_result: str,
    expected_reason: str | None,
) -> tuple[float, bool, bool]:
    started = clock()
    timed_out = False
    functional_error = False
    try:
        if runner is None:
            protocol_stdout, returncode, timed_out, overflowed = _run_streaming(
                command,
                payload,
                timeout_seconds=timeout_seconds,
                clock=clock,
            )
            functional_error = overflowed or returncode != 0
            if not timed_out and not functional_error:
                functional_error = not _valid_hook_result(
                    protocol_stdout,
                    expected_result=expected_result,
                    expected_reason=expected_reason,
                )
        else:
            completed = runner(
                command,
                input=payload,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=timeout_seconds,
                check=False,
            )
            protocol_stdout = getattr(completed, "stdout", None)
            returncode = int(getattr(completed, "returncode", 0))
            functional_error = returncode != 0 or not _valid_hook_result(
                protocol_stdout,
                expected_result=expected_result,
                expected_reason=expected_reason,
            )
    except subprocess.TimeoutExpired:
        timed_out = True
    elapsed = clock() - started
    if timed_out:
        elapsed = max(elapsed, timeout_seconds + 0.001)
    return elapsed, timed_out, functional_error


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass
    finally:
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass


def _run_streaming(
    command: list[str],
    payload: bytes,
    *,
    timeout_seconds: float,
    clock: Callable[[], float],
) -> tuple[bytes, int, bool, bool]:
    """Run a hook while enforcing the stdout cap before the child can finish."""
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=os.name == "posix",
    )
    assert process.stdin is not None
    assert process.stdout is not None
    try:
        process.stdin.write(payload)
        process.stdin.close()
    except BrokenPipeError:
        pass

    deadline = clock() + timeout_seconds
    captured = bytearray()
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    timed_out = False
    overflowed = False
    try:
        while True:
            remaining = deadline - clock()
            if remaining <= 0:
                timed_out = True
                _kill_process_group(process)
                break
            events = selector.select(min(remaining, 0.05))
            if not events:
                if process.poll() is not None:
                    chunk = os.read(process.stdout.fileno(), 8192)
                    if chunk:
                        captured.extend(chunk)
                        if len(captured) > _MAX_STDOUT_BYTES:
                            overflowed = True
                    break
                continue
            chunk = os.read(process.stdout.fileno(), 8192)
            if not chunk:
                break
            captured.extend(chunk)
            if len(captured) > _MAX_STDOUT_BYTES:
                overflowed = True
                _kill_process_group(process)
                break
    finally:
        selector.close()
        process.stdout.close()

    if process.poll() is None:
        try:
            process.wait(timeout=max(0.0, deadline - clock()))
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_process_group(process)
    returncode = process.returncode if process.returncode is not None else -1
    return bytes(captured[: _MAX_STDOUT_BYTES + 1]), int(returncode), timed_out, overflowed


def _measure_pair(
    old_command: list[str],
    new_command: list[str],
    payload: bytes,
    *,
    repeats: int,
    warmup: int,
    runner: Callable[..., object] | None,
    clock: Callable[[], float],
    timeout_seconds: float,
    expected_result: str,
    expected_reason: str | None,
) -> tuple[BenchmarkStats, BenchmarkStats]:
    durations = {"old": [], "new": []}
    timeouts = {"old": 0, "new": 0}
    errors = {"old": 0, "new": 0}
    commands = {"old": old_command, "new": new_command}
    for iteration in range(warmup + repeats):
        order = ("old", "new") if iteration % 2 == 0 else ("new", "old")
        for name in order:
            elapsed, timed_out, functional_error = _run_once(
                commands[name],
                payload,
                runner=runner,
                clock=clock,
                timeout_seconds=timeout_seconds,
                expected_result=expected_result,
                expected_reason=expected_reason,
            )
            if timed_out:
                timeouts[name] += 1
            elif functional_error:
                errors[name] += 1
            if iteration >= warmup:
                durations[name].append(elapsed)
    return (
        summarize(durations["old"], timeouts=timeouts["old"], errors=errors["old"]),
        summarize(durations["new"], timeouts=timeouts["new"], errors=errors["new"]),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-command", required=True)
    parser.add_argument("--new-command", required=True)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=30)
    parser.add_argument("--min-samples", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--timeout-seconds", type=float, default=2.0)
    parser.add_argument(
        "--expected-result",
        choices=("injected", "empty"),
        default="injected",
    )
    parser.add_argument("--expected-reason")
    parser.add_argument(
        "--unit-test-mode",
        action="store_true",
        help="allow reduced samples but always emit a non-publishable failed report",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: Callable[..., object] | None = None,
    clock: Callable[[], float] = time.perf_counter,
    stdout: TextIO = sys.stdout,
) -> int:
    args = _parser().parse_args(argv)
    if (
        args.repeats <= 0
        or args.min_samples <= 0
        or args.warmup < 0
        or args.timeout_seconds <= 0
    ):
        raise SystemExit("repeats/timeout must be positive and warmup non-negative")
    if not args.unit_test_mode and args.min_samples < _PUBLISHABLE_MIN_SAMPLES:
        raise SystemExit(
            f"publishable minimum sample count is {_PUBLISHABLE_MIN_SAMPLES}"
        )
    if runner is not None and not args.unit_test_mode:
        raise SystemExit("custom runner requires unit-test mode")
    if args.repeats < args.min_samples:
        raise SystemExit("repeats must meet the minimum sample count")
    expected_reason = args.expected_reason
    if expected_reason is None and args.expected_result == "injected":
        expected_reason = "included"
    if expected_reason is None:
        raise SystemExit("expected reason is required for empty result")
    if (
        expected_reason is not None
        and expected_reason not in _HOOK_REASONS[args.expected_result]
    ):
        raise SystemExit("expected reason is incompatible with expected result")
    payload = args.payload.read_bytes()
    old, new = _measure_pair(
        shlex.split(args.old_command),
        shlex.split(args.new_command),
        payload,
        repeats=args.repeats,
        warmup=args.warmup,
        runner=runner,
        clock=clock,
        timeout_seconds=args.timeout_seconds,
        expected_result=args.expected_result,
        expected_reason=expected_reason,
    )
    measured_status = exit_code(old, new)
    publishable = not args.unit_test_mode
    status = measured_status if publishable else 1
    report = {
        "old": old._asdict(),
        "new": new._asdict(),
        "p95_delta_ms": round(new.p95_ms - old.p95_ms, 3),
        "limits": {"max_new_ms": 2000.0, "max_p95_delta_ms": 150.0},
        "sample_policy": {
            "minimum": args.min_samples,
            "interleaved": True,
            "expected_result": args.expected_result,
            "expected_reason": expected_reason,
            "unit_test_mode": args.unit_test_mode,
        },
        "publishable": publishable,
        "passed": publishable and measured_status == 0,
    }
    json.dump(report, stdout, sort_keys=True)
    stdout.write("\n")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
