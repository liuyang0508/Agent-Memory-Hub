from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

BASE = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


def _seed_cohort(brain: Path, *, at: datetime, adapter: str = "codex") -> str:
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort

    cohort = record_injection_cohort(
        brain,
        item_ids=["mem-1"],
        adapter=adapter,
        session_id="sess",
        query="not persisted",
        now=at,
    )
    return cohort.cohort_id


def _seed_outcome(brain: Path, *, cohort_id: str, adopted: bool) -> None:
    from agent_brain.memory.governance.recall_events import record_task_outcome

    record_task_outcome(
        brain,
        task_id=f"auto-feedback:{cohort_id}",
        question=f"injection cohort {cohort_id}",
        outcome="success" if adopted else "corrected",
        injected_ids=["mem-1"],
        adopted_ids=["mem-1"] if adopted else (),
        rejected_ids=() if adopted else ["mem-1"],
        adapter="codex",
        cohort_id=cohort_id,
    )


def test_sweep_closes_only_stale_silent_cohorts(tmp_path: Path) -> None:
    from agent_brain.memory.context.injection_closures import (
        CLOSED_BY_SESSION_END,
        TERMINAL_NO_SIGNAL,
        closed_cohort_states,
        close_stale_cohorts,
    )

    stale = _seed_cohort(tmp_path, at=datetime(2026, 8, 18, 0, 0, tzinfo=timezone.utc))
    fresh = _seed_cohort(tmp_path, at=datetime(2026, 8, 19, 6, 0, tzinfo=timezone.utc))
    has_outcome = _seed_cohort(tmp_path, at=datetime(2026, 8, 18, 1, 0, tzinfo=timezone.utc))
    _seed_outcome(tmp_path, cohort_id=has_outcome, adopted=True)

    report = close_stale_cohorts(tmp_path, closed_by=CLOSED_BY_SESSION_END, now=BASE)

    assert report.closed == (stale,)
    assert report.in_window == 1
    assert report.has_explicit_outcome == 1
    states = closed_cohort_states(tmp_path)
    assert states == {stale: TERMINAL_NO_SIGNAL}
    assert fresh not in states and has_outcome not in states


def test_sweep_is_idempotent(tmp_path: Path) -> None:
    from agent_brain.memory.context.injection_closures import (
        CLOSED_BY_SESSION_END,
        INJECTION_CLOSURES_RELATIVE_PATH,
        close_stale_cohorts,
    )

    _seed_cohort(tmp_path, at=datetime(2026, 8, 18, 0, 0, tzinfo=timezone.utc))
    first = close_stale_cohorts(tmp_path, closed_by=CLOSED_BY_SESSION_END, now=BASE)
    second = close_stale_cohorts(tmp_path, closed_by=CLOSED_BY_SESSION_END, now=BASE)

    assert first.closed_count == 1
    assert second.closed == ()
    assert second.already_closed == 1
    lines = (tmp_path / INJECTION_CLOSURES_RELATIVE_PATH).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_sweep_window_boundary_is_strict(tmp_path: Path) -> None:
    from agent_brain.memory.context.injection_closures import (
        CLOSED_BY_DAILY_MAINTENANCE,
        close_stale_cohorts,
    )

    exactly_window = _seed_cohort(
        tmp_path, at=datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    )
    still_open = _seed_cohort(
        tmp_path, at=datetime(2026, 8, 18, 13, 0, tzinfo=timezone.utc)
    )

    report = close_stale_cohorts(
        tmp_path, closed_by=CLOSED_BY_DAILY_MAINTENANCE, window_hours=24.0, now=BASE
    )

    # Age >= window closes; the window boundary itself is inclusive.
    assert report.closed == (exactly_window,)
    assert report.in_window == 1
    assert still_open not in report.closed


def test_closures_first_wins_and_malformed_lines_skip(tmp_path: Path) -> None:
    from agent_brain.memory.context.injection_closures import (
        INJECTION_CLOSURES_RELATIVE_PATH,
        closed_cohort_states,
        iter_cohort_closures,
    )

    runtime = tmp_path / "runtime"
    runtime.mkdir(parents=True)
    rows = [
        {"cohort_id": "inj-20260818T000000-aaaaaaaa", "terminal_state": "closed_no_signal",
         "closed_at": "2026-08-19T00:00:00+00:00", "closed_by": "session_end"},
        {"cohort_id": "inj-20260818T000000-aaaaaaaa", "terminal_state": "closed_adopted",
         "closed_at": "2026-08-19T01:00:00+00:00", "closed_by": "session_end"},
        {"cohort_id": "inj-20260818T000001-bbbbbbbb", "terminal_state": "not_a_state",
         "closed_at": "2026-08-19T01:00:00+00:00", "closed_by": "session_end"},
        {"terminal_state": "closed_rejected"},
        {"cohort_id": "inj-20260818T000002-cccccccc", "terminal_state": "closed_rejected",
         "closed_at": "2026-08-19T02:00:00+00:00", "closed_by": "daily_maintenance"},
    ]
    payload = "".join(json.dumps(row) + "\n" for row in rows) + "not json at all\n"
    (tmp_path / INJECTION_CLOSURES_RELATIVE_PATH).write_text(payload, encoding="utf-8")

    closures = list(iter_cohort_closures(tmp_path))
    assert [c.cohort_id for c in closures] == [
        "inj-20260818T000000-aaaaaaaa",
        "inj-20260818T000002-cccccccc",
    ]
    states = closed_cohort_states(tmp_path)
    assert states["inj-20260818T000000-aaaaaaaa"] == "closed_no_signal"
    assert states["inj-20260818T000002-cccccccc"] == "closed_rejected"


def test_explicit_outcomes_derived_from_cohort_field_and_task_prefix(tmp_path: Path) -> None:
    from agent_brain.memory.context.injection_closures import (
        TERMINAL_ADOPTED,
        TERMINAL_REJECTED,
        explicit_outcome_cohort_ids,
    )
    from agent_brain.memory.governance.recall_events import record_task_outcome

    record_task_outcome(
        tmp_path,
        task_id="some-task",
        question="q",
        outcome="success",
        adopted_ids=["mem-1"],
        cohort_id="inj-20260818T000000-aaaaaaaa",
    )
    record_task_outcome(
        tmp_path,
        task_id="injection-feedback:inj-20260818T000001-bbbbbbbb",
        question="q",
        outcome="corrected",
        rejected_ids=["mem-2"],
    )
    record_task_outcome(
        tmp_path,
        task_id="unrelated-task",
        question="q",
        outcome="success",
        adopted_ids=["mem-3"],
    )

    states = explicit_outcome_cohort_ids(tmp_path)
    assert states == {
        "inj-20260818T000000-aaaaaaaa": TERMINAL_ADOPTED,
        "inj-20260818T000001-bbbbbbbb": TERMINAL_REJECTED,
    }


def test_record_cohort_closure_validates_inputs(tmp_path: Path) -> None:
    from agent_brain.memory.context.injection_closures import record_cohort_closure

    with pytest.raises(ValueError):
        record_cohort_closure(
            tmp_path, cohort_id="x", terminal_state="closed_adopted", closed_by="unknown"
        )
    with pytest.raises(ValueError):
        record_cohort_closure(
            tmp_path, cohort_id="x", terminal_state="bogus", closed_by="session_end"
        )
