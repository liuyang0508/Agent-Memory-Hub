#!/usr/bin/env python3
"""Reviewable one-time reuse of approved local source selections in a new Hub.

No network, credential copying, outbox copying or cursor copying. Run with the
client's installed Python or PYTHONPATH pointing at the matching checkout.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack
import fcntl
import json
from pathlib import Path
import sqlite3
from typing import Any

from agent_brain.native_sync.client import NativeClient
from agent_brain.native_sync.common import atomic_json, digest
from agent_brain.native_sync.history import screen
from agent_brain.native_sync.profiles import profile_pin, recall_allowed
from agent_brain.native_sync.source import source_text
from agent_brain.native_sync.source_policy import enabled, policy


def read_config(state: Path) -> dict[str, Any]:
    if state.is_symlink():
        raise ValueError("profile path must not be a symlink")
    value = json.loads((state / "config.json").read_text())
    profile_pin(value)
    return value


def preview(source: Path, target: Path) -> dict[str, Any]:
    source, target = source.resolve(), target.resolve()
    if source == target:
        raise ValueError("profiles must be independent")
    old, new = read_config(source), read_config(target)
    if old["hub"] == new["hub"]:
        raise ValueError("this migration requires two different Hubs")
    if recall_allowed(target, new):
        raise ValueError("prepare the target as sync-only / recall-disabled first")
    kinds = enabled(old)
    counts: Counter[str] = Counter()
    selections = []
    # mode=ro cannot create or migrate the source database. Its read transaction
    # yields one consistent sources/snapshots/quarantine view.
    with sqlite3.connect((source / "state.sqlite").as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        for row in db.execute("SELECT * FROM sources ORDER BY root,kind").fetchall():
            item = dict(row)
            snapshots = db.execute(
                "SELECT path,selected FROM snapshots WHERE source=? ORDER BY path", (item["id"],)
            ).fetchall()
            quarantined = db.execute(
                "SELECT 1 FROM meta WHERE key=?", ("quarantined-source:" + item["id"],)
            ).fetchone()
            if quarantined or item["kind"] not in kinds:
                counts["skipped_quarantined" if quarantined else "skipped_scope"] += len(snapshots)
                continue
            root = Path(item["root"])
            if root.is_symlink() or not root.exists():
                counts["skipped_missing_root"] += len(snapshots)
                continue
            approved = []
            for snapshot in snapshots:
                if not snapshot["selected"]:
                    counts["skipped_unselected"] += 1
                    continue
                rel = snapshot["path"]
                path = root if item["file_source"] else root / rel
                if path.is_symlink() or not path.resolve().is_relative_to(
                    root.resolve() if root.is_dir() else root.parent.resolve()
                ):
                    counts["skipped_unsafe_path"] += 1
                    continue
                try:
                    body = source_text(path, item["kind"])
                    if body is None:
                        raise ValueError("source not importable")
                    verdict = screen(body)
                except (OSError, ValueError, UnicodeError):
                    counts["skipped_unreadable"] += 1
                    continue
                if verdict["risk"] != "clean":
                    counts["skipped_" + verdict["risk"]] += 1
                    continue
                approved.append({"path": rel, "hash": digest(body)})
                counts["selected"] += 1
            # Include empty, enabled roots so future new files remain independent;
            # registration baselines every current unapproved file as excluded.
            selections.append(
                {key: item[key] for key in ("root", "product", "project", "kind")}
                | {"files": approved}
            )
    return {
        "version": 1,
        "source": str(source),
        "target": str(target),
        "source_pin": profile_pin(old),
        "target_pin": profile_pin(new),
        "source_policy": policy(kinds),
        "discovery_roots": old.get("discovery_roots", []),
        "sources": selections,
        "counts": dict(counts),
    }


def apply(plan: dict[str, Any]) -> dict[str, Any]:
    source, target = Path(plan["source"]), Path(plan["target"])
    # Refuse a busy worker instead of racing a scan/configuration change. Never
    # stop a running service. Deterministic order prevents crossed-copy deadlocks.
    with ExitStack() as stack:
        for state in sorted((source, target)):
            lock = stack.enter_context((state / "worker.lock").open("a"))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = preview(source, target)
        if current != plan:
            raise ValueError(
                "source selection/content/profile changed; generate and review a new plan"
            )
        client = NativeClient(target)
        client.config["source_policy"] = plan["source_policy"]
        client.config["discovery_roots"] = plan["discovery_roots"]
        atomic_json(target / "config.json", client.config)
        selected = 0
        for source_item in plan["sources"]:
            sid = client.register(
                Path(source_item["root"]),
                source_item["product"],
                source_item["project"],
                source_item["kind"],
            )
            versions = {item["path"]: item["hash"] for item in source_item["files"]}
            # select_targets re-reads selected bodies immediately before marking
            # versions; normal scan retains its normal source sanitization.
            client.select_targets(sid, versions)
            selected += len(versions)
    return {
        "applied": True,
        "selected": selected,
        "counts": plan["counts"],
        "sources": len(plan["sources"]),
        "uploaded": 0,
        "next": "Run sync/status for the target profile; no network requests were made.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        plan = json.loads(args.plan.read_text())
        if plan["source"] != str(args.source.resolve()) or plan["target"] != str(
            args.target.resolve()
        ):
            raise ValueError("plan profile paths differ from arguments")
        result = apply(plan)
    else:
        plan = preview(args.source, args.target)
        atomic_json(args.plan, plan)
        result = {
            "applied": False,
            "plan": str(args.plan.resolve()),
            "counts": plan["counts"],
            "sources": len(plan["sources"]),
            "source_policy": plan["source_policy"],
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
