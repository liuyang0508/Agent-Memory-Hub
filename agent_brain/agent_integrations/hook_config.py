"""Shared JSON and hook-command helpers for hook-based adapters."""
from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Literal

HOOK_COMMAND_PATH_VALUE = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
HOOK_COMMAND_PATH_PREFIX = (
    f"PATH={HOOK_COMMAND_PATH_VALUE}${{PATH:+:$PATH}}"
)
HOOK_COMMAND_FIXED_PATH_PREFIX = f"PATH={HOOK_COMMAND_PATH_VALUE}"
POSIX_PATH_EXPANSION = "${PATH:+:$PATH}"


def read_json_config(path: Path) -> dict:
    """Read an adapter JSON config, tolerating a leading UTF-8 BOM."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"refuse to overwrite malformed {path} — fix it by hand first: {exc}"
        ) from exc


def atomic_write_json(path: Path, data: dict) -> None:
    """Atomically write JSON to avoid half-written adapter settings files."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def hook_already_present(entries: list, hooks_dir_prefix: str) -> bool:
    """True if any existing entry has a command under the hub's hooks dir."""
    prefixes = hook_dir_aliases(hooks_dir_prefix)
    for entry in entries:
        for hook in entry.get("hooks", []):
            command = hook.get("command", "")
            if any(command_references_prefix(command, prefix) for prefix in prefixes):
                return True
    return False


def hook_belongs_to(entry: dict, hooks_dir_prefix: str) -> bool:
    """True if every hook in this entry belongs to the given hooks dir."""
    hooks = entry.get("hooks", [])
    if not hooks:
        return False
    prefixes = hook_dir_aliases(hooks_dir_prefix)
    return all(
        any(command_references_prefix(hook.get("command", ""), prefix) for prefix in prefixes)
        for hook in hooks
    )


def hook_script_present(entries: list, script_path: str) -> bool:
    """True if an adapter hook entry references a specific script path."""
    script_paths = hook_script_aliases(script_path)
    for entry in entries:
        for hook in entry.get("hooks", []):
            if any(command_references_path(hook.get("command", ""), path) for path in script_paths):
                return True
    return False


def update_hook_command(
    entries: list,
    script_path: str,
    expected_command: str,
    timeout: int | None = None,
) -> bool:
    """Update hook command fields for entries that already reference a script."""
    changed = False
    script_paths = hook_script_aliases(script_path)
    for entry in entries:
        for hook in entry.get("hooks", []):
            if not any(command_references_path(hook.get("command", ""), path) for path in script_paths):
                continue
            if hook.get("command") != expected_command:
                hook["command"] = expected_command
                changed = True
            if timeout is not None:
                if hook.get("timeout") != timeout:
                    hook["timeout"] = timeout
                    changed = True
                if "timeout_ms" in hook:
                    hook.pop("timeout_ms", None)
                    changed = True
    return changed


def adapter_hook_command(
    adapter: str,
    script: Path,
    *,
    extra_env: dict[str, str] | None = None,
    path_strategy: Literal["prepend", "fixed"] = "prepend",
) -> str:
    """Build the command stored in hook settings for a hub adapter script."""
    env = {"AGENT_MEMORY_HUB_ADAPTER": adapter}
    if extra_env:
        env.update(extra_env)
    env_parts = " ".join(f"{key}={shlex.quote(value)}" for key, value in env.items())
    path_prefix = (
        HOOK_COMMAND_FIXED_PATH_PREFIX if path_strategy == "fixed" else HOOK_COMMAND_PATH_PREFIX
    )
    return (
        f"{path_prefix} "
        f"{env_parts} "
        f"{shlex.quote(str(script))}"
    )


def hook_script_aliases(script_path: str) -> list[str]:
    """Return current and legacy paths that identify the same hub hook script."""
    aliases = [script_path]
    legacy = legacy_hook_path(script_path)
    if legacy and legacy not in aliases:
        aliases.append(legacy)
    return aliases


def hook_dir_aliases(hooks_dir_prefix: str) -> list[str]:
    """Return current and legacy hook directory prefixes for AMH-owned hooks."""
    prefixes = [hooks_dir_prefix]
    legacy = legacy_hook_path(hooks_dir_prefix)
    if legacy and legacy not in prefixes:
        prefixes.append(legacy)
    return prefixes


def legacy_hook_path(path: str) -> str | None:
    """Map the v1.1 runtime hook path back to the pre-rename brain hook path."""
    marker = "/agent_runtime_kit/hooks"
    if marker not in path:
        return None
    return path.replace(marker, "/brain/hooks", 1)


def command_references_path(command: str, path: str) -> bool:
    if command == path:
        return True
    return path in command_tokens(command)


def command_references_prefix(command: str, prefix: str) -> bool:
    if command.startswith(prefix):
        return True
    return any(token.startswith(prefix) for token in command_tokens(command))


def command_tokens(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


__all__ = [
    "adapter_hook_command",
    "atomic_write_json",
    "command_references_path",
    "command_references_prefix",
    "command_tokens",
    "HOOK_COMMAND_FIXED_PATH_PREFIX",
    "HOOK_COMMAND_PATH_PREFIX",
    "HOOK_COMMAND_PATH_VALUE",
    "hook_already_present",
    "hook_belongs_to",
    "hook_dir_aliases",
    "hook_script_present",
    "hook_script_aliases",
    "legacy_hook_path",
    "POSIX_PATH_EXPANSION",
    "read_json_config",
    "update_hook_command",
]
