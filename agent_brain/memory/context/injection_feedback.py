"""Feedback loop for memory items that were actually injected into context."""

from __future__ import annotations

from dataclasses import dataclass

from agent_brain.memory.governance.feedback import ConfidenceFeedback


@dataclass(frozen=True)
class InjectionFeedbackReport:
    injected: tuple[str, ...]
    adopted: tuple[str, ...]
    rejected: tuple[str, ...]
    ignored: tuple[str, ...]


class InjectionFeedback:
    """Apply explicit adoption/rejection feedback for an injected cohort.

    Retrieval and injection are not adoption. Only ``adopted_ids`` reinforce an
    item; ``rejected_ids`` penalize it; injected-but-unmentioned items stay
    unchanged so accidental exposure does not make them hotter.
    """

    def __init__(self, *, items_store=None, index=None) -> None:
        self.feedback = ConfidenceFeedback(index=index, items_store=items_store)

    def apply(
        self,
        *,
        injected_ids: list[str] | tuple[str, ...],
        adopted_ids: list[str] | tuple[str, ...] = (),
        rejected_ids: list[str] | tuple[str, ...] = (),
    ) -> InjectionFeedbackReport:
        injected = _dedupe(injected_ids)
        adopted = _dedupe(adopted_ids)
        rejected = _dedupe(rejected_ids)
        injected_set = set(injected)
        outside = (set(adopted) | set(rejected)) - injected_set
        if outside:
            raise ValueError(
                "feedback item(s) not in injected cohort: "
                + ", ".join(sorted(outside))
            )
        overlap = set(adopted) & set(rejected)
        if overlap:
            raise ValueError(
                "feedback item(s) cannot be both adopted and rejected: "
                + ", ".join(sorted(overlap))
            )

        for item_id in adopted:
            self.feedback.on_reaffirm(item_id)
        for item_id in rejected:
            self.feedback.on_reject(item_id)

        ignored = tuple(item_id for item_id in injected if item_id not in set(adopted) | set(rejected))
        return InjectionFeedbackReport(
            injected=injected,
            adopted=adopted,
            rejected=rejected,
            ignored=ignored,
        )


def _dedupe(item_ids: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for item_id in item_ids:
        if item_id in seen:
            continue
        seen.add(item_id)
        result.append(item_id)
    return tuple(result)


__all__ = ["InjectionFeedback", "InjectionFeedbackReport"]
