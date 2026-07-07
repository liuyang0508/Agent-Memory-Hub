"""Qoder adapter diagnostic helpers."""

from __future__ import annotations

from pathlib import Path

from .codex_config import (
    hook_script_present,
    read_json_config,
)
from .diagnostics import AdapterDiagnosticCheck


def diagnose_settings_hooks(
    *,
    settings_path: Path,
    hooks_dir: Path,
    hook_events: tuple[str, ...],
    hook_scripts: dict[str, str],
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

    missing: list[str] = []
    for event in hook_events:
        script = hooks_dir / hook_scripts[event]
        entries = hooks.get(event, [])
        if not hook_script_present(entries, str(script)):
            missing.append(event)

    if missing:
        return AdapterDiagnosticCheck(
            name=check_name,
            status="error",
            detail=f"missing hub hook event(s): {', '.join(missing)}",
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
    if missing:
        return AdapterDiagnosticCheck(
            name=f"{adapter_label} hook scripts",
            status="error",
            detail=f"missing script(s): {', '.join(missing)}",
            fix="restore the agent-memory-hub checkout or reinstall from source",
        )
    return AdapterDiagnosticCheck(
        name=f"{adapter_label} hook scripts",
        status="ok",
        detail=f"all hook scripts found under {hooks_dir}",
    )


__all__ = ["diagnose_hook_scripts", "diagnose_settings_hooks"]
