"""Single fail-closed boundary for turning retrieved memories into prompt context."""

import logging
from collections import Counter
from dataclasses import dataclass
from typing import Mapping, get_args

from agent_brain.memory.context.context_firewall import ContextFirewall
from agent_brain.memory.context.context_firewall_rules import exclude_with
from agent_brain.memory.context.context_firewall_types import (
    ContextCandidate,
    FirewallDecision,
    FirewallResult,
)
from agent_brain.memory.context.context_loading import ContextVerbosity
from agent_brain.memory.context.context_packing import PackedDecision, pack_decisions
from agent_brain.memory.context.query_signal import QuerySignal, analyze_injection_query

logger = logging.getLogger(__name__)
_CONTEXT_VERBOSITIES = frozenset(get_args(ContextVerbosity))


@dataclass(frozen=True)
class InjectionResult:
    included: list[PackedDecision]
    excluded: list[FirewallDecision]
    cohort_reasons: tuple[str, ...]
    used_tokens: int
    full_tokens: int

    def metrics(self) -> dict[str, object]:
        reason_counts = Counter(
            reason
            for decision in self.excluded
            for reason in set(decision.reasons)
        )
        return {
            "candidate_count": len(self.included) + len(self.excluded),
            "included_count": len(self.included),
            "excluded_count": len(self.excluded),
            "excluded_reasons": dict(sorted(reason_counts.items())),
            "packed_tokens": self.used_tokens,
            "full_tokens": self.full_tokens,
        }


def _record_injection_diagnostic(*, surface: str, reason: str, count: int) -> None:
    """Emit aggregate-only diagnostics without query or candidate content."""
    if count <= 0:
        return
    logger.warning(
        "injection diagnostic surface=%s reason=%s count=%d",
        surface,
        reason,
        count,
    )


def evaluate_injection_candidates(
    candidates: list[ContextCandidate],
    *,
    query: str | None = None,
    query_signal: QuerySignal | None = None,
    max_items: int | None = None,
    current_scope: Mapping[str, str] | None = None,
) -> FirewallResult:
    signal = query_signal
    if signal is None and query is not None:
        signal = analyze_injection_query(query.replace("|", " "))
    return ContextFirewall().filter(
        candidates,
        query=query,
        query_signal=signal,
        max_items=max_items,
        current_scope=current_scope,
    )


def build_injection_context(
    candidates: list[ContextCandidate],
    *,
    query: str | None = None,
    query_signal: QuerySignal | None = None,
    requested: ContextVerbosity = "auto",
    max_items: int | None = None,
    budget_tokens: int | None = None,
    current_scope: Mapping[str, str] | None = None,
) -> InjectionResult:
    if requested not in _CONTEXT_VERBOSITIES:
        raise ValueError(f"unsupported context verbosity: {requested!r}")
    firewall = evaluate_injection_candidates(
        candidates,
        query=query,
        query_signal=query_signal,
        max_items=max_items,
        current_scope=current_scope,
    )
    included = []
    excluded = list(firewall.excluded)
    used_tokens = 0
    full_tokens = 0
    for decision in firewall.included:
        remaining = None if budget_tokens is None else max(0, budget_tokens - used_tokens)
        try:
            packed = pack_decisions([decision], requested=requested, budget_tokens=remaining)
        except Exception:
            excluded.append(exclude_with(decision, "pack_error"))
            continue
        included.extend(packed.included)
        excluded.extend(packed.excluded)
        used_tokens += packed.used_tokens
        full_tokens += packed.full_tokens
    return InjectionResult(
        included=included,
        excluded=excluded,
        cohort_reasons=firewall.cohort_reasons,
        used_tokens=used_tokens,
        full_tokens=full_tokens,
    )


__all__ = [
    "InjectionResult",
    "build_injection_context",
    "evaluate_injection_candidates",
]
