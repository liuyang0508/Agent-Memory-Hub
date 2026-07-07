"""Obsidian wiki layer — Karpathy LLM-Wiki style overview pages.

On top of the per-item export in ``obsidian.py``, this generates the human-
browsable ``wiki/`` surface a beginner expects when opening the vault:

  - ``index.md``        — what's in the pool (counts by type / abstraction / tier
                          / project + the most recent items)
  - ``log.md``          — maintenance log (recent items, newest first)
  - ``health/report.md``— structural health (islands, unsourced claims, stale
                          items). Offline-only — run ``memory anti-drift`` for the
                          semantic contradiction pass.

Builders are pure (items in, markdown string out) so they test without a vault.
These pages are *generated views* — they never live in ``items/`` and never
pollute the md source of truth.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from agent_brain.memory.governance.tiering import tier_for_item
from agent_brain.memory.evidence.integrations.obsidian import _slugify
from agent_brain.contracts.memory_item import MemoryItem

ItemBody = tuple[MemoryItem, str]

_SOURCED_TYPES = {"fact", "decision"}


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _sort_ts(dt: datetime) -> float:
    return _aware(dt).timestamp()


def _link(item: MemoryItem) -> str:
    return f"[[{item.id}|{item.title}]]"


def build_index(items: list[ItemBody], *, now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    total = len(items)
    by_type = Counter(str(it.type) for it, _ in items)
    by_abs = Counter(str(it.abstraction) for it, _ in items)
    by_proj = Counter((it.project or "(none)") for it, _ in items)
    by_tier = Counter(tier_for_item(it, now).value for it, _ in items)

    lines = [
        "# Brain Pool Index",
        "",
        f"> 自动生成 {now:%Y-%m-%d %H:%M} · 共 {total} 条记忆",
        "",
        "## By type (功能)",
    ]
    for key, count in sorted(by_type.items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"- {key}: {count}")

    lines += ["", "## By abstraction (提炼层)"]
    for key in ("L0", "L1", "L2"):
        lines.append(f"- {key}: {by_abs.get(key, 0)}")

    lines += ["", "## By tier (存储热度)"]
    for key in ("hot", "warm", "cold"):
        lines.append(f"- {key}: {by_tier.get(key, 0)}")

    lines += ["", "## By project"]
    for key, count in sorted(by_proj.items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"- {key}: {count}")

    recent = sorted(items, key=lambda p: _sort_ts(p[0].created_at), reverse=True)[:10]
    lines += ["", "## Recent"]
    if not recent:
        lines.append("- (empty)")
    for it, _ in recent:
        lines.append(f"- {it.created_at:%Y-%m-%d} [{it.type}] {_link(it)}")

    return "\n".join(lines) + "\n"


def build_log(items: list[ItemBody], *, now: Optional[datetime] = None, limit: int = 30) -> str:
    now = now or datetime.now(timezone.utc)
    ordered = sorted(items, key=lambda p: _sort_ts(p[0].created_at), reverse=True)[:limit]
    lines = [
        "# Maintenance Log",
        "",
        f"> 自动生成 {now:%Y-%m-%d %H:%M} · 最近 {len(ordered)} 条写入",
        "",
        "## Recent items",
    ]
    if not ordered:
        lines.append("- (empty)")
    for it, _ in ordered:
        lines.append(
            f"- {it.created_at:%Y-%m-%d %H:%M} · [{it.type}] {it.title} "
            f"(`{it.id}`) · conf {it.confidence} · {it.abstraction}"
        )
    return "\n".join(lines) + "\n"


def _health_section(title: str, items: list[MemoryItem]) -> list[str]:
    lines = [f"## {title} ({len(items)})"]
    if not items:
        lines.append("- (none)")
    for it in items[:50]:
        lines.append(f"- {_link(it)} (`{it.id}`)")
    return lines


def build_health(
    items: list[ItemBody],
    *,
    now: Optional[datetime] = None,
    stale_days: int = 180,
    stale_confidence: float = 0.4,
) -> str:
    now = now or datetime.now(timezone.utc)
    referenced: set[str] = set()
    for it, _ in items:
        referenced.update(it.refs.mems)

    islands: list[MemoryItem] = []
    missing_source: list[MemoryItem] = []
    stale: list[MemoryItem] = []
    for it, _ in items:
        has_out = bool(it.refs.mems)
        has_in = it.id in referenced
        if not has_out and not has_in:
            islands.append(it)
        if str(it.type) in _SOURCED_TYPES and not it.refs.mems and not it.refs.urls:
            missing_source.append(it)
        age_days = (_aware(now) - _aware(it.created_at)).days
        if age_days >= stale_days and it.confidence < stale_confidence:
            stale.append(it)

    lines = [
        "# Health Report",
        "",
        f"> 自动生成 {now:%Y-%m-%d %H:%M} · 仅结构检查"
        "（语义矛盾请跑 `memory anti-drift`）",
        "",
    ]
    lines += _health_section("Islands (无 inbound/outbound 链接)", islands)
    lines += [""]
    lines += _health_section("Missing source (无 refs.mems / urls 的事实/决策)", missing_source)
    lines += [""]
    lines += _health_section("Possibly stale (老旧 + 低 confidence)", stale)
    return "\n".join(lines) + "\n"


def write_wiki_pages(
    items: list[ItemBody],
    vault_dir: Path,
    *,
    now: Optional[datetime] = None,
) -> list[Path]:
    """Write index.md / log.md / health/report.md into the vault. Returns paths."""
    vault_dir = Path(vault_dir)
    vault_dir.mkdir(parents=True, exist_ok=True)
    health_dir = vault_dir / "health"
    health_dir.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []
    index_path = vault_dir / "index.md"
    index_path.write_text(build_index(items, now=now), encoding="utf-8")
    paths.append(index_path)

    log_path = vault_dir / "log.md"
    log_path.write_text(build_log(items, now=now), encoding="utf-8")
    paths.append(log_path)

    health_path = health_dir / "report.md"
    health_path.write_text(build_health(items, now=now), encoding="utf-8")
    paths.append(health_path)

    return paths
