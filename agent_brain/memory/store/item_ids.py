"""Lightweight discovery of canonical MemoryItem IDs from store filenames."""

from __future__ import annotations

from pathlib import Path

from agent_brain.contracts.memory_item import is_valid_memory_item_id


def known_memory_item_ids(items_dir: Path) -> frozenset[str]:
    """Return canonical active and archived item IDs without parsing bodies."""

    root = Path(items_dir)
    if not root.exists():
        return frozenset()
    return frozenset(
        path.stem
        for path in root.rglob("*.md")
        if path.is_file() and is_valid_memory_item_id(path.stem)
    )


__all__ = ["known_memory_item_ids"]
