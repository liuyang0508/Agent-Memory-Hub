"""OpenHuman adapter.

OpenHuman exposes a public ``agentmemory`` backend path. This adapter bridges
Agent Memory Hub into that path by writing a sentinel-managed TOML block while
leaving unrelated OpenHuman config intact.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

from . import AdapterBase, AdapterConfig
from .diagnostics import AdapterDiagnosticCheck, AdapterDiagnosticReport, overall_status
from .python_runtime import amh_python_executable
from .registry import register_adapter


CONFIG_PATH = Path.home() / ".openhuman" / "config.toml"
BEGIN = "# BEGIN agent-memory-hub"
END = "# END agent-memory-hub"
AGENTMEMORY_ARGS = ["-m", "agent_brain.interfaces.mcp.server"]


class OpenHumanAdapter(AdapterBase):
    """Adapter for OpenHuman's agentmemory backend config."""

    def get_config(self) -> AdapterConfig:
        return AdapterConfig(
            agent_name="openhuman",
            config_dir=Path.home() / ".openhuman",
            hook_type="file",
            inject_method="system_prompt",
            supports_hooks=False,
            supports_mcp=False,
        )

    def install(self) -> str:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        existing = _read_text(CONFIG_PATH)
        unmanaged = _remove_managed_block(existing)
        include_memory_table = _validate_unmanaged_memory_backend(unmanaged)
        block = _managed_block(self.brain_dir, include_memory_table=include_memory_table)
        updated = _append_block(unmanaged, block)
        if updated == existing:
            return f"openhuman adapter: already installed at {CONFIG_PATH}"
        _atomic_write_text(CONFIG_PATH, updated)
        return f"openhuman adapter: installed agentmemory backend in {CONFIG_PATH}"

    def uninstall(self) -> str:
        if not CONFIG_PATH.exists():
            return f"openhuman adapter: {CONFIG_PATH} does not exist, nothing to remove"
        existing = CONFIG_PATH.read_text(encoding="utf-8")
        updated = _remove_managed_block(existing)
        if updated == existing:
            return "openhuman adapter: no hub block found, nothing to remove"
        _atomic_write_text(CONFIG_PATH, updated)
        return f"openhuman adapter: removed agentmemory backend block from {CONFIG_PATH}"

    def inject_context(self, query: str) -> str:
        return (
            "# OpenHuman agentmemory backend bridge\n"
            f"# Data: {self.brain_dir}\n"
            f"# Query for reference: {query}\n"
        )

    def get_install_instructions(self) -> str:
        return (
            "## OpenHuman Adapter\n\n"
            f"Writes a managed `agentmemory` backend block into `{CONFIG_PATH}`.\n"
            "Existing unrelated TOML settings are preserved. Existing non-agentmemory "
            "`memory.backend` values are not overwritten automatically.\n"
        )

    def diagnose(self) -> AdapterDiagnosticReport:
        checks = [self._diagnose_backend()]
        return AdapterDiagnosticReport(
            adapter="openhuman",
            overall_status=overall_status(checks),
            checks=checks,
            brain_dir=self.brain_dir,
        )

    def _diagnose_backend(self) -> AdapterDiagnosticCheck:
        if not CONFIG_PATH.exists():
            return AdapterDiagnosticCheck(
                name="OpenHuman agentmemory backend",
                status="error",
                detail=f"missing: {CONFIG_PATH}",
                fix="run: memory adapter install openhuman",
            )
        try:
            parsed = _parse_toml(CONFIG_PATH.read_text(encoding="utf-8"))
        except RuntimeError as exc:
            return AdapterDiagnosticCheck(
                name="OpenHuman agentmemory backend",
                status="error",
                detail=str(exc),
                fix="repair TOML by hand, then run: memory adapter install openhuman",
            )

        memory = parsed.get("memory")
        if not isinstance(memory, dict):
            return AdapterDiagnosticCheck(
                name="OpenHuman agentmemory backend",
                status="error",
                detail="missing [memory] table",
                fix="run: memory adapter install openhuman",
            )
        backend = memory.get("backend")
        agentmemory = memory.get("agentmemory")
        if backend != "agentmemory" or not isinstance(agentmemory, dict):
            return AdapterDiagnosticCheck(
                name="OpenHuman agentmemory backend",
                status="error",
                detail="memory.backend is not configured for agentmemory",
                fix="run: memory adapter install openhuman",
            )

        issues: list[str] = []
        if agentmemory.get("command") != amh_python_executable():
            issues.append("command")
        if agentmemory.get("args") != AGENTMEMORY_ARGS:
            issues.append("args")
        env = agentmemory.get("env")
        if not isinstance(env, dict) or env.get("BRAIN_DIR") != str(self.brain_dir):
            issues.append("env.BRAIN_DIR")

        if issues:
            return AdapterDiagnosticCheck(
                name="OpenHuman agentmemory backend",
                status="error",
                detail=f"invalid agentmemory field(s): {', '.join(issues)}",
                fix="run: memory adapter install openhuman",
            )

        return AdapterDiagnosticCheck(
            name="OpenHuman agentmemory backend",
            status="ok",
            detail=f"agentmemory backend registered in {CONFIG_PATH}",
        )


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _validate_unmanaged_memory_backend(content: str) -> bool:
    parsed = _parse_toml(content)
    memory = parsed.get("memory")
    if memory is None:
        return True
    if not isinstance(memory, dict):
        raise RuntimeError(f"refuse to overwrite {CONFIG_PATH}: [memory] must be a table")
    backend = memory.get("backend")
    if backend != "agentmemory":
        raise RuntimeError(
            f"refuse to overwrite {CONFIG_PATH}: memory.backend is {backend!r}, not 'agentmemory'"
        )
    if "agentmemory" in memory:
        raise RuntimeError(
            f"refuse to overwrite {CONFIG_PATH}: unmanaged memory.agentmemory already exists"
        )
    return False


def _parse_toml(content: str) -> dict[str, Any]:
    if not content.strip():
        return {}
    try:
        parsed = tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        raise RuntimeError(f"malformed {CONFIG_PATH}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"refuse to overwrite {CONFIG_PATH}: TOML root must be a table")
    return parsed


def _managed_block(brain_dir: Path, *, include_memory_table: bool) -> str:
    lines = [BEGIN]
    if include_memory_table:
        lines.extend([
            "[memory]",
            'backend = "agentmemory"',
            "",
        ])
    lines.extend([
        "[memory.agentmemory]",
        f"command = {json.dumps(amh_python_executable())}",
        f"args = {json.dumps(AGENTMEMORY_ARGS)}",
        "",
        "[memory.agentmemory.env]",
        f"BRAIN_DIR = {json.dumps(str(brain_dir))}",
        END,
    ])
    return "\n".join(lines)


def _append_block(existing: str, block: str) -> str:
    content = existing.rstrip()
    if content:
        return f"{content}\n\n{block}\n"
    return f"{block}\n"


def _remove_managed_block(content: str) -> str:
    if BEGIN not in content:
        return content
    start = content.index(BEGIN)
    end = _block_end(content, start)
    before = content[:start].rstrip()
    after = content[end:].lstrip("\n")
    if before and after:
        return before + "\n\n" + after
    if before:
        return before + "\n"
    return after


def _block_end(content: str, start: int) -> int:
    end = content.find(END, start)
    if end == -1:
        return len(content)
    return end + len(END)


def _atomic_write_text(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


register_adapter("openhuman", OpenHumanAdapter)
