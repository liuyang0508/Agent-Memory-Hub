"""Local profile policy. Recall never falls back to another organization."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def recall_allowed(state: Path, config: dict[str, Any] | None = None) -> bool:
    """Re-read on every request, including long-lived clients and MCP researches.

    Old profiles default on. A malformed/missing bound parent fails closed.
    The public parent binding also gates host-bound Cowork's separate identity.
    """
    try:
        path = Path(state) / "config.json"
        if not path.exists() and config and config.get("hub"):
            return False
        current = json.loads(path.read_text()) if path.exists() else (config or {})
        if current.get("recall_enabled", True) is not True or current.get("sync_only", False):
            return False
        binding = Path(state) / "recall-parent.json"
        if binding.exists():
            bound = json.loads(binding.read_text())
            parent = Path(bound["state"])
            parent_config = json.loads((parent / "config.json").read_text())
            if bound.get("pin") != organization_pin(parent_config):
                return False
            if current and organization_pin(current) != bound["pin"]:
                return False
            return (
                parent_config.get("recall_enabled", True) is True
                and not parent_config.get("sync_only", False)
                and not (parent / "paused").exists()
            )
        return True
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return False


def organization_pin(config: dict[str, Any]) -> dict[str, str]:
    return {
        "hub": config["hub"],
        **{key: config["identity"][key] for key in ("tenant_id", "member_id")},
    }


def profile_pin(config: dict[str, Any]) -> dict[str, str]:
    identity = config["identity"]
    return {
        "hub": config["hub"],
        **{key: identity[key] for key in ("tenant_id", "member_id", "device_id")},
    }


def observe_backups(
    client: Any, product: str, payload: dict[str, Any], session: str, epoch: str
) -> None:
    """Queue at most four explicit peers; no network, memory reads, DB, or bodies.

    Only metadata of witnessed successful creates is retained. Background workers
    baseline roots independently. Queues coalesce and are capped at 1024 files.
    """
    import itertools
    from .common import atomic_json, digest

    if product != "claude-code":
        return  # Codex roots are fixed; Cowork requires its separate runtime.
    event = payload.get("hook_event_name")
    observation: dict[str, Any] = {"hook_event_name": event, "session": session, "epoch": epoch}
    if event == "PostToolUse":
        inputs, result = payload.get("tool_input", {}), payload.get("tool_response", {})
        if (
            payload.get("tool_name") != "Write"
            or not isinstance(inputs, dict)
            or not isinstance(result, dict)
            or result.get("type") != "create"
            or result.get("error")
            or result.get("is_error")
        ):
            return
        value = inputs.get("file_path")
        if not isinstance(value, str):
            return
        path = Path(value)
        if not path.is_absolute():
            path = Path(str(payload.get("cwd", "."))) / path
        if path.is_symlink():
            return
        try:
            path = path.resolve()
            stat = path.stat()
        except OSError:
            return
        observation.update(
            path=str(path),
            identity=[stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns],
        )
    elif event != "SessionStart":
        return
    key = digest(str(observation.get("path", "SessionStart")))
    for peer in client.config.get("observation_targets", [])[:4]:
        try:
            state = Path(peer["state"])
            config = json.loads((state / "config.json").read_text())
            if profile_pin(config) != peer["pin"] or recall_allowed(state, config):
                continue
            if (state / "paused").exists():
                continue
            if event == "PostToolUse":
                allowed = False
                for watcher in config.get("discovery_roots", []):
                    base = Path(watcher["root"]).resolve()
                    if watcher["product"] != "claude-code" or not path.is_relative_to(base):
                        continue
                    relative = path.relative_to(base)
                    allowed |= (
                        len(relative.parts) >= 3
                        and relative.parts[1] == "memory"
                        and path.suffix == ".md"
                    )
                if not allowed:
                    continue
            folder = state / "source-observations"
            if folder.is_symlink():
                continue
            folder.mkdir(exist_ok=True, mode=0o700)
            target = folder / (key + ".json")
            if (
                not target.exists()
                and sum(1 for _ in itertools.islice(folder.iterdir(), 1024)) >= 1024
            ):
                continue
            atomic_json(target, observation)
            (state / "wake").touch(exist_ok=True)
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue


def process_source_observations(client: Any) -> list[str]:
    """Worker-only discovery; batch witnessed creates before baselining each root."""
    from .discovery import observe
    from .source import read_document
    from .storage import database

    folder = client.root / "source-observations"
    if not folder.is_dir() or folder.is_symlink():
        return []
    events = []
    errors = []
    fresh_roots: dict[Path, set[str]] = {}
    client.refresh_excluded_history()
    watchers = [
        Path(r["root"]).resolve()
        for r in client.config.get("discovery_roots", [])
        if r["product"] == "claude-code"
    ]
    for path in sorted(folder.glob("*.json"))[:1024]:
        try:
            if path.is_symlink() or path.stat().st_size > 16384:
                raise ValueError("invalid observation")
            raw = path.read_bytes()
            item = json.loads(raw)
            if not isinstance(item, dict):
                raise ValueError("invalid observation")
            event = item["hook_event_name"]
            if event == "PostToolUse":
                source = Path(item["path"])
                stat = source.stat()
                if source.is_symlink() or item["identity"] != [
                    stat.st_dev,
                    stat.st_ino,
                    stat.st_size,
                    stat.st_mtime_ns,
                    stat.st_ctime_ns,
                ]:
                    raise ValueError("witnessed create changed; history review required")
                source = source.resolve()
                for watcher in watchers:
                    if not source.is_relative_to(watcher):
                        continue
                    relative = source.relative_to(watcher)
                    if (
                        len(relative.parts) < 3
                        or relative.parts[1] != "memory"
                        or source.suffix != ".md"
                    ):
                        continue
                    root = watcher / relative.parts[0] / "memory"
                    document = read_document(source, "memory")
                    with database(client.path) as db:
                        denied = (
                            document
                            and db.execute(
                                "SELECT 1 FROM history_hashes WHERE hash=?", (document[2],)
                            ).fetchone()
                        )
                    if document and not denied:
                        fresh_roots.setdefault(root, set()).add(str(source.relative_to(root)))
            elif event != "SessionStart":
                raise ValueError("invalid source event")
            events.append((path, raw, item))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            errors.append("source observation: " + str(exc))
    for root, fresh in fresh_roots.items():
        client.register(root, "claude-code", "", "memory", fresh_files=fresh)
    for path, raw, item in events:
        try:
            if item["hook_event_name"] == "SessionStart":
                observe(client, "claude-code", item, item["session"], item["epoch"])
            if path.read_bytes() == raw:
                path.unlink()
        except (OSError, ValueError, TypeError, KeyError) as exc:
            errors.append("source observation: " + str(exc))
    return errors
