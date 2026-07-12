"""Read-only health aggregation for configured core hook adapters."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from agent_brain.agent_integrations.diagnostics import (
    AdapterDiagnosticCheck,
    CheckStatus,
)
from agent_brain.agent_integrations.hook_config import HUB_HOOK_DIR_MARKERS
from agent_brain.platform.install_repair import CORE_HOOK_ADAPTERS


DETAIL_LIMIT = 1200


@dataclass(frozen=True)
class CoreAdapterHealth:
    adapter: str
    status: CheckStatus
    non_ok_checks: tuple[AdapterDiagnosticCheck, ...]


def bounded_diagnostic_text(value: object, *, limit: int = DETAIL_LIMIT) -> str:
    """Return printable diagnostic text without flattening useful line structure."""
    if limit <= 0:
        return ""
    text = str(value)
    cleaned = "".join(char if char in "\n\t" or char.isprintable() else " " for char in text)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _contains_any(path: Path, markers: tuple[str, ...]) -> bool:
    content = _read_text(path)
    return any(marker in content for marker in markers)


def _command_references_hub_hook(command: object) -> bool:
    return isinstance(command, str) and any(marker in command for marker in HUB_HOOK_DIR_MARKERS)


def _valid_json_has_hub_hook(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    events = payload.get("hooks")
    if not isinstance(events, dict):
        return False
    for entries in events.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            handlers = entry.get("hooks")
            if not isinstance(handlers, list):
                continue
            for handler in handlers:
                if isinstance(handler, dict) and _command_references_hub_hook(
                    handler.get("command")
                ):
                    return True
    return False


def _malformed_json_has_mcp_ownership(content: str, server_name: str) -> bool:
    return '"mcpServers"' in content and f'"{server_name}"' in content


def _json_footprint(path: Path, *, server_name: str | None = None) -> bool:
    content = _read_text(path)
    if not content:
        return False
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return any(marker in content for marker in HUB_HOOK_DIR_MARKERS) or (
            server_name is not None and _malformed_json_has_mcp_ownership(content, server_name)
        )

    if _valid_json_has_hub_hook(payload):
        return True
    if server_name is None or not isinstance(payload, dict):
        return False
    servers = payload.get("mcpServers")
    return isinstance(servers, dict) and server_name in servers


def _has_exact_section(path: Path, section: str) -> bool:
    return any(line.strip() == section for line in _read_text(path).splitlines())


def has_managed_footprint(adapter_name: str) -> bool:
    """Return whether an adapter has any AMH-owned managed configuration."""
    if adapter_name == "codex":
        from agent_brain.agent_integrations import codex as mod
        from agent_brain.agent_integrations.codex_config import BEGIN, END, MCP_SECTION

        return (
            _contains_any(mod.AGENTS_MD, (BEGIN, END))
            or _json_footprint(mod.CODEX_HOOKS_JSON)
            or _has_exact_section(mod.CODEX_CONFIG_TOML, MCP_SECTION)
        )
    if adapter_name == "claude_code":
        from agent_brain.agent_integrations import claude_code as mod
        from agent_brain.agent_integrations.awareness import BEGIN, END

        return _contains_any(mod.AWARENESS_PATH, (BEGIN, END)) or _json_footprint(
            mod.SETTINGS_PATH, server_name=mod.SERVER_NAME
        )
    return False


def diagnose_configured_core_adapters(brain_dir: Path) -> tuple[CoreAdapterHealth, ...]:
    """Diagnose only core adapters with an AMH-owned footprint."""
    from agent_brain import agent_integrations
    from agent_brain.agent_integrations import registry

    agent_integrations.discover_adapters()
    results: list[CoreAdapterHealth] = []
    for adapter_name in CORE_HOOK_ADAPTERS:
        if not has_managed_footprint(adapter_name):
            continue
        try:
            report = registry.get_adapter(adapter_name, brain_dir).diagnose()
        except Exception as exc:
            check = AdapterDiagnosticCheck(
                name=f"{adapter_name} adapter doctor",
                status="error",
                detail=bounded_diagnostic_text(exc),
                fix=f"run: memory adapter install {adapter_name}",
            )
            results.append(CoreAdapterHealth(adapter_name, "error", (check,)))
            continue
        non_ok = tuple(check for check in report.checks if check.status != "ok")
        results.append(CoreAdapterHealth(adapter_name, report.overall_status, non_ok))
    return tuple(results)


__all__ = [
    "CoreAdapterHealth",
    "bounded_diagnostic_text",
    "diagnose_configured_core_adapters",
    "has_managed_footprint",
]
