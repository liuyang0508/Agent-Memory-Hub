"""Qoder adapter diagnostic helpers."""

from __future__ import annotations

import os
from pathlib import Path

from .codex_config import (
    command_references_path,
    read_json_config,
)
from .diagnostics import AdapterDiagnosticCheck
from .hook_config import command_references_hub_hook_script


def diagnose_settings_hooks(
    *,
    settings_path: Path,
    hooks_dir: Path,
    hook_events: tuple[str, ...],
    hook_scripts: dict[str, str],
    expected_commands: dict[str, str],
    adapter_label: str = "Qoder",
    install_command: str = "run: memory adapter install qoder",
) -> AdapterDiagnosticCheck:
    """Check a Qoder-compatible settings.json contains hub-owned hook entries."""
    check_name = f"{adapter_label} settings hooks"
    if not settings_path.exists():
        return AdapterDiagnosticCheck(
            name=check_name,
            status="error",
            detail=f"missing: {settings_path}",
            fix=install_command,
        )
    try:
        settings = read_json_config(settings_path)
    except RuntimeError as exc:
        return AdapterDiagnosticCheck(
            name=check_name,
            status="error",
            detail=str(exc),
            fix=f"repair JSON by hand, then {install_command}",
        )

    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return AdapterDiagnosticCheck(
            name=check_name,
            status="error",
            detail="missing top-level hooks object",
            fix=install_command,
        )

    managed_script_names = frozenset(hook_scripts.values())
    problems: list[str] = []
    for event in hook_events:
        script = hooks_dir / hook_scripts[event]
        entries = hooks.get(event, [])
        if not isinstance(entries, list):
            problems.append(f"{event}: hooks must be a list")
            continue
        managed: list[str] = []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
                continue
            for hook in entry["hooks"]:
                if not isinstance(hook, dict) or not isinstance(hook.get("command"), str):
                    continue
                command = hook["command"]
                if any(
                    command_references_hub_hook_script(command, name)
                    for name in managed_script_names
                ):
                    managed.append(command)
        if len(managed) != 1:
            problems.append(
                f"{event}: expected exactly 1 managed handler, found {len(managed)}"
            )
            continue
        command = managed[0]
        if not command_references_path(command, str(script)):
            problems.append(f"{event}: managed handler points to the wrong script")
        elif command != expected_commands[event]:
            problems.append(
                f"{event}: managed handler command does not match the canonical command"
            )

    if problems:
        return AdapterDiagnosticCheck(
            name=check_name,
            status="error",
            detail="; ".join(problems),
            fix=install_command,
        )
    return AdapterDiagnosticCheck(
        name=check_name,
        status="ok",
        detail=f"hub hooks present for {', '.join(hook_events)}",
    )


def diagnose_hook_scripts(
    *,
    hooks_dir: Path,
    hook_scripts: dict[str, str],
    adapter_label: str = "Qoder",
) -> AdapterDiagnosticCheck:
    """Check the hook scripts referenced by the Qoder adapter exist."""
    missing = [
        str(hooks_dir / script)
        for script in hook_scripts.values()
        if not (hooks_dir / script).exists()
    ]
    not_executable = [
        str(hooks_dir / script)
        for script in hook_scripts.values()
        if (hooks_dir / script).exists() and not os.access(hooks_dir / script, os.X_OK)
    ]
    if missing or not_executable:
        problems: list[str] = []
        if missing:
            problems.append(f"missing script(s): {', '.join(missing)}")
        if not_executable:
            problems.append(f"not executable: {', '.join(not_executable)}")
        return AdapterDiagnosticCheck(
            name=f"{adapter_label} hook scripts",
            status="error",
            detail="; ".join(problems),
            fix="restore the agent-memory-hub checkout or reinstall from source",
        )
    return AdapterDiagnosticCheck(
        name=f"{adapter_label} hook scripts",
        status="ok",
        detail=f"all hook scripts found under {hooks_dir}",
    )


__all__ = ["diagnose_hook_scripts", "diagnose_settings_hooks"]
