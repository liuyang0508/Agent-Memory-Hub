"""Read-only health aggregation for configured core hook adapters."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import tomllib
from typing import Any, Protocol, cast

from agent_brain.agent_integrations.diagnostics import (
    AdapterDiagnosticCheck,
    CheckStatus,
)
from agent_brain.agent_integrations.hook_config import HUB_HOOK_DIR_MARKERS
from agent_brain.platform.install_repair import CORE_HOOK_ADAPTERS


DETAIL_LIMIT = 1200
_VALID_STATUSES = frozenset({"ok", "warn", "error"})
_RAW_HOOK_FIELD = re.compile(r'(?<!\\)"(?:command|hooks)"\s*:\s*"((?:\\.|[^"\\])*)')


@dataclass(frozen=True)
class CoreAdapterHealth:
    adapter: str
    status: CheckStatus
    non_ok_checks: tuple[AdapterDiagnosticCheck, ...]


class _DiagnosticAdapter(Protocol):
    def diagnose(self) -> object: ...


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
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""


def _contains_any(path: Path, markers: tuple[str, ...]) -> bool:
    content = _read_text(path)
    return any(marker in content for marker in markers)


def _command_references_hub_hook(command: object) -> bool:
    if not isinstance(command, str):
        return False
    normalized = re.sub(r"/+", "/", command.replace("\\", "/"))
    return any(marker in normalized for marker in HUB_HOOK_DIR_MARKERS)


def _hooks_subtree_has_hub_hook(value: Any, *, direct_hooks_value: bool) -> bool:
    if isinstance(value, str):
        return direct_hooks_value and _command_references_hub_hook(value)
    if isinstance(value, list):
        return any(
            _hooks_subtree_has_hub_hook(item, direct_hooks_value=direct_hooks_value)
            for item in value
        )
    if not isinstance(value, dict):
        return False
    for key, child in value.items():
        if key == "command" and _command_references_hub_hook(child):
            return True
        if key == "hooks" and _hooks_subtree_has_hub_hook(child, direct_hooks_value=True):
            return True
        if isinstance(child, (dict, list)) and _hooks_subtree_has_hub_hook(
            child, direct_hooks_value=False
        ):
            return True
    return False


def _valid_json_has_hub_hook(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and "hooks" in payload
        and _hooks_subtree_has_hub_hook(payload["hooks"], direct_hooks_value=True)
    )


def _malformed_json_has_hub_hook(content: str) -> bool:
    return any(
        _command_references_hub_hook(match.group(1)) for match in _RAW_HOOK_FIELD.finditer(content)
    )


def _json_object_has_direct_key(content: str, open_brace: int, key: str) -> bool:
    stack = ["{"]
    index = open_brace + 1
    while index < len(content) and stack:
        char = content[index]
        if char == '"':
            token_start = index
            index += 1
            escaped = False
            while index < len(content):
                current = content[index]
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    break
                index += 1
            if index >= len(content):
                return False

            if len(stack) == 1 and stack[-1] == "{":
                after = index + 1
                while after < len(content) and content[after] in " \t\r\n":
                    after += 1
                if after < len(content) and content[after] == ":":
                    raw_token = content[token_start : index + 1]
                    try:
                        decoded_token = json.loads(raw_token)
                    except json.JSONDecodeError:
                        decoded_token = raw_token[1:-1]
                    if decoded_token == key:
                        return True
        elif char in "{[":
            stack.append(char)
        elif char == "}" and stack[-1] == "{":
            stack.pop()
        elif char == "]" and stack[-1] == "[":
            stack.pop()
        index += 1
    return False


def _malformed_json_has_mcp_ownership(content: str, server_name: str) -> bool:
    container = re.compile(
        r'(?<!\\)"mcpServers"\s*:\s*\{',
    )
    return any(
        _json_object_has_direct_key(content, match.end() - 1, server_name)
        for match in container.finditer(content)
    )


def _json_footprint(path: Path, *, server_name: str | None = None) -> bool:
    content = _read_text(path)
    if not content:
        return False
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return _malformed_json_has_hub_hook(content) or (
            server_name is not None and _malformed_json_has_mcp_ownership(content, server_name)
        )

    if _valid_json_has_hub_hook(payload):
        return True
    if server_name is None or not isinstance(payload, dict):
        return False
    servers = payload.get("mcpServers")
    return isinstance(servers, dict) and server_name in servers


def _malformed_toml_has_section(content: str, section: str) -> bool:
    pattern = re.compile(
        rf"^[ \t]*{re.escape(section)}[ \t]*(?:#[^\r\n]*)?$",
        re.MULTILINE,
    )
    return pattern.search(content) is not None


def _toml_mcp_footprint(path: Path, *, server_name: str, section: str) -> bool:
    content = _read_text(path)
    if not content:
        return False
    try:
        payload = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        return _malformed_toml_has_section(content, section)
    servers = payload.get("mcp_servers")
    return isinstance(servers, dict) and server_name in servers


def has_managed_footprint(adapter_name: str) -> bool:
    """Return whether an adapter has any AMH-owned managed configuration."""
    if adapter_name == "codex":
        from agent_brain.agent_integrations import codex as codex_mod
        from agent_brain.agent_integrations.codex_config import BEGIN, END, MCP_SECTION

        return (
            _contains_any(codex_mod.AGENTS_MD, (BEGIN, END))
            or _json_footprint(codex_mod.CODEX_HOOKS_JSON)
            or _toml_mcp_footprint(
                codex_mod.CODEX_CONFIG_TOML,
                server_name="agent-memory-hub",
                section=MCP_SECTION,
            )
        )
    if adapter_name == "claude_code":
        from agent_brain.agent_integrations import claude_code as claude_mod
        from agent_brain.agent_integrations.awareness import BEGIN, END

        return _contains_any(claude_mod.AWARENESS_PATH, (BEGIN, END)) or _json_footprint(
            claude_mod.SETTINGS_PATH, server_name=claude_mod.SERVER_NAME
        )
    return False


def _adapter_error(adapter_name: str, exc: Exception) -> CoreAdapterHealth:
    check = AdapterDiagnosticCheck(
        name=f"{adapter_name} adapter doctor",
        status="error",
        detail=bounded_diagnostic_text(exc),
        fix=f"run: memory adapter install {adapter_name}",
    )
    return CoreAdapterHealth(adapter_name, "error", (check,))


def _normalize_diagnostic_report(adapter_name: str, report: object) -> CoreAdapterHealth:
    if report is None:
        raise TypeError("adapter diagnose returned no report")

    status = getattr(report, "overall_status", None)
    if status not in _VALID_STATUSES:
        raise ValueError(f"invalid report status: {status!r}")

    try:
        checks = tuple(getattr(report, "checks"))
    except (AttributeError, TypeError) as exc:
        raise TypeError("report checks must be iterable") from exc

    for check in checks:
        if not isinstance(check, AdapterDiagnosticCheck):
            raise TypeError("report checks must contain AdapterDiagnosticCheck values")
        if check.status not in _VALID_STATUSES:
            raise ValueError(f"invalid check status: {check.status!r}")

    non_ok = tuple(check for check in checks if check.status != "ok")
    return CoreAdapterHealth(adapter_name, status, non_ok)


def diagnose_configured_core_adapters(brain_dir: Path) -> tuple[CoreAdapterHealth, ...]:
    """Diagnose only core adapters with an AMH-owned footprint."""
    from agent_brain import agent_integrations
    from agent_brain.agent_integrations import registry

    results: dict[str, CoreAdapterHealth] = {}
    configured: list[str] = []
    for adapter_name in CORE_HOOK_ADAPTERS:
        try:
            managed = has_managed_footprint(adapter_name)
        except Exception as exc:
            results[adapter_name] = _adapter_error(adapter_name, exc)
            continue
        if managed:
            configured.append(adapter_name)

    if not configured:
        return tuple(results[name] for name in CORE_HOOK_ADAPTERS if name in results)

    try:
        agent_integrations.discover_adapters()
    except Exception as exc:
        for adapter_name in configured:
            results[adapter_name] = _adapter_error(adapter_name, exc)
        return tuple(results[name] for name in CORE_HOOK_ADAPTERS if name in results)

    for adapter_name in configured:
        try:
            adapter = cast(_DiagnosticAdapter, registry.get_adapter(adapter_name, brain_dir))
            report = adapter.diagnose()
            results[adapter_name] = _normalize_diagnostic_report(adapter_name, report)
        except Exception as exc:
            results[adapter_name] = _adapter_error(adapter_name, exc)
    return tuple(results[name] for name in CORE_HOOK_ADAPTERS if name in results)


__all__ = [
    "CoreAdapterHealth",
    "bounded_diagnostic_text",
    "diagnose_configured_core_adapters",
    "has_managed_footprint",
]
