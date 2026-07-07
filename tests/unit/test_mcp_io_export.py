from __future__ import annotations

import json
from datetime import datetime, timezone

from agent_brain.contracts.memory_item import MemoryItem, MemoryType


def _item(
    suffix: str,
    *,
    type: str = "fact",
    project: str | None = "agent-memory-hub",
    tenant_id: str | None = "default",
) -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260610-100000-{suffix}",
        type=MemoryType(type),
        created_at=datetime(2026, 6, 10, tzinfo=timezone.utc),
        title=suffix,
        summary=f"summary {suffix}",
        project=project,
        tenant_id=tenant_id,
    )


def test_build_export_payload_filters_items_and_formats_jsonl() -> None:
    from agent_brain.interfaces.mcp.tools.io_export import build_export_payload

    payload = build_export_payload(
        [
            (_item("keep", type="fact", project="p1", tenant_id="t1"), "body keep"),
            (_item("wrong-type", type="decision", project="p1", tenant_id="t1"), "body"),
            (_item("wrong-project", type="fact", project="p2", tenant_id="t1"), "body"),
            (_item("wrong-tenant", type="fact", project="p1", tenant_id="t2"), "body"),
        ],
        type="fact",
        project="p1",
        tenant_id="t1",
        format="jsonl",
    )

    assert payload["format"] == "jsonl"
    assert payload["count"] == 1
    rows = [json.loads(line) for line in payload["data"].splitlines()]
    assert rows[0]["frontmatter"]["id"] == "mem-20260610-100000-keep"
    assert rows[0]["body"] == "body keep"
