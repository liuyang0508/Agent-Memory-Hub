"""Read-only health aggregation for configured core hook adapters."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import json
from pathlib import Path
import re
import sys
import tomllib
from typing import Any, Literal, Protocol, cast

from agent_brain.diagnostic_types import AdapterDiagnosticCheck, CheckStatus
from agent_brain.platform.install_repair import CORE_HOOK_ADAPTERS


DETAIL_LIMIT = 1200
_VALID_STATUSES = frozenset({"ok", "warn", "error"})
_STATUS_SEVERITY: dict[CheckStatus, int] = {"ok": 0, "warn": 1, "error": 2}
_RAW_HOOK_FIELD = re.compile(r'(?<!\\)"(?:command|hooks)"\s*:\s*"((?:\\.|[^"\\])*)')
_TomlLexState = Literal["normal", "multiline-basic", "multiline-literal"]
_JsonContainer = Literal["object", "array"]
_JsonRole = Literal["root", "mcp-servers", "other"]
_CODEX_MODULE = "agent_brain.agent_integrations.codex"
_CLAUDE_MODULE = "agent_brain.agent_integrations.claude_code"
_REGISTRY_MODULE = "agent_brain.agent_integrations.registry"
_CODEX_AGENTS_MD = Path.home() / ".codex" / "AGENTS.md"
_CODEX_HOOKS_JSON = Path.home() / ".codex" / "hooks.json"
_CODEX_CONFIG_TOML = Path.home() / ".codex" / "config.toml"
_CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"
_CLAUDE_AWARENESS = Path.home() / ".claude" / "CLAUDE.md"
_CODEX_SENTINELS = (
    "<!-- BEGIN agent-memory-hub -->",
    "<!-- END agent-memory-hub -->",
)
_AWARENESS_SENTINELS = (
    "<!-- BEGIN agent-memory-hub-awareness -->",
    "<!-- END agent-memory-hub-awareness -->",
)
_HUB_HOOK_DIR_MARKERS = (
    "/agent_runtime_kit/hooks/",
    "/brain/hooks/",
)
_SERVER_NAME = "agent-memory-hub"


@dataclass(frozen=True)
class CoreAdapterHealth:
    adapter: str
    status: CheckStatus
    non_ok_checks: tuple[AdapterDiagnosticCheck, ...]


class _DiagnosticAdapter(Protocol):
    def diagnose(self) -> object: ...


class _IntegrationPackage(Protocol):
    def discover_adapters(self) -> list[str]: ...


class _RegistryModule(Protocol):
    def get_adapter(self, name: str, brain_dir: Path) -> object: ...


@dataclass
class _JsonFrame:
    container: _JsonContainer
    role: _JsonRole
    expecting_key: bool = False


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
    except FileNotFoundError:
        return ""


def _contains_any(path: Path, markers: tuple[str, ...]) -> bool:
    content = _read_text(path)
    return any(marker in content for marker in markers)


def _loaded_path(module_name: str, attribute: str, default: Path) -> Path:
    module = sys.modules.get(module_name)
    value = getattr(module, attribute, default) if module is not None else default
    return value if isinstance(value, Path) else default


def _loaded_server_name(module_name: str, default: str) -> str:
    module = sys.modules.get(module_name)
    value = getattr(module, "SERVER_NAME", default) if module is not None else default
    return value if isinstance(value, str) else default


def _command_references_hub_hook(command: object) -> bool:
    if not isinstance(command, str):
        return False
    normalized = re.sub(r"/+", "/", command.replace("\\", "/"))
    return any(marker in normalized for marker in _HUB_HOOK_DIR_MARKERS)


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


def _skip_json_whitespace(content: str, index: int) -> int:
    while index < len(content) and content[index] in " \t\r\n":
        index += 1
    return index


def _read_json_string(content: str, start: int) -> tuple[str | None, int]:
    index = start + 1
    escaped = False
    while index < len(content):
        char = content[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            raw_token = content[start : index + 1]
            try:
                decoded = json.loads(raw_token)
            except json.JSONDecodeError:
                decoded = None
            return decoded if isinstance(decoded, str) else None, index
        index += 1
    return None, len(content)


def _malformed_json_has_mcp_ownership(content: str, server_name: str) -> bool:
    root = _skip_json_whitespace(content, 0)
    if root >= len(content) or content[root] != "{":
        return False

    stack = [_JsonFrame("object", "root", expecting_key=True)]
    pending_mcp_object: int | None = None
    index = root + 1
    while index < len(content) and stack:
        char = content[index]
        frame = stack[-1]

        if char == '"':
            token, end = _read_json_string(content, index)
            if frame.container == "object" and frame.expecting_key:
                colon = _skip_json_whitespace(content, end + 1)
                if colon < len(content) and content[colon] == ":":
                    frame.expecting_key = False
                    value = _skip_json_whitespace(content, colon + 1)
                    if frame.role == "mcp-servers" and token == server_name:
                        return True
                    if (
                        frame.role == "root"
                        and token == "mcpServers"
                        and value < len(content)
                        and content[value] == "{"
                    ):
                        pending_mcp_object = value
            index = end + 1
            continue

        if char == "{":
            role: _JsonRole = "mcp-servers" if index == pending_mcp_object else "other"
            stack.append(_JsonFrame("object", role, expecting_key=True))
            if index == pending_mcp_object:
                pending_mcp_object = None
        elif char == "[":
            stack.append(_JsonFrame("array", "other"))
        elif char == "}" and frame.container == "object":
            stack.pop()
            if not stack:
                return False
        elif char == "]" and frame.container == "array":
            stack.pop()
        elif char == "," and frame.container == "object":
            frame.expecting_key = True
        index += 1
    return False


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


def _toml_state_after_line(line: str, state: _TomlLexState) -> _TomlLexState:
    index = 0
    while index < len(line):
        if state == "multiline-basic":
            if line.startswith('"""', index):
                state = "normal"
                index += 3
            elif line[index] == "\\":
                index += 2
            else:
                index += 1
            continue

        if state == "multiline-literal":
            if line.startswith("'''", index):
                state = "normal"
                index += 3
            else:
                index += 1
            continue

        char = line[index]
        if char == "#":
            break
        if line.startswith('"""', index):
            state = "multiline-basic"
            index += 3
            continue
        if line.startswith("'''", index):
            state = "multiline-literal"
            index += 3
            continue
        if char == '"':
            index += 1
            while index < len(line):
                if line[index] == "\\":
                    index += 2
                elif line[index] == '"':
                    index += 1
                    break
                else:
                    index += 1
            continue
        if char == "'":
            closing = line.find("'", index + 1)
            index = len(line) if closing < 0 else closing + 1
            continue
        index += 1
    return state


def _toml_line_has_managed_mcp(line: str, server_name: str) -> bool:
    candidate = line.lstrip(" \t")
    try:
        payload = tomllib.loads(f"{candidate}\n__amh_footprint_probe__ = true\n")
    except tomllib.TOMLDecodeError:
        return False
    servers = payload.get("mcp_servers")
    return isinstance(servers, dict) and server_name in servers


def _malformed_toml_has_mcp_table(content: str, server_name: str) -> bool:
    state: _TomlLexState = "normal"
    at_root = True
    for line in content.splitlines():
        if state == "normal":
            candidate = line.lstrip(" \t")
            starts_table = candidate.startswith("[")
            if (starts_table or at_root) and _toml_line_has_managed_mcp(line, server_name):
                return True
            if starts_table:
                at_root = False
        state = _toml_state_after_line(line, state)
    return False


def _toml_mcp_footprint(path: Path, *, server_name: str) -> bool:
    content = _read_text(path)
    if not content:
        return False
    try:
        payload = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        return _malformed_toml_has_mcp_table(content, server_name)
    servers = payload.get("mcp_servers")
    return isinstance(servers, dict) and server_name in servers


def has_managed_footprint(adapter_name: str) -> bool:
    """Return whether an adapter has any AMH-owned managed configuration."""
    if adapter_name == "codex":
        return (
            _contains_any(
                _loaded_path(_CODEX_MODULE, "AGENTS_MD", _CODEX_AGENTS_MD),
                _CODEX_SENTINELS,
            )
            or _json_footprint(_loaded_path(_CODEX_MODULE, "CODEX_HOOKS_JSON", _CODEX_HOOKS_JSON))
            or _toml_mcp_footprint(
                _loaded_path(_CODEX_MODULE, "CODEX_CONFIG_TOML", _CODEX_CONFIG_TOML),
                server_name=_SERVER_NAME,
            )
        )
    if adapter_name == "claude_code":
        return _contains_any(
            _loaded_path(_CLAUDE_MODULE, "AWARENESS_PATH", _CLAUDE_AWARENESS),
            _AWARENESS_SENTINELS,
        ) or _json_footprint(
            _loaded_path(_CLAUDE_MODULE, "SETTINGS_PATH", _CLAUDE_SETTINGS),
            server_name=_loaded_server_name(_CLAUDE_MODULE, _SERVER_NAME),
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

    normalized_status = cast(CheckStatus, status)
    for check in checks:
        if _STATUS_SEVERITY[check.status] > _STATUS_SEVERITY[normalized_status]:
            normalized_status = check.status
    non_ok = tuple(check for check in checks if check.status != "ok")
    return CoreAdapterHealth(adapter_name, normalized_status, non_ok)


def diagnose_configured_core_adapters(brain_dir: Path) -> tuple[CoreAdapterHealth, ...]:
    """Diagnose only core adapters with an AMH-owned footprint."""
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
        integrations = cast(
            _IntegrationPackage,
            importlib.import_module("agent_brain.agent_integrations"),
        )
        integrations.discover_adapters()
        registry = cast(_RegistryModule, importlib.import_module(_REGISTRY_MODULE))
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
