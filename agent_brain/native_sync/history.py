"""One-click history import: discover, group and select existing local memory.

The Hub never sees anything here. Selection is still bound to a fresh preview
per source, so a category selected in the terminal cannot upload a file that
changed after it was counted.
"""

from __future__ import annotations

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
        if root.is_file():
            seen.setdefault(str(root.resolve()), (root, entry["kind"]))
            continue
        if not root.is_dir():
            continue
        seen.setdefault(str(root.resolve()), (root, entry["kind"]))
    # Claude Code keeps per-project memory outside ~/.claude: one directory each.
    if product == "claude-code":
        for memory in sorted((home / ".claude/projects").glob("*/memory")):
            if memory.is_dir() and not memory.is_symlink():
                seen.setdefault(str(memory.resolve()), (memory, "memory"))
    # Cowork stores each workspace's memory beside its session rather than in a fixed root.
    if product == "cowork":
        base = home / "Library/Application Support/Claude/local-agent-mode-sessions"
        if base.is_dir():
            for memory in sorted(base.rglob("MEMORY.md")):
                if memory.is_symlink() or not memory.parent.is_dir():
                    continue
                seen.setdefault(str(memory.parent.resolve()), (memory.parent, "memory"))
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


def import_category(client: NativeClient, home: Path, product: str, kind: str) -> dict[str, Any]:
    """Register every root of one category and select all currently visible files.

    Each source is previewed immediately before selection, so the hash check in
    ``select`` still guards against a file that changed between counting and now.
    """
    selected = 0
    skipped = 0
    roots_used = []
    for root, root_kind in roots(home, product):
        if root_kind != kind:
            continue
        roots_used.append(str(root))
        if kind == "memory":
            root.mkdir(parents=True, exist_ok=True)
        sid = client.register(root, product, "", kind)
        rows = client.preview(sid)
        selection = {row["path"]: row["hash"] for row in rows}
        if selection:
            client.select(sid, selection)
            selected += len(selection)
        skipped += sum(1 for row in rows if not row["hash"])
    return {"product": product, "kind": kind, "roots": roots_used, "selected": selected, "skipped": skipped}


def describe(row: dict[str, Any]) -> str:
    """Human line for one category; never includes file contents."""
    from datetime import datetime

    when = (
        datetime.fromtimestamp(row["newest"]).strftime("%Y-%m-%d") if row["newest"] else "未知"
    )
    size = row["bytes"] / 1024 / 1024
    label = f"{row['product_name']} · {display_name(row['kind'])}"
    return f"{label}：{row['files']} 个文件 / {size:.1f} MB / 最近 {when}"
