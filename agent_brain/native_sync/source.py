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
from .common import legacy_memory_command, wire_product
from .facets import source_meta

SECRET_NAMES = {
    "auth.json",
    ".credentials.json",
    "credentials.json",
    ".env",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "id_dsa",
    ".netrc",
    "_netrc",
    ".git-credentials",
    "credentials",
    "kubeconfig",
    ".npmrc",
    ".pypirc",
    ".pgpass",
    ".htpasswd",
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


BLOCKING_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "私钥"),
    (r"\bsk-[A-Za-z0-9_-]{20,}", "API 密钥"),
    (r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}", "GitHub 令牌"),
    (r"\bAIza[0-9A-Za-z_-]{30,}", "Google 密钥"),
    (r"\bLTAI[0-9A-Za-z]{12,24}\b", "阿里云访问密钥"),
    (r"\bxox[baprs]-[A-Za-z0-9-]{10,}", "Slack 令牌"),
    (r"\bAKIA[0-9A-Z]{16}\b", "云访问密钥"),
    (r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token)\s*[:=]\s*\S{6,}", "明文口令"),
    (r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}", "持有者令牌"),
    (r"(?:密码|口令|密钥)\s*(?:[:：=]|是|为)\s*[A-Za-z0-9!@#$%^&*._+/=-]{6,}", "明文口令"),
    (r"(?i)://[^/\s:@]+:[^/\s@]{3,}@", "链接里的账号密码"),
)

SENSITIVE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(?<!\d)1[3-9]\d{9}(?!\d)", "手机号"),
    (r"(?<!\d)\d{17}[\dXx](?!\d)", "身份证号"),
    (r"(?<!\d)\d{16}(?![\dXx])", "疑似银行卡号"),
    (r"[\w.+-]+@[\w-]+\.[\w.-]+", "邮箱"),
    (
        r"(工资|薪资|月薪|年薪|奖金|存款|房贷|贷款|住院|病历|诊断|体检|家庭住址|身份证|银行卡)",
        "个人隐私措辞",
    ),
)


def screen(body: str) -> dict[str, Any]:
    """Classify one document's text: blocked never imports, sensitive needs a person to decide.

    Returns category labels only, never the matched text, so the report itself
    cannot leak what it is warning about.
    """
    blocked = sorted({label for pattern, label in BLOCKING_PATTERNS if re.search(pattern, body)})
    if blocked:
        return {"risk": "blocked", "reasons": blocked}
    sensitive = sorted({label for pattern, label in SENSITIVE_PATTERNS if re.search(pattern, body)})
    if sensitive:
        return {"risk": "sensitive", "reasons": sensitive}
    return {"risk": "clean", "reasons": []}


# The same credentials, cut precisely enough to mask a value inside a JSON line
# without eating the structure around it.
_VALUE = r"""\\?["']?[^\s"'\\,;}\]]{6,}"""
REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), label)
    for pattern, label in (
        (
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]{0,8000}?(?:-----END [A-Z ]*PRIVATE KEY-----|$)",
            "私钥",
        ),
        (
            r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token)\s*[:=]\s*" + _VALUE,
            "明文口令",
        ),
        (r"(?:密码|口令|密钥)\s*(?:[:：=]|是|为)\s*[A-Za-z0-9!@#$%^&*._+/=-]{6,}", "明文口令"),
        (r"(?i)://[^/\s:@\"']+:[^/\s@\"']{3,}@", "链接里的账号密码"),
        *BLOCKING_PATTERNS[1:],
    )
)


def redact(text: str) -> str:
    """Mask credentials in a session record, which stays useful as evidence.

    Memories, rules, skills and configs are refused whole instead: a person wrote
    them to be shared, and one credential means the file needs fixing. Half of a
    real machine's session records quote something shaped like a credential, so
    refusing those whole would lose the record for the sake of one line.
    """
    for pattern, label in REDACTIONS:
        text = pattern.sub("[已隐去:" + label + "]", text)
    return text


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
    if compact in {"bypass", "compass", "encompass", "compat"}:
        return False
    if compact.endswith(("pass", "passwd", "pwd")) or compact.endswith(
        ("githubpat", "gitlabpat", "accesspat")
    ):
        return True
    return any(
        "".join(c for c in part if c.isalnum()) in compact
        for part in SECRET_PARTS + ("credential", "invite", "bearer", "accesskey")
    )


# Wraps what the member sees of a recall: Claude Code keeps a hook's message in its
# transcript, and other members' text must not reach this member's search index from there.
RECALL_DETAIL = "组织记忆明细"


def indexable_history(text: str) -> str:
    """Mask AMH-injected evidence for search while preserving transcript offsets and stored originals."""
    # Fixed machine boundaries survive wording changes and also match escaped
    # newlines in JSONL. Preserve offsets so nearby native text remains usable.
    for block in (
        "AMH_ORG_CONTEXT_V1",
        "AMH_RECALL_FEEDBACK_V1",
        RECALL_DETAIL,
        json.dumps(RECALL_DETAIL)[1:-1].replace("\\", "\\\\"),
    ):
        text = re.sub(
            rf"\[{block}\].*?\[/{block}\]",
            lambda m: " " * len(m.group()),
            text,
            flags=re.S,
        )
    # Match the banner by its stable prefix rather than its exact wording: the
    # label changed once already, and a stale copy here let other members' text
    # through the mask and into this member's index.
    labels = "|".join(re.escape(label) for label in ("组织共享", json.dumps("组织共享")[1:-1]))
    text = re.sub(
        r"\[(?:" + labels + r")[^\]]*\].*?amh-native-recall:[a-f0-9]{24}",
        lambda m: " " * len(m.group()),
        text,
        flags=re.S,
    )
    return re.sub(r"amh-native-recall:[a-f0-9]{24}", lambda m: " " * len(m.group()), text)


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
    if kind == "transcript":
        text = redact(text)
    raw = base64.b64decode(text) if encoding == "base64" else text.encode()
    return text, encoding, hashlib.sha256(raw).hexdigest()


def source_text(path: Path, kind: str) -> str | None:
    """Expose the same sanitized representation used by capture, so preview cannot diverge."""
    result = read_document(path, kind)
    return result[0] if result is not None else None


def _raise_walk_error(error: OSError) -> None:
    """Surface an unreadable directory instead of letting os.walk skip it silently."""
    raise error


def transcript_metadata(path: str | Path) -> bool:
    """Agent metadata sidecars are not conversations, even with a nested source root."""
    return Path(path).name.lower().endswith(".meta.json")


def withheld(root: Path, kind: str, rel: Path | str) -> bool:
    """Codex files that stay on this machine even though they sit in its memory store.

    raw_memories.md is consolidation's intermediate input, not memory meant to be
    read, and extensions/skysight/ holds what Computer History derives from what a
    person did on screen. ``memories`` is the store's name only for Codex; other
    products keep theirs in ``memory``.
    """
    if kind != "memory" or root.name != "memories":
        return False
    parts = Path(rel).parts
    return parts == ("raw_memories.md",) or parts[:2] == ("extensions", "skysight")


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
        if forbidden_file(p) or withheld(root, kind, rel):
            continue
        if kind in ("memory", "rule") and p.suffix.lower() != ".md":
            continue
        if kind == "transcript" and (
            p.suffix.lower() not in (".jsonl", ".json", ".md") or "memory" in rel.parts
        ):
            continue
        # A subagent's .meta.json holds only agentType/description/toolUseId — no
        # conversation. Admitting it made these one-line stubs the largest group
        # in a member's selection (208 of 212 on a real machine), because being
        # short they never trip the privacy screen and every "select clean" pass
        # swept them in. The sibling .jsonl is the actual transcript and stays.
        if kind == "transcript" and transcript_metadata(p):
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
    *,
    mtime: float | None = None,
) -> None:
    """Coalesce a complete document into versioned parts inside the caller's transaction.

    Part IDs remain stable across edits. Superseded tails become tombstones; the
    outbox retains only the latest desired state until an acknowledged upload.
    Every part carries the same source_meta: the file's modification time plus,
    for Markdown memory/rules, declared front matter, wikilinks and headings.
    """
    text, encoding, document_hash = document
    indexed = indexable_history(text) if source["kind"] == "transcript" else None
    meta = (
        source_meta(text, mtime=mtime)
        if source["kind"] in ("memory", "rule") and encoding == "utf8"
        else source_meta("", mtime=mtime)
    )
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
            product=wire_product(source["product"]),
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
        if meta:
            item["source_meta"] = meta
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
    if item.get("index_text"):
        item["index_text"] = ""  # a copy of the text; a tombstone carries none
    payload = json.dumps(item, ensure_ascii=False)
    db.execute(
        "INSERT OR REPLACE INTO outbox VALUES(?,?,?)",
        (row["asset"], payload, hashlib.sha256(payload.encode()).hexdigest()),
    )
    db.execute("UPDATE source_parts SET payload=? WHERE asset=?", (payload, row["asset"]))
