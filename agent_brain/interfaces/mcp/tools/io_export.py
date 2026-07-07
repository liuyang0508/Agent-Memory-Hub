from __future__ import annotations

import json
from typing import Any

from agent_brain.contracts.memory_item import MemoryItem


def build_export_payload(
    item_bodies: list[tuple[MemoryItem, str]],
    *,
    type: str | None = None,
    project: str | None = None,
    tenant_id: str | None = None,
    format: str = "json",
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for item, body in item_bodies:
        if type and str(item.type) != type:
            continue
        if project and item.project != project:
            continue
        if tenant_id and item.tenant_id != tenant_id:
            continue
        items.append({
            "frontmatter": item.model_dump(mode="json"),
            "body": body,
        })
    if format == "jsonl":
        lines = [json.dumps(it, ensure_ascii=False) for it in items]
        return {"format": "jsonl", "count": len(items), "data": "\n".join(lines)}
    return {"format": "json", "count": len(items), "items": items}
