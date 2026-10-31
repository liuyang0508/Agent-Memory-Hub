"""One-click history import: discover, group and select existing local memory.

The Hub never sees anything here. Selection is still bound to a fresh preview
per source, so a category selected in the terminal cannot upload a file that
changed after it was counted.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .client import NativeClient
from .install import discover_assets
from .source import files, forbidden_file
from .common import PRODUCTS, display_name

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
        base = home / "Library/Application Support/Claude/local-agent-mode-sessions"
        if base.is_dir():
            for memory in sorted(base.rglob("MEMORY.md")):
                if memory.is_symlink() or not memory.parent.is_dir():
                    continue
                resolved = memory.parent.resolve()
                seen.setdefault(str(resolved), (resolved, "memory"))
    return list(seen.values())


def catalog(client: NativeClient, home: Path, products: list[str]) -> list[dict[str, Any]]:
    """Count files per (product, kind) without reading any body."""
    entries: list[dict[str, Any]] = []
    for product in products:
        source_product = (
            "codex-shared"
            if product in ("codex-cli", "codex-desktop", "chatgpt-desktop")
            else product
        )
        for root, kind in roots(home, product):
            files_count = size = oversized = 0
            newest = 0.0
            for path in files(root, kind):
                if forbidden_file(path):
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if stat.st_size > MAX_BYTES:
                    # Counted separately so the list never promises an upload that will fail.
                    oversized += 1
                    continue
                files_count += 1
                size += stat.st_size
                newest = max(newest, stat.st_mtime)
            if not files_count and not oversized:
                continue
            entries.append(
                {
                    "product": source_product,
                    "product_name": PRODUCTS[source_product],
                    "kind": kind,
                    "root": str(root),
                    "files": files_count,
                    "oversized": oversized,
                    "bytes": size,
                    "newest": newest,
                    "note": "" if source_product != "codex-shared" else "CLI/桌面共用存储",
                }
            )
    entries.sort(key=lambda e: (CATEGORY_ORDER.index(e["kind"]) if e["kind"] in CATEGORY_ORDER else 99, e["product"]))
    return entries


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


def items(home: Path, products: list[str]) -> list[dict[str, Any]]:
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
    for (root_path, product, kind), rows in by_root.items():
        root = Path(root_path)
        sid = client.register(root, product, "", kind)
        registered += 1
        wanted = {row["path"] for row in rows}
        current = {}
        for preview in client.preview(sid):
            if not preview["hash"]:
                continue
            absolute = str(root if root.is_file() else root / preview["path"])
            current[absolute] = preview
        selection = {}
        for absolute in wanted:
            preview = current.get(absolute)
            if preview is None:
                rejected.append(absolute)
                continue
            verdict = screen(preview.get("body", ""))
            if verdict["risk"] == "blocked":
                rejected.append(absolute)
                continue
            selection[preview["path"]] = preview["hash"]
        if selection:
            client.select(sid, selection)
            taken += len(selection)
    return {"roots": registered, "selected": taken, "rejected": rejected}


def group(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse per-root rows into one row per (product, kind) for a clean selection list."""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in entries:
        key = (entry["product"], entry["kind"])
        row = merged.setdefault(
            key,
            {
                "product": entry["product"],
                "product_name": entry["product_name"],
                "kind": entry["kind"],
                "roots": [],
                "files": 0,
                "oversized": 0,
                "bytes": 0,
                "newest": 0.0,
                "note": entry["note"],
            },
        )
        row["roots"].append(entry["root"])
        row["files"] += entry["files"]
        row["oversized"] += entry.get("oversized", 0)
        row["bytes"] += entry["bytes"]
        row["newest"] = max(row["newest"], entry["newest"])
    ordered = sorted(
        merged.values(),
        key=lambda r: (
            CATEGORY_ORDER.index(r["kind"]) if r["kind"] in CATEGORY_ORDER else 99,
            r["product"],
        ),
    )
    return ordered


# Content screening runs before anything is selected. A memory file is free text:
# grouping by product and kind says nothing about whether one person's salary,
# home address or a pasted private key sits inside it.
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


def import_category(
    client: NativeClient,
    home: Path,
    product: str,
    kind: str,
    *,
    accept_sensitive: set[str] | None = None,
) -> dict[str, Any]:
    """Select a category's files, screening every body first.

    Clean files are selected automatically. Blocked files are never selected.
    Sensitive files are selected only when their path was explicitly approved,
    so "导入这一个类别" can no longer mean "上传我全部的私人内容".
    """
    accepted = accept_sensitive or set()
    selected = 0
    skipped = 0
    blocked: list[str] = []
    sensitive: list[dict[str, Any]] = []
    roots_used = []
    for root, root_kind in roots(home, product):
        if root_kind != kind:
            continue
        roots_used.append(str(root))
        if kind == "memory":
            root.mkdir(parents=True, exist_ok=True)
        sid = client.register(root, product, "", kind)
        rows = client.preview(sid)
        selection = {}
        for row in rows:
            if not row["hash"]:
                skipped += 1
                continue
            # Decisions are keyed by absolute path: two roots may hold the same
            # relative name, and approving one must not approve the other.
            absolute = str(root if root.is_file() else root / row["path"])
            verdict = screen(row.get("body", ""))
            if verdict["risk"] == "blocked":
                blocked.append(absolute)
                continue
            if verdict["risk"] == "sensitive":
                sensitive.append({"path": absolute, "reasons": verdict["reasons"]})
                if absolute not in accepted:
                    continue
            selection[row["path"]] = row["hash"]
        if selection:
            client.select(sid, selection)
            selected += len(selection)
    return {
        "product": product,
        "kind": kind,
        "roots": roots_used,
        "selected": selected,
        "skipped": skipped,
        "blocked": blocked,
        "sensitive": sensitive,
    }


def survey_category(home: Path, product: str, kind: str) -> dict[str, Any]:
    """Screen a category without a joined profile, for the pre-import preview.

    Reads bodies locally only. Counts are what the member will actually be
    offered: blocked files are reported and never offered for selection.
    """
    from .source import read_document

    clean = blocked = unreadable = 0
    sensitive: list[dict[str, Any]] = []
    for root, root_kind in roots(home, product):
        if root_kind != kind:
            continue
        for path in files(root, kind):
            if forbidden_file(path):
                continue
            try:
                document = read_document(path, kind)
            except (OSError, ValueError, UnicodeError):
                unreadable += 1
                continue
            if document is None:
                continue
            verdict = screen(document[0])
            if verdict["risk"] == "blocked":
                blocked += 1
            elif verdict["risk"] == "sensitive":
                sensitive.append(
                    {"path": str(path), "reasons": verdict["reasons"], "size": len(document[0])}
                )
            else:
                clean += 1
    return {
        "product": product,
        "kind": kind,
        "clean": clean,
        "sensitive": sensitive,
        "blocked": blocked,
        "unreadable": unreadable,
    }


def review_sensitive(
    client: NativeClient, home: Path, product: str, kind: str
) -> list[dict[str, Any]]:
    """List sensitive files for a category with a short excerpt per file.

    Excerpts stay local and are shown one at a time so a member can decide what
    a shared brain should contain, rather than trusting a filename.
    """
    found: list[dict[str, Any]] = []
    for root, root_kind in roots(home, product):
        if root_kind != kind:
            continue
        sid = client.register(root, product, "", kind)
        for row in client.preview(sid):
            if not row["hash"]:
                continue
            verdict = screen(row.get("body", ""))
            if verdict["risk"] != "sensitive":
                continue
            excerpt = " ".join(row.get("body", "").split())[:160]
            found.append({"path": row["path"], "reasons": verdict["reasons"], "excerpt": excerpt})
    return found


def describe(row: dict[str, Any]) -> str:
    """Human line for one category; never includes file contents."""
    from datetime import datetime

    when = (
        datetime.fromtimestamp(row["newest"]).strftime("%Y-%m-%d") if row["newest"] else "未知"
    )
    size = row["bytes"] / 1024 / 1024
    label = f"{row['product_name']} · {display_name(row['kind'])}"
    return f"{label}：{row['files']} 个文件 / {size:.1f} MB / 最近 {when}"
