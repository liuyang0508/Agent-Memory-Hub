"""OpenSquilla adapter.

OpenSquilla uses ``~/.opensquilla/config.toml`` and supports MCP client
configuration. This adapter appends a sentinel-bracketed TOML block for the
Agent Memory Hub MCP server and removes only that block on uninstall.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from . import AdapterBase, AdapterConfig
from .awareness import (
    diagnose_awareness_block,
    install_awareness_block,
    render_awareness_block,
    uninstall_awareness_block,
)
from .diagnostics import AdapterDiagnosticCheck, AdapterDiagnosticReport, overall_status
from .mcp_diagnostics import validate_mcp_server
from .python_runtime import amh_python_executable
from .registry import register_adapter

CONFIG_PATH = Path.home() / ".opensquilla" / "config.toml"
AWARENESS_PATH = Path.home() / ".opensquilla" / "agent-memory-hub-awareness.md"
SERVER_NAME = "agent-memory-hub"
MCP_ARGS = ["-m", "agent_brain.interfaces.mcp.server"]
BEGIN = "# BEGIN agent-memory-hub"
END = "# END agent-memory-hub"


class OpenSquillaAdapter(AdapterBase):
    """Install-ready adapter for OpenSquilla via config.toml MCP registration."""

    def get_config(self) -> AdapterConfig:
        return AdapterConfig(
            agent_name="opensquilla",
            config_dir=Path.home() / ".opensquilla",
            hook_type="file",
            inject_method="mcp_tool",
            supports_hooks=False,
            supports_mcp=True,
        )

    def install(self) -> str:
        awareness_msg = self._install_awareness()
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        existing = CONFIG_PATH.read_text(encoding="utf-8-sig") if CONFIG_PATH.exists() else ""
        _parse_toml(existing, CONFIG_PATH)
        block = _build_block(existing, self.brain_dir)

        if BEGIN in existing:
            start = existing.index(BEGIN)
            end = _block_end(existing, start)
            if existing[start:end] == block:
                return (
                    f"{awareness_msg} | "
                    f"opensquilla adapter: MCP server already registered in {CONFIG_PATH}"
                )
            updated = existing[:start] + block + existing[end:]
        else:
            updated = existing
            if updated and not updated.endswith("\n"):
                updated += "\n"
            if updated:
                updated += "\n"
            updated += block + "\n"

        _parse_toml(updated, CONFIG_PATH)
        _atomic_write(CONFIG_PATH, updated)
        return f"{awareness_msg} | opensquilla adapter: registered MCP server in {CONFIG_PATH}"

    def uninstall(self) -> str:
        awareness_msg = self._uninstall_awareness()
        if not CONFIG_PATH.exists():
            return (
                f"{awareness_msg} | "
                f"opensquilla adapter: {CONFIG_PATH} does not exist, nothing to remove"
            )
        content = CONFIG_PATH.read_text(encoding="utf-8-sig")
        if BEGIN not in content:
            return f"{awareness_msg} | opensquilla adapter: no hub MCP block found, nothing to remove"
        start = content.index(BEGIN)
        end = _block_end(content, start)
        before = content[:start].rstrip("\n")
        after = content[end:].lstrip("\n")
        cleaned = before + ("\n\n" if before and after else "") + after
        _parse_toml(cleaned, CONFIG_PATH)
        _atomic_write(CONFIG_PATH, cleaned)
        return f"{awareness_msg} | opensquilla adapter: removed MCP server block from {CONFIG_PATH}"

    def inject_context(self, query: str) -> str:
        return (
            f"# OpenSquilla context injection via MCP server '{SERVER_NAME}'\n"
            f"# Use MCP search_memory with query: {query}\n"
            f"# Data: {self.brain_dir}\n"
        )

    def get_install_instructions(self) -> str:
        return (
            "## OpenSquilla Adapter\n\n"
            f"Writes an Awareness Channel to `{AWARENESS_PATH}` and adds a managed "
            f"MCP server block to `{CONFIG_PATH}`. The adapter preserves existing "
            "TOML and removes only the managed blocks during uninstall."
        )

    def diagnose(self) -> AdapterDiagnosticReport:
        checks = [
            diagnose_awareness_block(
                check_name="OpenSquilla awareness channel",
                path=AWARENESS_PATH,
                brain_dir=self.brain_dir,
                install_command="memory adapter install opensquilla",
            ),
            _diagnose_toml_mcp(config_path=CONFIG_PATH, brain_dir=self.brain_dir),
        ]
        return AdapterDiagnosticReport(
            adapter="opensquilla",
            overall_status=overall_status(checks),
            checks=checks,
        )

    def _install_awareness(self) -> str:
        changed = install_awareness_block(AWARENESS_PATH, self._awareness_block())
        if not changed:
            return f"opensquilla adapter: awareness channel already installed in {AWARENESS_PATH}"
        return f"opensquilla adapter: installed awareness channel in {AWARENESS_PATH}"

    def _uninstall_awareness(self) -> str:
        if uninstall_awareness_block(AWARENESS_PATH):
            return f"opensquilla adapter: removed awareness channel from {AWARENESS_PATH}"
        return "opensquilla adapter: no awareness channel, nothing to remove"

    def _awareness_block(self) -> str:
        return render_awareness_block(
            agent_name="OpenSquilla",
            brain_dir=self.brain_dir,
            tool_channel="OpenSquilla TOML MCP server block",
            extra_guidance=(
                "The TOML block registers the tool channel; this sidecar records when AMH should be used.",
                "Keep OpenSquilla runtime verification separate from install-ready configuration evidence.",
            ),
        )


def _build_block(existing_content: str, brain_dir: Path) -> str:
    parsed = _parse_toml(existing_content, CONFIG_PATH)
    has_mcp_table = isinstance(parsed.get("mcp"), dict)
    lines = [BEGIN]
    if not has_mcp_table:
        lines.extend(["[mcp]", "enabled = true", ""])
    lines.extend([
        '[mcp.servers."agent-memory-hub"]',
        f"command = {_toml_string(amh_python_executable())}",
        'args = ["-m", "agent_brain.interfaces.mcp.server"]',
        "",
        '[mcp.servers."agent-memory-hub".env]',
        f"BRAIN_DIR = {_toml_string(str(brain_dir))}",
        END,
    ])
    return "\n".join(lines)


def _block_end(content: str, start: int) -> int:
    end_idx = content.find(END, start)
    if end_idx == -1:
        return len(content)
    return end_idx + len(END)


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _parse_toml(content: str, path: Path) -> dict:
    if not content.strip():
        return {}
    try:
        parsed = tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        raise RuntimeError(f"refuse to overwrite malformed {path}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"refuse to overwrite {path}: TOML root must be a mapping")
    return parsed


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _diagnose_toml_mcp(*, config_path: Path, brain_dir: Path) -> AdapterDiagnosticCheck:
    if not config_path.exists():
        return AdapterDiagnosticCheck(
            name="OpenSquilla MCP server",
            status="error",
            detail=f"missing: {config_path}",
            fix="run: memory adapter install opensquilla",
        )
    content = config_path.read_text(encoding="utf-8-sig")
    try:
        data = _parse_toml(content, config_path)
    except RuntimeError as exc:
        return AdapterDiagnosticCheck(
            name="OpenSquilla MCP server",
            status="error",
            detail=str(exc),
            fix="repair TOML by hand, then run: memory adapter install opensquilla",
        )
    mcp = data.get("mcp")
    if not isinstance(mcp, dict):
        return AdapterDiagnosticCheck(
            name="OpenSquilla MCP server",
            status="error",
            detail="missing top-level mcp table",
            fix="run: memory adapter install opensquilla",
        )
    servers = mcp.get("servers")
    if not isinstance(servers, dict):
        return AdapterDiagnosticCheck(
            name="OpenSquilla MCP server",
            status="error",
            detail="missing mcp.servers table",
            fix="run: memory adapter install opensquilla",
        )
    server = servers.get(SERVER_NAME)
    if not isinstance(server, dict):
        return AdapterDiagnosticCheck(
            name="OpenSquilla MCP server",
            status="error",
            detail=f"missing MCP server: {SERVER_NAME}",
            fix="run: memory adapter install opensquilla",
        )
    return validate_mcp_server(
        check_name="OpenSquilla MCP server",
        config_path=config_path,
        server_name=SERVER_NAME,
        server=server,
        expected_command=amh_python_executable(),
        expected_args=MCP_ARGS,
        expected_env={"BRAIN_DIR": str(brain_dir)},
        install_command="memory adapter install opensquilla",
    )


register_adapter("opensquilla", OpenSquillaAdapter)
