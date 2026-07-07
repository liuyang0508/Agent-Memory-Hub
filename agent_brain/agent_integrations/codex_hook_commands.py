"""Codex hook command helper compatibility exports."""

from __future__ import annotations

from .hook_config import (
    command_references_path,
    command_references_prefix,
    hook_already_present,
    hook_belongs_to,
    hook_script_present,
    update_hook_command,
)


__all__ = [
    "command_references_path",
    "command_references_prefix",
    "hook_already_present",
    "hook_belongs_to",
    "hook_script_present",
    "update_hook_command",
]
