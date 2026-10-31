"""Explicit, provenance-preserving activation of shared behavior assets."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any
from .common import atomic_bytes, atomic_json, atomic_text, digest
from .materialize import complete_snapshot


def provenance(record: dict[str, Any]) -> str:
    """Render original member/product/version metadata for explicitly activated behavior assets."""
    return json.dumps(
        {
            k: record[k]
            for k in (
                "producer_member_id",
                "producer_name",
                "product",
                "project",
                "asset_id",
                "revision",
            )
        },
        ensure_ascii=False,
    )


def activate_skills(
    root: Path, records: list[dict[str, Any]], *, source_store: str | None = None
) -> dict[str, Any]:
    """Apply a complete source-scoped skill snapshot; preserve user changes and unrelated sources."""
    root = Path(root)
    if root.is_symlink():
        raise ValueError("native skill root must not be symlink")
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / ".amh-native-skills.json"
    previous = json.loads(manifest.read_text()) if manifest.exists() else {"files": {}}
    owned = previous["files"]
    package_sources = dict(previous.get("package_sources", {}))
    groups: dict[str, list[tuple[PurePosixPath, dict[str, Any]]]] = {}
    for r in complete_snapshot(
        [r for r in records if source_store is None or r["source_store"] == source_store], {"skill"}
    ):
        if r["kind"] != "skill" or (source_store is not None and r["source_store"] != source_store):
            continue
        rel = PurePosixPath(r["path"])
        if rel.is_absolute() or ".." in rel.parts or "\\" in str(rel):
            raise ValueError("invalid skill path")
        package = rel.parts[0] if len(rel.parts) > 1 else "shared"
        relative = PurePosixPath(*rel.parts[1:]) if len(rel.parts) > 1 else rel
        scope = digest(r["tenant_id"] + r["source_device_id"] + r["source_store"])[:12]
        name = "amh-org-" + scope + "-" + re.sub("[^a-z0-9-]", "-", package.lower())[:30]
        package_sources[name] = r["source_store"]
        groups.setdefault(name, []).append((relative, r))
    conflicts = []
    installed = []
    desired: set[str] = set()
    for name, entries in groups.items():
        if not any(str(rel) == "SKILL.md" for rel, r in entries):
            continue
        staged = []
        for rel, r in entries:
            p = root / name / str(rel)
            if any(x.is_symlink() for x in [p, *p.parents] if x != root.parent):
                raise ValueError("symlink in skill destination")
            raw = (
                base64.b64decode(r["body"]) if r.get("encoding") == "base64" else r["body"].encode()
            )
            if str(rel) == "SKILL.md":
                text = raw.decode()
                if not text.startswith("---\n"):
                    raise ValueError("skill requires YAML frontmatter")
                end = text.find("\n---", 4)
                if end < 0:
                    raise ValueError("unterminated skill metadata")
                front = re.sub(r"(?m)^name:.*$", "name: " + name, text[:end])
                if not re.search(r"(?m)^name:", front):
                    front += "\nname: " + name
                text = front + text[end:]
                metadata_end = text.find("\n---", 4) + 4
                text = (
                    text[:metadata_end]
                    + "\n\n组织技能来源："
                    + provenance(r)
                    + "\n"
                    + text[metadata_end:]
                )
                raw = text.encode()
            key = str(p.relative_to(root))
            desired.add(key)
            current = digest(p.read_bytes().hex()) if p.exists() else None
            wanted = digest(raw.hex())
            if current and current != wanted and current != owned.get(key):
                conflicts.append(str(p))
                continue
            staged.append((p, key, raw, wanted))
        if len(staged) != len(entries):
            continue  # Do not activate half a package.
        for p, key, raw, wanted in staged:
            atomic_bytes(p, raw)
            owned[key] = wanted
        installed.append(name)
    for key in set(owned) - desired:
        package = Path(key).parts[0]
        if source_store is not None and package_sources.get(package) != source_store:
            continue
        p = root / key
        if not p.exists():
            owned.pop(key, None)
        elif not p.is_symlink() and digest(p.read_bytes().hex()) == owned[key]:
            p.unlink()
            owned.pop(key, None)
        else:
            conflicts.append(str(p))
    active = sorted(
        {
            Path(key).parts[0]
            for key in owned
            if key.endswith("/SKILL.md") and (root / key).is_file()
        }
    )
    package_sources = {name: value for name, value in package_sources.items() if name in active}
    atomic_json(manifest, {"files": owned, "packages": active, "package_sources": package_sources})
    return {"packages": len(active), "names": active, "updated": installed, "conflicts": conflicts}


def activate_rules(path: Path, records: list[dict[str, Any]]) -> dict[str, Any]:
    """Replace the owned rules block only after complete-snapshot and edit checks; never auto-activate."""
    path = Path(path)
    start = "<!-- amh-native-rules:start -->"
    end = "<!-- amh-native-rules:end -->"
    original = path.read_text() if path.exists() else ""
    receipt = path.parent / ("." + path.name + ".amh-native.json")
    previous = json.loads(receipt.read_text()) if receipt.exists() else {}
    content = original
    if start in original:
        if end not in original:
            raise ValueError("incomplete managed rules block")
        left, right = original.index(start), original.index(end) + len(end)
        current = original[left:right]
        if digest(current) != previous.get("block_hash"):
            raise ValueError("managed rules changed locally")
        content = original[:left] + original[right:].removeprefix("\n")
    parts = []
    for r in complete_snapshot(records, {"rule"}):
        if r["kind"] != "rule":
            continue
        # Legacy AMH-owned discipline is not re-enabled through shared rules.
        body = re.sub(
            r"<!-- BEGIN agent-memory-hub(?:-awareness)? -->.*?<!-- END agent-memory-hub(?:-awareness)? -->",
            "",
            r["body"],
            flags=re.S,
        )
        parts.append("来源：" + provenance(r) + "\n\n" + body)
    block = start + "\n\n" + "\n\n".join(parts) + "\n" + end
    if len(block.encode()) > 16000:
        raise ValueError("selected rules exceed 16KB; select a smaller set")
    if path.exists() and path.read_text() != original:
        raise ValueError("instructions changed; retry")
    content = content.rstrip("\n") + "\n" + block + "\n"
    atomic_json(receipt, {"block_hash": digest(block), "before": previous.get("before", original)})
    atomic_text(path, content)
    return {"rules": len(parts), "path": str(path)}


def apply_config(path: Path, record: dict[str, Any], product: str) -> dict[str, Any]:
    """Apply portable fields only; unrelated fields and tables remain intact."""
    codex = {"codex-cli", "codex-desktop", "chatgpt-desktop", "codex-shared"}
    if (record["product"] in codex) != (product in codex):
        raise ValueError("cross-family model/settings fields require an explicit semantic mapping")
    if record["kind"] != "config":
        raise ValueError("not a configuration asset")
    values = json.loads(record["body"])
    if not isinstance(values, dict):
        raise ValueError("configuration must be an object")
    allowed = (
        {"model", "model_reasoning_effort", "personality"}
        if product in codex
        else {"model", "language", "outputStyle", "autoMemoryEnabled"}
    )
    selected = {k: v for k, v in values.items() if k in allowed}
    for key, value in selected.items():
        if not isinstance(value, bool if key == "autoMemoryEnabled" else str):
            raise ValueError("invalid portable field type: " + key)
    original = path.read_text() if path.exists() else ""
    if product in codex:
        import tomllib

        if original:
            tomllib.loads(original)
        boundary = re.search(r"(?m)^\s*\[", original)
        root = original[: boundary.start()] if boundary else original
        tables = original[boundary.start() :] if boundary else ""
        for key, value in selected.items():
            line = key + " = " + json.dumps(value, ensure_ascii=False)
            pattern = r"(?m)^" + re.escape(key) + r"\s*=.*$"
            if re.search(pattern, root):
                root = re.sub(pattern, lambda m: line, root)
            else:
                root = root.rstrip("\n") + "\n" + line + "\n"
        updated = root + tables
        tomllib.loads(updated)
    else:
        data = json.loads(original) if original else {}
        data.update(selected)
        updated = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text() != original:
        raise ValueError("configuration changed; preview again")
    backup = path.parent / ("." + path.name + ".amh-native-config.json")
    previous = json.loads(backup.read_text()) if backup.exists() else {}
    atomic_json(
        backup,
        {
            "before": previous.get("before", original),
            "producer": provenance(record),
            "after_hash": digest(updated),
        },
    )
    atomic_text(path, updated)
    return {"applied": list(selected), "skipped": sorted(set(values) - allowed), "path": str(path)}
