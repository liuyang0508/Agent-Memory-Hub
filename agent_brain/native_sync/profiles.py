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
    """Worker-only discovery; permanent invalid creates become excluded review entries.

    Transient filesystem/database failures leave the original event retryable.
    Terminal events leave the bounded active queue only after denial metadata is
    durable. Fresh sibling creates are registered together before denials.
    """
    import sqlite3
    import uuid
    from .common import atomic_json
    from .discovery import observe
    from .source import read_document
    from .storage import database

    folder = client.root / "source-observations"
    if not folder.is_dir() or folder.is_symlink():
        return []
    events = []
    rejected = []
    errors = []
    fresh_roots: dict[Path, set[str]] = {}
    client.refresh_excluded_history()
    watchers = [
        Path(r["root"]).resolve()
        for r in client.config.get("discovery_roots", [])
        if r["product"] == "claude-code"
    ]

    def location(source: Path) -> tuple[Path, str] | None:
        for watcher in watchers:
            if not source.is_absolute() or not source.is_relative_to(watcher):
                continue
            relative = source.relative_to(watcher)
            if ".." in relative.parts or not source.resolve().is_relative_to(watcher):
                continue
            if (
                len(relative.parts) >= 3
                and relative.parts[1] == "memory"
                and source.suffix == ".md"
            ):
                root = watcher / relative.parts[0] / "memory"
                return root, str(source.relative_to(root))
        return None

    for path in sorted(folder.glob("*.json"))[:1024]:
        raw = None
        source = None
        try:
            if path.is_symlink():
                raise ValueError("invalid observation symlink")
            raw = path.read_bytes()
            if len(raw) > 16384:
                raise ValueError("invalid observation size")
            item = json.loads(raw)
            if not isinstance(item, dict) or any(
                not isinstance(item.get(key), str) for key in ("session", "epoch")
            ):
                raise ValueError("invalid observation")
            event = item["hook_event_name"]
            if event == "PostToolUse":
                source = Path(item["path"])
                scoped = location(source)
                if scoped is None:
                    raise ValueError("observation outside authorized roots")
                stat = source.stat()
                if source.is_symlink() or item["identity"] != [
                    stat.st_dev,
                    stat.st_ino,
                    stat.st_size,
                    stat.st_mtime_ns,
                    stat.st_ctime_ns,
                ]:
                    raise ValueError("witnessed create changed; history review required")
                root, relative = scoped
                document = read_document(source, "memory")
                with database(client.path) as db:
                    denied = (
                        document
                        and db.execute(
                            "SELECT 1 FROM history_hashes WHERE hash=?", (document[2],)
                        ).fetchone()
                    )
                if document and not denied:
                    fresh_roots.setdefault(root, set()).add(relative)
            elif event != "SessionStart":
                raise ValueError("invalid source event")
            events.append((path, raw, item))
        except (FileNotFoundError, ValueError, TypeError, KeyError) as exc:
            rejected.append((path, raw, source, str(exc)))
        except (OSError, sqlite3.Error) as exc:
            errors.append("source observation retry: " + type(exc).__name__)
    for root, fresh in fresh_roots.items():
        client.register(root, "claude-code", "", "memory", fresh_files=fresh)
    for path, raw, source, reason in rejected:
        try:
            scoped = location(source) if source is not None else None
            if scoped:
                root, relative = scoped
                if root.is_dir() and not root.is_symlink():
                    sid = client.register(root, "claude-code", "", "memory")
                else:
                    # Missing/replaced root is not evidence of a new empty source.
                    # Quarantine it until independent review, retaining path denial.
                    with database(client.path) as db:
                        row = db.execute(
                            "SELECT id FROM sources WHERE root=? AND kind='memory'", (str(root),)
                        ).fetchone()
                        sid = row["id"] if row else "src_" + uuid.uuid4().hex
                        if not row:
                            db.execute(
                                "INSERT INTO sources VALUES(?,?,?,?,?,?)",
                                (sid, str(root), "claude-code", "", "memory", 0),
                            )
                        db.execute(
                            "INSERT OR REPLACE INTO meta VALUES(?,?)",
                            (
                                "quarantined-source:" + sid,
                                "invalid observed source requires review",
                            ),
                        )
                with database(client.path) as db:
                    db.execute(
                        "INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?,?,?,?)",
                        (sid, relative, "", 0, "asset_" + uuid.uuid4().hex, 0, 1),
                    )
                    db.execute(
                        "INSERT OR IGNORE INTO aliases VALUES(?,?,?,?)",
                        (sid, relative, "[]", "needs-history-review"),
                    )
            # Metadata-only review marker replaces an unusable active event. Do
            # not copy raw corrupt payloads; they need not obey our event schema.
            review = client.root / "source-observation-review"
            atomic_json(
                review / path.name,
                {"path": str(source) if scoped else None, "reason": reason[:500]},
            )
            if path.is_symlink():
                path.unlink()
            elif raw is not None and path.exists() and path.read_bytes() == raw:
                path.unlink()
            errors.append("source observation needs history review: " + path.name)
        except (OSError, sqlite3.Error) as exc:
            errors.append("source observation retry: " + type(exc).__name__)
    for path, raw, item in events:
        try:
            if item["hook_event_name"] == "SessionStart":
                observe(client, "claude-code", item, item["session"], item["epoch"])
            if path.read_bytes() == raw:
                path.unlink()
        except (OSError, ValueError, TypeError, KeyError) as exc:
            errors.append("source observation: " + str(exc))
    return errors
