"""Pure config-editing helpers for the Codex adapter.

This module has no adapter registry side effects and does not know about
``CodexAdapter``. It only owns sentinel blocks, hooks JSON predicates, and the
Codex MCP TOML section editing used by install/uninstall/doctor.
"""

from __future__ import annotations

import json
from pathlib import Path

from .codex_hook_commands import (
    command_references_path,  # noqa: F401 - re-exported for split-module compatibility
    command_references_prefix,  # noqa: F401 - re-exported for split-module compatibility
    hook_already_present,  # noqa: F401 - re-exported for split-module compatibility
    hook_belongs_to,  # noqa: F401 - re-exported for split-module compatibility
    hook_script_present,  # noqa: F401 - re-exported for split-module compatibility
    update_hook_command,  # noqa: F401 - re-exported for split-module compatibility
)


BEGIN = "<!-- BEGIN agent-memory-hub -->"
END = "<!-- END agent-memory-hub -->"
MCP_SECTION = "[mcp_servers.agent-memory-hub]"


def atomic_write_text(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def read_json_config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"refuse to overwrite malformed {path}: {exc}") from exc


def atomic_write_json(path: Path, data: dict) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def mcp_block(command: Path) -> str:
    return f'{MCP_SECTION}\ncommand = "{command}"\n'


def upsert_mcp_block(existing: str, block: str) -> str:
    start, end = mcp_section_bounds(existing)
    if start is None:
        prefix = existing.rstrip()
        if prefix:
            prefix += "\n\n"
        return prefix + block
    prefix = existing[:start].rstrip()
    suffix = existing[end:].lstrip("\n")
    parts = []
    if prefix:
        parts.append(prefix)
    parts.append(block.rstrip())
    if suffix:
        parts.append(suffix.rstrip())
    return "\n\n".join(parts) + "\n"


def remove_mcp_block(existing: str) -> str:
    start, end = mcp_section_bounds(existing)
    if start is None:
        return existing
    prefix = existing[:start].rstrip()
    suffix = existing[end:].lstrip("\n")
    if prefix and suffix:
        return prefix + "\n\n" + suffix
    if prefix:
        return prefix + "\n"
    return suffix


def mcp_section_bounds(existing: str) -> tuple[int | None, int | None]:
    lines = existing.splitlines(keepends=True)
    start_line: int | None = None
    offset = 0
    start_offset = 0
    for idx, line in enumerate(lines):
        if line.strip() == MCP_SECTION:
            start_line = idx
            start_offset = offset
            break
        offset += len(line)
    if start_line is None:
        return None, None

    end_offset = len(existing)
    offset = start_offset + len(lines[start_line])
    for line in lines[start_line + 1:]:
        if line.lstrip().startswith("["):
            end_offset = offset
            break
        offset += len(line)
    return start_offset, end_offset


def block_end(existing: str, start: int) -> int:
    """Index just past the END sentinel for the block starting at ``start``.
    If END is missing (a truncated / corrupted block) treat the rest of the
    file as the block so install / uninstall recover instead of crashing."""
    end_idx = existing.find(END, start)
    if end_idx == -1:
        return len(existing)
    return end_idx + len(END)


def upsert_block(existing: str, block: str) -> tuple[str, str]:
    """Insert block at end if not present, or replace existing bracketed block.
    Returns (new_content, action_taken) where action is 'installed' or 'updated'."""
    if BEGIN not in existing:
        if existing and not existing.endswith("\n"):
            existing += "\n"
        if existing:
            existing += "\n"
        return existing + block, "installed"
    start = existing.index(BEGIN)
    end_marker = block_end(existing, start)
    suffix = existing[end_marker:]
    if not suffix.startswith("\n"):
        suffix = "\n" + suffix
    return existing[:start] + block.rstrip() + suffix, "updated"


def remove_block(existing: str) -> str:
    if BEGIN not in existing:
        return existing
    start = existing.index(BEGIN)
    end_marker = block_end(existing, start)
    suffix = existing[end_marker:]
    if suffix.startswith("\n"):
        suffix = suffix[1:]
    prefix = existing[:start].rstrip() + ("\n" if existing[:start].rstrip() else "")
    return prefix + suffix


__all__ = [
    "atomic_write_json",
    "atomic_write_text",
    "block_end",
    "command_references_path",
    "command_references_prefix",
    "hook_already_present",
    "hook_belongs_to",
    "hook_script_present",
    "mcp_block",
    "mcp_section_bounds",
    "read_json_config",
    "remove_block",
    "remove_mcp_block",
    "update_hook_command",
    "upsert_block",
    "upsert_mcp_block",
]
