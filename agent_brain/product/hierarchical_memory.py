"""Derived L2/L3 memory hierarchy sidecar.

This module builds a deterministic summary projection from MemoryItem markdown.
It never writes canonical memories; the output is a rebuildable sidecar under
``derived/``.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.memory.store.items_store import ItemsStore

DEFAULT_HIERARCHY_PATH = Path("derived") / "hierarchical-memory.json"


@dataclass(frozen=True)
class HierarchicalMemoryReport:
    path: Path
    payload: dict[str, Any]
    applied: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "payload": self.payload,
            "applied": self.applied,
        }


def build_hierarchical_memory(
    brain_dir: Path,
    *,
    apply: bool = False,
    max_topics: int = 24,
    max_items_per_node: int = 8,
    now: datetime | None = None,
) -> HierarchicalMemoryReport:
    root = Path(brain_dir)
    items = list(ItemsStore(root / "items").iter_all())
    generated_at = _utc(now).isoformat()
    l2_topics = _build_l2_topics(
        items,
        max_topics=max_topics,
        max_items_per_node=max_items_per_node,
    )
    l3_projects = _build_l3_projects(items, max_items_per_node=max_items_per_node)
    payload = {
        "version": 1,
        "generated_at": generated_at,
        "source": "items",
        "l2_topics": l2_topics,
        "l3_projects": l3_projects,
    }
    path = root / DEFAULT_HIERARCHY_PATH
    if apply:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return HierarchicalMemoryReport(path=path, payload=payload, applied=apply)


def _build_l2_topics(
    items: list[tuple[MemoryItem, str]],
    *,
    max_topics: int,
    max_items_per_node: int,
) -> list[dict[str, Any]]:
    groups: dict[str, list[tuple[MemoryItem, str]]] = defaultdict(list)
    for item, body in items:
        keys = item.tags or [str(item.type)]
        for key in keys:
            groups[_slug(key)].append((item, body))
    ranked = sorted(
        groups.items(),
        key=lambda pair: (len(pair[1]), _avg_confidence(pair[1]), pair[0]),
        reverse=True,
    )
    return [
        _node(
            level="L2",
            node_id=f"topic:{key}",
            title=f"Topic: {key}",
            items=group,
            max_items=max_items_per_node,
        )
        for key, group in ranked[:max_topics]
    ]


def _build_l3_projects(
    items: list[tuple[MemoryItem, str]],
    *,
    max_items_per_node: int,
) -> list[dict[str, Any]]:
    groups: dict[str, list[tuple[MemoryItem, str]]] = defaultdict(list)
    for item, body in items:
        groups[_slug(item.project or "default")].append((item, body))
    ranked = sorted(
        groups.items(),
        key=lambda pair: (len(pair[1]), _avg_confidence(pair[1]), pair[0]),
        reverse=True,
    )
    return [
        _node(
            level="L3",
            node_id=f"project:{key}",
            title=f"Project: {key}",
            items=group,
            max_items=max_items_per_node,
        )
        for key, group in ranked
    ]


def _node(
    *,
    level: str,
    node_id: str,
    title: str,
    items: list[tuple[MemoryItem, str]],
    max_items: int,
) -> dict[str, Any]:
    ranked = sorted(items, key=lambda pair: (pair[0].confidence, pair[0].created_at), reverse=True)
    source_ids = [item.id for item, _ in ranked[:max_items]]
    return {
        "id": node_id,
        "level": level,
        "title": title,
        "summary": _summarize(ranked),
        "source_item_ids": source_ids,
        "item_count": len(items),
        "confidence": round(_avg_confidence(items), 4),
        "types": sorted({str(item.type) for item, _ in items}),
        "tags": sorted({tag for item, _ in items for tag in item.tags}),
    }


def _summarize(items: list[tuple[MemoryItem, str]], limit: int = 360) -> str:
    snippets = []
    for item, _body in items[:5]:
        snippets.append(f"{item.title}: {item.summary}")
    compact = " ".join(" ".join(part.split()) for part in snippets if part.strip())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _avg_confidence(items: list[tuple[MemoryItem, str]]) -> float:
    if not items:
        return 0.0
    return sum(item.confidence for item, _ in items) / len(items)


def _slug(value: str) -> str:
    cleaned = "-".join(str(value).strip().lower().replace("_", "-").split())
    return cleaned or "default"


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


__all__ = ["DEFAULT_HIERARCHY_PATH", "HierarchicalMemoryReport", "build_hierarchical_memory"]
