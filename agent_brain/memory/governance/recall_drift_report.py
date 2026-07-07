"""Read-only summaries for recall drift sidecar records."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from agent_brain.memory.governance.recall_events import (
    iter_gap_records,
    iter_task_outcome_feedback_applications,
    iter_task_outcomes,
)


@dataclass(frozen=True)
class RecallDriftReport:
    gap_count: int
    task_outcome_count: int
    task_outcome_feedback_applied_count: int
    task_outcome_feedback_skipped_count: int
    gaps_by_reason: dict[str, int]
    gaps_by_family: dict[str, int]
    task_outcomes_by_status: dict[str, int]
    implicit_positive_count: int
    explicit_correction_count: int


def build_recall_drift_report(brain_dir: Path) -> RecallDriftReport:
    gaps = list(iter_gap_records(brain_dir))
    outcomes = list(iter_task_outcomes(brain_dir))
    applications = list(iter_task_outcome_feedback_applications(brain_dir))
    gaps_by_reason = Counter(gap.reason for gap in gaps)
    gaps_by_family = Counter(_gap_family(gap.reason) for gap in gaps)
    outcomes_by_status = Counter(outcome.outcome for outcome in outcomes)
    implicit_positive_count = sum(
        1 for outcome in outcomes if "implicit_continue" in outcome.feedback_signals
    )
    explicit_correction_count = sum(
        1 for outcome in outcomes if "user_correction" in outcome.feedback_signals
    )
    return RecallDriftReport(
        gap_count=len(gaps),
        task_outcome_count=len(outcomes),
        task_outcome_feedback_applied_count=sum(
            1 for application in applications if application.applied
        ),
        task_outcome_feedback_skipped_count=sum(
            1 for application in applications if not application.applied
        ),
        gaps_by_reason=dict(gaps_by_reason),
        gaps_by_family=dict(gaps_by_family),
        task_outcomes_by_status=dict(outcomes_by_status),
        implicit_positive_count=implicit_positive_count,
        explicit_correction_count=explicit_correction_count,
    )


def _gap_family(reason: str) -> str:
    if reason == "query_not_injectable":
        return "query_gate"
    if reason == "empty_recall":
        return "empty_recall"
    if reason in {"all_candidates_rejected", "only_rejected", "partial_candidates_rejected"}:
        return "context_rejected"
    if reason == "manual_revalidation":
        return "manual_review"
    return "other"


__all__ = ["RecallDriftReport", "build_recall_drift_report"]
