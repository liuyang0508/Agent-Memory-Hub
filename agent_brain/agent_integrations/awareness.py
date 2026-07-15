"""Shared Awareness Channel helpers for agent adapters.

An MCP server only makes tools available.  Agents still need a small, stable
instruction surface that tells them when to search, read, or write memory.
Adapters use this module to install that surface without overwriting user
custom instructions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .diagnostics import AdapterDiagnosticCheck

BEGIN = "<!-- BEGIN agent-memory-hub-awareness -->"
END = "<!-- END agent-memory-hub-awareness -->"


def render_awareness_block(
    *,
    agent_name: str,
    brain_dir: Path,
    tool_channel: str,
    mcp_tools_available: bool = True,
    extra_guidance: Sequence[str] = (),
) -> str:
    """Return the managed Awareness Channel block for an adapter."""
    lines = [
        BEGIN,
        "# Agent Memory Hub Awareness Channel",
        "",
        f"Agent: {agent_name}",
        f"Brain directory: `{brain_dir}`",
        "",
        "Agent Memory Hub (AMH) is the shared memory layer for this agent.",
    ]
    if mcp_tools_available:
        lines.extend(
            [
                "MCP tools are available, but tools are not enough by themselves:",
                "this Awareness Channel tells you when to use them.",
            ]
        )
    else:
        lines.extend(
            [
                "This adapter currently installs a static awareness channel, not a verified MCP bridge.",
                "Use it to decide when AMH context is needed; if no in-client tool exists, use the `memory` CLI.",
            ]
        )
    lines.extend(
        [
        "",
        "Before non-trivial work, resume, planning, debugging, or cross-agent handoff:",
        "- call `brief_memory` or `search_memory(..., verbosity=\"auto\")` first; auto search returns only locator/overview candidates;",
        "- select only the 1-3 items whose detail is actually needed, then call `read_memory(id, head=2000, view=\"detail\")`;",
        "- reserve explicit search `verbosity=\"detail\"` for deliberate bounded diagnostics, not ordinary Top-K discovery;",
        "- treat retrieved memory as candidates, not as the current chat transcript;",
        "- current user instructions and live repository evidence override stale memory candidates.",
        "",
        "Treat a one-word or short project/name prompt as a context request, not a greeting.",
        "If MCP tools are available, call `brief_memory` or `search_memory` before replying;",
        "if hook-injected candidates are already present, use them first and then ask what to do next.",
        "",
        "When durable knowledge is created, call `write_memory` through an available MCP/provider/CLI channel.",
        "Write only decisions, facts, signals, episodes, artifacts, handoffs, policies, or reusable skills.",
        "Do not write raw transcripts, throwaway debug output, source code copies, or secrets.",
        "",
        f"Tool channel: {tool_channel}",
        ]
    )
    if extra_guidance:
        lines.extend(["", "Adapter-specific guidance:"])
        lines.extend(f"- {item}" for item in extra_guidance)
    lines.extend(["", END])
    return "\n".join(lines)


def install_awareness_block(path: Path, block: str, *, placement: str = "append") -> bool:
    """Install or update the managed block.

    Returns True when the file changed.  Any user content outside the sentinel
    block is preserved.
    """
    if placement not in {"append", "prepend"}:
        raise ValueError(f"unsupported awareness placement: {placement}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        content = path.read_text(encoding="utf-8")
        if BEGIN in content:
            start = content.index(BEGIN)
            end = _block_end(content, start)
            if placement == "prepend":
                new_content = _prepend_block(block, content[:start], content[end:])
                if content == new_content:
                    return False
                _write_backup(path, content)
                _atomic_write(path, new_content)
                return True
            if content[start:end] == block:
                return False
            _write_backup(path, content)
            _atomic_write(path, content[:start] + block + content[end:])
            return True
    else:
        content = ""

    if placement == "prepend":
        new_content = _prepend_block(block, "", content)
        if path.exists():
            _write_backup(path, path.read_text(encoding="utf-8"))
        _atomic_write(path, new_content)
        return True

    if content and not content.endswith("\n"):
        content += "\n"
    if content:
        content += "\n"
    content += block + "\n"
    if path.exists():
        _write_backup(path, path.read_text(encoding="utf-8"))
    _atomic_write(path, content)
    return True


def uninstall_awareness_block(path: Path) -> bool:
    """Remove the managed block, preserving all other file content."""
    if not path.exists():
        return False
    content = path.read_text(encoding="utf-8")
    if BEGIN not in content:
        if not content.strip():
            path.unlink()
            return True
        return False
    start = content.index(BEGIN)
    end = _block_end(content, start)
    before = content[:start].rstrip("\n")
    after = content[end:].lstrip("\n")
    cleaned = before + ("\n" if before and after else "") + after
    _write_backup(path, content)
    if not cleaned.strip():
        path.unlink()
        return True
    _atomic_write(path, cleaned)
    return True


def diagnose_awareness_block(
    *,
    check_name: str,
    path: Path,
    brain_dir: Path,
    install_command: str,
    require_first: bool = False,
) -> AdapterDiagnosticCheck:
    if not path.exists():
        return AdapterDiagnosticCheck(
            name=check_name,
            status="error",
            detail=f"missing awareness channel: {path}",
            fix=f"run: {install_command}",
        )
    content = path.read_text(encoding="utf-8")
    if BEGIN not in content or END not in content:
        return AdapterDiagnosticCheck(
            name=check_name,
            status="error",
            detail=f"hub awareness block missing or incomplete: {path}",
            fix=f"run: {install_command}",
        )
    if require_first and not content.lstrip().startswith(BEGIN):
        return AdapterDiagnosticCheck(
            name=check_name,
            status="error",
            detail=f"hub awareness block is present but not first in {path}",
            fix=f"run: {install_command}",
        )
    block = content[content.index(BEGIN):_block_end(content, content.index(BEGIN))]
    required = ("Awareness Channel", "search_memory", "write_memory", str(brain_dir))
    missing = [text for text in required if text not in block]
    if missing:
        return AdapterDiagnosticCheck(
            name=check_name,
            status="error",
            detail=f"awareness block missing required text: {', '.join(missing)}",
            fix=f"run: {install_command}",
        )
    return AdapterDiagnosticCheck(
        name=check_name,
        status="ok",
        detail=f"hub awareness block present in {path}",
    )


def _block_end(content: str, start: int) -> int:
    end_idx = content.find(END, start)
    if end_idx == -1:
        return len(content)
    return end_idx + len(END)


def _prepend_block(block: str, before: str, after: str) -> str:
    preserved = before.rstrip("\n")
    trailing = after.lstrip("\n")
    if preserved and trailing:
        preserved = f"{preserved}\n{trailing}"
    else:
        preserved = preserved or trailing
    if preserved.strip():
        return f"{block}\n\n{preserved.rstrip()}\n"
    return f"{block}\n"


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _write_backup(path: Path, content: str) -> Path | None:
    if not _has_user_content(content):
        return None
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%fZ")
    backup = path.with_name(f"{path.name}.bak.amh-awareness.{timestamp}")
    suffix = 1
    while backup.exists():
        backup = path.with_name(f"{path.name}.bak.amh-awareness.{timestamp}.{suffix}")
        suffix += 1
    backup.write_text(content, encoding="utf-8")
    return backup


def _has_user_content(content: str) -> bool:
    if BEGIN not in content:
        return bool(content.strip())
    start = content.index(BEGIN)
    end = _block_end(content, start)
    outside_managed_block = content[:start] + content[end:]
    return bool(outside_managed_block.strip())
