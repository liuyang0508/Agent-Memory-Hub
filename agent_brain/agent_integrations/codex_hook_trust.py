"""Codex hook trust-state helpers.

Codex persists trust per hook position under ``[hooks.state]`` in
``config.toml``. The hash format mirrors OpenAI Codex's
``command_hook_hash`` implementation: normalized hook identity -> canonical
JSON -> SHA-256.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .codex_config import atomic_write_text
from .hook_config import (
    command_references_prefix,
    hook_dir_aliases,
)


EVENT_KEY_LABELS = {
    "PreToolUse": "pre_tool_use",
    "PermissionRequest": "permission_request",
    "PostToolUse": "post_tool_use",
    "PreCompact": "pre_compact",
    "PostCompact": "post_compact",
    "SessionStart": "session_start",
    "UserPromptSubmit": "user_prompt_submit",
    "SubagentStart": "subagent_start",
    "SubagentStop": "subagent_stop",
    "Stop": "stop",
}

EVENTS_WITH_MATCHERS = {
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "PreCompact",
    "PostCompact",
    "SessionStart",
    "SubagentStart",
    "SubagentStop",
}

_STATE_HEADER_RE = re.compile(r'(?m)^\[hooks\.state\."([^"]+)"\]\s*$')
_TRUSTED_HASH_RE = re.compile(r'(?m)^trusted_hash\s*=\s*"([^"]+)"\s*$')


def codex_hook_state_key(
    hooks_json: Path,
    event: str,
    group_index: int,
    handler_index: int,
) -> str:
    return f"{hooks_json}:{_event_key_label(event)}:{group_index}:{handler_index}"


def codex_command_hook_hash(
    event: str,
    *,
    matcher: str | None,
    hook: dict[str, Any],
) -> str:
    """Return the current Codex trust hash for one command hook."""
    timeout = hook.get("timeout")
    if not isinstance(timeout, int):
        timeout = 600
    timeout = max(timeout, 1)
    handler = {
        "async": bool(hook.get("async", False)),
        "command": str(hook.get("command", "")),
        "timeout": timeout,
        "type": "command",
    }
    status_message = hook.get("statusMessage")
    if status_message is not None:
        handler["statusMessage"] = status_message
    identity: dict[str, Any] = {
        "event_name": _event_key_label(event),
        "hooks": [handler],
    }
    normalized_matcher = _normalized_matcher(event, matcher)
    if normalized_matcher is not None:
        identity["matcher"] = normalized_matcher
    serialized = json.dumps(
        _canonical(identity),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(serialized).hexdigest()


def sync_codex_hook_trust_state(
    *,
    config_toml: Path,
    hooks_json: Path,
    hooks_data: dict[str, Any],
    hooks_dir: Path,
) -> bool:
    """Trust AMH hooks and preserve already-trusted non-AMH hooks after moves."""
    content = config_toml.read_text(encoding="utf-8") if config_toml.exists() else ""
    existing_hashes = set(trusted_hashes(content).values())
    updates = trust_updates_for_hooks(
        hooks_json=hooks_json,
        hooks_data=hooks_data,
        hooks_dir=hooks_dir,
        existing_trusted_hashes=existing_hashes,
    )
    if not updates:
        return False
    current_state = trusted_hashes(content)
    if all(current_state.get(key) == trusted_hash for key, trusted_hash in updates.items()):
        return False
    updated = upsert_trusted_hashes(content, updates)
    if updated == content:
        return False
    config_toml.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(config_toml, updated)
    return True


def trust_updates_for_hooks(
    *,
    hooks_json: Path,
    hooks_data: dict[str, Any],
    hooks_dir: Path,
    existing_trusted_hashes: set[str],
) -> dict[str, str]:
    updates: dict[str, str] = {}
    hooks = hooks_data.get("hooks")
    if not isinstance(hooks, dict):
        return updates
    for event, entries in hooks.items():
        if event not in EVENT_KEY_LABELS or not isinstance(entries, list):
            continue
        for group_index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            matcher = entry.get("matcher") if isinstance(entry.get("matcher"), str) else None
            hook_list = entry.get("hooks", [])
            if not isinstance(hook_list, list):
                continue
            for handler_index, hook in enumerate(hook_list):
                if not isinstance(hook, dict) or hook.get("type") != "command":
                    continue
                command = str(hook.get("command", ""))
                current_hash = codex_command_hook_hash(event, matcher=matcher, hook=hook)
                should_trust = (
                    _command_belongs_to_hub(command, hooks_dir)
                    or current_hash in existing_trusted_hashes
                )
                if not should_trust:
                    continue
                key = codex_hook_state_key(hooks_json, event, group_index, handler_index)
                updates[key] = current_hash
    return updates


def trusted_hashes(config_text: str) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for match in _STATE_HEADER_RE.finditer(config_text):
        key = match.group(1)
        _, end = _state_section_bounds(config_text, key, start=match.start())
        section = config_text[match.end():end]
        hash_match = _TRUSTED_HASH_RE.search(section)
        if hash_match:
            hashes[key] = hash_match.group(1)
    return hashes


def hub_hook_trust_mismatches(
    *,
    config_toml: Path,
    hooks_json: Path,
    hooks_data: dict[str, Any],
    hook_scripts: dict[str, tuple[str, str]],
    hooks_dir: Path,
) -> list[str]:
    config_text = config_toml.read_text(encoding="utf-8") if config_toml.exists() else ""
    current_state = trusted_hashes(config_text)
    mismatches: list[str] = []
    hooks = hooks_data.get("hooks")
    if not isinstance(hooks, dict):
        return list(hook_scripts)
    for event, (matcher, script_name) in hook_scripts.items():
        found = False
        entries = hooks.get(event, [])
        if not isinstance(entries, list):
            mismatches.append(event)
            continue
        for group_index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            if (entry.get("matcher") or "") != (matcher or ""):
                continue
            hook_list = entry.get("hooks", [])
            if not isinstance(hook_list, list):
                continue
            script_path = str(hooks_dir / script_name)
            for handler_index, hook in enumerate(hook_list):
                if not isinstance(hook, dict) or hook.get("type") != "command":
                    continue
                command = str(hook.get("command", ""))
                if not _command_references_script(command, script_path):
                    continue
                found = True
                key = codex_hook_state_key(hooks_json, event, group_index, handler_index)
                expected = codex_command_hook_hash(
                    event,
                    matcher=matcher,
                    hook=hook,
                )
                if current_state.get(key) != expected:
                    mismatches.append(event)
                break
        if not found:
            mismatches.append(event)
    return mismatches


def upsert_trusted_hashes(content: str, updates: dict[str, str]) -> str:
    updated = content
    for key, trusted_hash in sorted(updates.items()):
        block = f'[hooks.state."{key}"]\ntrusted_hash = "{trusted_hash}"\n\n'
        bounds = _state_section_bounds(updated, key)
        if bounds is None:
            if updated and not updated.endswith("\n"):
                updated += "\n"
            if "[hooks.state]" not in updated:
                updated += "\n[hooks.state]\n"
            if updated and not updated.endswith("\n\n"):
                updated += "\n"
            updated += block
            continue
        start, end = bounds
        updated = updated[:start] + block + updated[end:].lstrip("\n")
    return updated


def _state_section_bounds(
    content: str,
    key: str,
    *,
    start: int | None = None,
) -> tuple[int, int] | None:
    if start is None:
        pattern = re.compile(rf'(?m)^\[hooks\.state\. "{re.escape(key)}"\]\s*$')
        match = pattern.search(content)
        if match is None:
            pattern = re.compile(rf'(?m)^\[hooks\.state\."{re.escape(key)}"\]\s*$')
            match = pattern.search(content)
        if match is None:
            return None
        start = match.start()
    next_match = re.search(r"(?m)^\[", content[start + 1:])
    end = len(content) if next_match is None else start + 1 + next_match.start()
    return start, end


def _command_belongs_to_hub(command: str, hooks_dir: Path) -> bool:
    return any(command_references_prefix(command, prefix) for prefix in hook_dir_aliases(str(hooks_dir)))


def _command_references_script(command: str, script_path: str) -> bool:
    from .hook_config import command_references_path, hook_script_aliases

    return any(command_references_path(command, path) for path in hook_script_aliases(script_path))


def _event_key_label(event: str) -> str:
    try:
        return EVENT_KEY_LABELS[event]
    except KeyError as exc:
        raise ValueError(f"unsupported Codex hook event: {event}") from exc


def _normalized_matcher(event: str, matcher: str | None) -> str | None:
    if event not in EVENTS_WITH_MATCHERS:
        return None
    return matcher or None


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


__all__ = [
    "codex_command_hook_hash",
    "codex_hook_state_key",
    "hub_hook_trust_mismatches",
    "sync_codex_hook_trust_state",
    "trusted_hashes",
    "trust_updates_for_hooks",
    "upsert_trusted_hashes",
]
