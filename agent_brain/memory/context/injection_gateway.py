"""Single fail-closed boundary for turning retrieved memories into prompt context."""

import logging
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, get_args

from agent_brain.memory.context.context_firewall import ContextFirewall
from agent_brain.memory.context.context_firewall_rules import exclude_with
from agent_brain.memory.context.context_firewall_types import (
    ContextCandidate,
    FirewallDecision,
    FirewallResult,
)
from agent_brain.memory.context.injection_contract import (
    GATEWAY_SYNTHETIC_EXCLUSION_REASONS,
    HYDRATE_ERROR_REASON,
    INJECTION_EXCLUSION_REASONS,
    PACK_ERROR_REASON,
)
from agent_brain.memory.context.context_loading import ContextVerbosity
from agent_brain.memory.context.context_packing import PackedDecision, pack_decisions
from agent_brain.memory.context.injection_query_context import InjectionQueryContext
from agent_brain.memory.context.query_signal import QuerySignal, analyze_injection_query

logger = logging.getLogger(__name__)
_CONTEXT_VERBOSITIES = frozenset(get_args(ContextVerbosity))
_INJECTION_OVERFETCH_CAP = 50
_UNKNOWN_EXCLUSION_REASON_ERROR = "unsupported injection exclusion reason"
_PACKING_ANNOTATION_REASONS = frozenset({
    "budget_downgraded_to_locator",
    "budget_downgraded_to_overview",
})


@dataclass(frozen=True)
class InjectionResult:
    included: list[PackedDecision]
    excluded: list[FirewallDecision]
    cohort_reasons: tuple[str, ...]
    used_tokens: int
    full_tokens: int

    def metrics(self) -> dict[str, object]:
        view_counts = Counter(entry.pack.selected_view for entry in self.included)
        return {
            "candidate_count": len(self.included) + len(self.excluded),
            "included_count": len(self.included),
            "excluded_count": len(self.excluded),
            "excluded_reasons": injection_exclusion_reason_counts(self.excluded),
            "selected_views": dict(sorted(view_counts.items())),
            "compressed_count": sum(
                1 for entry in self.included if entry.pack.compressed
            ),
            "packed_tokens": self.used_tokens,
            "full_tokens": self.full_tokens,
        }


def injection_exclusion_reason_counts(
    decisions: Iterable[FirewallDecision],
    *,
    hydrate_error_count: int = 0,
) -> dict[str, int]:
    """Return closed-set aggregate exclusion counts without item identity."""
    _require_nonnegative_int(hydrate_error_count, "hydrate_error_count")
    observed_reasons = {
        reason
        for decision in decisions
        for reason in set(decision.reasons)
    }
    reason_counts = Counter(
        reason
        for decision in decisions
        for reason in set(decision.reasons)
        if reason in INJECTION_EXCLUSION_REASONS
    )
    if hydrate_error_count:
        reason_counts[HYDRATE_ERROR_REASON] += hydrate_error_count
    unknown = observed_reasons - INJECTION_EXCLUSION_REASONS - _PACKING_ANNOTATION_REASONS
    if unknown:
        raise ValueError(_UNKNOWN_EXCLUSION_REASON_ERROR)
    return dict(sorted(reason_counts.items()))


def surface_injection_metrics(
    result: InjectionResult,
    *,
    raw_candidate_count: int,
    hydrate_error_count: int,
) -> dict[str, object]:
    """Merge pre-Gateway hydrate failures into aggregate surface metrics.

    Bare ``InjectionResult.metrics()`` keeps its original Gateway-only meaning.
    Surface ``candidate_count`` and ``raw_candidate_count`` cover every raw hit,
    while ``gateway_candidate_count`` covers only hydrated Gateway inputs.
    """
    _require_nonnegative_int(raw_candidate_count, "raw_candidate_count")
    _require_nonnegative_int(hydrate_error_count, "hydrate_error_count")
    metrics = result.metrics()
    gateway_candidate_count = int(metrics["candidate_count"])
    expected_raw_count = gateway_candidate_count + hydrate_error_count
    if raw_candidate_count != expected_raw_count:
        raise ValueError(
            "raw candidates must equal Gateway candidates plus hydrate errors: "
            f"{raw_candidate_count} != {gateway_candidate_count} + "
            f"{hydrate_error_count}"
        )
    metrics.update({
        "candidate_count": raw_candidate_count,
        "raw_candidate_count": raw_candidate_count,
        "gateway_candidate_count": gateway_candidate_count,
        "hydrate_error_count": hydrate_error_count,
        "excluded_count": int(metrics["excluded_count"]) + hydrate_error_count,
        "excluded_reasons": injection_exclusion_reason_counts(
            result.excluded,
            hydrate_error_count=hydrate_error_count,
        ),
    })
    return metrics


def _require_nonnegative_int(value: object, field: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")


def injection_retrieval_top_k(top_k: int) -> int:
    """Return the shared pre-Gateway candidate limit for prompt surfaces."""
    if top_k <= 0:
        return top_k
    overfetch = max(top_k * 4, top_k + 8)
    return max(top_k, min(overfetch, _INJECTION_OVERFETCH_CAP))


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
    brain_dir: Path | None = None,
    max_items: int | None = None,
    current_scope: Mapping[str, str] | None = None,
    query_context: InjectionQueryContext | None = None,
) -> FirewallResult:
    signal = query_signal
    if query_context is None and signal is None and query is not None:
        signal = _analyze_query(query, brain_dir=brain_dir)
    return ContextFirewall().filter(
        candidates,
        query=query,
        query_signal=signal,
        max_items=max_items,
        current_scope=current_scope,
        query_context=query_context,
    )


def build_injection_context(
    candidates: list[ContextCandidate],
    *,
    query: str | None = None,
    query_signal: QuerySignal | None = None,
    brain_dir: Path | None = None,
    requested: ContextVerbosity = "auto",
    max_items: int | None = None,
    budget_tokens: int | None = None,
    current_scope: Mapping[str, str] | None = None,
    query_context: InjectionQueryContext | None = None,
) -> InjectionResult:
    if requested not in _CONTEXT_VERBOSITIES:
        raise ValueError(f"unsupported context verbosity: {requested!r}")
    signal = query_signal
    if query_context is None and signal is None and query is not None:
        signal = _analyze_query(query, brain_dir=brain_dir)
    firewall_engine = ContextFirewall()
    firewall = firewall_engine.filter(
        candidates,
        query=query,
        query_signal=signal,
        max_items=None,
        current_scope=current_scope,
        query_context=query_context,
    )
    included: list[PackedDecision] = []
    packing_excluded: list[FirewallDecision] = []
    max_excluded: list[FirewallDecision] = []
    slot_limit = None if max_items is None else max(0, max_items)
    used_tokens = 0

    for decision in firewall.included:
        if slot_limit is not None and len(included) >= slot_limit:
            max_excluded.append(exclude_with(decision, "max_items_exceeded"))
            continue
        remaining = (
            None if budget_tokens is None else max(0, budget_tokens - used_tokens)
        )
        try:
            packed = pack_decisions(
                [decision],
                requested=requested,
                budget_tokens=remaining,
            )
        except Exception:
            packing_excluded.append(exclude_with(decision, PACK_ERROR_REASON))
            continue
        if not packed.included:
            packing_excluded.extend(
                packed.excluded or [exclude_with(decision, PACK_ERROR_REASON)]
            )
            continue
        included.extend(packed.included)
        used_tokens += packed.used_tokens

    final_cohort = firewall_engine.validate_cohort(
        [entry.decision for entry in included],
        query_signal=signal,
        query_context=query_context,
    )
    final_ids = {decision.candidate.item.id for decision in final_cohort.included}
    final_included = [
        entry
        for entry in included
        if entry.decision.candidate.item.id in final_ids
    ]
    cohort_reasons = tuple(dict.fromkeys([
        *firewall.cohort_reasons,
        *final_cohort.reasons,
    ]))
    return InjectionResult(
        included=final_included,
        excluded=[
            *firewall.excluded,
            *packing_excluded,
            *max_excluded,
            *final_cohort.excluded,
        ],
        cohort_reasons=cohort_reasons,
        used_tokens=sum(entry.pack.packed_tokens for entry in final_included),
        full_tokens=sum(entry.pack.full_tokens for entry in final_included),
    )


def _analyze_query(query: str, *, brain_dir: Path | None) -> QuerySignal:
    normalized = query.replace("|", " ")
    if brain_dir is None:
        return analyze_injection_query(normalized)
    return analyze_injection_query(normalized, brain_dir=brain_dir)


__all__ = [
    "GATEWAY_SYNTHETIC_EXCLUSION_REASONS",
    "HYDRATE_ERROR_REASON",
    "INJECTION_EXCLUSION_REASONS",
    "InjectionResult",
    "PACK_ERROR_REASON",
    "build_injection_context",
    "evaluate_injection_candidates",
    "injection_exclusion_reason_counts",
    "injection_retrieval_top_k",
    "surface_injection_metrics",
]
