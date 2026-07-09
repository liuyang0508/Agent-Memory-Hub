"""Read-only diagnostics for the Codex CLI adapter."""

from __future__ import annotations

import shutil
from pathlib import Path

from .codex_config import (
    BEGIN,
    END,
    MCP_SECTION,
    mcp_section_bounds,
)
from .codex_hook_diagnostics import (
    diagnose_hook_scripts,
    diagnose_hooks_json,
)
from .diagnostics import (
    AdapterDiagnosticCheck,
    AdapterDiagnosticReport,
    diagnose_layered_context_pack_evidence,
    diagnose_runtime_evidence,
    overall_status,
)


def diagnose_codex_config(
    *,
    agents_md: Path,
    hooks_json: Path,
    config_toml: Path,
    brain_dir: Path,
    hook_events: tuple[str, ...],
    hook_scripts: dict[str, tuple[str, str]],
    hooks_dir: Path,
    mcp_server: Path,
) -> AdapterDiagnosticReport:
    checks = [
        _diagnose_agents_block(agents_md),
        diagnose_hooks_json(hooks_json, hook_events, hook_scripts, hooks_dir, config_toml),
        _diagnose_mcp_server(config_toml, mcp_server),
        diagnose_hook_scripts(hook_scripts, hooks_dir),
        _diagnose_hook_timeout_tooling(),
        _diagnose_mcp_launcher(mcp_server),
        diagnose_runtime_evidence(
            brain_dir=brain_dir,
            adapter="codex",
            check_name="Codex runtime evidence",
        ),
        diagnose_layered_context_pack_evidence(
            brain_dir=brain_dir,
            adapter="codex",
            check_name="Codex layered context pack evidence",
        ),
    ]
    return AdapterDiagnosticReport(
        adapter="codex",
        overall_status=overall_status(checks),
        checks=checks,
        brain_dir=brain_dir,
    )


def _diagnose_agents_block(agents_md: Path) -> AdapterDiagnosticCheck:
    if not agents_md.exists():
        return AdapterDiagnosticCheck(
            name="AGENTS.md discipline block",
            status="error",
            detail=f"missing: {agents_md}",
            fix="run: memory adapter install codex",
        )
    content = agents_md.read_text(encoding="utf-8")
    if BEGIN in content and END in content:
        return AdapterDiagnosticCheck(
            name="AGENTS.md discipline block",
            status="ok",
            detail=f"present: {agents_md}",
        )
    return AdapterDiagnosticCheck(
        name="AGENTS.md discipline block",
        status="error",
        detail=f"hub sentinel block missing or incomplete: {agents_md}",
        fix="run: memory adapter install codex",
    )


def _diagnose_mcp_server(config_toml: Path, mcp_server: Path) -> AdapterDiagnosticCheck:
    if not config_toml.exists():
        return AdapterDiagnosticCheck(
            name="Codex MCP server",
            status="error",
            detail=f"missing: {config_toml}",
            fix="run: memory adapter install codex",
        )
    content = config_toml.read_text(encoding="utf-8")
    start, end = mcp_section_bounds(content)
    if start is None:
        return AdapterDiagnosticCheck(
            name="Codex MCP server",
            status="error",
            detail=f"missing section: {MCP_SECTION}",
            fix="run: memory adapter install codex",
        )
    section = content[start:end]
    if str(mcp_server) not in section:
        return AdapterDiagnosticCheck(
            name="Codex MCP server",
            status="error",
            detail="agent-memory-hub section points to a different command",
            fix="run: memory adapter install codex",
        )
    return AdapterDiagnosticCheck(
        name="Codex MCP server",
        status="ok",
        detail=f"registered: {mcp_server}",
    )


def _diagnose_mcp_launcher(mcp_server: Path) -> AdapterDiagnosticCheck:
    if not mcp_server.exists():
        return AdapterDiagnosticCheck(
            name="Codex MCP launcher",
            status="error",
            detail=f"missing: {mcp_server}",
            fix="restore the agent-memory-hub checkout or reinstall from source",
        )
    return AdapterDiagnosticCheck(
        name="Codex MCP launcher",
        status="ok",
        detail=f"found: {mcp_server}",
    )


def _diagnose_hook_timeout_tooling() -> AdapterDiagnosticCheck:
    timeout_bin = shutil.which("gtimeout") or shutil.which("timeout")
    if timeout_bin:
        return AdapterDiagnosticCheck(
            name="Codex hook timeout tooling",
            status="ok",
            detail=f"external timeout command available: {timeout_bin}",
        )
    return AdapterDiagnosticCheck(
        name="Codex hook timeout tooling",
        status="ok",
        detail=(
            "external timeout command not found; "
            "inject-context.sh uses Python subprocess timeout fallback"
        ),
        fix="optional on macOS: brew install coreutils",
    )
