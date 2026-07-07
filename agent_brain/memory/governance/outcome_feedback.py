"""Apply task outcome records to memory injection feedback safely."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from agent_brain.memory.governance.feedback import ConfidenceFeedback
from agent_brain.memory.context.injection_feedback import InjectionFeedback
from agent_brain.memory.governance.recall_events import (
    TaskOutcome,
    iter_task_outcome_feedback_applications,
    iter_task_outcomes,
    record_task_outcome_feedback_application,
)

_IMPLICIT_POSITIVE_SIGNALS = {
    "implicit_continue",
    "implicit_no_correction",
    "implicit_task_success",
    "implicit_user_confirmed",
}
_SUCCESS_OUTCOMES = {"success", "succeeded", "complete", "completed", "accepted", "resolved"}
_IMPLICIT_POSITIVE_MIN_CONFIDENCE = 0.85
_IMPLICIT_POSITIVE_GAIN_DELTA = 0.03
_IMPLICIT_POSITIVE_VALUE_TAG = "implicit-positive"


@dataclass(frozen=True)
class TaskOutcomeFeedbackReport:
    outcome_id: str
    applied: bool
    adopted: tuple[str, ...] = ()
    rejected: tuple[str, ...] = ()
    ignored: tuple[str, ...] = ()
    skipped_reason: str | None = None


@dataclass(frozen=True)
class TaskOutcomeFeedbackBatchReport:
    reports: tuple[TaskOutcomeFeedbackReport, ...]

    @property
    def applied_count(self) -> int:
        return sum(1 for report in self.reports if report.applied)

    @property
    def skipped_count(self) -> int:
        return sum(
            1
            for report in self.reports
            if not report.applied and report.skipped_reason != "already_applied"
        )

    @property
    def already_applied_count(self) -> int:
        return sum(1 for report in self.reports if report.skipped_reason == "already_applied")

    def to_dict(self) -> dict[str, object]:
        return {
            "applied_count": self.applied_count,
            "skipped_count": self.skipped_count,
            "already_applied_count": self.already_applied_count,
            "reports": [
                {
                    "outcome_id": report.outcome_id,
                    "applied": report.applied,
                    "adopted": list(report.adopted),
                    "rejected": list(report.rejected),
                    "ignored": list(report.ignored),
                    "skipped_reason": report.skipped_reason,
                }
                for report in self.reports
            ],
        }


def apply_task_outcome_feedback(
    brain_dir: Path,
    *,
    items_store,
    index,
    outcome: TaskOutcome,
    force: bool = False,
) -> TaskOutcomeFeedbackReport:
    """Apply explicit adopted/rejected ids from one outcome exactly once.

    Implicit positive signals are applied only for a narrow safe subset: the
    task succeeded with high confidence, exactly one memory was injected, and
    the signal is allow-listed. Ambiguous multi-memory cohorts remain reporting
    signals only.
    """
    applied_ids = _applied_outcome_ids(brain_dir)
    if outcome.outcome_id in applied_ids and not force:
        return TaskOutcomeFeedbackReport(
            outcome_id=outcome.outcome_id,
            applied=False,
            skipped_reason="already_applied",
        )

    implicit_adopted = _implicit_positive_adopted_ids(outcome)
    adopted_ids = outcome.adopted_ids or implicit_adopted
    rejected_ids = outcome.rejected_ids

    if not adopted_ids and not rejected_ids:
        _record_skip(brain_dir, outcome, "no_explicit_feedback")
        return TaskOutcomeFeedbackReport(
            outcome_id=outcome.outcome_id,
            applied=False,
            skipped_reason="no_explicit_feedback",
        )

    if _missing_feedback_items(items_store, adopted_ids=adopted_ids, rejected_ids=rejected_ids):
        return TaskOutcomeFeedbackReport(
            outcome_id=outcome.outcome_id,
            applied=False,
            skipped_reason="missing_feedback_items",
        )

    if implicit_adopted and not outcome.adopted_ids and not outcome.rejected_ids:
        ConfidenceFeedback(index=index, items_store=items_store).on_reaffirm(
            implicit_adopted[0],
            support_delta=1,
            gain_delta=_IMPLICIT_POSITIVE_GAIN_DELTA,
        )
        record_task_outcome_feedback_application(
            brain_dir,
            outcome_id=outcome.outcome_id,
            applied=True,
            adopted_ids=implicit_adopted,
            rejected_ids=(),
            skipped_reason=None,
            adapter=outcome.adapter,
            session_id=outcome.session_id,
        )
        _apply_value_tags(
            items_store,
            index,
            adopted_ids=implicit_adopted,
            value_tags=(*outcome.value_tags, _IMPLICIT_POSITIVE_VALUE_TAG),
        )
        return TaskOutcomeFeedbackReport(
            outcome_id=outcome.outcome_id,
            applied=True,
            adopted=implicit_adopted,
        )

    try:
        injection_report = InjectionFeedback(items_store=items_store, index=index).apply(
            injected_ids=list(outcome.injected_ids),
            adopted_ids=list(adopted_ids),
            rejected_ids=list(rejected_ids),
        )
    except ValueError:
        _record_skip(brain_dir, outcome, "invalid_feedback_ids")
        return TaskOutcomeFeedbackReport(
            outcome_id=outcome.outcome_id,
            applied=False,
            skipped_reason="invalid_feedback_ids",
        )

    record_task_outcome_feedback_application(
        brain_dir,
        outcome_id=outcome.outcome_id,
        applied=True,
        adopted_ids=injection_report.adopted,
        rejected_ids=injection_report.rejected,
        skipped_reason=None,
        adapter=outcome.adapter,
        session_id=outcome.session_id,
    )
    _apply_value_tags(
        items_store,
        index,
        adopted_ids=injection_report.adopted,
        value_tags=outcome.value_tags,
    )
    return TaskOutcomeFeedbackReport(
        outcome_id=outcome.outcome_id,
        applied=True,
        adopted=injection_report.adopted,
        rejected=injection_report.rejected,
        ignored=injection_report.ignored,
    )


def apply_task_outcome_feedback_batch(
    brain_dir: Path,
    *,
    items_store,
    index,
    force: bool = False,
) -> TaskOutcomeFeedbackBatchReport:
    reports = tuple(
        apply_task_outcome_feedback(
            brain_dir,
            items_store=items_store,
            index=index,
            outcome=outcome,
            force=force,
        )
        for outcome in iter_task_outcomes(brain_dir)
    )
    return TaskOutcomeFeedbackBatchReport(reports=reports)


def _record_skip(brain_dir: Path, outcome: TaskOutcome, reason: str) -> None:
    record_task_outcome_feedback_application(
        brain_dir,
        outcome_id=outcome.outcome_id,
        applied=False,
        adopted_ids=(),
        rejected_ids=(),
        skipped_reason=reason,
        adapter=outcome.adapter,
        session_id=outcome.session_id,
    )


def _missing_feedback_items(
    items_store,
    *,
    adopted_ids: tuple[str, ...],
    rejected_ids: tuple[str, ...],
) -> bool:
    for item_id in (*adopted_ids, *rejected_ids):
        try:
            items_store.get(item_id)
        except FileNotFoundError:
            return True
    return False


def _implicit_positive_adopted_ids(outcome: TaskOutcome) -> tuple[str, ...]:
    if outcome.adopted_ids or outcome.rejected_ids:
        return ()
    if outcome.outcome.strip().lower() not in _SUCCESS_OUTCOMES:
        return ()
    if outcome.confidence < _IMPLICIT_POSITIVE_MIN_CONFIDENCE:
        return ()
    if len(outcome.injected_ids) != 1:
        return ()
    signals = {signal.strip().lower() for signal in outcome.feedback_signals}
    if not signals & _IMPLICIT_POSITIVE_SIGNALS:
        return ()
    return outcome.injected_ids


def _apply_value_tags(
    items_store,
    index,
    *,
    adopted_ids: tuple[str, ...],
    value_tags: tuple[str, ...],
) -> None:
    tags = tuple(_normalize_value_tag(tag) for tag in value_tags)
    tags = tuple(tag for tag in tags if tag)
    if not tags:
        return

    for item_id in adopted_ids:
        item, body = items_store.get(item_id)
        merged = sorted({*item.tags, *tags})
        if merged == item.tags:
            continue
        updated = items_store.update_frontmatter(item_id, tags=merged)
        if index is not None:
            index.upsert(updated, body, embedding=None)


def _normalize_value_tag(tag: str) -> str | None:
    raw = str(tag or "").strip().lower()
    if raw.startswith("value:"):
        raw = raw.removeprefix("value:")
    slug = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-")
    if not slug:
        return None
    return f"value:{slug}"


def _applied_outcome_ids(brain_dir: Path) -> set[str]:
    return {
        application.outcome_id
        for application in iter_task_outcome_feedback_applications(brain_dir)
    }


__all__ = [
    "TaskOutcomeFeedbackBatchReport",
    "TaskOutcomeFeedbackReport",
    "apply_task_outcome_feedback",
    "apply_task_outcome_feedback_batch",
]
