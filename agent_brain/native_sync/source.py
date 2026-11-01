"""Stable source reads and bounded lossless chunks for text, history and skill assets."""

from __future__ import annotations
from typing import Any, Iterator
import sqlite3

import base64
import os
import hashlib
import json
import re
from pathlib import Path
from .projection import strip_managed
from .common import legacy_memory_command

SECRET_NAMES = {
    "auth.json",
    ".credentials.json",
    "credentials.json",
    ".env",
    "id_rsa",
    "id_ed25519",
}
SECRET_PARTS = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "private_key",
    "authorization",
    "cookie",
)


def forbidden_file(path: Path) -> bool:
    """Exclude known credential filenames/extensions before any content is read."""
    name = path.name.lower()
    return (
        name in SECRET_NAMES
        or name.startswith(".env.")
        or name.endswith((".pem", ".key", ".p12", ".pfx", ".keystore"))
    )


def sensitive_key(key: str) -> bool:
    """Match credential-key variants while retaining ordinary token-budget configuration."""
    compact = "".join(c for c in key.lower() if c.isalnum())
    if compact in {"maxtokens", "maxinputtokens", "maxoutputtokens", "tokenbudget", "tokencount"}:
        return False
    return any(
        "".join(c for c in part if c.isalnum()) in compact
        for part in SECRET_PARTS + ("credential", "invite", "bearer", "accesskey")
    )


def indexable_history(text: str) -> str:
    """Mask AMH-injected evidence for search while preserving transcript offsets and stored originals."""
    # Match the banner by its stable prefix rather than its exact wording: the
    # label changed once already, and a stale copy here let other members' text
    # through the mask and into this member's index.
    text = re.sub(
        r"\[组织共享资料[^\]]*\].*?amh-native-recall:[a-f0-9]{24}",
        lambda m: " " * len(m.group()),
        text,
        flags=re.S,
    )
    return "".join(
        " " * len(line.rstrip("\r\n")) + line[len(line.rstrip("\r\n")) :]
        if "amh-native-recall:" in line
        else line
        for line in text.splitlines(keepends=True)
    )


def read_document(path: Path, kind: str) -> tuple[str, str, str] | None:
    """Return sanitized (body, encoding, SHA-256 of decoded bytes), or None for excluded files.

    Read a stable inode/version; raise on oversized or changing files rather than
    silently truncating. Only skill resources may use lossless base64. Native index
    navigation and known configuration credentials are removed before fingerprinting.
    """
    if forbidden_file(path) or path.is_symlink():
        return None
    before = path.stat()
    if before.st_size > 64 * 1024 * 1024:
        raise ValueError("source exceeds 64MiB file limit: " + path.name)
    raw = path.read_bytes()
    after = path.stat()
    if (before.st_mtime_ns, before.st_size, before.st_ino) != (
        after.st_mtime_ns,
        after.st_size,
        after.st_ino,
    ):
        raise ValueError("source changed during read: " + path.name)
    encoding = "utf8"
    try:
        text = raw.decode("utf8")
    except UnicodeDecodeError:
        if kind != "skill":
            raise
        text = base64.b64encode(raw).decode()
        encoding = "base64"
    if kind == "config":
        if path.suffix == ".json":
            data = json.loads(text)
        elif path.suffix == ".toml":
            import tomllib

            data = tomllib.loads(text)
        else:
            raise ValueError("configuration must be JSON or TOML")

        skip = object()
        secret_flags = {
            "apikey",
            "token",
            "accesstoken",
            "authtoken",
            "password",
            "secret",
            "clientsecret",
            "authorization",
            "credential",
        }

        def clean(value: Any) -> Any:
            if isinstance(value, dict):
                command = value.get("command", "")
                if isinstance(command, str) and (
                    legacy_memory_command(command) or "agent_runtime_kit/mcp/server.sh" in command
                ):
                    return skip
                result = {}
                for key, child in value.items():
                    if sensitive_key(key) or key == "agent-memory-hub":
                        continue
                    cleaned = clean(child)
                    if cleaned is not skip:
                        result[key] = cleaned
                return result
            if isinstance(value, list):
                result_list = []
                redact_next = False
                for child in value:
                    if redact_next:
                        result_list.append("[REDACTED]")
                        redact_next = False
                        continue
                    if isinstance(child, str) and child.startswith("--"):
                        flag = child.split("=", 1)[0]
                        compact = "".join(c for c in flag.lower() if c.isalnum())
                        if compact in secret_flags:
                            if "=" in child:
                                result_list.append(flag + "=[REDACTED]")
                            else:
                                result_list.append(flag)
                                redact_next = True
                            continue
                    cleaned = clean(child)
                    if cleaned is not skip:
                        result_list.append(cleaned)
                return result_list
            if isinstance(value, str):
                value = re.sub(
                    r'(?i)((?:proxy-)?authorization\s*[:=]\s*(?:(?:bearer|basic|token)\s+)?)[^\s"\';]+',
                    lambda m: m.group(1) + "[REDACTED]",
                    value,
                )
                value = re.sub(
                    r'(?i)((?:x[-_])?api[-_]key\s*[:=]\s*)[^\s"\';]+',
                    lambda m: m.group(1) + "[REDACTED]",
                    value,
                )
                value = re.sub(
                    r'(?i)((?:set-)?cookie\s*:\s*)[^"\'\n]+',
                    lambda m: m.group(1) + "[REDACTED]",
                    value,
                )
                # Preserve portable command text while removing common inline credential arguments.
                value = re.sub(
                    r'(?i)((?:--(?:api[-_]key|access[-_]token|auth[-_]token|token|password|client[-_]secret)|[A-Z][A-Z0-9_]*(?:API_KEY|TOKEN|PASSWORD|SECRET))\s*(?:=|\s)\s*)(?:"[^"]*"|\'[^\']*\'|[^\s;]+)',
                    lambda m: m.group(1) + "[REDACTED]",
                    value,
                )
                if "://" in value:
                    from urllib.parse import urlsplit, urlunsplit, parse_qsl

                    try:
                        url = urlsplit(value)
                        if url.username or url.password:
                            return "[REDACTED_URL_CREDENTIALS]"
                        if any(sensitive_key(k) for k in dict(parse_qsl(url.query))):
                            return urlunsplit((url.scheme, url.netloc, url.path, "", ""))
                    except ValueError:
                        return "[INVALID_URL]"
            return value

        text = json.dumps(clean(data), ensure_ascii=False, indent=2)
    if kind == "memory":
        text = strip_managed(text)
    raw = base64.b64decode(text) if encoding == "base64" else text.encode()
    return text, encoding, hashlib.sha256(raw).hexdigest()


def source_text(path: Path, kind: str) -> str | None:
    """Expose the same sanitized representation used by capture, so preview cannot diverge."""
    result = read_document(path, kind)
    return result[0] if result is not None else None


def _raise_walk_error(error: OSError) -> None:
    """Surface an unreadable directory instead of letting os.walk skip it silently."""
    raise error


def files(root: Path, kind: str) -> Iterator[Path]:
    """Enumerate eligible sources, excluding credentials, generated resources and symlink traversal.

    Raises OSError when a directory cannot be read. An unreadable tree used to
    yield nothing, which the caller could not tell apart from an empty one: every
    file would then look deleted and be removed from the Hub.
    """
    if root.is_file():
        candidates = [root]
    else:
        found: list[Path] = []
        walker = os.walk(root, onerror=_raise_walk_error)
        for base, _dirs, names in walker:
            found.extend(Path(base) / name for name in names)
        candidates = sorted(found)
    for p in candidates:
        if not p.is_file() or p.is_symlink():
            continue
        rel = Path(p.name) if root.is_file() else p.relative_to(root)
        if any(
            part in ("amh-org", ".git", "node_modules", "__pycache__")
            or part.startswith("amh-org-")
            or part.startswith(".amh-native")
            for part in rel.parts
        ):
            continue
        if any((root / Path(*rel.parts[:i])).is_symlink() for i in range(1, len(rel.parts))):
            continue
        if forbidden_file(p):
            continue
        if kind in ("memory", "rule") and p.suffix.lower() != ".md":
            continue
        if kind == "transcript" and (
            p.suffix.lower() not in (".jsonl", ".json", ".md") or "memory" in rel.parts
        ):
            continue
        if kind == "config" and p.suffix.lower() not in (".json", ".toml"):
            continue
        yield p


def enqueue_document(
    db: sqlite3.Connection,
    source: Any,
    rel: str,
    base_id: str,
    document: tuple[str, str, str],
    unknown: bool,
    upstream: list[str] | None = None,
) -> None:
    """Coalesce a complete document into versioned parts inside the caller's transaction.

    Part IDs remain stable across edits. Superseded tails become tombstones; the
    outbox retains only the latest desired state until an acknowledged upload.
    """
    text, encoding, document_hash = document
    indexed = indexable_history(text) if source["kind"] == "transcript" else None
    chunks = [text[i : i + 8000] for i in range(0, len(text), 8000)] or [""]
    existing = {
        r["asset"]: r for r in db.execute("SELECT * FROM source_parts WHERE base=?", (base_id,))
    }
    active = set()
    for n, chunk in enumerate(chunks):
        aid = base_id + "__p" + str(n).zfill(6)
        active.add(aid)
        rev = existing[aid]["revision"] if aid in existing else 0
        item = dict(
            asset_id=aid,
            base_revision=rev,
            source_store=source["id"],
            product=source["product"],
            project=source["project"],
            kind=source["kind"],
            path=rel,
            title=Path(rel).stem,
            body=chunk,
            index_text=indexed[n * 8000 : (n + 1) * 8000] if indexed is not None else None,
            upstream=upstream or [],
            producer_unknown=unknown,
            deleted=False,
            encoding=encoding,
            document_id=base_id,
            document_hash=document_hash,
            part_index=n,
            part_count=len(chunks),
        )
        payload = json.dumps(item, ensure_ascii=False)
        db.execute(
            "INSERT OR REPLACE INTO source_parts VALUES(?,?,?,?)", (base_id, aid, payload, rev)
        )
        db.execute(
            "INSERT OR REPLACE INTO outbox VALUES(?,?,?)",
            (aid, payload, hashlib.sha256(payload.encode()).hexdigest()),
        )
    for aid in set(existing) - active:
        queue_delete(db, existing[aid])


def queue_delete(db: sqlite3.Connection, row: Any) -> None:
    """Queue an owned part tombstone at its known base revision without deleting its identity."""
    item = json.loads(row["payload"])
    item.update(base_revision=row["revision"], deleted=True, body="")
    payload = json.dumps(item, ensure_ascii=False)
    db.execute(
        "INSERT OR REPLACE INTO outbox VALUES(?,?,?)",
        (row["asset"], payload, hashlib.sha256(payload.encode()).hexdigest()),
    )
    db.execute("UPDATE source_parts SET payload=? WHERE asset=?", (payload, row["asset"]))
