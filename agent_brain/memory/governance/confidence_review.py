"""Deterministic triage for low-confidence memory review."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.memory.governance.review_queue import TERMINAL_REVIEW_TAGS


LowConfidenceDisposition = Literal[
    "contested",
    "source_gap",
    "source_backed",
    "terminal",
]

LOW_CONFIDENCE_THRESHOLD = 0.5


@dataclass(frozen=True)
class LowConfidenceAssessment:
    disposition: LowConfidenceDisposition
    explicit_source_ref_count: int
    provenance_ref_count: int
    recommended_action: str
    actionable: bool


def assess_low_confidence(item: MemoryItem) -> LowConfidenceAssessment | None:
    """Classify one low-confidence item without changing confidence or meaning."""

    if item.confidence >= LOW_CONFIDENCE_THRESHOLD or item.superseded_by:
        return None
    tags = {tag.casefold() for tag in item.tags}
    explicit_sources = (
        len(item.refs.files)
        + len(item.refs.urls)
        + len(item.refs.mems)
        + len(item.refs.commits)
    )
    provenance = len(item.refs.resources) + len(item.refs.extractions)
    if tags & TERMINAL_REVIEW_TAGS:
        return LowConfidenceAssessment(
            "terminal",
            explicit_sources,
            provenance,
            "none",
            False,
        )
    if "contested" in tags:
        return LowConfidenceAssessment(
            "contested",
            explicit_sources,
            provenance,
            "resolve_contestation_then_approve_or_reject",
            True,
        )
    if explicit_sources == 0:
        return LowConfidenceAssessment(
            "source_gap",
            explicit_sources,
            provenance,
            "attach_source_or_reject",
            True,
        )
    return LowConfidenceAssessment(
        "source_backed",
        explicit_sources,
        provenance,
        "inspect_source_then_approve_or_reject",
        True,
    )


__all__ = [
    "LOW_CONFIDENCE_THRESHOLD",
    "LowConfidenceAssessment",
    "LowConfidenceDisposition",
    "assess_low_confidence",
]
