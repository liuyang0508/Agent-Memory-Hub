"""Aggregate injection-effectiveness metrics over closed cohorts.

This is the "measure" read model: it joins injection cohorts, explicit task
outcomes, and the closure ledger into terminal states, then reports adoption,
rejection and signal-coverage rates. Silence is never treated as adoption —
a ``closed_no_signal`` cohort counts in the denominator only.

Read-only and side-effect free. Weak signals never feed back into item
confidence; only the existing ``outcome_feedback`` application path does that.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from agent_brain.memory.context.injection_closures import (
    TERMINAL_ADOPTED,
    TERMINAL_NO_SIGNAL,
    TERMINAL_REJECTED,
    closed_cohort_states,
    explicit_outcome_cohort_ids,
)
from agent_brain.memory.context.injection_cohorts import (
    InjectionCohort,
    iter_injection_cohorts,
)


@dataclass(frozen=True)
class EffectivenessRates:
    closed_total: int
    adopted_count: int
    rejected_count: int
    no_signal_count: int
    signal_coverage: float
    adoption_rate: float
    rejection_rate: float

    def to_dict(self) -> dict[str, object]:
        return {
            "closed_total": self.closed_total,
            "adopted_count": self.adopted_count,
            "rejected_count": self.rejected_count,
            "no_signal_count": self.no_signal_count,
            "signal_coverage": self.signal_coverage,
            "adoption_rate": self.adoption_rate,
            "rejection_rate": self.rejection_rate,
        }


@dataclass(frozen=True)
class EffectivenessReport:
    window_days: int | None
    rates: EffectivenessRates
    by_group: dict[str, dict[str, EffectivenessRates]]

    def to_dict(self) -> dict[str, object]:
        return {
            "window_days": self.window_days,
            "rates": self.rates.to_dict(),
            "by_group": {
                dimension: {key: rates.to_dict() for key, rates in groups.items()}
                for dimension, groups in self.by_group.items()
            },
        }


def resolve_cohort_terminal_state(
    cohort: InjectionCohort,
    *,
    closure_states: dict[str, str],
    explicit_states: dict[str, str],
) -> str | None:
    """Return the terminal state for one cohort, or None if still in window.

    Explicit outcome states win over ledger closures; the closure ledger only
    supplies ``closed_no_signal``.
    """
    explicit = explicit_states.get(cohort.cohort_id)
    if explicit is not None:
        return explicit
    closed = closure_states.get(cohort.cohort_id)
    if closed is not None:
        return closed
    return None


def measure_injection_effectiveness(
    brain_dir: Path,
    *,
    since_days: int | None = None,
    adapter: str | None = None,
    group_by: tuple[str, ...] = ("adapter",),
    now: datetime | None = None,
) -> EffectivenessReport:
    """Compute aggregate effectiveness rates over closed cohorts.

    Only cohorts with a terminal state enter the denominator. Cohorts that are
    neither explicitly resolved nor closed by the sweep are in-window and are
    excluded, so in-flight sessions never read as silence.
    """
    brain = Path(brain_dir)
    reference = _as_utc(now or datetime.now(timezone.utc))
    cutoff = None
    if since_days is not None and since_days > 0:
        cutoff = reference.timestamp() - since_days * 86400.0

    closure_states = closed_cohort_states(brain)
    explicit_states = explicit_outcome_cohort_ids(brain)

    counters: dict[str, dict[str, int]] = {"": {"adopted": 0, "rejected": 0, "no_signal": 0}}
    group_counters: dict[str, dict[str, dict[str, int]]] = {
        dimension: {} for dimension in group_by
    }

    for cohort in iter_injection_cohorts(brain, adapter=adapter):
        if cutoff is not None and _cohort_timestamp(cohort) < cutoff:
            continue
        state = resolve_cohort_terminal_state(
            cohort,
            closure_states=closure_states,
            explicit_states=explicit_states,
        )
        if state is None:
            continue
        bucket = _bucket_for_state(state)
        if bucket is None:
            continue
        counters[""][bucket] += 1
        for dimension in group_by:
            key = _group_key(dimension, cohort)
            group_counters[dimension].setdefault(
                key, {"adopted": 0, "rejected": 0, "no_signal": 0}
            )[bucket] += 1

    return EffectivenessReport(
        window_days=since_days,
        rates=_rates(counters[""]),
        by_group={
            dimension: {key: _rates(counts) for key, counts in groups.items()}
            for dimension, groups in group_counters.items()
        },
    )


def _bucket_for_state(state: str) -> str | None:
    if state == TERMINAL_ADOPTED:
        return "adopted"
    if state == TERMINAL_REJECTED:
        return "rejected"
    if state == TERMINAL_NO_SIGNAL:
        return "no_signal"
    return None


def _rates(counts: dict[str, int]) -> EffectivenessRates:
    adopted = counts["adopted"]
    rejected = counts["rejected"]
    no_signal = counts["no_signal"]
    closed_total = adopted + rejected + no_signal
    return EffectivenessRates(
        closed_total=closed_total,
        adopted_count=adopted,
        rejected_count=rejected,
        no_signal_count=no_signal,
        signal_coverage=_ratio(adopted + rejected, closed_total),
        adoption_rate=_ratio(adopted, closed_total),
        rejection_rate=_ratio(rejected, closed_total),
    )


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _group_key(dimension: str, cohort: InjectionCohort) -> str:
    if dimension == "adapter":
        return cohort.adapter or "unknown"
    return "unknown"


def _cohort_timestamp(cohort: InjectionCohort) -> float:
    try:
        parsed = datetime.fromisoformat(cohort.timestamp)
    except (ValueError, TypeError):
        return float("inf")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).timestamp()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "EffectivenessRates",
    "EffectivenessReport",
    "measure_injection_effectiveness",
    "resolve_cohort_terminal_state",
]
