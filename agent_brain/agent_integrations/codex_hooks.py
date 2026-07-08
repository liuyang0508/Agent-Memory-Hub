from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from .hook_config import (
    adapter_hook_command,
    atomic_write_json,
    command_references_path,
    command_references_prefix,
    hook_belongs_to,
    hook_dir_aliases,
    hook_script_aliases,
    hook_script_present,
    prune_duplicate_hub_hook_handlers,
    read_json_config,
    update_hook_command,
)
from .codex_hook_trust import sync_codex_hook_trust_state


HookScripts = Mapping[str, tuple[str, str]]


def install_hooks(
    hooks_json: Path,
    hooks_dir: Path,
    hook_scripts: HookScripts,
    config_toml: Path | None = None,
) -> bool:
    hooks_json.parent.mkdir(parents=True, exist_ok=True)
    data = read_json_config(hooks_json)
    hooks = data.setdefault("hooks", {})
    changed = False

    for event, (matcher, script_name) in hook_scripts.items():
        script = hooks_dir / script_name
        entries = hooks.setdefault(event, [])
        command = adapter_hook_command("codex", script)
        target = _matching_entry(entries, matcher)
        if target is None:
            target = {"hooks": []}
            if matcher:
                target["matcher"] = matcher
            entries.append(target)
            changed = True

        if hook_script_present([target], str(script)):
            changed = update_hook_command(
                [target],
                script_path=str(script),
                expected_command=command,
                timeout=10,
            ) or changed
        else:
            target.setdefault("hooks", []).insert(0, {
                "type": "command",
                "command": command,
                "timeout": 10,
            })
            changed = True

        if _move_script_hook_first(target, str(script)):
            changed = True

        if _remove_duplicate_script_hooks(entries, target, str(script)):
            changed = True

    if changed:
        atomic_write_json(hooks_json, data)
    if config_toml is not None:
        changed = sync_codex_hook_trust_state(
            config_toml=config_toml,
            hooks_json=hooks_json,
            hooks_data=data,
            hooks_dir=hooks_dir,
        ) or changed
    return changed


def uninstall_hooks(
    hooks_json: Path,
    hooks_dir: Path,
    hook_events: Iterable[str],
) -> bool:
    if not hooks_json.exists():
        return False
    data = read_json_config(hooks_json)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False

    changed = False
    for event in hook_events:
        entries = hooks.get(event, [])
        kept = _remove_hub_hooks_from_entries(entries, str(hooks_dir))
        if kept != entries:
            hooks[event] = kept
            changed = True

    if changed:
        atomic_write_json(hooks_json, data)
    return changed


def _remove_hub_hooks_from_entries(entries: list, hooks_dir_prefix: str) -> list:
    prefixes = hook_dir_aliases(hooks_dir_prefix)
    kept_entries: list = []
    for entry in entries:
        if not isinstance(entry, dict):
            kept_entries.append(entry)
            continue
        hooks = entry.get("hooks", [])
        if not isinstance(hooks, list):
            kept_entries.append(entry)
            continue
        if hook_belongs_to(entry, hooks_dir_prefix):
            continue
        filtered_hooks = [
            hook
            for hook in hooks
            if not isinstance(hook, dict)
            or not any(
                command_references_prefix(hook.get("command", ""), prefix)
                for prefix in prefixes
            )
        ]
        if filtered_hooks:
            updated_entry = dict(entry)
            updated_entry["hooks"] = filtered_hooks
            kept_entries.append(updated_entry)
    return kept_entries


def _matching_entry(entries: list, matcher: str) -> dict | None:
    expected = matcher or ""
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if (entry.get("matcher") or "") == expected:
            return entry
    return None


def _remove_duplicate_script_hooks(entries: list, target: dict, script_path: str) -> bool:
    return prune_duplicate_hub_hook_handlers(entries, script_path, keep_entry=target)


def _move_script_hook_first(entry: dict, script_path: str) -> bool:
    hooks = entry.get("hooks", [])
    if not isinstance(hooks, list):
        return False
    script_paths = hook_script_aliases(script_path)
    for index, hook in enumerate(hooks):
        if not isinstance(hook, dict):
            continue
        command = hook.get("command", "")
        if not any(command_references_path(command, path) for path in script_paths):
            continue
        if index == 0:
            return False
        hooks.insert(0, hooks.pop(index))
        return True
    return False


__all__ = ["install_hooks", "uninstall_hooks"]
