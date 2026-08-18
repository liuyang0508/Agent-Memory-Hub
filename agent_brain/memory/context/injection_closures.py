"""Terminal-state ledger for injection cohorts.

Injection cohorts are append-only and never rewritten. When a cohort receives
no explicit adoption/rejection feedback within its observation window, the sweep
records a ``closed_no_signal`` terminal state here so that effectiveness
aggregation has an honest denominator. Explicit adopted/rejected states are
derived from task outcomes, not from this ledger.

This log is deliberately mechanical: it records cohort ids, terminal state and
timestamps only — never prompt text, memory bodies, summaries, or titles.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from agent_brain.memory.context.injection_cohorts import (
    InjectionCohort,
    iter_injection_cohorts,
)
from agent_brain.memory.context.injection_metrics import sanitize_cohort_id
from agent_brain.memory.governance.recall_events import iter_task_outcomes
from agent_brain.platform.bounded_jsonl import iter_bounded_jsonl


INJECTION_CLOSURES_RELATIVE_PATH = "runtime/injection-cohort-closures.jsonl"

TERMINAL_ADOPTED = "closed_adopted"
TERMINAL_REJECTED = "closed_rejected"
TERMINAL_NO_SIGNAL = "closed_no_signal"
_TERMINAL_STATES = (TERMINAL_ADOPTED, TERMINAL_REJECTED, TERMINAL_NO_SIGNAL)

CLOSED_BY_SESSION_END = "session_end"
CLOSED_BY_DAILY_MAINTENANCE = "daily_maintenance"
_CLOSED_BY_VALUES = (CLOSED_BY_SESSION_END, CLOSED_BY_DAILY_MAINTENANCE)

DEFAULT_WINDOW_HOURS = 24.0

_EXPLICIT_TASK_ID_PREFIXES = ("auto-feedback:", "injection-feedback:")


@dataclass(frozen=True)
class CohortClosure:
    cohort_id: str
    terminal_state: str
    closed_at: str
    closed_by: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SweepReport:
    closed: tuple[str, ...] = ()
    already_closed: int = 0
    has_explicit_outcome: int = 0
    in_window: int = 0

    @property
    def closed_count(self) -> int:
        return len(self.closed)

    def to_dict(self) -> dict[str, object]:
        return {
            "closed": list(self.closed),
            "closed_count": self.closed_count,
            "already_closed": self.already_closed,
            "has_explicit_outcome": self.has_explicit_outcome,
            "in_window": self.in_window,
        }


def injection_cohort_closures_path(brain_dir: Path) -> Path:
    return Path(brain_dir) / INJECTION_CLOSURES_RELATIVE_PATH


def record_cohort_closure(
    brain_dir: Path,
    *,
    cohort_id: str,
    terminal_state: str,
    closed_by: str,
    now: datetime | None = None,
) -> CohortClosure:
    if terminal_state not in _TERMINAL_STATES:
        raise ValueError(f"unknown terminal state: {terminal_state}")
    if closed_by not in _CLOSED_BY_VALUES:
        raise ValueError(f"unknown closed_by: {closed_by}")
    closure = CohortClosure(
        cohort_id=sanitize_cohort_id(cohort_id),
        terminal_state=terminal_state,
        closed_at=_timestamp(now),
        closed_by=closed_by,
    )
    path = injection_cohort_closures_path(brain_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(closure.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    return closure


def iter_cohort_closures(brain_dir: Path) -> Iterator[CohortClosure]:
    """Yield the first closure per cohort id; later duplicates are ignored."""
    seen: set[str] = set()
    for data in iter_bounded_jsonl(injection_cohort_closures_path(brain_dir)):
        try:
            closure = CohortClosure(
                cohort_id=sanitize_cohort_id(data["cohort_id"]),
                terminal_state=str(data["terminal_state"]),
                closed_at=str(data["closed_at"]),
                closed_by=str(data["closed_by"]),
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if closure.terminal_state not in _TERMINAL_STATES:
            continue
        if closure.cohort_id in seen:
            continue
        seen.add(closure.cohort_id)
        yield closure


def closed_cohort_states(brain_dir: Path) -> dict[str, str]:
    """Map cohort_id -> terminal_state from the closure ledger (first wins)."""
    return {
        closure.cohort_id: closure.terminal_state
        for closure in iter_cohort_closures(brain_dir)
    }


def explicit_outcome_cohort_ids(brain_dir: Path) -> dict[str, str]:
    """Map cohort_id -> explicit terminal state derived from task outcomes.

    A cohort with a task outcome carrying adopted ids is adopted; with rejected
    ids it is rejected. Matching uses the outcome ``cohort_id`` field first and
    falls back to the ``task_id`` prefix convention used by auto-feedback.
    """
    states: dict[str, str] = {}
    for outcome in iter_task_outcomes(brain_dir):
        cohort_id = outcome.cohort_id or _cohort_id_from_task_id(outcome.task_id)
        if not cohort_id or cohort_id in states:
            continue
        if outcome.adopted_ids:
            states[cohort_id] = TERMINAL_ADOPTED
        elif outcome.rejected_ids:
            states[cohort_id] = TERMINAL_REJECTED
    return states


def close_stale_cohorts(
    brain_dir: Path,
    *,
    closed_by: str,
    window_hours: float = DEFAULT_WINDOW_HOURS,
    now: datetime | None = None,
) -> SweepReport:
    """Idempotently close cohorts whose observation window elapsed with no signal.

    A cohort is closable when it has no closure yet, no explicit task outcome,
    and its timestamp is older than ``window_hours``. Silent cohorts are closed
    as ``closed_no_signal``; silence is never treated as adoption.
    """
    if closed_by not in _CLOSED_BY_VALUES:
        raise ValueError(f"unknown closed_by: {closed_by}")
    if window_hours <= 0:
        raise ValueError("window_hours must be positive")

    reference = _as_utc(now or datetime.now(timezone.utc))
    already_closed = closed_cohort_states(brain_dir)
    explicit = explicit_outcome_cohort_ids(brain_dir)

    closed: list[str] = []
    already_closed_count = 0
    has_outcome_count = 0
    in_window_count = 0

    for cohort in iter_injection_cohorts(brain_dir):
        if cohort.cohort_id in already_closed:
            already_closed_count += 1
            continue
        if cohort.cohort_id in explicit:
            has_outcome_count += 1
            continue
        age_hours = _age_hours(cohort, reference)
        if age_hours is None or age_hours < window_hours:
            in_window_count += 1
            continue
        record_cohort_closure(
            brain_dir,
            cohort_id=cohort.cohort_id,
            terminal_state=TERMINAL_NO_SIGNAL,
            closed_by=closed_by,
            now=reference,
        )
        already_closed[cohort.cohort_id] = TERMINAL_NO_SIGNAL
        closed.append(cohort.cohort_id)

    return SweepReport(
        closed=tuple(closed),
        already_closed=already_closed_count,
        has_explicit_outcome=has_outcome_count,
        in_window=in_window_count,
    )


def _cohort_id_from_task_id(task_id: str) -> str | None:
    for prefix in _EXPLICIT_TASK_ID_PREFIXES:
        if task_id.startswith(prefix):
            return task_id.removeprefix(prefix)
    return None


def _age_hours(cohort: InjectionCohort, reference: datetime) -> float | None:
    parsed = _parse_utc(cohort.timestamp)
    if parsed is None:
        return None
    return (reference - parsed).total_seconds() / 3600.0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Close silent injection cohorts whose observation window elapsed."
    )
    parser.add_argument("--brain-dir", type=Path, required=True)
    parser.add_argument(
        "--closed-by",
        choices=list(_CLOSED_BY_VALUES),
        default=CLOSED_BY_DAILY_MAINTENANCE,
    )
    parser.add_argument("--window-hours", type=float, default=DEFAULT_WINDOW_HOURS)
    args = parser.parse_args(argv)
    report = close_stale_cohorts(
        args.brain_dir,
        closed_by=args.closed_by,
        window_hours=args.window_hours,
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def _parse_utc(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _timestamp(now: datetime | None) -> str:
    return _as_utc(now or datetime.now(timezone.utc)).isoformat()


__all__ = [
    "CLOSED_BY_DAILY_MAINTENANCE",
    "CLOSED_BY_SESSION_END",
    "DEFAULT_WINDOW_HOURS",
    "INJECTION_CLOSURES_RELATIVE_PATH",
    "TERMINAL_ADOPTED",
    "TERMINAL_NO_SIGNAL",
    "TERMINAL_REJECTED",
    "CohortClosure",
    "SweepReport",
    "close_stale_cohorts",
    "closed_cohort_states",
    "explicit_outcome_cohort_ids",
    "injection_cohort_closures_path",
    "iter_cohort_closures",
    "record_cohort_closure",
]
