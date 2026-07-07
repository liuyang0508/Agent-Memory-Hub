from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.memory.store.items_store import ItemsStore


def _write_item(
    brain_dir: Path,
    item_id: str,
    mem_type: MemoryType,
    title: str,
    summary: str,
    body: str,
    *,
    confidence: float = 0.8,
    tags: list[str] | None = None,
) -> MemoryItem:
    store = ItemsStore(brain_dir / "items")
    item = MemoryItem(
        id=item_id,
        type=mem_type,
        created_at=datetime(2026, 6, 21, tzinfo=timezone.utc),
        title=title,
        summary=summary,
        tags=tags or [],
        confidence=confidence,
        project="agent-memory-hub",
    )
    store.write(item, body)
    return item


def test_memory_profile_export_renders_agent_rule_file_without_writing_items(tmp_path: Path) -> None:
    from agent_brain.product.memory_profiles import export_memory_profile

    source = _write_item(
        tmp_path,
        "mem-20260621-010000-write-funnel",
        MemoryType.decision,
        "Use WriteService as the write funnel",
        "All durable memory writes must go through WriteService.",
        "**决策** Use WriteService.\n**理由** audit gate and index consistency.",
        tags=["architecture"],
    )
    _write_item(
        tmp_path,
        "mem-20260621-010100-low-confidence",
        MemoryType.fact,
        "Low confidence note",
        "Should not be exported.",
        "draft",
        confidence=0.2,
    )

    result = export_memory_profile(tmp_path, target="codex", apply=False)

    assert result.target == "codex"
    assert result.relative_path == "AGENTS.md"
    assert source.id in result.source_item_ids
    assert "Use WriteService as the write funnel" in result.text
    assert "Low confidence note" not in result.text
    assert not (tmp_path / "AGENTS.md").exists()
    assert len(list((tmp_path / "items").glob("*.md"))) == 2


def test_memory_profile_export_apply_writes_managed_block_idempotently(tmp_path: Path) -> None:
    from agent_brain.product.memory_profiles import export_memory_profile

    _write_item(
        tmp_path,
        "mem-20260621-010200-project-policy",
        MemoryType.policy,
        "Keep docs honest",
        "Do not describe install-ready adapters as verified.",
        "**规则** verified requires runtime evidence.",
    )
    path = tmp_path / "CLAUDE.md"
    path.write_text("# Existing\n\nKeep this.\n", encoding="utf-8")

    first = export_memory_profile(tmp_path, target="claude-code", apply=True)
    second = export_memory_profile(tmp_path, target="claude-code", apply=True)

    assert first.path == path
    assert second.path == path
    text = path.read_text(encoding="utf-8")
    assert text.count("BEGIN agent-memory-hub profile") == 1
    assert "Keep this." in text
    assert "Do not describe install-ready adapters as verified." in text
