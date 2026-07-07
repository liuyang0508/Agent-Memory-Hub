"""Shared helpers for isolated dreaming phases."""
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TypeVar


class _DreamReport(Protocol):
    errors: list[str]


T = TypeVar("T")


def run_dream_phase(report: _DreamReport, label: str, phase: Callable[[], T]) -> T | None:
    """Run a dreaming phase and record a labeled error without stopping the cycle."""
    try:
        return phase()
    except Exception as e:
        report.errors.append(f"{label}: {e}")
        return None
