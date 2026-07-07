"""Higher-order self-evolution control read model.

This module deliberately produces recommendations, not mutations. It connects
the three-day data-flow ledger to evolve reports so Web/CLI surfaces can explain
why self-evolution should stay in shadow/review mode.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agent_brain.observability.data_flow import DataFlowEvent, DataFlowLedger


@dataclass(frozen=True)
class EvolutionGate:
    name: str
    status: str
    description: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class HigherOrderRecommendation:
    action: str
    risk: str
    reason: str
    source_count: int
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence"] = list(self.evidence)
        return data


@dataclass(frozen=True)
class EvolutionControlReport:
    mode: str
    mutation_boundary: str
    data_flow: dict[str, Any]
    gates: tuple[EvolutionGate, ...]
    recommendations: tuple[HigherOrderRecommendation, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "mutation_boundary": self.mutation_boundary,
            "data_flow": self.data_flow,
            "gates": [gate.to_dict() for gate in self.gates],
            "recommendations": [rec.to_dict() for rec in self.recommendations],
        }


def build_evolution_control_report(
    brain_dir: Path,
    *,
    apply_requested: bool,
    evolve_report: Any | None = None,
) -> EvolutionControlReport:
    """Build a Web-safe control-plane report for self-evolution."""

    ledger = DataFlowLedger(Path(brain_dir))
    events = ledger.list_events(since_hours=72, limit=500)
    summary = ledger.summary(events, since_hours=72)
    recommendations = _recommendations(events, summary.failures)
    audit_blocked = int(getattr(evolve_report, "audit_blocked", 0) or 0)
    approved = len(getattr(evolve_report, "approved_proposals", []) or [])
    executed = int(getattr(evolve_report, "executed", 0) or 0)
    gates = (
        EvolutionGate(
            name="audit_gate",
            status="blocked" if audit_blocked else "enforced",
            description=f"{audit_blocked} evolve proposal(s) blocked by audit scanner.",
        ),
        EvolutionGate(
            name="write_funnel",
            status="review_required" if apply_requested else "shadow_mode",
            description="High-risk evolve/governance writes must pass review or WriteService; control report never writes items.",
        ),
        EvolutionGate(
            name="release_gate",
            status="required_for_default_change",
            description="Retrieval, compression, ML/DL, adapter, and Loop changes need benchmark/runtime evidence before becoming defaults.",
        ),
        EvolutionGate(
            name="data_flow_observability",
            status="has_gaps" if summary.failures else "observed",
            description=f"{summary.total} event(s), {summary.failures} gap/failure event(s) in the last 72 hours.",
        ),
    )
    return EvolutionControlReport(
        mode="apply_requested" if apply_requested else "shadow_mode",
        mutation_boundary="executed_by_evolve_engine" if executed else "proposal_only",
        data_flow={
            "window_hours": summary.window_hours,
            "total": summary.total,
            "failures": summary.failures,
            "by_source": summary.by_source,
            "by_stage": summary.by_stage,
            "approved_proposals": approved,
            "executed": executed,
        },
        gates=gates,
        recommendations=tuple(recommendations),
    )


def _recommendations(
    events: list[DataFlowEvent],
    failure_count: int,
) -> list[HigherOrderRecommendation]:
    recs: list[HigherOrderRecommendation] = []
    recall_gaps = [event for event in events if event.source == "recall_gap"]
    failed_adapters = [
        event
        for event in events
        if event.source == "adapter_verification" and event.status == "failed"
    ]
    failed_loops = [
        event
        for event in events
        if event.source == "loop" and event.status == "failed"
    ]
    if recall_gaps:
        recs.append(
            HigherOrderRecommendation(
                action="review_recall_gaps",
                risk="review_required",
                reason="Recent recall gaps should become search cases, candidate memories, or firewall tuning proposals before default behavior changes.",
                source_count=len(recall_gaps),
                evidence=tuple(event.event_id for event in recall_gaps[:5]),
            )
        )
    if failed_adapters:
        recs.append(
            HigherOrderRecommendation(
                action="collect_adapter_verification",
                risk="review_required",
                reason="Adapter verification failures block promotion from install-ready to verified.",
                source_count=len(failed_adapters),
                evidence=tuple(event.event_id for event in failed_adapters[:5]),
            )
        )
    if failed_loops:
        recs.append(
            HigherOrderRecommendation(
                action="inspect_failed_loops",
                risk="review_required",
                reason="Failed Loop events should be inspected before self-evolution turns them into durable process changes.",
                source_count=len(failed_loops),
                evidence=tuple(event.event_id for event in failed_loops[:5]),
            )
        )
    if failure_count and not recs:
        recs.append(
            HigherOrderRecommendation(
                action="inspect_runtime_failures",
                risk="review_required",
                reason="The data-flow ledger contains gap/failure events that should be reviewed before applying evolve proposals.",
                source_count=failure_count,
            )
        )
    if not recs:
        recs.append(
            HigherOrderRecommendation(
                action="continue_shadow_mode",
                risk="safe_observation",
                reason="No recent data-flow gap requires automatic mutation; keep evolve suggestions in preview/review mode.",
                source_count=0,
            )
        )
    return recs


__all__ = [
    "EvolutionControlReport",
    "EvolutionGate",
    "HigherOrderRecommendation",
    "build_evolution_control_report",
]
