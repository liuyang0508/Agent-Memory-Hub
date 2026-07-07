from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.memory.store.items_store import ItemsStore


def _seed(brain_dir: Path, item_id: str, title: str, tag: str) -> None:
    ItemsStore(brain_dir / "items").write(
        MemoryItem(
            id=item_id,
            type=MemoryType.decision,
            created_at=datetime(2026, 6, 21, tzinfo=timezone.utc),
            title=title,
            summary=f"{title} summary",
            tags=[tag],
            project="agent-memory-hub",
            confidence=0.8,
        ),
        f"Body for {title}",
    )


def test_build_hierarchical_memory_writes_l2_l3_derived_sidecar_only(tmp_path: Path) -> None:
    from agent_brain.product.hierarchical_memory import build_hierarchical_memory

    _seed(tmp_path, "mem-20260621-030000-write-service", "WriteService funnel", "write")
    _seed(tmp_path, "mem-20260621-030100-web-sync", "Web capability sync", "web")

    report = build_hierarchical_memory(tmp_path, apply=True)

    assert report.path == tmp_path / "derived" / "hierarchical-memory.json"
    payload = json.loads(report.path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["l2_topics"]
    assert payload["l3_projects"]
    assert {node["level"] for node in payload["l2_topics"]} == {"L2"}
    assert {node["level"] for node in payload["l3_projects"]} == {"L3"}
    assert len(list((tmp_path / "items").glob("*.md"))) == 2
