"""Shared adapter diagnostic records."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_brain.diagnostic_types import AdapterDiagnosticCheck, CheckStatus

from .runtime_events import runtime_event_summary


_CONTEXT_PACK_VIEWS = frozenset({"locator", "overview", "detail"})
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

    packed_tokens = metrics.get("packed_tokens")
    full_tokens = metrics.get("full_tokens")
    if "selected_views" in metrics:
        included_count = metrics.get("included_count")
        selected_view_counts = metrics.get("selected_views")
        compressed_count = metrics.get("compressed_count")
        valid_view_counts = (
            isinstance(selected_view_counts, dict)
            and bool(selected_view_counts)
            and all(
                view in _CONTEXT_PACK_VIEWS
                and _is_nonnegative_int(count)
                and count > 0
                for view, count in selected_view_counts.items()
            )
        )
        aggregate_valid = (
            _is_nonnegative_int(included_count)
            and included_count > 0
            and valid_view_counts
            and sum(selected_view_counts.values()) == included_count
            and _is_nonnegative_int(compressed_count)
            and 0 <= compressed_count <= included_count
            and _is_nonnegative_int(packed_tokens)
            and _is_nonnegative_int(full_tokens)
        )
        if not aggregate_valid:
            return AdapterDiagnosticCheck(
                name=check_name,
                status="warn",
                detail=f"latest injection cohort {cohort.cohort_id} has malformed pack metrics",
                fix="reinstall the adapter, trigger UserPromptSubmit, then re-run adapter doctor",
            )
        selected_views = sorted(selected_view_counts)
        return AdapterDiagnosticCheck(
            name=check_name,
            status="ok",
            detail=(
                f"observed context_pack cohort {cohort.cohort_id}; "
                f"items={included_count} selected_view={','.join(selected_views)} "
                f"packed={packed_tokens}/{full_tokens}t compressed={compressed_count}"
            ),
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
    legacy_valid = (
        len(item_metrics) == len(items)
        and _is_nonnegative_int(packed_tokens)
        and _is_nonnegative_int(full_tokens)
        and all(
            item.get("selected_view") in _CONTEXT_PACK_VIEWS
            and _is_nonnegative_int(item.get("packed_tokens"))
            and _is_nonnegative_int(item.get("full_tokens"))
            for item in item_metrics
        )
    )
    if not legacy_valid:
        return AdapterDiagnosticCheck(
            name=check_name,
            status="warn",
            detail=f"latest injection cohort {cohort.cohort_id} has malformed pack metrics",
            fix="reinstall the adapter, trigger UserPromptSubmit, then re-run adapter doctor",
        )
    selected_views = sorted({
        str(item.get("selected_view"))
        for item in item_metrics
        if item.get("selected_view")
    })

    return AdapterDiagnosticCheck(
        name=check_name,
        status="ok",
        detail=(
            f"observed context_pack cohort {cohort.cohort_id}; "
            f"items={len(item_metrics)} selected_view={','.join(selected_views)} "
            f"packed={packed_tokens}/{full_tokens}t"
        ),
    )


def _is_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


from .mcp_config_diagnostics import (  # noqa: E402
    diagnose_mcp_json_server,
    diagnose_mcp_yaml_server,
)


__all__ = [
    "AdapterDiagnosticCheck",
    "AdapterDiagnosticReport",
    "CheckStatus",
    "diagnose_layered_context_pack_evidence",
    "diagnose_mcp_json_server",
    "diagnose_mcp_yaml_server",
    "diagnose_runtime_evidence",
    "overall_status",
]
