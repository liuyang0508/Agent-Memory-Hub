from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence

from .diagnostics import AdapterDiagnosticCheck


def validate_mcp_server(
    *,
    check_name: str,
    config_path: Path,
    server_name: str,
    server: Mapping[str, object],
    expected_command: str,
    expected_args: Sequence[str],
    expected_env: Mapping[str, str],
    install_command: str,
) -> AdapterDiagnosticCheck:
    issues: list[str] = []
    if server.get("command") != expected_command:
        issues.append("command")
    if server.get("args") != list(expected_args):
        issues.append("args")
    env = server.get("env")
    if not isinstance(env, dict):
        issues.append("env")
    else:
        for key, value in expected_env.items():
            if env.get(key) != value:
                issues.append(f"env.{key}")

    if issues:
        return AdapterDiagnosticCheck(
            name=check_name,
            status="error",
            detail=f"invalid MCP server field(s): {', '.join(issues)}",
            fix=f"run: {install_command}",
        )

    return AdapterDiagnosticCheck(
        name=check_name,
        status="ok",
        detail=f"registered: {server_name} in {config_path}",
    )


__all__ = ["validate_mcp_server"]
