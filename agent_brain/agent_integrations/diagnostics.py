"""Shared adapter diagnostic records."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_brain.diagnostic_types import AdapterDiagnosticCheck, CheckStatus

from .runtime_events import runtime_event_summary


@dataclass(frozen=True)
class AdapterDiagnosticReport:
    adapter: str
    overall_status: CheckStatus
    checks: list[AdapterDiagnosticCheck]
    brain_dir: Path | None = None

    def to_dict(self) -> dict[str, object]:
        from .memory_boundary import memory_boundary_for_adapter

        return {
            "adapter": self.adapter,
            "overall_status": self.overall_status,
            "checks": [check.to_dict() for check in self.checks],
            "memory_boundary": memory_boundary_for_adapter(
                self.adapter,
                brain_dir=self.brain_dir,
                native_memory_observed=_native_memory_observed(self.checks),
            ).to_dict(),
        }


def overall_status(checks: list[AdapterDiagnosticCheck]) -> CheckStatus:
    if any(check.status == "error" for check in checks):
        return "error"
    if any(check.status == "warn" for check in checks):
        return "warn"
    return "ok"


def _native_memory_observed(checks: list[AdapterDiagnosticCheck]) -> bool:
    return any(
        check.status == "ok" and "native memory bridge" in check.name.lower() for check in checks
    )


def diagnose_runtime_evidence(
    *,
    brain_dir: Path,
    adapter: str,
    check_name: str,
) -> AdapterDiagnosticCheck:
    summary = runtime_event_summary(brain_dir, adapter)
    if not summary.observed:
        return AdapterDiagnosticCheck(
            name=check_name,
            status="warn",
            detail=f"runtime hook event not observed for adapter: {adapter}",
            fix="start a new agent session after adapter install, then re-run adapter doctor",
        )
    event = summary.last_event or {}
    return AdapterDiagnosticCheck(
        name=check_name,
        status="ok",
        detail=(
            f"observed {summary.count} runtime event(s); "
            f"last={event.get('event_name')} at {event.get('timestamp')}"
        ),
    )


def diagnose_layered_context_pack_evidence(
    *,
    brain_dir: Path,
    adapter: str,
    check_name: str,
) -> AdapterDiagnosticCheck:
    """Report whether prompt-time injection recorded reversible context packs."""

    from agent_brain.memory.context.injection_cohorts import latest_injection_cohort

    cohort = latest_injection_cohort(brain_dir, adapter=adapter)
    if cohort is None:
        return AdapterDiagnosticCheck(
            name=check_name,
            status="warn",
            detail=f"injection cohort pack metrics not observed for adapter: {adapter}",
            fix=(
                "submit a prompt that should recall memory, then re-run adapter doctor; "
                "expected UserPromptSubmit to record context_pack metrics"
            ),
        )

    metrics = cohort.pack_metrics
    if not isinstance(metrics, dict):
        return AdapterDiagnosticCheck(
            name=check_name,
            status="warn",
            detail=(
                f"latest injection cohort {cohort.cohort_id} has no pack metrics; "
                "hook may be old or search did not use context_pack"
            ),
            fix="reinstall the adapter, trigger UserPromptSubmit, then re-run adapter doctor",
        )

    items = metrics.get("items")
    if not isinstance(items, list) or not items:
        return AdapterDiagnosticCheck(
            name=check_name,
            status="warn",
            detail=f"latest injection cohort {cohort.cohort_id} has incomplete pack metrics",
            fix="trigger a memory recall that injects at least one item, then re-run adapter doctor",
        )

    item_metrics = [item for item in items if isinstance(item, dict)]
    selected_views = sorted(
        {str(item.get("selected_view")) for item in item_metrics if item.get("selected_view")}
    )
    packed_tokens = metrics.get("packed_tokens")
    full_tokens = metrics.get("full_tokens")
    if not selected_views or not isinstance(packed_tokens, int) or not isinstance(full_tokens, int):
        return AdapterDiagnosticCheck(
            name=check_name,
            status="warn",
            detail=f"latest injection cohort {cohort.cohort_id} has malformed pack metrics",
            fix="reinstall the adapter, trigger UserPromptSubmit, then re-run adapter doctor",
        )

    return AdapterDiagnosticCheck(
        name=check_name,
        status="ok",
        detail=(
            f"observed context_pack cohort {cohort.cohort_id}; "
            f"items={len(item_metrics)} selected_view={','.join(selected_views)} "
            f"packed={packed_tokens}/{full_tokens}t"
        ),
    )


from .mcp_config_diagnostics import (  # noqa: E402
    diagnose_mcp_json_server,
    diagnose_mcp_yaml_server,
)
