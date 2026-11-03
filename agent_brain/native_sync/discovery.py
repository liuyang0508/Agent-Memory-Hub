"""Discover newly opened Claude projects without automatically importing old content."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from .storage import database
from .source import read_document


def observe(client: Any, product: str, payload: dict[str, Any], session: str, epoch: str) -> None:
    """Register adapter-observed native roots; only verified successful file creation may bypass baselining."""
    watchers = [r for r in client.config.get("discovery_roots", []) if r["product"] == product]
    event = payload.get("hook_event_name")
    if event == "SessionStart":
        existing = {r["root"] for r in client.sources()}
        for watcher in watchers:
            for root in Path(watcher["root"]).glob("*/memory"):
                if not root.is_dir() or root.is_symlink():
                    continue
                root = root.resolve()
                if str(root) not in existing:
                    client.register(root, product, "", "memory")
                    # Discovery adds the root as a *source* so its memory is shared.
                    # Making it a projection target as well would copy other
                    # members' memory back onto this disk, so that only happens
                    # when the member opted in.
                    if client.projection_enabled():
                        with database(client.path) as db:
                            db.execute(
                                "INSERT OR IGNORE INTO dynamic_targets VALUES(?,?)",
                                (str(root), product),
                            )
        return
    if event != "PostToolUse" or payload.get("tool_name") != "Write":
        return
    tool_input = payload.get("tool_input", {})
    result = payload.get("tool_response", {})
    if not isinstance(tool_input, dict) or (
        isinstance(result, dict) and (result.get("error") or result.get("is_error"))
    ):
        return
    if not isinstance(result, dict) or result.get("type") != "create":
        return  # An update or an unproven write must not turn pre-existing files into new history.
    filename = tool_input.get("file_path")
    if not isinstance(filename, str):
        return
    path = Path(filename).expanduser()
    if not path.is_absolute():
        path = Path(str(payload.get("cwd", "."))) / path
    path = path.resolve()
    for watcher in watchers:
        base = Path(watcher["root"]).resolve()
        if not path.is_relative_to(base):
            continue
        relative = path.relative_to(base)
        if len(relative.parts) < 3 or relative.parts[1] != "memory" or path.suffix != ".md":
            continue
        root = base / relative.parts[0] / "memory"
        if any(r["root"] == str(root) for r in client.sources()):
            return  # Registered sources already track new files and preserve excluded-history choices.
        try:
            document = read_document(path, "memory")
        except (OSError, ValueError, UnicodeError):
            return
        if not document:
            return
        with database(client.path) as db:
            denied = db.execute(
                "SELECT 1 FROM history_hashes WHERE hash=?", (document[2],)
            ).fetchone()
        sid = client.register(
            root,
            product,
            "",
            "memory",
            fresh_files={str(path.relative_to(root))} if not denied else set(),
        )
        with database(client.path) as db:
            # Same rule as the SessionStart branch: a target recorded now would
            # become live the moment someone later enables projection, which is
            # not a decision this hook is allowed to make in advance.
            if client.projection_enabled():
                db.execute(
                    "INSERT OR IGNORE INTO dynamic_targets VALUES(?,?)", (str(root), product)
                )
            if not denied and tool_input.get("content") == document[0]:
                db.execute(
                    "INSERT OR REPLACE INTO native_proofs VALUES(?,?,?,?,?)",
                    (session, epoch, sid, str(path), document[2]),
                )
        return
