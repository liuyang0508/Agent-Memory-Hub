"""Privacy-safe automatic feedback from an explicit next-user response."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from agent_brain.memory.context.injection_cohorts import (
    InjectionCohort,
    latest_injection_cohort,
)
from agent_brain.memory.governance.outcome_feedback import (
    TaskOutcomeFeedbackReport,
    apply_task_outcome_feedback,
)
from agent_brain.memory.governance.recall_events import (
    iter_task_outcomes,
    record_task_outcome,
)
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.platform.indexing.index import HubIndex


_NEGATIVE = re.compile(
    r"(?:刚才|上次|之前|这些|全部|都).{0,20}"
    r"(?:不对|错了|错误|没用|无用|无关|答非所问)"
    r"|(?:previous|last|those|all).{0,30}"
    r"(?:wrong|incorrect|unhelpful|irrelevant)",
    re.IGNORECASE,
)
_POSITIVE = re.compile(
    r"(?:刚才|上次|之前|这些|全部|都).{0,20}"
    r"(?:对了|正确|有用|有效|帮到了)"
    r"|(?:previous|last|those|all).{0,30}"
    r"(?:correct|helpful|useful|relevant)",
    re.IGNORECASE,
)
_CONTINUE = re.compile(
    r"^\s*(?:继续|接着|往下|可以|好的|好|对|没问题|"
    r"continue|go on|looks good|sounds good)",
    re.IGNORECASE,
)
_MIXED = re.compile(r"(?:但是|但|不过|然而|不对|错|but|however|wrong)", re.IGNORECASE)


@dataclass(frozen=True)
class AutoFeedbackObservation:
    cohort_id: str | None
    decision: str
    applied: bool
    adopted: tuple[str, ...] = ()
    rejected: tuple[str, ...] = ()
    reason: str = ""


def observe_prompt_feedback(
    brain_dir: Path,
    *,
    prompt: str,
    adapter: str,
    session_id: str | None,
    items_store: ItemsStore | None = None,
    index: HubIndex | None = None,
) -> AutoFeedbackObservation:
    """Apply only unambiguous feedback about the immediately preceding cohort.

    Raw prompt text is used in memory and never persisted. Multi-item cohorts
    require explicit all-positive/all-negative wording; a generic continuation
    reinforces only a single-item cohort.
    """

    cohort = latest_injection_cohort(
        brain_dir,
        adapter=adapter or None,
        session_id=session_id,
    )
    if cohort is None:
        return AutoFeedbackObservation(None, "none", False, reason="no_cohort")
    if _cohort_already_observed(brain_dir, cohort):
        return AutoFeedbackObservation(
            cohort.cohort_id,
            "none",
            False,
            reason="already_observed",
        )

    decision = _classify(prompt, cohort)
    if decision == "none":
        return AutoFeedbackObservation(
            cohort.cohort_id,
            decision,
            False,
            reason="ambiguous_or_no_signal",
        )

    adopted = cohort.item_ids if decision == "adopted" else ()
    rejected = cohort.item_ids if decision == "rejected" else ()
    outcome = record_task_outcome(
        brain_dir,
        task_id=f"auto-feedback:{cohort.cohort_id}",
        question=f"injection cohort {cohort.cohort_id}",
        outcome="success" if adopted else "corrected",
        feedback_signals=(
            "automatic_explicit_feedback"
            if len(cohort.item_ids) > 1
            else "automatic_next_prompt_feedback",
        ),
        confidence=0.98 if len(cohort.item_ids) > 1 else 0.9,
        injected_ids=cohort.item_ids,
        adopted_ids=adopted,
        rejected_ids=rejected,
        adapter=adapter,
        session_id=session_id,
        cwd=cohort.cwd,
        cohort_id=cohort.cohort_id,
    )

    store = items_store or ItemsStore(Path(brain_dir) / "items")
    owns_index = index is None and (Path(brain_dir) / "index.db").exists()
    live_index = index
    if owns_index:
        live_index = HubIndex(Path(brain_dir) / "index.db")
    try:
        report = apply_task_outcome_feedback(
            brain_dir,
            items_store=store,
            index=live_index,
            outcome=outcome,
        )
    finally:
        if owns_index and live_index is not None:
            live_index.close()
    from agent_brain.memory.recall.adaptive_learning import refresh_learning_profile

    try:
        refresh_learning_profile(brain_dir)
    except OSError:
        pass
    return _observation(cohort, decision, report)


def _classify(prompt: str, cohort: InjectionCohort) -> str:
    text = str(prompt or "").strip()
    if not text:
        return "none"
    negative = bool(_NEGATIVE.search(text))
    positive = bool(_POSITIVE.search(text))
    if negative == positive and (negative or positive):
        return "none"
    if negative:
        return "rejected"
    if positive:
        return "adopted"
    if len(cohort.item_ids) == 1 and _CONTINUE.search(text) and not _MIXED.search(text):
        return "adopted"
    return "none"


def _cohort_already_observed(brain_dir: Path, cohort: InjectionCohort) -> bool:
    expected = {
        f"auto-feedback:{cohort.cohort_id}",
        f"injection-feedback:{cohort.cohort_id}",
    }
    return any(outcome.task_id in expected for outcome in iter_task_outcomes(brain_dir))


def _observation(
    cohort: InjectionCohort,
    decision: str,
    report: TaskOutcomeFeedbackReport,
) -> AutoFeedbackObservation:
    return AutoFeedbackObservation(
        cohort_id=cohort.cohort_id,
        decision=decision,
        applied=report.applied,
        adopted=report.adopted,
        rejected=report.rejected,
        reason=report.skipped_reason or "applied",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply privacy-safe automatic recall feedback.")
    parser.add_argument("--brain-dir", type=Path, required=True)
    parser.add_argument("--adapter", default="unknown")
    parser.add_argument("--session")
    args = parser.parse_args(argv)
    observe_prompt_feedback(
        args.brain_dir,
        prompt=sys.stdin.read(),
        adapter=args.adapter,
        session_id=args.session,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["AutoFeedbackObservation", "observe_prompt_feedback"]
