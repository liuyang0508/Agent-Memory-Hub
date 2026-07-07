from datetime import datetime
import logging
from pathlib import Path

from agent_brain.contracts.memory_item import MemoryItem, MemoryType


def test_load_sample_fixture(fixtures_dir: Path) -> None:
    from agent_brain.memory.store.items_store import ItemsStore

    store = ItemsStore(items_dir=fixtures_dir / "sample_items")
    items = list(store.iter_all())
    # v1.1 B6: fixture set expanded to 10 items covering 6 types + schema
    # edge cases (CJK id, `+` in id, refs.tags forward-compat, YAML quirks).
    # See tests/conformance/test_v05_compat.py for the exhaustive contract.
    assert len(items) >= 10
    by_id = {item.id: (item, body) for item, body in items}
    item, body = by_id["mem-20260101-120000-sample-fact"]
    assert item.type == "fact"
    assert "**事实**" in body


def test_write_then_read(tmp_brain_dir: Path) -> None:
    from agent_brain.memory.store.items_store import ItemsStore

    store = ItemsStore(items_dir=tmp_brain_dir / "items")
    item = MemoryItem(
        id="mem-20260519-103000-roundtrip",
        type=MemoryType.fact,
        created_at=datetime.fromisoformat("2026-05-19T10:30:00+08:00"),
        title="round trip",
        summary="write then read back",
    )
    body = "**事实**: round-trip 测试"
    path = store.write(item, body)
    assert path.exists()
    loaded_items = list(store.iter_all())
    assert len(loaded_items) == 1
    loaded_item, loaded_body = loaded_items[0]
    assert loaded_item.id == item.id
    assert loaded_body.strip() == body


def test_item_markdown_codec_roundtrips_historical_frontmatter_quirks() -> None:
    from agent_brain.memory.store.item_markdown import parse_item_markdown, render_item_markdown

    text = (
        "\ufeff---\r\n"
        "id: mem-20260519-103000-codec\r\n"
        "type: fact\r\n"
        "created_at: 2026-05-19T10:30:00+08:00\r\n"
        "title: codec\r\n"
        "summary: parse v0.5 quirks\r\n"
        "tags:[]\r\n"
        "---\r\n"
        "\r\n"
        "body text\r\n"
    )

    item, body = parse_item_markdown(text)
    rendered = render_item_markdown(item, body)
    reloaded_item, reloaded_body = parse_item_markdown(rendered)

    assert reloaded_item.id == item.id
    assert reloaded_body.strip() == "body text"


def test_item_markdown_render_omits_unproven_null_fields() -> None:
    from agent_brain.memory.store.item_markdown import render_item_markdown

    item = MemoryItem(
        id="mem-20260519-103001-no-null-noise",
        type=MemoryType.fact,
        created_at=datetime.fromisoformat("2026-05-19T10:30:00+08:00"),
        title="no null noise",
        summary="omit optional null frontmatter fields",
    )

    rendered = render_item_markdown(item, "body text")

    assert "transcript_id: null" not in rendered
    assert "span_hash: null" not in rendered
    assert "extractor: null" not in rendered
    assert "observed_at: null" not in rendered
    assert "last_accessed: null" not in rendered


def test_iter_all_records_bad_items_without_warning_noise(tmp_brain_dir: Path, caplog) -> None:
    from agent_brain.memory.store.items_store import ItemsStore

    store = ItemsStore(items_dir=tmp_brain_dir / "items")
    (store.items_dir / "bad.md").write_text("missing frontmatter\n", encoding="utf-8")

    caplog.set_level(logging.WARNING, logger="agent_brain.memory.store.items_store")

    assert list(store.iter_all()) == []
    assert store.last_scan.skipped_count == 1
    assert store.last_scan.skipped[0].path.name == "bad.md"
    assert not [
        record for record in caplog.records
        if record.name == "agent_brain.memory.store.items_store" and record.levelno >= logging.WARNING
    ]
