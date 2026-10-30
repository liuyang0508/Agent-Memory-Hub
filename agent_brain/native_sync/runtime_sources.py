"""Durable runtime observations: Hooks record metadata; workers baseline native stores."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

from .common import atomic_json, digest
from .source import read_document
from .storage import database

if TYPE_CHECKING:
    from .client import NativeClient


def queue_observation(state: Path, payload: dict[str, Any]) -> None:
    """Persist a bounded root/create observation without reading any memory body or DB.

    This also runs before runtime enrollment, so the first successful Write event
    is not discarded while pairing is in flight. Filesystem identity and content
    digest are evidence, never an instruction to bypass existing history choices.
    """
    event = payload.get("hook_event_name")
    value = os.environ.get("CLAUDE_COWORK_MEMORY_PATH_OVERRIDE")
    if not value or event not in ("SessionStart", "PostToolUse"):
        return
    root = Path(value).expanduser()
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        return
    root = root.resolve()
    root_stat = root.stat()
    observation: dict[str, Any] = {
        "root": str(root),
        "project": os.environ.get("CLAUDE_PROJECT_UUID", "")[:256],
        "event": event,
        "time": time.time(),
        "root_identity": [root_stat.st_dev, root_stat.st_ino],
    }
    relative = ""
    if event == "PostToolUse":
        result, inputs = payload.get("tool_response", {}), payload.get("tool_input", {})
        if (
            payload.get("tool_name") != "Write"
            or not isinstance(result, dict)
            or result.get("type") != "create"
            or result.get("error")
            or result.get("is_error")
            or not isinstance(inputs, dict)
            or not isinstance(inputs.get("file_path"), str)
        ):
            return
        path = Path(inputs["file_path"])
        if not path.is_absolute():
            path = Path(str(payload.get("cwd", "."))) / path
        if path.is_symlink():
            return
        path = path.resolve()
        if not path.is_relative_to(root) or path.suffix != ".md":
            return
        stat = path.stat()
        relative = str(path.relative_to(root))
        observation.update(
            path=relative,
            file_identity=[
                stat.st_dev,
                stat.st_ino,
                stat.st_size,
                stat.st_mtime_ns,
                stat.st_ctime_ns,
            ],
        )
        content = inputs.get("content")
        if isinstance(content, str):
            observation["content_hash"] = digest(content)
    folder = state / "runtime-observations"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    leases = state / "runtime-roots"
    leases.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_json(
        leases / (digest(str(root)) + ".json"),
        {k: observation[k] for k in ("root", "project", "root_identity", "time")},
    )
    key = digest(json.dumps([str(root), observation["project"], event, relative]))
    atomic_json(folder / (key + ".json"), observation)


def observe_native_root(
    client: NativeClient,
    payload: dict[str, Any],
    *,
    root_value: str | None = None,
    project_value: str | None = None,
    fresh_files: set[str] | None = None,
    root_identity: list[int] | None = None,
) -> None:
    """Worker-only binding; history scanning must never run in a timed Hook process.

    A stable project UUID may retarget a missing previous mount. A reused path
    with conflicting identity is quarantined, including its queued uploads and
    projections, rather than silently merging unrelated projects.
    """
    value = root_value or os.environ.get("CLAUDE_COWORK_MEMORY_PATH_OVERRIDE")
    if not value:
        return
    root = Path(value).expanduser()
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        return
    root = root.resolve()
    stat = root.stat()
    observed_identity = [stat.st_dev, stat.st_ino]
    if root_identity is not None and root_identity != observed_identity:
        raise ValueError("runtime mount changed before its observation was processed")
    project = (
        project_value if project_value is not None else os.environ.get("CLAUDE_PROJECT_UUID", "")
    )[:256]
    key = "cowork-project:" + project if project else "cowork-root:" + digest(str(root))
    conflict = False
    with database(client.path) as db:
        db.execute("BEGIN IMMEDIATE")
        occupied = db.execute(
            "SELECT * FROM sources WHERE root=? AND kind='memory'", (str(root),)
        ).fetchone()
        if occupied and (occupied["product"] != "cowork" or occupied["project"] != project):
            db.execute(
                "INSERT OR REPLACE INTO meta VALUES(?,?)",
                ("quarantined-source:" + occupied["id"], "runtime source identity changed"),
            )
            db.execute("DELETE FROM dynamic_targets WHERE root=?", (str(root),))
            conflict = True
        else:
            saved = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            old = (
                db.execute("SELECT * FROM sources WHERE id=?", (saved["value"],)).fetchone()
                if saved
                else None
            )
            if old and old["root"] != str(root):
                previous_root = Path(old["root"])
                if previous_root.exists() and not previous_root.samefile(root):
                    raise ValueError("another live mount exists for the same Cowork project")
                db.execute("UPDATE sources SET root=? WHERE id=?", (str(root), old["id"]))
                db.execute("DELETE FROM dynamic_targets WHERE root=?", (old["root"],))
    if conflict:
        # Raise only after the quarantine commits; raising inside the transaction
        # would undo the protection and let the next scan upload ambiguous data.
        raise ValueError("runtime source identity changed; source quarantined")
    sid = client.register(root, "cowork", project, "memory", fresh_files=fresh_files or set())
    if sid in client.quarantined_sources():
        raise ValueError("runtime source identity requires explicit rebind")
    with database(client.path) as db:
        db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, sid))
        db.execute(
            "INSERT OR REPLACE INTO meta VALUES(?,?)",
            (
                "cowork-binding:" + sid,
                json.dumps(
                    {"root": str(root), "project": project, "root_identity": observed_identity}
                ),
            ),
        )
        db.execute("INSERT OR IGNORE INTO dynamic_targets VALUES(?,?)", (str(root), "cowork"))


def quarantine_changed_roots(client: NativeClient) -> list[str]:
    """Recheck runtime identity at scan/upload, not only when processing discovery.

    Directory device/inode pinning catches a replaced mount before the next Hook;
    the latest Hook scope also detects a project switch at an unchanged directory.
    A missing mount alone does not mean its captured records were deleted.
    """
    errors = []
    with database(client.path) as db:
        rows = db.execute("SELECT key,value FROM meta WHERE key LIKE 'cowork-binding:%'").fetchall()
    for row in rows:
        sid = row["key"].removeprefix("cowork-binding:")
        expected = json.loads(row["value"])
        root = Path(expected["root"])
        reason = ""
        try:
            if root.exists():
                stat = root.stat()
                if root.is_symlink() or [stat.st_dev, stat.st_ino] != expected["root_identity"]:
                    reason = "runtime mount identity changed"
            lease = client.root / "runtime-roots" / (digest(str(root)) + ".json")
            if lease.exists():
                if lease.is_symlink() or lease.stat().st_size > 16384:
                    reason = "invalid runtime scope observation"
                else:
                    current = json.loads(lease.read_text())
                    if any(current.get(k) != expected[k] for k in ("project", "root_identity")):
                        reason = "runtime project or mount identity changed"
        except (OSError, ValueError):
            reason = "runtime mount identity unavailable"
        if reason:
            with database(client.path) as db:
                db.execute(
                    "INSERT OR REPLACE INTO meta VALUES(?,?)", ("quarantined-source:" + sid, reason)
                )
                db.execute("DELETE FROM dynamic_targets WHERE root=?", (str(root),))
            errors.append(reason + ": " + sid)
    return errors


def process_observations(client: NativeClient) -> list[str]:
    """Consume observations inside the sync worker lock, never inside the Hook budget.

    Only unchanged, witnessed creates may bypass the initial history baseline.
    Events arriving during a baseline remain on disk for the next pass. If their
    file was already excluded or changed before inspection, retain a visible
    history-review entry rather than silently importing it or losing the evidence.
    """
    folder = client.root / "runtime-observations"
    if not folder.is_dir() or folder.is_symlink():
        return []
    groups: dict[tuple[str, str], list[tuple[Path, bytes, dict[str, Any]]]] = {}
    errors = []
    for path in sorted(folder.glob("*.json")):
        if path.is_symlink():
            continue
        try:
            raw = path.read_bytes()
            if len(raw) > 16384:
                raise ValueError("runtime observation too large")
            item = json.loads(raw)
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("root"), str)
                or not isinstance(item.get("project"), str)
            ):
                raise ValueError("invalid runtime observation")
            groups.setdefault((item["root"], item["project"]), []).append((path, raw, item))
        except (OSError, ValueError):
            errors.append("invalid runtime observation: " + path.name)
    if not groups:
        return errors
    client.refresh_excluded_history()
    for (root_value, project), events in groups.items():
        root = Path(root_value)
        if not root.is_absolute() or not root.is_dir() or root.is_symlink():
            continue
        fresh: set[str] = set()
        witnessed: set[str] = set()
        for _, _, event in events:
            relative = event.get("path")
            if not isinstance(relative, str):
                continue
            path = root / relative
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                continue
            witnessed.add(relative)
            try:
                stat = path.stat()
                identity = [
                    stat.st_dev,
                    stat.st_ino,
                    stat.st_size,
                    stat.st_mtime_ns,
                    stat.st_ctime_ns,
                ]
                if identity != event.get("file_identity"):
                    continue
                document = read_document(path, "memory")
                with database(client.path) as db:
                    denied = (
                        document
                        and db.execute(
                            "SELECT 1 FROM history_hashes WHERE hash=?", (document[2],)
                        ).fetchone()
                    )
                if document and not denied:
                    fresh.add(relative)
            except (OSError, ValueError, UnicodeError):
                continue
        try:
            observe_native_root(
                client,
                {},
                root_value=root_value,
                project_value=project,
                fresh_files=fresh,
                root_identity=max(events, key=lambda e: e[2].get("time", 0))[2].get(
                    "root_identity"
                ),
            )
            with database(client.path) as db:
                source = db.execute(
                    "SELECT id FROM sources WHERE root=? AND kind='memory'", (str(root.resolve()),)
                ).fetchone()
                if source:
                    for relative in witnessed:
                        row = db.execute(
                            "SELECT selected FROM snapshots WHERE source=? AND path=?",
                            (source["id"], relative),
                        ).fetchone()
                        if row and not row["selected"]:
                            db.execute(
                                "INSERT OR IGNORE INTO aliases VALUES(?,?,?,?)",
                                (source["id"], relative, "[]", "needs-history-review"),
                            )
                            errors.append(
                                "runtime write needs history review: "
                                + source["id"]
                                + "/"
                                + relative
                            )
            for path, raw, _ in events:
                if not path.is_symlink() and path.exists() and path.read_bytes() == raw:
                    path.unlink()
        except (OSError, ValueError) as exc:
            errors.append("runtime source binding: " + str(exc))
    return errors
