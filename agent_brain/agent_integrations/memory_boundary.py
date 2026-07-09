"""Agent-native memory boundary records.

These records keep adapter capability and doctor output honest: Agent-native
memories are candidate hints, while AMH MemoryItems remain the shared fact
layer after current user instructions and live repository evidence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal


NativeMemoryState = Literal[
    "documented",
    "needs_current_verification",
    "adapter_specific",
    "not_claimed",
]

PRIORITY_ORDER = [
    "current_user_message",
    "live_repository_evidence",
    "current_project_instructions",
    "amh_memory_item",
    "agent_native_memory",
    "explored_trace",
]

EVIDENCE_LAYERS = [
    "awareness",
    "tool",
    "automatic_hook",
    "fallback",
]

NATIVE_MEMORY_STATE_BY_ADAPTER: dict[str, NativeMemoryState] = {
    "codex": "documented",
    "claude_code": "documented",
    "cursor": "needs_current_verification",
    "qoder": "needs_current_verification",
    "qoder_work": "needs_current_verification",
    "wukong": "adapter_specific",
    "hermes_agent": "adapter_specific",
    "github_copilot": "needs_current_verification",
}


@dataclass(frozen=True)
class AdapterMemoryBoundary:
    adapter: str
    amh_role: str
    native_memory_role: str
    native_memory_state: NativeMemoryState
    native_memory_observed: bool
    explored_trace_role: str
    last_injection: dict[str, object]
    priority_order: list[str]
    evidence_layers: list[str]
    conflict_policy: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def memory_boundary_for_adapter(
    adapter: str,
    *,
    brain_dir: Path | None = None,
    native_memory_observed: bool = False,
) -> AdapterMemoryBoundary:
    native_memory_state = NATIVE_MEMORY_STATE_BY_ADAPTER.get(adapter, "not_claimed")
    return AdapterMemoryBoundary(
        adapter=adapter,
        amh_role="shared_truth_source",
        native_memory_role="candidate_hint",
        native_memory_state=native_memory_state,
        native_memory_observed=native_memory_observed,
        explored_trace_role="session_trace_only",
        last_injection=_last_injection_summary(brain_dir, adapter),
        priority_order=list(PRIORITY_ORDER),
        evidence_layers=list(EVIDENCE_LAYERS),
        conflict_policy=(
            "current user instructions and live repository evidence override memory; "
            "AMH MemoryItem outranks agent-native memory; explored traces are not "
            "long-term facts unless promoted through the AMH write funnel"
        ),
    )


def _last_injection_summary(brain_dir: Path | None, adapter: str) -> dict[str, object]:
    if brain_dir is None:
        return {"observed": False}
    from agent_brain.memory.context.injection_cohorts import latest_injection_cohort

    cohort = latest_injection_cohort(brain_dir, adapter=adapter)
    if cohort is None:
        return {"observed": False}
    metrics = cohort.pack_metrics or {}
    summary: dict[str, object] = {
        "observed": True,
        "cohort_id": cohort.cohort_id,
        "timestamp": cohort.timestamp,
        "session_id": cohort.session_id,
        "cwd": cohort.cwd,
        "item_count": len(cohort.item_ids),
    }
    packed_tokens = metrics.get("packed_tokens")
    full_tokens = metrics.get("full_tokens")
    if isinstance(packed_tokens, int):
        summary["packed_tokens"] = packed_tokens
    if isinstance(full_tokens, int):
        summary["full_tokens"] = full_tokens
    return summary
