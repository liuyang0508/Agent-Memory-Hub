"""MCP config-file diagnostics for adapters."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Sequence

import yaml

from .diagnostics import AdapterDiagnosticCheck
from .mcp_diagnostics import validate_mcp_server


def diagnose_mcp_json_server(
    *,
    check_name: str,
    config_path: Path,
    server_name: str,
    expected_command: str,
    expected_args: Sequence[str],
    expected_env: Mapping[str, str],
    install_command: str,
) -> AdapterDiagnosticCheck:
    """Diagnose a JSON MCP config without modifying the user's file."""
    if not config_path.exists():
        return AdapterDiagnosticCheck(
            name=check_name,
            status="error",
            detail=f"missing: {config_path}",
            fix=f"run: {install_command}",
        )

    try:
        data = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        return AdapterDiagnosticCheck(
            name=check_name,
            status="error",
            detail=f"malformed JSON in {config_path}: {exc}",
            fix=f"repair JSON by hand, then run: {install_command}",
        )

    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        return AdapterDiagnosticCheck(
            name=check_name,
            status="error",
            detail="missing top-level mcpServers object",
            fix=f"run: {install_command}",
        )

    server = servers.get(server_name)
    if not isinstance(server, dict):
        return AdapterDiagnosticCheck(
            name=check_name,
            status="error",
            detail=f"missing MCP server: {server_name}",
            fix=f"run: {install_command}",
        )

    return validate_mcp_server(
        check_name=check_name,
        config_path=config_path,
        server_name=server_name,
        server=server,
        expected_command=expected_command,
        expected_args=expected_args,
        expected_env=expected_env,
        install_command=install_command,
    )


def diagnose_mcp_yaml_server(
    *,
    check_name: str,
    config_path: Path,
    server_name: str,
    expected_command: str,
    expected_args: Sequence[str],
    expected_env: Mapping[str, str],
    install_command: str,
) -> AdapterDiagnosticCheck:
    """Diagnose a YAML MCP config without modifying the user's file."""
    if not config_path.exists():
        return AdapterDiagnosticCheck(
            name=check_name,
            status="error",
            detail=f"missing: {config_path}",
            fix=f"run: {install_command}",
        )

    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8-sig"))
    except yaml.YAMLError as exc:
        return AdapterDiagnosticCheck(
            name=check_name,
            status="error",
            detail=f"malformed YAML in {config_path}: {exc}",
            fix=f"repair YAML by hand, then run: {install_command}",
        )

    if not isinstance(loaded, dict):
        return AdapterDiagnosticCheck(
            name=check_name,
            status="error",
            detail=f"{config_path} must contain a YAML mapping",
            fix=f"run: {install_command}",
        )

    servers = loaded.get("mcpServers")
    if not isinstance(servers, (dict, list)):
        return AdapterDiagnosticCheck(
            name=check_name,
            status="error",
            detail="missing top-level mcpServers object/list",
            fix=f"run: {install_command}",
        )

    if isinstance(servers, dict):
        server = servers.get(server_name)
    else:
        server = next(
            (
                entry
                for entry in servers
                if isinstance(entry, dict) and entry.get("name") == server_name
            ),
            None,
        )
    if not isinstance(server, dict):
        return AdapterDiagnosticCheck(
            name=check_name,
            status="error",
            detail=f"missing MCP server: {server_name}",
            fix=f"run: {install_command}",
        )

    return validate_mcp_server(
        check_name=check_name,
        config_path=config_path,
        server_name=server_name,
        server=server,
        expected_command=expected_command,
        expected_args=expected_args,
        expected_env=expected_env,
        install_command=install_command,
    )


__all__ = ["diagnose_mcp_json_server", "diagnose_mcp_yaml_server"]
