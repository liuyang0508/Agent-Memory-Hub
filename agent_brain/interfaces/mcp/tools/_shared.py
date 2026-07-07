"""Shared helpers + the common import surface for every MCP tool tier module.

Moved verbatim from the former monolithic ``mcp_server.py``. ``_components_cache``
lives here so the read/write path and ``mcp_server._components_cache.clear()`` in
tests operate on the SAME dict object across all tier modules.
"""
from __future__ import annotations

from __future__ import annotations
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from agent_brain.memory.governance.audit.outbound import list_outbound_events
from agent_brain.memory.governance.audit.rules import load_builtin_rules
from agent_brain.memory.governance.audit.scanner import SkillScanner
from agent_brain.platform.embedding import get_default_embedder
from agent_brain.platform.indexing.index import HubIndex
from agent_brain.memory.store.items_store import ItemsStore, make_item_id
from agent_brain.memory.recall.retrieval import Retriever, SearchFilter, suggest_tags as _suggest_tags
from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Sensitivity, TYPE_TO_DECAY_CLASS

_components_cache: dict[str, tuple[ItemsStore, HubIndex, Retriever]] = {}


def _brain_dir() -> Path:
    return Path(os.environ.get("BRAIN_DIR", os.path.expanduser("~/.agent-memory-hub")))


def _components() -> tuple[ItemsStore, HubIndex, Retriever]:
    """Return cached (store, index, retriever) per brain dir.

    Building these per call re-loaded the embedder model and opened a fresh
    sqlite connection every time — leaking file descriptors and risking lock
    crashes. Cache by brain-dir path so different BRAIN_DIRs (e.g. across tests)
    stay isolated while a stable dir reuses one set.
    """
    brain = _brain_dir()
    key = str(brain)
    cached = _components_cache.get(key)
    if cached is None:
        store = ItemsStore(items_dir=brain / "items")
        embedder = get_default_embedder()
        idx = HubIndex(db_path=brain / "index.db", embedding_dim=embedder.dim)
        cached = (store, idx, Retriever(index=idx, embedder=embedder))
        _components_cache[key] = cached
    return cached


def _resolve_item_path(store: ItemsStore, item_id: str) -> Path:
    """Resolve ``item_id`` to its md path, refusing path traversal.

    ``delete_memory`` / ``batch_archive`` build the path straight from a
    caller-supplied id, so ``item_id="../../../tmp/evil"`` would unlink files
    outside the brain pool. Reject any id with path separators or ``..`` and
    confirm the resolved path stays inside ``items_dir``.
    """
    if not item_id or any(ch in item_id for ch in ("/", "\\")) or ".." in item_id:
        raise ValueError(f"invalid item_id (path traversal): {item_id!r}")
    items_dir = store.items_dir.resolve()
    candidate = (items_dir / f"{item_id}.md").resolve()
    if not candidate.is_relative_to(items_dir):
        raise ValueError(f"invalid item_id (path traversal): {item_id!r}")
    return candidate


__all__ = ['_brain_dir', '_components', '_components_cache', '_resolve_item_path', 'os', 'uuid', 'datetime', 'timezone', 'Path', 'Any', 'list_outbound_events', 'load_builtin_rules', 'SkillScanner', 'get_default_embedder', 'HubIndex', 'ItemsStore', 'make_item_id', 'Retriever', 'SearchFilter', '_suggest_tags', 'MemoryItem', 'MemoryType', 'Sensitivity', 'TYPE_TO_DECAY_CLASS']
