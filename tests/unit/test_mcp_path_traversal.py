"""P1-3: MCP item_id must not escape the items dir (path traversal).

delete_memory / batch_archive built ``items_dir / f"{item_id}.md"`` directly,
so item_id="../../../tmp/evil" resolved outside the brain pool — an attacker
(or a confused agent) could unlink arbitrary files.
"""
from pathlib import Path

import pytest


def test_resolve_rejects_parent_traversal(tmp_path: Path) -> None:
    from agent_brain.memory.store.items_store import ItemsStore
    from agent_brain.interfaces.mcp.server import _resolve_item_path

    store = ItemsStore(items_dir=tmp_path / "items")
    with pytest.raises(ValueError):
        _resolve_item_path(store, "../../../tmp/evil")


def test_resolve_rejects_absolute_and_slashes(tmp_path: Path) -> None:
    from agent_brain.memory.store.items_store import ItemsStore
    from agent_brain.interfaces.mcp.server import _resolve_item_path

    store = ItemsStore(items_dir=tmp_path / "items")
    for bad in ("/etc/passwd", "sub/dir/escape", "..\\..\\win"):
        with pytest.raises(ValueError):
            _resolve_item_path(store, bad)


def test_resolve_accepts_normal_id(tmp_path: Path) -> None:
    from agent_brain.memory.store.items_store import ItemsStore
    from agent_brain.interfaces.mcp.server import _resolve_item_path

    store = ItemsStore(items_dir=tmp_path / "items")
    p = _resolve_item_path(store, "mem-20260519-100000-ok")
    assert p == (store.items_dir.resolve() / "mem-20260519-100000-ok.md")
