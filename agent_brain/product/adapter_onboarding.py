"""Read/action model for adapter onboarding."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import threading
import time
from typing import Any

from agent_brain.agent_integrations import WIPAdapter, discover_adapters
from agent_brain.agent_integrations.capabilities import AdapterCapability, capabilities_for_all
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
        "adapter": adapter_name,
        "requested_adapter": name,
        "alias": alias_used,
        "status": "failed",
        "install": None,
        "verification": None,
        "uninstall": None,
        "persistent_verification_recorded": False,
    }
    try:
        result["install"] = {
            "status": "installed",
            "message": adapter.install(),
        }
    except NotImplementedError as exc:
        result["install"] = {"status": "unsupported", "message": str(exc)}
        result["blockers"] = ["adapter install not implemented"]
        return result
    except (FileNotFoundError, RuntimeError) as exc:
        result["install"] = {"status": "failed", "message": str(exc)}
        result["blockers"] = ["adapter install failed"]
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
        "adapter": adapter_name,
        "requested_adapter": requested_name,
        "alias": alias_used,
        "status": status,
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
    if cap.verified:
        return "verified"
    if cap.status == "wip":
        return "install"
    if not cap.runtime_observed and (cap.supports_hooks or cap.supports_mcp):
        return "wait-runtime"
    if cap.verification_blockers:
        return "doctor"
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
        session_id = str(record.get("session_id") or "").strip()
        if session_id:
            sessions.add(session_id)
    return sessions


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
    "build_onboarding_summary",
    "doctor_adapter",
    "install_verify_adapter",
    "install_adapter",
    "uninstall_adapter",
    "verify_adapter",
]
