from __future__ import annotations

from typing import Any


def apply_reaffirm(feedback: Any, item_id: str) -> None:
    feedback.on_reaffirm(item_id)


def apply_reject(feedback: Any, item_id: str) -> None:
    feedback.on_reject(item_id)


def apply_injection_feedback(
    store: Any,
    index: Any,
    *,
    injected_ids: list[str],
    adopted_ids: list[str] | None = None,
    rejected_ids: list[str] | None = None,
) -> dict[str, list[str]]:
    from agent_brain.memory.context.injection_feedback import InjectionFeedback

    report = InjectionFeedback(items_store=store, index=index).apply(
        injected_ids=injected_ids,
        adopted_ids=adopted_ids or [],
        rejected_ids=rejected_ids or [],
    )
    return {
        "injected": list(report.injected),
        "adopted": list(report.adopted),
        "rejected": list(report.rejected),
        "ignored": list(report.ignored),
    }


def apply_task_outcome_feedback(
    brain_dir: Any,
    store: Any,
    index: Any,
    *,
    force: bool = False,
) -> dict[str, Any]:
    from agent_brain.memory.governance.outcome_feedback import apply_task_outcome_feedback_batch

    report = apply_task_outcome_feedback_batch(
        brain_dir,
        items_store=store,
        index=index,
        force=force,
    )
    return report.to_dict()


def apply_confirm(store: Any, index: Any, item_id: str, confidence: float) -> None:
    bounded_confidence = min(max(confidence, 0.0), 1.0)
    store.update_frontmatter(item_id, confidence=bounded_confidence)
    index.update_confidence(item_id, confidence)


__all__ = [
    "apply_confirm",
    "apply_injection_feedback",
    "apply_task_outcome_feedback",
    "apply_reaffirm",
    "apply_reject",
]
