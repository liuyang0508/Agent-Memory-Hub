"""Codex hook diagnostic checks."""

from __future__ import annotations

from pathlib import Path

from .codex_config import read_json_config
from .codex_hook_trust import hub_hook_trust_mismatches
from .diagnostics import AdapterDiagnosticCheck
from .hook_config import command_references_path, hook_script_aliases


def diagnose_hooks_json(
    hooks_json: Path,
    hook_events: tuple[str, ...],
    hook_scripts: dict[str, tuple[str, str]],
    hooks_dir: Path,
    config_toml: Path | None = None,
) -> AdapterDiagnosticCheck:
    if not hooks_json.exists():
        return AdapterDiagnosticCheck(
            name="Codex hooks.json",
            status="error",
            detail=f"missing: {hooks_json}",
            fix="run: memory adapter install codex",
        )
    try:
        data = read_json_config(hooks_json)
    except RuntimeError as exc:
        return AdapterDiagnosticCheck(
            name="Codex hooks.json",
            status="error",
            detail=str(exc),
            fix="repair JSON by hand, then run: memory adapter install codex",
        )
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return AdapterDiagnosticCheck(
            name="Codex hooks.json",
            status="error",
            detail="missing top-level hooks object",
            fix="run: memory adapter install codex",
        )

    missing: list[str] = []
    shadowed: list[str] = []
    not_first: list[str] = []
    legacy_timeout: list[str] = []
    for event in hook_events:
        matcher, script_name = hook_scripts[event]
        script = hooks_dir / script_name
        entries = hooks.get(event, [])
        if not _hook_script_present(entries, str(script)):
            missing.append(event)
            continue
        if not _hook_in_matching_entry(entries, matcher, str(script)):
            shadowed.append(event)
        elif not _hook_first_in_matching_entry(entries, matcher, str(script)):
            not_first.append(event)
        if _hook_uses_legacy_timeout(entries, str(script)):
            legacy_timeout.append(event)
    if missing:
        return AdapterDiagnosticCheck(
            name="Codex hooks.json",
            status="error",
            detail=f"missing hub hook event(s): {', '.join(missing)}",
            fix="run: memory adapter install codex",
        )
    if shadowed:
        return AdapterDiagnosticCheck(
            name="Codex hooks.json",
            status="error",
            detail=(
                "hub hook shadowed by an earlier matching Codex hook entry for event(s): "
                f"{', '.join(shadowed)}"
            ),
            fix="run: memory adapter install codex",
        )
    if not_first:
        return AdapterDiagnosticCheck(
            name="Codex hooks.json",
            status="error",
            detail=(
                "hub hook is not first in matching Codex hook entry for event(s): "
                f"{', '.join(not_first)}"
            ),
            fix="run: memory adapter install codex",
        )
    if legacy_timeout:
        return AdapterDiagnosticCheck(
            name="Codex hooks.json",
            status="error",
            detail=(
                "hub hook uses legacy timeout_ms instead of Codex timeout for event(s): "
                f"{', '.join(legacy_timeout)}"
            ),
            fix="run: memory adapter install codex",
        )
    if config_toml is not None:
        untrusted = hub_hook_trust_mismatches(
            config_toml=config_toml,
            hooks_json=hooks_json,
            hooks_data=data,
            hook_scripts=hook_scripts,
            hooks_dir=hooks_dir,
        )
        if untrusted:
            return AdapterDiagnosticCheck(
                name="Codex hooks.json",
                status="error",
                detail=f"hub hook is not trusted for event(s): {', '.join(untrusted)}",
                fix="run: memory adapter install codex",
            )
    return AdapterDiagnosticCheck(
        name="Codex hooks.json",
        status="ok",
        detail=f"hub hooks present for {', '.join(hook_events)}",
    )


def diagnose_hook_scripts(
    hook_scripts: dict[str, tuple[str, str]],
    hooks_dir: Path,
) -> AdapterDiagnosticCheck:
    missing = [
        str(hooks_dir / script)
        for _, script in hook_scripts.values()
        if not (hooks_dir / script).exists()
    ]
    if missing:
        return AdapterDiagnosticCheck(
            name="Codex hook scripts",
            status="error",
            detail=f"missing script(s): {', '.join(missing)}",
            fix="restore the agent-memory-hub checkout or reinstall from source",
        )
    return AdapterDiagnosticCheck(
        name="Codex hook scripts",
        status="ok",
        detail=f"all hook scripts found under {hooks_dir}",
    )


def _hook_script_present(entries: list, script_path: str) -> bool:
    return any(_entry_has_script(entry, script_path) for entry in entries if isinstance(entry, dict))


def _hook_in_matching_entry(entries: list, matcher: str, script_path: str) -> bool:
    expected = matcher or ""
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if (entry.get("matcher") or "") != expected:
            continue
        return _entry_has_script(entry, script_path)
    return False


def _hook_first_in_matching_entry(entries: list, matcher: str, script_path: str) -> bool:
    expected = matcher or ""
    script_paths = hook_script_aliases(script_path)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if (entry.get("matcher") or "") != expected:
            continue
        hooks = entry.get("hooks", [])
        if not isinstance(hooks, list) or not hooks:
            return False
        first = hooks[0]
        if not isinstance(first, dict):
            return False
        command = first.get("command", "")
        return any(command_references_path(command, path) for path in script_paths)
    return False


def _entry_has_script(entry: dict, script_path: str) -> bool:
    script_paths = hook_script_aliases(script_path)
    hooks = entry.get("hooks", [])
    if not isinstance(hooks, list):
        return False
    for hook in hooks:
        if not isinstance(hook, dict):
            continue
        command = hook.get("command", "")
        if any(command_references_path(command, path) for path in script_paths):
            return True
    return False


def _hook_uses_legacy_timeout(entries: list, script_path: str) -> bool:
    script_paths = hook_script_aliases(script_path)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        hooks = entry.get("hooks", [])
        if not isinstance(hooks, list):
            continue
        for hook in hooks:
            if not isinstance(hook, dict):
                continue
            command = hook.get("command", "")
            if not any(command_references_path(command, path) for path in script_paths):
                continue
            if "timeout_ms" in hook:
                return True
    return False


__all__ = ["diagnose_hook_scripts", "diagnose_hooks_json"]
