from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from typer.testing import CliRunner

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


def _seed_outcome(brain: Path, *, cohort_id: str, adopted: bool, adapter: str = "codex") -> None:
    from agent_brain.memory.governance.recall_events import record_task_outcome

    record_task_outcome(
        brain,
        task_id=f"auto-feedback:{cohort_id}",
        question=f"injection cohort {cohort_id}",
        outcome="success" if adopted else "corrected",
        injected_ids=["mem-1"],
        adopted_ids=["mem-1"] if adopted else (),
        rejected_ids=() if adopted else ["mem-1"],
        adapter=adapter,
        cohort_id=cohort_id,
    )


def _close(brain: Path, cohort_id: str, closed_by: str = "session_end") -> None:
    from agent_brain.memory.context.injection_closures import (
        TERMINAL_NO_SIGNAL,
        record_cohort_closure,
    )

    record_cohort_closure(
        brain, cohort_id=cohort_id, terminal_state=TERMINAL_NO_SIGNAL, closed_by=closed_by
    )


def _seed_scenario(brain: Path) -> dict[str, str]:
    stale = datetime(2026, 8, 18, 0, 0, tzinfo=timezone.utc)
    codex_adopted = _seed_cohort(brain, at=stale, adapter="codex")
    codex_rejected = _seed_cohort(brain, at=stale, adapter="codex")
    codex_silent = _seed_cohort(brain, at=stale, adapter="codex")
    claude_adopted = _seed_cohort(brain, at=stale, adapter="claude_code")
    in_window = _seed_cohort(
        brain, at=datetime(2026, 8, 19, 6, 0, tzinfo=timezone.utc), adapter="codex"
    )

    _seed_outcome(brain, cohort_id=codex_adopted, adopted=True)
    _seed_outcome(brain, cohort_id=codex_rejected, adopted=False)
    _seed_outcome(brain, cohort_id=claude_adopted, adopted=True, adapter="claude_code")
    _close(brain, codex_silent)
    return {
        "codex_adopted": codex_adopted,
        "codex_rejected": codex_rejected,
        "codex_silent": codex_silent,
        "claude_adopted": claude_adopted,
        "in_window": in_window,
    }


def test_silence_counts_in_denominator_only(tmp_path: Path) -> None:
    from agent_brain.memory.governance.injection_effectiveness import (
        measure_injection_effectiveness,
    )

    ids = _seed_scenario(tmp_path)
    report = measure_injection_effectiveness(tmp_path, now=BASE)

    rates = report.rates
    assert rates.closed_total == 4
    assert rates.adopted_count == 2
    assert rates.rejected_count == 1
    assert rates.no_signal_count == 1
    assert rates.signal_coverage == 0.75
    assert rates.adoption_rate == 0.5
    assert rates.rejection_rate == 0.25
    assert ids["in_window"] not in json.dumps(report.to_dict())


def test_explicit_outcome_beats_ledger_closure(tmp_path: Path) -> None:
    from agent_brain.memory.context.injection_closures import (
        CLOSED_BY_DAILY_MAINTENANCE,
        close_stale_cohorts,
    )
    from agent_brain.memory.governance.injection_effectiveness import (
        measure_injection_effectiveness,
    )

    stale = datetime(2026, 8, 18, 0, 0, tzinfo=timezone.utc)
    explicit_adopted = _seed_cohort(tmp_path, at=stale)
    closed = _seed_cohort(tmp_path, at=stale)

    _seed_outcome(tmp_path, cohort_id=explicit_adopted, adopted=True)
    _close(tmp_path, closed)
    # Sweep sees the explicit outcome and must not write a conflicting closure.
    sweep = close_stale_cohorts(tmp_path, closed_by=CLOSED_BY_DAILY_MAINTENANCE, now=BASE)
    assert sweep.closed == ()
    assert sweep.has_explicit_outcome == 1

    report = measure_injection_effectiveness(tmp_path, now=BASE)
    assert report.rates.closed_total == 2
    assert report.rates.adopted_count == 1
    assert report.rates.no_signal_count == 1


def test_group_by_adapter_joins_counts(tmp_path: Path) -> None:
    from agent_brain.memory.governance.injection_effectiveness import (
        measure_injection_effectiveness,
    )

    _seed_scenario(tmp_path)
    report = measure_injection_effectiveness(tmp_path, group_by=("adapter",), now=BASE)

    codex = report.by_group["adapter"]["codex"]
    assert codex.closed_total == 3
    assert codex.adopted_count == 1
    assert codex.rejected_count == 1
    assert codex.no_signal_count == 1
    assert codex.signal_coverage == 2 / 3

    claude = report.by_group["adapter"]["claude_code"]
    assert claude.closed_total == 1
    assert claude.adopted_count == 1
    assert claude.signal_coverage == 1.0


def test_adapter_filter_and_since_days_window(tmp_path: Path) -> None:
    from agent_brain.memory.governance.injection_effectiveness import (
        measure_injection_effectiveness,
    )

    ids = _seed_scenario(tmp_path)
    old_cohort = _seed_cohort(
        tmp_path, at=datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc), adapter="codex"
    )
    _close(tmp_path, old_cohort)

    filtered = measure_injection_effectiveness(tmp_path, adapter="claude_code", now=BASE)
    assert filtered.rates.closed_total == 1
    assert filtered.rates.adopted_count == 1

    recent = measure_injection_effectiveness(tmp_path, since_days=7, now=BASE)
    assert recent.rates.closed_total == 4
    assert old_cohort not in json.dumps(recent.to_dict())
    assert ids["in_window"] not in json.dumps(recent.to_dict())


def test_empty_brain_reports_zero_rates(tmp_path: Path) -> None:
    from agent_brain.memory.governance.injection_effectiveness import (
        measure_injection_effectiveness,
    )

    report = measure_injection_effectiveness(tmp_path, now=BASE)
    assert report.rates.closed_total == 0
    assert report.rates.signal_coverage == 0.0
    assert report.rates.adoption_rate == 0.0


def test_measure_injection_cli_json(tmp_brain: Path) -> None:
    from agent_brain.interfaces.cli import app

    _seed_scenario(tmp_brain)
    result = CliRunner().invoke(
        app, ["measure", "injection", "--format", "json"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["rates"]["closed_total"] == 4
    assert payload["rates"]["no_signal_count"] == 1
    assert payload["rates"]["signal_coverage"] == 0.75
    assert set(payload["by_group"]["adapter"]) == {"codex", "claude_code"}


def test_measure_injection_cli_table(tmp_brain: Path) -> None:
    from agent_brain.interfaces.cli import app

    _seed_scenario(tmp_brain)
    result = CliRunner().invoke(app, ["measure", "injection"])

    assert result.exit_code == 0, result.output
    assert "Injection Effectiveness" in result.output
    assert "75.0%" in result.output
