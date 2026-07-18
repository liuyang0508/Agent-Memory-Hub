"""Read/action model for adapter onboarding."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
import time
from typing import Any, Literal
import uuid

from agent_brain.agent_integrations import AdapterBase, WIPAdapter, discover_adapters
from agent_brain.agent_integrations.capabilities import AdapterCapability, capabilities_for_all
from agent_brain.agent_integrations.diagnostics import AdapterDiagnosticReport
from agent_brain.agent_integrations.lifecycle_records import (
    LifecycleAction,
    LifecycleReasonCode,
    LifecycleStatus,
    lifecycle_evidence_summary,
    record_lifecycle_event,
)
from agent_brain.agent_integrations.manifests import AdapterManifest, manifest_for_adapter
from agent_brain.agent_integrations.registry import get_adapter, resolve_adapter_name
from agent_brain.agent_integrations.runtime_events import (
    record_runtime_event,
    runtime_event_summary,
)
from agent_brain.agent_integrations.verifications import record_adapter_verification


PRIORITY_ADAPTERS = ("codex", "claude_code", "qoder", "wukong", "hermes_agent")
CONTEXT_EFFECTIVE_ADAPTERS = {"qoder", "qoder_work"}
CONTEXT_EFFECTIVE_MARKERS = ("<agent_brain>", "Auto-injected memory candidates")
CONTEXT_TOOL_TRACE_RECENCY_SECONDS = 3 * 24 * 60 * 60
AMH_MCP_TOOL_MARKERS = (
    "mcp_agent-memory-hub_",
    "mcp__agent-memory-hub__",
    "agent-memory-hub.search_memory",
    "agent-memory-hub.brief_memory",
    "agent-memory-hub.read_memory",
    "agent-memory-hub.write_memory",
)
LIFECYCLE_RESULT_SCHEMA_VERSION = "amh-adapter-lifecycle-result/v1"


@dataclass(frozen=True)
class AdapterLifecycleResult:
    schema_version: str
    adapter: str
    requested_adapter: str
    action: LifecycleAction
    status: LifecycleStatus
    reason_code: LifecycleReasonCode
    message: str
    state_before: dict[str, object]
    state_after: dict[str, object]
    evidence: list[str]
    repair_command: str
    provenance: dict[str, object] | None
    backup_id: str | None = None
    rollback_status: Literal["passed", "failed"] | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_onboarding_summary(brain_dir: Path) -> dict[str, Any]:
    """Return the adapter onboarding cockpit read model."""

    caps = capabilities_for_all(brain_dir)
    rows = [_adapter_row(cap) for cap in caps]
    rows.sort(key=lambda row: (row["priority_rank"], row["name"]))
    return {
        "total": len(caps),
        "install_ready": sum(1 for cap in caps if cap.support_level == "install-ready"),
        "wip": sum(1 for cap in caps if cap.status == "wip"),
        "verified": sum(1 for cap in caps if cap.verified),
        "implemented": sum(1 for cap in caps if cap.states["implemented"]),
        "installed": sum(1 for cap in caps if cap.states["installed"]),
        "configured": sum(1 for cap in caps if cap.states["configured"]),
        "doctor_passed": sum(1 for cap in caps if cap.states["doctor_passed"]),
        "runtime_observed": sum(1 for cap in caps if cap.states["runtime_observed"]),
        "context_injected": sum(1 for cap in caps if cap.states["context_injected"]),
        "adapters": rows,
    }


def install_adapter(brain_dir: Path, name: str) -> dict[str, Any]:
    """Run an adapter install and return a Web-safe result."""

    adapter_name, alias_used, adapter = _adapter(brain_dir, name)
    try:
        message = adapter.install()
        return {
            "adapter": adapter_name,
            "requested_adapter": name,
            "alias": alias_used,
            "status": "installed",
            "message": message,
        }
    except NotImplementedError as exc:
        return {
            "adapter": adapter_name,
            "requested_adapter": name,
            "alias": alias_used,
            "status": "unsupported",
            "message": str(exc),
        }
    except (FileNotFoundError, RuntimeError) as exc:
        return {
            "adapter": adapter_name,
            "requested_adapter": name,
            "alias": alias_used,
            "status": "failed",
            "message": str(exc),
        }


def uninstall_adapter(brain_dir: Path, name: str) -> dict[str, Any]:
    """Remove hub-owned adapter config and return a Web-safe result."""

    adapter_name, alias_used, adapter = _adapter(brain_dir, name)
    uninstall = getattr(adapter, "uninstall", None)
    if not callable(uninstall):
        return {
            "adapter": adapter_name,
            "requested_adapter": name,
            "alias": alias_used,
            "status": "unsupported",
            "message": "adapter has no uninstall path",
        }
    try:
        message = uninstall()
        return {
            "adapter": adapter_name,
            "requested_adapter": name,
            "alias": alias_used,
            "status": "uninstalled",
            "message": message,
        }
    except (FileNotFoundError, RuntimeError) as exc:
        return {
            "adapter": adapter_name,
            "requested_adapter": name,
            "alias": alias_used,
            "status": "failed",
            "message": str(exc),
        }


def execute_adapter_action(
    brain_dir: Path,
    name: str,
    action: LifecycleAction,
    *,
    verifier: str = "product",
    context_probe: bool = False,
) -> AdapterLifecycleResult:
    """Execute one adapter lifecycle action with a stable result contract."""

    try:
        adapter_name, _alias_used, adapter = _adapter(brain_dir, name)
    except ValueError as exc:
        return AdapterLifecycleResult(
            schema_version=LIFECYCLE_RESULT_SCHEMA_VERSION,
            adapter=name,
            requested_adapter=name,
            action=action,
            status="blocked",
            reason_code="UNKNOWN_ADAPTER",
            message=str(exc),
            state_before={},
            state_after={},
            evidence=[],
            repair_command="memory adapter list",
            provenance=None,
        )

    adapter_manifest = _manifest_for(adapter_name, adapter)
    state_before = _lifecycle_state(brain_dir, adapter_name)
    backup_id: str | None = None
    rollback_status: Literal["passed", "failed"] | None = None
    evidence: list[str] = []
    status: LifecycleStatus = "passed"
    reason_code: LifecycleReasonCode = "OK"
    message = ""

    from agent_brain.agent_integrations.release_controls import get_adapter_release_control

    release_control = get_adapter_release_control(brain_dir, adapter_name)

    if (
        release_control is not None
        and release_control.stage == "disabled"
        and action not in {"doctor", "uninstall"}
    ):
        status = "blocked"
        reason_code = "ADAPTER_DISABLED"
        message = "adapter is disabled by its release control; recover through shadow"
    elif isinstance(adapter, WIPAdapter) and action not in {"doctor"}:
        status = "blocked"
        reason_code = "ADAPTER_WIP"
        message = "adapter install path is not implemented"
    else:
        try:
            if action == "install":
                message = adapter.install()
            elif action == "doctor":
                report = _diagnose(adapter)
                evidence = [f"doctor:{report.overall_status}"]
                message = f"adapter doctor: {report.overall_status}"
                if report.overall_status == "error":
                    status = "failed"
                    reason_code = "DOCTOR_FAILED"
            elif action == "verify":
                verification = _verify_adapter(
                    brain_dir,
                    requested_name=name,
                    adapter_name=adapter_name,
                    alias_used=None,
                    adapter=adapter,
                    verifier=verifier,
                    context_probe_required=context_probe,
                    record=True,
                )
                evidence = [str(item) for item in verification.get("evidence") or []]
                message = f"adapter verify: {verification.get('status')}"
                if verification.get("status") != "passed":
                    status = "failed"
                    reason_code = _verification_reason(verification)
            elif action == "repair":
                report = _diagnose(adapter)
                if report.overall_status == "error":
                    message = adapter.install()
                    repaired = _diagnose(adapter)
                    evidence = [f"doctor_before:{report.overall_status}", f"doctor_after:{repaired.overall_status}"]
                    if repaired.overall_status == "error":
                        status = "failed"
                        reason_code = "DOCTOR_FAILED"
                        message = f"repair did not clear doctor errors: {message}"
                else:
                    evidence = [f"doctor:{report.overall_status}"]
                    message = "adapter repair: no AMH-owned error drift detected"
            elif action == "upgrade":
                owned_paths = adapter.owned_paths()
                if not owned_paths:
                    status = "blocked"
                    reason_code = "BACKUP_FAILED"
                    message = "adapter does not declare rollback-owned paths"
                else:
                    backup_id = _create_adapter_backup(brain_dir, adapter_name, owned_paths)
                    try:
                        message = adapter.install()
                        upgraded = _diagnose(adapter)
                        evidence = [f"doctor_after:{upgraded.overall_status}"]
                        if upgraded.overall_status == "error":
                            raise RuntimeError("adapter doctor failed after upgrade")
                    except (FileNotFoundError, RuntimeError) as exc:
                        status = "failed"
                        reason_code = _exception_reason(exc)
                        message = str(exc)
                        try:
                            _restore_adapter_backup(
                                brain_dir,
                                adapter_name,
                                backup_id,
                                owned_paths,
                            )
                            rollback_status = "passed"
                        except (OSError, RuntimeError, ValueError):
                            rollback_status = "failed"
                            reason_code = "ROLLBACK_FAILED"
            elif action == "uninstall":
                uninstall = getattr(adapter, "uninstall", None)
                if not callable(uninstall):
                    status = "blocked"
                    reason_code = "ADAPTER_WIP"
                    message = "adapter has no uninstall path"
                else:
                    message = str(uninstall())
            else:  # pragma: no cover - LifecycleAction is closed, defensive at runtime
                status = "blocked"
                reason_code = "INTERNAL_ERROR"
                message = f"unsupported lifecycle action: {action}"
        except (FileNotFoundError, RuntimeError) as exc:
            status = "failed"
            reason_code = _exception_reason(exc)
            message = str(exc)

    artifact_hashes = _artifact_hashes(adapter.owned_paths())
    provenance_record = record_lifecycle_event(
        brain_dir,
        adapter=adapter_name,
        action=action,
        status=status,
        reason_code=reason_code,
        artifact_hashes=artifact_hashes,
        backup_id=backup_id,
    )
    state_after = _lifecycle_state(brain_dir, adapter_name)
    return AdapterLifecycleResult(
        schema_version=LIFECYCLE_RESULT_SCHEMA_VERSION,
        adapter=adapter_name,
        requested_adapter=name,
        action=action,
        status=status,
        reason_code=reason_code,
        message=message,
        state_before=state_before,
        state_after=state_after,
        evidence=evidence,
        repair_command=adapter_manifest.lifecycle.repair,
        provenance=provenance_record.to_dict(),
        backup_id=backup_id,
        rollback_status=rollback_status,
    )


def _manifest_for(adapter_name: str, adapter: AdapterBase) -> AdapterManifest:
    from agent_brain.agent_integrations.manifests import manifest_for_adapter

    return manifest_for_adapter(adapter_name, adapter)


def _lifecycle_state(brain_dir: Path, adapter_name: str) -> dict[str, object]:
    from agent_brain.agent_integrations.capabilities import capability_for_adapter

    adapter = get_adapter(adapter_name, brain_dir)
    capability = capability_for_adapter(adapter_name, adapter)
    return {
        "states": capability.states,
        "verified": capability.verified,
        "verification_blockers": capability.verification_blockers,
    }


def _diagnose(adapter: AdapterBase) -> AdapterDiagnosticReport:
    diagnose = getattr(adapter, "diagnose", None)
    if not callable(diagnose):
        raise RuntimeError("adapter doctor not implemented")
    report = diagnose()
    if not isinstance(report, AdapterDiagnosticReport):
        raise RuntimeError("adapter doctor returned an invalid report")
    return report


def _verification_reason(payload: dict[str, Any]) -> LifecycleReasonCode:
    blockers = " ".join(str(item).lower() for item in payload.get("blockers") or [])
    if "context" in blockers:
        return "CONTEXT_MISSING"
    if "runtime" in blockers:
        return "RUNTIME_MISSING"
    return "DOCTOR_FAILED"


def _exception_reason(exc: Exception) -> LifecycleReasonCode:
    message = str(exc).lower()
    if "malformed" in message or "must be an object" in message:
        return "CONFIG_MALFORMED"
    if isinstance(exc, FileNotFoundError) or "not found" in message or "does not exist" in message:
        return "CLIENT_MISSING"
    return "INTERNAL_ERROR"


def _create_adapter_backup(
    brain_dir: Path,
    adapter_name: str,
    paths: tuple[Path, ...],
) -> str:
    backup_id = f"backup-{uuid.uuid4().hex[:16]}"
    root = Path(brain_dir) / "backups" / "adapters" / adapter_name / backup_id
    files_dir = root / "files"
    files_dir.mkdir(parents=True, mode=0o700)
    os.chmod(root, 0o700)
    os.chmod(files_dir, 0o700)
    entries: list[dict[str, object]] = []
    for index, path in enumerate(paths):
        if path.exists() and not path.is_file():
            raise RuntimeError(f"owned path is not a regular file: {path.name}")
        exists = path.is_file()
        entry: dict[str, object] = {
            "slot": index,
            "path": str(path),
            "exists": exists,
            "sha256": _sha256(path) if exists else None,
        }
        if exists:
            destination = files_dir / str(index)
            shutil.copyfile(path, destination)
            destination.chmod(0o600)
        entries.append(entry)
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps({"paths": entries}, sort_keys=True), encoding="utf-8")
    manifest_path.chmod(0o600)
    return backup_id


def _restore_adapter_backup(
    brain_dir: Path,
    adapter_name: str,
    backup_id: str,
    owned_paths: tuple[Path, ...],
) -> None:
    root = Path(brain_dir) / "backups" / "adapters" / adapter_name / backup_id
    manifest_path = root / "manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("adapter backup manifest is unavailable") from exc
    allowed = {str(path): path for path in owned_paths}
    entries = payload.get("paths")
    if not isinstance(entries, list):
        raise RuntimeError("adapter backup manifest paths are malformed")
    for entry in entries:
        if not isinstance(entry, dict) or str(entry.get("path")) not in allowed:
            raise RuntimeError("adapter backup ownership mismatch")
        path = allowed[str(entry["path"])]
        if bool(entry.get("exists")):
            source = root / "files" / str(entry.get("slot"))
            if not source.is_file() or _sha256(source) != entry.get("sha256"):
                raise RuntimeError("adapter backup checksum mismatch")
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, path)
        elif path.exists():
            if not path.is_file():
                raise RuntimeError("refuse to remove non-file rollback target")
            path.unlink()


def _artifact_hashes(paths: tuple[Path, ...]) -> dict[str, str]:
    return {
        f"{index}-{path.name}": f"sha256:{_sha256(path)}"
        for index, path in enumerate(paths)
        if path.is_file()
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def doctor_adapter(brain_dir: Path, name: str) -> dict[str, Any]:
    """Run adapter doctor without modifying user config."""

    adapter_name, alias_used, adapter = _adapter(brain_dir, name)
    diagnose = getattr(adapter, "diagnose", None)
    if not callable(diagnose):
        return {
            "adapter": adapter_name,
            "requested_adapter": name,
            "alias": alias_used,
            "overall_status": "error",
            "checks": [],
            "message": "adapter doctor not implemented",
        }
    data = diagnose().to_dict()
    data["requested_adapter"] = name
    data["adapter"] = adapter_name
    data["alias"] = alias_used
    data["schema_version"] = LIFECYCLE_RESULT_SCHEMA_VERSION
    data["action"] = "doctor"
    data["status"] = "failed" if data.get("overall_status") == "error" else "passed"
    data["reason_code"] = "DOCTOR_FAILED" if data.get("overall_status") == "error" else "OK"
    data["repair_command"] = f"memory adapter repair {adapter_name}"
    return data


def verify_adapter(
    brain_dir: Path,
    name: str,
    *,
    verifier: str = "web",
    note: str | None = None,
    context_probe: bool = False,
) -> dict[str, Any]:
    """Record verification evidence only after doctor/runtime gates pass."""

    adapter_name, alias_used, adapter = _adapter(brain_dir, name)
    return _verify_adapter(
        brain_dir,
        requested_name=name,
        adapter_name=adapter_name,
        alias_used=alias_used,
        adapter=adapter,
        verifier=verifier,
        note=note,
        context_probe_required=context_probe,
        record=True,
    )


def install_verify_adapter(
    brain_dir: Path,
    name: str,
    *,
    verifier: str = "web",
    uninstall_check: bool = False,
    context_probe: bool = False,
) -> dict[str, Any]:
    """Install, run doctor/runtime verification gates, and optionally verify uninstall.

    ``uninstall_check`` is a transaction smoke test. It intentionally does not
    persist a passed adapter-verification record, because the final state is
    uninstalled and should not be reported as verified.
    """

    adapter_name, alias_used, adapter = _adapter(brain_dir, name)
    result: dict[str, Any] = {
        "schema_version": LIFECYCLE_RESULT_SCHEMA_VERSION,
        "action": "install-verify",
        "adapter": adapter_name,
        "requested_adapter": name,
        "alias": alias_used,
        "status": "failed",
        "install": None,
        "verification": None,
        "uninstall": None,
        "persistent_verification_recorded": False,
        "reason_code": "INTERNAL_ERROR",
        "repair_command": f"memory adapter repair {adapter_name}",
    }
    try:
        result["install"] = {
            "status": "installed",
            "message": adapter.install(),
        }
    except NotImplementedError as exc:
        result["install"] = {"status": "unsupported", "message": str(exc)}
        result["blockers"] = ["adapter install not implemented"]
        result["reason_code"] = "ADAPTER_WIP"
        return result
    except (FileNotFoundError, RuntimeError) as exc:
        result["install"] = {"status": "failed", "message": str(exc)}
        result["blockers"] = ["adapter install failed"]
        result["reason_code"] = _exception_reason(exc)
        return result

    verification = _verify_adapter(
        brain_dir,
        requested_name=name,
        adapter_name=adapter_name,
        alias_used=alias_used,
        adapter=adapter,
        verifier=verifier,
        note="install-verify transaction",
        context_probe_required=context_probe,
        record=not uninstall_check,
    )
    result["verification"] = verification
    result["persistent_verification_recorded"] = bool(verification.get("record"))
    blockers = list(verification.get("blockers") or [])

    if uninstall_check:
        uninstall = getattr(adapter, "uninstall", None)
        if not callable(uninstall):
            blockers.append("adapter has no uninstall path")
            result["uninstall"] = {"status": "unsupported", "message": "adapter has no uninstall path"}
        else:
            try:
                result["uninstall"] = {"status": "uninstalled", "message": uninstall()}
                doctor_after = doctor_adapter(brain_dir, adapter_name)
                result["uninstall"]["doctor_after_status"] = doctor_after.get("overall_status")
            except (FileNotFoundError, RuntimeError) as exc:
                blockers.append("adapter uninstall failed")
                result["uninstall"] = {"status": "failed", "message": str(exc)}

    result["blockers"] = blockers
    result["status"] = "failed" if blockers else "passed"
    result["reason_code"] = _verification_reason(verification) if blockers else "OK"
    return result


def _verify_adapter(
    brain_dir: Path,
    *,
    requested_name: str,
    adapter_name: str,
    alias_used: str | None,
    adapter,
    verifier: str,
    note: str | None = None,
    context_probe_required: bool = False,
    record: bool = True,
) -> dict[str, Any]:
    """Run adapter verification gates, optionally persisting evidence."""

    diagnose = getattr(adapter, "diagnose", None)
    if not callable(diagnose):
        return {
            "adapter": adapter_name,
            "requested_adapter": requested_name,
            "alias": alias_used,
            "status": "failed",
            "blockers": ["adapter doctor not implemented"],
            "record": None,
        }
    report = diagnose()
    cfg = adapter.get_config()
    runtime_required = (cfg.supports_hooks or cfg.supports_mcp) and not isinstance(
        adapter, WIPAdapter
    )
    runtime_summary = runtime_event_summary(brain_dir, adapter_name)
    runtime_observed_for_gate = runtime_summary.observed
    mcp_probe = None
    if cfg.supports_mcp and not runtime_summary.observed and report.overall_status != "error":
        mcp_probe = _probe_mcp_tools()
        if mcp_probe["status"] == "passed":
            runtime_observed_for_gate = True
            if record:
                record_runtime_event(
                    brain_dir,
                    adapter=adapter_name,
                    event_name="MCPProbe",
                    session_id=f"{adapter_name}-adapter-verify-mcp-probe",
                    cwd=str(Path.cwd()),
                    source="adapter-verify",
                )
                runtime_summary = runtime_event_summary(brain_dir, adapter_name)
    blockers: list[str] = []
    if report.overall_status == "error":
        blockers.append("adapter doctor has error checks")
    if mcp_probe and mcp_probe["status"] != "passed":
        blockers.append(f"MCP probe failed: {mcp_probe['detail']}")
    if runtime_required and not runtime_observed_for_gate:
        blockers.append("runtime event not observed")
    context_probe = None
    if (
        (context_probe_required or adapter_name in CONTEXT_EFFECTIVE_ADAPTERS)
        and runtime_observed_for_gate
        and report.overall_status != "error"
    ):
        context_probe = _probe_context_effectiveness(brain_dir, adapter_name)
        if context_probe["status"] != "passed":
            blockers.append("context effectiveness not observed")
    if record and not (context_probe and context_probe["status"] == "passed"):
        manifest = manifest_for_adapter(adapter_name, adapter)
        freshness = lifecycle_evidence_summary(
            brain_dir,
            adapter_name,
            now=datetime.now(timezone.utc),
            runtime_ttl_seconds=manifest.evidence.runtime_ttl_seconds,
            context_ttl_seconds=manifest.evidence.context_ttl_seconds,
            verification_ttl_seconds=manifest.evidence.verification_ttl_seconds,
        )
        if not freshness.context_injection.observed:
            blockers.append("context injection not observed")
        elif not freshness.context_injection.fresh:
            blockers.append("context injection evidence stale")
    status = "failed" if blockers else "passed"
    evidence = [
        f"memory adapter doctor {adapter_name} --format json",
        f"runtime_events={runtime_summary.count}",
    ]
    if mcp_probe and mcp_probe["status"] == "passed":
        evidence.append(f"mcp_tools={mcp_probe['tool_count']}")
    if context_probe and context_probe["status"] == "passed":
        evidence.append(str(context_probe["evidence"]))
    verification_record = None
    if record:
        verification_record = record_adapter_verification(
            brain_dir,
            adapter=adapter_name,
            status=status,
            verifier=verifier,
            evidence=evidence,
            note=note or ("; ".join(blockers) if blockers else "doctor and runtime checks passed"),
        )
    return {
        "schema_version": LIFECYCLE_RESULT_SCHEMA_VERSION,
        "action": "verify",
        "adapter": adapter_name,
        "requested_adapter": requested_name,
        "alias": alias_used,
        "status": status,
        "reason_code": _verification_reason({"blockers": blockers}) if blockers else "OK",
        "repair_command": f"memory adapter repair {adapter_name}",
        "blockers": blockers,
        "evidence": evidence,
        "runtime_events": runtime_summary.count,
        "doctor_status": report.overall_status,
        "mcp_probe": mcp_probe,
        "context_probe": context_probe,
        "record": verification_record.to_dict() if verification_record else None,
    }


def _adapter(brain_dir: Path, name: str):
    discover_adapters()
    canonical, alias_used = resolve_adapter_name(name)
    return canonical, alias_used, get_adapter(canonical, brain_dir)


def _adapter_row(cap: AdapterCapability) -> dict[str, Any]:
    data = cap.to_dict()
    data["priority_rank"] = (
        PRIORITY_ADAPTERS.index(cap.name)
        if cap.name in PRIORITY_ADAPTERS
        else len(PRIORITY_ADAPTERS)
    )
    data["next_action"] = _next_action(cap)
    return data


def _next_action(cap: AdapterCapability) -> str:
    if cap.release_control and cap.release_control.get("stage") == "disabled":
        return "enable-shadow"
    if cap.verified:
        return "verified"
    if not cap.states["implemented"]:
        return "unsupported"
    if not cap.states["installed"]:
        return "install"
    if not cap.states["configured"] or not cap.states["doctor_passed"]:
        return "repair"
    stale_reasons = cap.evidence_freshness.get("stale_reasons")
    if isinstance(stale_reasons, list) and stale_reasons:
        return "verify"
    if not cap.states["runtime_observed"]:
        return "wait-runtime"
    if not cap.states["context_injected"]:
        return "trigger-recall"
    return "verify"


def _probe_mcp_tools() -> dict[str, Any]:
    """Actively prove the AMH MCP tool surface can load.

    Adapter doctors prove the client config points at AMH. This probe proves the
    AMH MCP server surface itself can enumerate tools in the current Python
    environment, which is the strongest local verification we can run without
    driving each proprietary UI.
    """

    try:
        from agent_brain.interfaces.mcp.server import mcp

        tools = _run_list_tools(mcp)
        names = {str(getattr(tool, "name", "")) for tool in tools}
    except Exception as exc:  # pragma: no cover - defensive diagnostics path
        return {
            "status": "failed",
            "detail": f"{type(exc).__name__}: {exc}",
            "tool_count": 0,
        }

    required = {"search_memory", "write_memory", "read_memory", "get_usage_guide"}
    missing = sorted(required - names)
    if missing:
        return {
            "status": "failed",
            "detail": f"missing MCP tool(s): {', '.join(missing)}",
            "tool_count": len(names),
        }
    return {
        "status": "passed",
        "detail": "MCP tool surface loaded",
        "tool_count": len(names),
    }


def _run_list_tools(mcp) -> list[Any]:
    """Run FastMCP.list_tools from sync product code.

    Web routes may call adapter verification while an event loop is already
    active. Creating the coroutine and then failing `asyncio.run` leaks an
    un-awaited coroutine warning, so detect that case before constructing it.
    """

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(mcp.list_tools())

    result: dict[str, Any] = {}

    def _runner() -> None:
        try:
            result["tools"] = asyncio.run(mcp.list_tools())
        except BaseException as exc:  # pragma: no cover - re-raised in caller
            result["error"] = exc

    thread = threading.Thread(target=_runner, name="amh-mcp-tool-probe", daemon=True)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return list(result.get("tools") or [])


def _probe_context_effectiveness(brain_dir: Path, adapter_name: str) -> dict[str, str]:
    """Check whether AMH context actually reached the proprietary client.

    Runtime events only prove the hook command ran.  Qoder/QoderWork also need
    transcript-level evidence that the injected AMH context was visible to the
    model session, otherwise native client memory can be mistaken for AMH.
    """

    injected_sessions = _sessions_with_injection_cohort(brain_dir, adapter_name)
    for path in _iter_candidate_transcripts(adapter_name):
        if not _is_recent_context_transcript(path):
            continue
        qoderwork_gui = adapter_name == "qoder_work" and _is_qoderwork_gui_transcript(path)
        if adapter_name == "qoder_work" and not qoderwork_gui:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if all(marker in text for marker in CONTEXT_EFFECTIVE_MARKERS):
            evidence = "context_effective=transcript_agent_brain"
            if qoderwork_gui:
                evidence = f"context_effective=qoderwork_gui_agent_brain:{_session_hint_from_path(path)}"
            return {
                "status": "passed",
                "detail": f"AMH context marker found in {path}",
                "evidence": evidence,
            }
        if adapter_name == "qoder" and _is_recent_context_transcript(path):
            for session_id, tool_name in _iter_assistant_tool_names(path):
                if _is_amh_mcp_tool_name(tool_name):
                    return {
                        "status": "passed",
                        "detail": f"AMH MCP tool trace found in {path}",
                        "evidence": f"context_effective=amh_mcp_tool_use:{session_id}",
                    }
        for session_id, assistant_text in _iter_assistant_transcript_text(path):
            if session_id not in injected_sessions:
                continue
            if _assistant_observed_agent_brain(assistant_text):
                evidence = f"context_effective=model_observed_agent_brain:{session_id}"
                if qoderwork_gui:
                    evidence = f"context_effective=qoderwork_gui_agent_brain:{session_id}"
                return {
                    "status": "passed",
                    "detail": f"model observed AMH context in {path}",
                    "evidence": evidence,
                }
            if qoderwork_gui and _assistant_used_memory_candidates(assistant_text):
                return {
                    "status": "passed",
                    "detail": f"QoderWork GUI used injected memory candidates in {path}",
                    "evidence": f"context_effective=qoderwork_gui_memory_candidates:{session_id}",
                }
    return {
        "status": "failed",
        "detail": f"AMH context marker not found in {adapter_name} transcripts",
        "evidence": "",
    }


def _iter_candidate_transcripts(adapter_name: str) -> list[Path]:
    paths: list[Path] = []
    for root in _context_transcript_roots(adapter_name):
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in {".jsonl", ".json", ".log", ".md", ".txt"}:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_size > 2_000_000:
                continue
            paths.append(path)
    paths.sort(key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True)
    return paths[:200]


def _context_transcript_roots(adapter_name: str) -> list[Path]:
    return {
        "codex": [Path.home() / ".codex" / "sessions"],
        "claude_code": [Path.home() / ".claude" / "projects"],
        "qoder": [Path.home() / ".qoder" / "projects"],
        "qoder_work": [
            Path.home() / ".qoderwork" / "projects",
            Path.home() / ".qoderwork" / "workspace",
        ],
    }.get(adapter_name, [])


def _is_qoderwork_gui_transcript(path: Path) -> bool:
    """Return whether a QoderWork transcript belongs to a GUI workspace task."""
    text = str(path)
    if ".qoderwork/workspace/" in text:
        return True
    return "--qoderwork-workspace-" in text


def _is_recent_context_transcript(path: Path) -> bool:
    try:
        return path.stat().st_mtime >= time.time() - CONTEXT_TOOL_TRACE_RECENCY_SECONDS
    except OSError:
        return False


def _is_amh_mcp_tool_name(name: str) -> bool:
    for marker in AMH_MCP_TOOL_MARKERS:
        if marker in name:
            return True
    return False


def _session_hint_from_path(path: Path) -> str:
    name = path.name
    for suffix in ("-session.json", ".jsonl", ".json", ".log", ".md", ".txt"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return path.stem


def _sessions_with_injection_cohort(brain_dir: Path, adapter_name: str) -> set[str]:
    path = brain_dir / "runtime" / "injection-cohorts.jsonl"
    sessions: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return sessions
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(record.get("adapter") or "") != adapter_name:
            continue
        if not record.get("item_ids"):
            continue
        timestamp = record.get("timestamp")
        if timestamp and not _is_recent_context_timestamp(timestamp):
            continue
        session_id = str(record.get("session_id") or "").strip()
        if session_id:
            sessions.add(session_id)
    return sessions


def _is_recent_context_timestamp(value: object) -> bool:
    try:
        observed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        age_seconds = time.time() - observed.timestamp()
    except (TypeError, ValueError, OverflowError, OSError):
        return False
    return -5 <= age_seconds <= CONTEXT_TOOL_TRACE_RECENCY_SECONDS


def _iter_assistant_transcript_text(path: Path) -> list[tuple[str, str]]:
    observations: list[tuple[str, str]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return observations
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        if str(record.get("type") or "") != "assistant":
            message = record.get("message") if isinstance(record.get("message"), dict) else {}
            if str(message.get("role") or "") != "assistant":
                continue
        session_id = str(
            record.get("sessionId")
            or record.get("session_id")
            or (record.get("message") or {}).get("session_id")
            or ""
        ).strip()
        if not session_id:
            continue
        text = " ".join(_collect_text_fragments(record))
        if text:
            observations.append((session_id, text))
    return observations


def _iter_assistant_tool_names(path: Path) -> list[tuple[str, str]]:
    observations: list[tuple[str, str]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return observations
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict) or not _is_assistant_transcript_record(record):
            continue
        session_id = str(
            record.get("sessionId")
            or record.get("session_id")
            or (record.get("message") or {}).get("session_id")
            or _session_hint_from_path(path)
        ).strip()
        for tool_name in _collect_tool_names(record):
            observations.append((session_id, tool_name))
    return observations


def _is_assistant_transcript_record(record: dict[str, Any]) -> bool:
    if str(record.get("type") or "") == "assistant":
        return True
    message = record.get("message") if isinstance(record.get("message"), dict) else {}
    return str(message.get("role") or "") == "assistant"


def _collect_tool_names(value: Any) -> list[str]:
    names: list[str] = []
    if isinstance(value, list):
        for item in value:
            names.extend(_collect_tool_names(item))
        return names
    if isinstance(value, dict):
        if str(value.get("type") or "") == "tool_use" and value.get("name"):
            names.append(str(value["name"]))
        for child in value.values():
            names.extend(_collect_tool_names(child))
    return names


def _collect_text_fragments(value: Any) -> list[str]:
    fragments: list[str] = []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        for item in value:
            fragments.extend(_collect_text_fragments(item))
        return fragments
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"text", "thinking"}:
                fragments.extend(_collect_text_fragments(child))
            elif key in {"message", "content"}:
                fragments.extend(_collect_text_fragments(child))
        return fragments
    return fragments


def _assistant_observed_agent_brain(text: str) -> bool:
    lowered = text.lower()
    if "<agent_brain>" not in lowered:
        return False
    has_context_phrase = any(
        phrase in lowered
        for phrase in ("system context", "context", "上下文")
    )
    has_observed_phrase = any(
        phrase in lowered
        for phrase in ("can see", "i see", "saw", "visible", "看到", "看见")
    )
    return has_context_phrase and has_observed_phrase


def _assistant_used_memory_candidates(text: str) -> bool:
    lowered = text.lower()
    has_memory_candidate = any(
        phrase in lowered
        for phrase in (
            "memory candidates",
            "memory candidate",
            "retrieved memory",
            "memory 候选",
            "记忆候选",
            "召回的 memory",
            "召回的记忆",
            "共享记忆",
        )
    )
    has_usage_signal = any(
        phrase in lowered
        for phrase in (
            "based on",
            "according to",
            "retrieved",
            "根据",
            "按照",
            "召回",
        )
    )
    return has_memory_candidate and has_usage_signal


__all__ = [
    "AdapterLifecycleResult",
    "build_onboarding_summary",
    "doctor_adapter",
    "execute_adapter_action",
    "install_verify_adapter",
    "install_adapter",
    "uninstall_adapter",
    "verify_adapter",
]
