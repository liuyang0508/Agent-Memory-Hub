"""Lossless organization asset export; never executes or overwrites user edits."""

from __future__ import annotations
from typing import Any

import base64
import binascii
import hashlib
import json
from pathlib import Path, PurePosixPath
from .common import atomic_json, digest


def assembled(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reassemble only complete, contiguous, hash-verified document versions; omit partial revisions."""
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for r in records:
        if not r.get("deleted"):
            groups.setdefault(
                (
                    r["tenant_id"],
                    r["source_device_id"],
                    r["source_store"],
                    r.get("document_id") or r["asset_id"],
                    r.get("document_hash") or r["body_hash"],
                ),
                [],
            ).append(r)
    output = []
    for group in groups.values():
        group.sort(key=lambda r: r.get("part_index", 0))
        first = group[0]
        count = first.get("part_count", 1)
        if len(group) != count or [r.get("part_index", 0) for r in group] != list(range(count)):
            continue  # Do not expose a partially downloaded revision.
        text = "".join(r["body"] for r in group)
        try:
            raw = (
                base64.b64decode(text, validate=True)
                if first.get("encoding") == "base64"
                else text.encode()
            )
        except (binascii.Error, UnicodeError, ValueError):
            # A malformed part is that document's problem, not everyone's. This
            # used to raise out of search(), so one member could make a keyword
            # unsearchable for the whole organization.
            continue
        expected = first.get("document_hash") or first["body_hash"]
        if hashlib.sha256(raw).hexdigest() != expected:
            continue
        output.append(
            {
                **first,
                "body": text,
                "body_hash": expected,
                "assembled_revisions": [(r["asset_id"], r["revision"]) for r in group],
            }
        )
    return output


def complete_snapshot(records: list[dict[str, Any]], kinds: set[str]) -> list[dict[str, Any]]:
    """Require every selected live document to be complete before applying rules, skills or exports."""
    relevant = [r for r in records if not r.get("deleted") and r["kind"] in kinds]
    complete = assembled(relevant)

    def key(record: dict[str, Any]) -> tuple[str, str, str, str]:
        return (
            record["tenant_id"],
            record["source_device_id"],
            record["source_store"],
            record.get("document_id") or record["asset_id"],
        )

    if {key(r) for r in relevant} != {key(r) for r in complete}:
        raise ValueError(
            "selected assets include incomplete documents; finish sync before applying"
        )
    return complete


def materialize(root: Path, records: list[dict[str, Any]], *, kinds: set[str]) -> dict[str, Any]:
    """Export selected asset kinds under source-scoped paths; preserve edits and never execute content."""
    root = Path(root)
    if root.is_symlink():
        raise ValueError("destination must not be symlink")
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / "amh-native-export.json"
    try:
        previous = json.loads(manifest.read_text()) if manifest.exists() else {}
    except (OSError, ValueError):
        # A corrupted manifest means we no longer know which files we own; start
        # clean rather than refusing to export at all.
        previous = {}
    if not isinstance(previous, dict):
        previous = {}
    owned = previous.get("files", {})
    next_owned = dict(owned)
    desired = set()
    conflicts = []
    for r in complete_snapshot(records, kinds):
        if r["kind"] not in kinds:
            continue
        relative = PurePosixPath(r["path"])
        if relative.is_absolute() or ".." in relative.parts or "\\" in r["path"]:
            raise ValueError("invalid original relative path")
        scope = digest(r["tenant_id"] + r["source_device_id"] + r["source_store"])[:16]
        relative = PurePosixPath(r["kind"]) / scope / relative
        key = str(relative)
        desired.add(key)
        p = root / key
        if any(parent.is_symlink() for parent in [p, *p.parents] if parent != root.parent):
            raise ValueError("destination contains symlink")
        raw = base64.b64decode(r["body"]) if r.get("encoding") == "base64" else r["body"].encode()
        current = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
        want = hashlib.sha256(raw).hexdigest()
        if current and current != want and current != owned.get(key):
            conflicts.append(str(p))
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        from .common import atomic_bytes

        atomic_bytes(p, raw)
        next_owned[key] = want
    # Only remove exports belonging to the selected kinds, and only if untouched.
    for key in set(owned) - desired:
        if key.split("/")[0] not in kinds:
            continue
        p = root / key
        if (
            p.exists()
            and not p.is_symlink()
            and hashlib.sha256(p.read_bytes()).hexdigest() == owned[key]
        ):
            p.unlink()
            next_owned.pop(key, None)
        elif p.exists():
            conflicts.append(str(p))
    atomic_json(
        manifest,
        {
            "files": next_owned,
            "sources": [
                {
                    "asset": r["asset_id"],
                    "producer": r["producer_member_id"],
                    "name": r["producer_name"],
                    "product": r["product"],
                    "path": r["path"],
                }
                for r in records
                if not r["deleted"] and r["kind"] in kinds
            ],
        },
    )
    return {"files": len(desired), "conflicts": conflicts}
