"""Tests for dreaming phase error isolation helpers."""
from datetime import datetime, timezone

from agent_brain.memory.governance.evolve.dream_phases import run_dream_phase
from agent_brain.memory.governance.evolve.dreaming import DreamReport


def _report() -> DreamReport:
    return DreamReport(started_at=datetime(2026, 6, 10, tzinfo=timezone.utc))


def test_run_dream_phase_returns_successful_result_without_errors() -> None:
    report = _report()

    result = run_dream_phase(report, "harvest", lambda: 3)

    assert result == 3
    assert report.errors == []


def test_run_dream_phase_records_labeled_error_and_returns_none() -> None:
    report = _report()

    def fail() -> int:
        raise RuntimeError("store unavailable")

    result = run_dream_phase(report, "harvest", fail)

    assert result is None
    assert report.errors == ["harvest: store unavailable"]
