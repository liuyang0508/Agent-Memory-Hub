"""Shared types for pre-injection context firewall decisions."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Literal

from agent_brain.contracts.memory_item import MemoryItem

FirewallAction = Literal["include", "demote", "exclude"]


@dataclass(frozen=True)
class ContextCandidate:
    """A memory candidate plus retrieval-time context used by the firewall."""

    item: MemoryItem
    body: str = ""
    score: float = 0.0
    source: str = "retrieval"
    cluster_key: str | None = None

    def render_text(self) -> str:
        """Render the text that would be charged against injection budget."""
        if self.body:
            return self.body
        summary = f" — {self.item.summary}" if self.item.summary else ""
        return f"[{self.item.type}] {self.item.title}{summary}"


@dataclass(frozen=True)
class FirewallDecision:
    """The firewall action and scoring outcome for one candidate."""

    candidate: ContextCandidate
    action: FirewallAction
    reasons: tuple[str, ...]
    score: float
    effective_score: float


@dataclass(frozen=True)
class FirewallResult:
    """The ordered include/exclude sets produced for a candidate cohort."""

    included: list[FirewallDecision]
    excluded: list[FirewallDecision]
    decisions: list[FirewallDecision]
    cohort_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class CohortGateResult:
    """Intermediate include/exclude split returned by a cohort-level gate."""

    included: list[FirewallDecision]
    excluded: list[FirewallDecision]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ContextFirewallConfig:
    """Policy thresholds for deciding what memory context may be injected."""

    allowed_sensitivities: tuple[str, ...] = ("public", "internal")
    stale_signal_days: int = 14
    stale_handoff_days: int = 30
    low_confidence_exclude_threshold: float = 0.2
    low_confidence_demote_threshold: float = 0.4
    low_confidence_penalty: float = 0.5
    negative_feedback_exclude_min_contradictions: int = 3
    negative_feedback_exclude_gain_threshold: float = -0.5
    contested_penalty: float = 0.35
    l0_evidence_only_penalty: float = 0.2
    require_source_for_fact_decision: bool = True
    max_per_duplicate_cluster: int = 1
    min_strong_term_coverage: float = 1.0
    topic_recency_min_shared_terms: int = 3
    query_term_coverage_bonus: float = 0.01
    semantic_route_min_similarity: float = 0.56
    raw_route_min_coverage: float = 0.45

    def __post_init__(self) -> None:
        for field in (
            "semantic_route_min_similarity",
            "raw_route_min_coverage",
        ):
            value = getattr(self, field)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0.0 <= value <= 1.0
            ):
                raise ValueError(f"{field} must be a finite number in [0, 1]")


__all__ = [
    "CohortGateResult",
    "ContextCandidate",
    "ContextFirewallConfig",
    "FirewallAction",
    "FirewallDecision",
    "FirewallResult",
]
