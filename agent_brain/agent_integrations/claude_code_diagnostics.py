"""Claude Code adapter diagnostic checks."""

from __future__ import annotations

from pathlib import Path

from .diagnostics import AdapterDiagnosticCheck
from .hook_config import hook_script_present, read_json_config


def diagnose_settings_hooks(
    *,
    settings_path: Path,
    hooks_dir: Path,
    hook_events: tuple[str, ...],
    hook_scripts: dict[str, str],
) -> AdapterDiagnosticCheck:
    """Check that Claude Code settings.json contains all hub hook entries."""
    if not settings_path.exists():
        return AdapterDiagnosticCheck(
            name="Claude Code settings hooks",
            status="error",
            detail=f"missing: {settings_path}",
            fix="run: memory adapter install claude_code",
        )
    try:
        settings = read_json_config(settings_path)
    except RuntimeError as exc:
        return AdapterDiagnosticCheck(
            name="Claude Code settings hooks",
            status="error",
            detail=str(exc),
            fix="repair JSON by hand, then run: memory adapter install claude_code",
        )

    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return AdapterDiagnosticCheck(
            name="Claude Code settings hooks",
            status="error",
            detail="missing top-level hooks object",
            fix="run: memory adapter install claude_code",
        )

    missing: list[str] = []
    for event in hook_events:
        script = hooks_dir / hook_scripts[event]
        entries = hooks.get(event, [])
        if not hook_script_present(entries, str(script)):
            missing.append(event)

    if missing:
        return AdapterDiagnosticCheck(
            name="Claude Code settings hooks",
            status="error",
            detail=f"missing hub hook event(s): {', '.join(missing)}",
            fix="run: memory adapter install claude_code",
        )

    return AdapterDiagnosticCheck(
        name="Claude Code settings hooks",
        status="ok",
        detail=f"hub hooks present for {', '.join(hook_events)}",
    )


def diagnose_hook_scripts(
    *,
    hooks_dir: Path,
    hook_scripts: dict[str, str],
) -> AdapterDiagnosticCheck:
    """Check that all Claude Code hook script files are present."""
    missing = [
        str(hooks_dir / script)
        for script in hook_scripts.values()
        if not (hooks_dir / script).exists()
    ]
    if missing:
        return AdapterDiagnosticCheck(
            name="Claude Code hook scripts",
            status="error",
            detail=f"missing script(s): {', '.join(missing)}",
            fix="restore the agent-memory-hub checkout or reinstall from source",
        )
    return AdapterDiagnosticCheck(
        name="Claude Code hook scripts",
        status="ok",
        detail=f"all hook scripts found under {hooks_dir}",
    )


__all__ = ["diagnose_hook_scripts", "diagnose_settings_hooks"]
