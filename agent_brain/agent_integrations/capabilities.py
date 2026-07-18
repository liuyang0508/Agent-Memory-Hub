"""Adapter capability records for truth-contract reporting.

This module is read-only: it inspects adapter objects and never installs or
modifies user config. Public claims should use these records instead of
free-form wording.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from . import AdapterBase, WIPAdapter
from .evidence import SupportLevel, evidence_for_adapter
from .memory_boundary import memory_boundary_for_adapter
from .manifests import manifest_for_adapter
from .runtime_events import AdapterRuntimeSummary, runtime_event_summary
from .verifications import AdapterVerificationSummary, adapter_verification_summary

CONTEXT_EFFECTIVE_ADAPTERS = {"qoder", "qoder_work"}
CONTEXT_EFFECTIVE_EVIDENCE_PREFIXES_BY_ADAPTER = {
    # QoderWork GUI sessions run under ~/.qoderwork/workspace/* and can launch
    # qodercli with --skip-plugin-mcp.  A standalone qodercli smoke can prove
    # hook output mechanics, but it must not promote the GUI adapter.
    "qoder_work": (
        "context_effective=qoderwork_gui_agent_brain",
        "context_effective=qoderwork_gui_memory_candidates",
    ),
}
DEFAULT_CONTEXT_EFFECTIVE_EVIDENCE_PREFIXES = (
    "context_effective=transcript_agent_brain",
    "context_effective=model_observed_agent_brain",
    "context_effective=amh_mcp_tool_use",
    "transcript_agent_brain=",
    "amh_context_effective=",
)


@dataclass(frozen=True)
class AdapterCapability:
    name: str
    display_names: list[str]
    aliases: list[str]
    status: Literal["ready", "wip"]
    support_level: SupportLevel
    hook_type: str
    inject_method: str
    supports_hooks: bool
    supports_mcp: bool
    integration_modes: list[str]
    limitations: list[str]
    evidence_paths: list[str]
    evidence_level: SupportLevel | None
    runtime_observed: bool
    runtime_event_count: int
    last_runtime_event: dict[str, str | None] | None
    last_verification: dict[str, object] | None
    verified: bool
    verification_status: Literal["verified", "not_verified"]
    verification_blockers: list[str]
    memory_boundary: dict[str, object]
    manifest: dict[str, object]
    states: dict[str, bool]
    evidence_freshness: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def capability_for_adapter(
    name: str,
    adapter: AdapterBase,
    *,
    now: datetime | None = None,
) -> AdapterCapability:
    from .registry import metadata_for_adapter

    cfg = adapter.get_config()
    is_wip = isinstance(adapter, WIPAdapter)
    metadata = metadata_for_adapter(name)
    integration_modes = _integration_modes(cfg.hook_type, cfg.supports_hooks, cfg.supports_mcp)
    evidence = evidence_for_adapter(name, is_wip=is_wip)
    manifest = manifest_for_adapter(name, adapter)
    runtime_summary = runtime_event_summary(adapter.brain_dir, name)
    verification_summary = adapter_verification_summary(adapter.brain_dir, name)
    from agent_brain.memory.context.injection_cohorts import latest_injection_cohort

    injection_cohort = latest_injection_cohort(adapter.brain_dir, adapter=name)
    verification_effective = _verification_is_effective(
        name,
        verification_summary,
    )
    effective_evidence_level = "verified" if verification_effective else (
        evidence.evidence_level or evidence.support_level
    )
    verification_blockers = _verification_blockers(
        adapter_name=name,
        evidence_level=effective_evidence_level,
        runtime_summary=runtime_summary,
        verification_summary=verification_summary,
        verification_effective=verification_effective,
        runtime_required=(cfg.supports_hooks or cfg.supports_mcp) and not is_wip,
        context_injected=injection_cohort is not None,
        is_wip=is_wip,
        limitations=evidence.limitations,
    )
    verified = not verification_blockers
    support_level: SupportLevel = "verified" if verified else evidence.support_level
    evidence_level: SupportLevel | None = "verified" if verified else evidence.evidence_level
    evidence_paths = [*evidence.evidence_paths, *verification_summary.evidence]
    doctor_passed = bool(verification_summary.verified)
    states = {
        "implemented": not is_wip,
        # Until the lifecycle ledger lands, a passed verification record is
        # the conservative durable proof that install/config/doctor completed.
        "installed": doctor_passed,
        "configured": doctor_passed,
        "doctor_passed": doctor_passed,
        "runtime_observed": runtime_summary.observed,
        "context_injected": injection_cohort is not None,
    }

    return AdapterCapability(
        name=name,
        display_names=list(metadata.display_names),
        aliases=list(metadata.aliases),
        status="wip" if is_wip else "ready",
        support_level=support_level,
        hook_type=cfg.hook_type,
        inject_method=cfg.inject_method,
        supports_hooks=cfg.supports_hooks,
        supports_mcp=cfg.supports_mcp,
        integration_modes=integration_modes,
        limitations=list(evidence.limitations),
        evidence_paths=evidence_paths,
        evidence_level=evidence_level,
        runtime_observed=runtime_summary.observed,
        runtime_event_count=runtime_summary.count,
        last_runtime_event=runtime_summary.last_event,
        last_verification=verification_summary.last_record,
        verified=verified,
        verification_status="verified" if verified else "not_verified",
        verification_blockers=verification_blockers,
        memory_boundary=memory_boundary_for_adapter(name, brain_dir=adapter.brain_dir).to_dict(),
        manifest=manifest.to_dict(),
        states=states,
        evidence_freshness={
            "evaluated_at": now.isoformat() if now is not None else None,
            "runtime": "unbounded",
            "context_injection": "unbounded",
            "verification": "unbounded",
        },
    )


def capabilities_for_all(
    brain_dir: Path,
    *,
    now: datetime | None = None,
) -> list[AdapterCapability]:
    from agent_brain.agent_integrations import discover_adapters
    from agent_brain.agent_integrations.registry import get_adapter, list_adapters

    discover_adapters()
    return [
        capability_for_adapter(name, get_adapter(name, brain_dir), now=now)
        for name in list_adapters()
    ]


def _integration_modes(hook_type: str, supports_hooks: bool, supports_mcp: bool) -> list[str]:
    modes: list[str] = []
    if hook_type:
        for mode in hook_type.split("+"):
            if mode and mode not in modes:
                modes.append(mode)
    if supports_hooks and "hook" not in modes:
        modes.append("hook")
    if supports_mcp and "mcp" not in modes:
        modes.append("mcp")
    return modes


def _verification_blockers(
    *,
    adapter_name: str,
    evidence_level: SupportLevel,
    runtime_summary: AdapterRuntimeSummary,
    verification_summary: AdapterVerificationSummary,
    verification_effective: bool,
    runtime_required: bool,
    context_injected: bool,
    is_wip: bool,
    limitations: tuple[str, ...],
) -> list[str]:
    if is_wip:
        return list(limitations or ("install path not implemented",))

    blockers: list[str] = []
    if not verification_summary.verified:
        blockers.append(f"evidence level is {evidence_level}, not verified")
    elif not verification_effective:
        blockers.append("context effectiveness not observed")
    elif evidence_level != "verified":
        blockers.append(f"evidence level is {evidence_level}, not verified")
    if runtime_required and not runtime_summary.observed:
        blockers.append("runtime event not observed")
    if not context_injected:
        blockers.append("context injection not observed")
    return blockers


def _verification_is_effective(
    adapter_name: str,
    verification_summary: AdapterVerificationSummary,
) -> bool:
    if not verification_summary.verified:
        return False
    if adapter_name not in CONTEXT_EFFECTIVE_ADAPTERS:
        return True
    prefixes = CONTEXT_EFFECTIVE_EVIDENCE_PREFIXES_BY_ADAPTER.get(
        adapter_name,
        DEFAULT_CONTEXT_EFFECTIVE_EVIDENCE_PREFIXES,
    )
    return any(
        str(entry).startswith(prefixes)
        for entry in verification_summary.evidence
    )
