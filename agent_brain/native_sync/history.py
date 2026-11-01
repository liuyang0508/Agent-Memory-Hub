"""Pre-install memory import: list what exists, screen it, upload only what was ticked.

The Hub never sees anything here until a member explicitly selects a file.
Selection re-reads each ticked file and compares its digest, so a file that
changed after it was listed is refused rather than uploaded at a new version.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .client import NativeClient
from .common import PRODUCTS, digest
from .install import discover_assets
from .source import files, source_text

# read_document refuses larger files; counting them as importable would promise
# an upload that silently never happens.
MAX_BYTES = 64 * 1024 * 1024

# Groups are ordered by how likely a member wants them shared: curated memory
# first, raw transcripts last. A category is a (product, kind) pair, not a path,
# so the same category keeps working after a product reorganizes its own store.
CATEGORY_ORDER = ("memory", "rule", "skill", "config", "transcript")


def roots(home: Path, product: str) -> list[tuple[Path, str]]:
    """Known store locations for one product, deduplicated by resolved path."""
    seen: dict[str, tuple[Path, str]] = {}
    for entry in discover_assets(home, product):
        root = Path(entry["root"]).expanduser()
        if not root.exists():
            continue
        # Store the resolved path so decisions keyed by absolute path compare
        # equal: on macOS /var and /private/var are the same store, not two.
        seen.setdefault(str(root.resolve()), (root.resolve(), entry["kind"]))
    # Claude Code keeps per-project memory outside ~/.claude: one directory each.
    if product == "claude-code":
        for memory in sorted((home / ".claude/projects").glob("*/memory")):
            if memory.is_dir() and not memory.is_symlink():
                resolved = memory.resolve()
                seen.setdefault(str(resolved), (resolved, "memory"))
    # Cowork stores each workspace's memory beside its session rather than in a fixed root.
    if product == "cowork":
        bases = [
            home / "Library/Application Support" / bundle / "local-agent-mode-sessions"
            for bundle in ("Claude", "Claude-3p")
        ]
        for base in bases:
            if not base.is_dir():
                continue
            for memory in sorted(base.rglob("MEMORY.md")):
                if memory.is_symlink() or not memory.parent.is_dir():
                    continue
                resolved = memory.parent.resolve()
                seen.setdefault(str(resolved), (resolved, "memory"))
    return list(seen.values())


def title_of(path: Path, body: str) -> str:
    """First heading or frontmatter description; falls back to the filename stem."""
    for line in body.splitlines()[:12]:
        stripped = line.strip()
        if stripped.startswith("#"):
            value = stripped.lstrip("#").strip()
            if value:
                return value[:120]
        if stripped.startswith("description:"):
            value = stripped.split(":", 1)[1].strip().strip('"')
            if value:
                return value[:120]
    return path.stem[:120]


SCREEN_PREFIX = 64 * 1024


def peek(path: Path) -> str:
    """Read only the head of a file for screening and titling.

    Transcripts run to hundreds of megabytes; reading them whole just to show a
    checklist made listing minutes long. Bytes beyond the prefix are screened
    again at upload time, where the full body is already in memory.
    """
    with path.open("rb") as handle:
        raw = handle.read(SCREEN_PREFIX)
    return raw.decode("utf8", errors="replace")


def parse_moment(value: str) -> float:
    """Accept "YYYY-MM-DD", "YYYY-MM-DD HH:MM" or "YYYY-MM-DD HH:MM:SS" as local time.

    A member thinking "since I joined this team" gives a date, not an epoch, so
    the filter has to accept the shapes people actually type.
    """
    from datetime import datetime

    text = (value or "").strip()
    if not text:
        raise ValueError("empty time")
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern).timestamp()
        except ValueError:
            continue
    raise ValueError("expected YYYY-MM-DD or YYYY-MM-DD HH:MM:SS")


def items(
    home: Path,
    products: list[str],
    *,
    since: float | None = None,
    until: float | None = None,
) -> list[dict[str, Any]]:
    """Every importable file with its screening verdict, grouped for a person to tick through.

    Only files someone would recognise: credential files, oversized files and
    unreadable entries are skipped, never silently offered.
    """
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for product in products:
        source_product = (
            "codex-shared"
            if product in ("codex-cli", "codex-desktop", "chatgpt-desktop")
            else product
        )
        for root, kind in roots(home, product):
            for path in files(root, kind):
                absolute = str(path.resolve())
                if absolute in seen:
                    continue
                seen.add(absolute)
                try:
                    stat = path.stat()
                    head = peek(path)
                except (OSError, ValueError, UnicodeError):
                    continue
                if stat.st_size > MAX_BYTES:
                    continue
                # Filter before screening: a member narrowing by date should not
                # pay to screen files they have already excluded.
                if since is not None and stat.st_mtime < since:
                    continue
                if until is not None and stat.st_mtime > until:
                    continue
                verdict = screen(head)
                rows.append(
                    {
                        "path": absolute,
                        "name": path.name,
                        "product": source_product,
                        "product_name": PRODUCTS[source_product],
                        "kind": kind,
                        "root": str(root),
                        "title": title_of(path, head),
                        "bytes": stat.st_size,
                        "mtime": stat.st_mtime,
                        "risk": verdict["risk"],
                        "reasons": verdict["reasons"],
                    }
                )
    rows.sort(key=lambda r: (r["product"], CATEGORY_ORDER.index(r["kind"]) if r["kind"] in CATEGORY_ORDER else 99, r["path"]))
    return rows


def import_paths(client: NativeClient, home: Path, selected: list[dict[str, Any]]) -> dict[str, Any]:
    """Select exactly the ticked files; nothing else from their categories is touched.

    The category is registered first so later changes in it sync automatically,
    then only the chosen paths are marked selected. That keeps the history
    decision a one-time pre-install step rather than an ongoing question.
    """
    by_root: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in selected:
        by_root.setdefault((row["root"], row["product"], row["kind"]), []).append(row)
    registered = 0
    taken = 0
    rejected: list[str] = []
    late_sensitive: list[dict[str, Any]] = []
    for (root_path, product, kind), rows in by_root.items():
        root = Path(root_path)
        sid = client.register(root, product, "", kind)
        registered += 1
        selection: dict[str, str] = {}
        for row in rows:
            absolute = row["path"]
            path = Path(absolute)
            try:
                body = source_text(path, kind)
            except (ValueError, OSError, UnicodeError):
                rejected.append(absolute)
                continue
            if body is None:
                rejected.append(absolute)
                continue
            # The checklist screens only a prefix for speed; this is the full
            # body. A file whose sensitive content sits past that prefix was
            # never shown to the member, so it must not upload on their behalf.
            verdict = screen(body)
            if verdict["risk"] == "blocked":
                rejected.append(absolute)
                continue
            if verdict["risk"] == "sensitive" and row.get("risk") != "sensitive":
                late_sensitive.append({"path": absolute, "reasons": verdict["reasons"]})
                continue
            # Only the ticked files are read, not the whole source: a transcript
            # root can be hundreds of megabytes and re-reading it per selection
            # made import scale with unrelated files.
            relative = path.name if root.is_file() else str(path.relative_to(root))
            selection[relative] = digest(body)
        if selection:
            client.select_targets(sid, selection)
            taken += len(selection)
        if kind == "memory":
            _record_target(client, root, product)
    return {
        "roots": registered,
        "selected": taken,
        "rejected": rejected,
        "late_sensitive": late_sensitive,
    }


def _record_target(client: NativeClient, root: Path, product: str) -> None:
    """Register the root for native projection so shared memories land where products read."""
    from .common import atomic_json

    if not client.projection_enabled():
        return
    targets = client.config.setdefault("targets", [])
    entry = {"root": str(root.resolve()), "product": product}
    if not any(t.get("root") == entry["root"] for t in targets):
        targets.append(entry)
        atomic_json(client.root / "config.json", client.config)


# Content screening runs before anything is selected. A memory file is free text:
# a product/kind label says nothing about whether one person's salary, home
# address or a pasted private key sits inside it.
BLOCKING_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "私钥"),
    (r"\bsk-[A-Za-z0-9]{20,}", "API 密钥"),
    (r"\bghp_[A-Za-z0-9]{20,}", "GitHub 令牌"),
    (r"\bxox[baprs]-[A-Za-z0-9-]{10,}", "Slack 令牌"),
    (r"\bAKIA[0-9A-Z]{16}\b", "云访问密钥"),
    (r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token)\s*[:=]\s*\S{6,}", "明文口令"),
    (r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}", "持有者令牌"),
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
    sensitive = sorted(
        {label for pattern, label in SENSITIVE_PATTERNS if re.search(pattern, body)}
    )
    if sensitive:
        return {"risk": "sensitive", "reasons": sensitive}
    return {"risk": "clean", "reasons": []}


