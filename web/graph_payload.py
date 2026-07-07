"""Web graph payload assembly helpers."""

from __future__ import annotations

import re
from typing import Any

from web.auth import CurrentUser
from web.visibility import visible


def _resolve_wiki_target(token: str, valid_ids: set[str], title_to_id: dict[str, str]) -> str | None:
    """Resolve an Obsidian wiki-link token to a visible memory id."""
    target = token.strip()
    for sep in ("|", "#", "^"):
        if sep in target:
            target = target.split(sep, 1)[0].strip()
    if "/" in target:
        target = target.rsplit("/", 1)[-1].strip()
    if target.lower().endswith(".md"):
        target = target[:-3].strip()
    if not target:
        return None
    if target in valid_ids:
        return target

    resolved = title_to_id.get(target.lower())
    if resolved:
        return resolved

    for item_id in valid_ids:
        if item_id.lower() == target.lower():
            return item_id
    return None


def build_full_graph_payload(store: Any, index: Any, user: CurrentUser, debug: bool = False) -> dict[str, Any]:
    """Build the full graph visualization payload for visible memory items."""
    nodes: list[dict[str, Any]] = []
    all_edges: list[dict[str, str]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    items_pairs: list[tuple[Any, str]] = []

    for item, body in store.iter_all():
        if not visible(item, user):
            continue
        items_pairs.append((item, body))
        nodes.append({
            "id": item.id,
            "title": item.title,
            "type": str(item.type),
            "project": item.project,
            "confidence": item.confidence,
        })

    valid_ids = {node["id"] for node in nodes}
    title_to_id: dict[str, str] = {}
    for node in nodes:
        if node["title"]:
            title_to_id.setdefault(node["title"].strip().lower(), node["id"])

    def add_edge(source: str, target: str, label: str) -> None:
        if source == target or target not in valid_ids or source not in valid_ids:
            return
        key = (source, target, label)
        if key in seen_edges:
            return
        seen_edges.add(key)
        all_edges.append({"source": source, "target": target, "label": label})

    wiki_re = re.compile(r"\[\[([^\]\n]+?)\]\]")
    debug_info = {"wiki_total": 0, "wiki_resolved": 0, "wiki_unmatched_samples": []}

    for item, body in items_pairs:
        for source, target, label in index.get_refs(item.id):
            add_edge(source, target, label)
        for target in getattr(item.refs, "mems", []) or []:
            add_edge(item.id, target, "refs")
        if body:
            for match in wiki_re.findall(body):
                debug_info["wiki_total"] += 1
                resolved = _resolve_wiki_target(match, valid_ids, title_to_id)
                if resolved:
                    debug_info["wiki_resolved"] += 1
                    add_edge(item.id, resolved, "wiki")
                elif len(debug_info["wiki_unmatched_samples"]) < 10:
                    debug_info["wiki_unmatched_samples"].append(match.strip()[:80])

    result: dict[str, Any] = {"nodes": nodes, "edges": all_edges}
    if debug:
        result["_debug"] = debug_info
    return result


__all__ = ["build_full_graph_payload"]
