"""Shared CLI imports + module-level state + helpers, moved verbatim from cli.py.

``console`` lives here so every command module resolves to one object.
``_SCHEMA_COMPAT_VERSION`` is internal — re-exported via cli/__init__ for backwards compat.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
import typer
from rich.console import Console
from rich.table import Table
from agent_brain._version import __version__
from agent_brain.memory.governance.audit.outbound import list_outbound_events
from agent_brain.memory.governance.audit.custom_rules import load_merged_rules
from agent_brain.memory.governance.audit.rules import load_builtin_rules, load_rules_from_file
from agent_brain.memory.governance.audit.scanner import SkillScanner
from agent_brain.platform.embedding import get_default_embedder
from agent_brain.platform.indexing.index import HubIndex
from agent_brain.memory.store.items_store import ItemsStore, make_item_id
from agent_brain.memory.recall.retrieval import Retriever, SearchFilter
from agent_brain.memory.governance.evolve.engine import EvolveEngine, EvolveReport
from agent_brain.observability import BrainStats, HealthScore, collect_stats
from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Sensitivity
from agent_brain.interfaces.cli.doctor_offline import doctor_offline as _doctor_offline

# Late binding to the cli package so `_open_components`'s internal embedder
# lookup stays interceptable by mock.patch("agent_brain.interfaces.cli.get_default_embedder").
import agent_brain.interfaces.cli as _cli  # noqa: E402

console = Console()

_SCHEMA_COMPAT_VERSION: str = str(MemoryItem.model_fields["schema_version"].default)


def _brain_dir() -> Path:
    return Path(os.environ.get("BRAIN_DIR", os.path.expanduser("~/.agent-memory-hub")))


def _store_only() -> ItemsStore:
    """Open just the markdown store. No embedder, no sqlite index — usable offline
    and without sentence-transformers model download. Use for govern / anti-drift /
    evolve / audit / inspect commands that only need md frontmatter."""
    return ItemsStore(items_dir=_brain_dir() / "items")


def _resolve_id(store: ItemsStore, prefix: str) -> str:
    """Resolve an item ID prefix to a full ID. Raises typer.Exit on ambiguity or miss.

    Walks ``items_dir`` recursively to match ItemsStore.iter_all's rglob sweep,
    so items living in subdirectories — notably ``items/archived/`` after a
    batch-archive — stay addressable by read/delete instead of resolving only
    against the top level (which left archived items unaddressable).
    """
    candidates = {p.stem: p for p in store.items_dir.rglob("*.md")}
    if prefix in candidates:
        return prefix
    matches = sorted(stem for stem in candidates if stem.startswith(prefix))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        typer.echo(f"ambiguous prefix '{prefix}', matches: {', '.join(matches[:5])}", err=True)
        raise typer.Exit(1)
    typer.echo(f"item not found: {prefix}", err=True)
    raise typer.Exit(1)


def _open_components() -> tuple[ItemsStore, HubIndex, Retriever]:
    """Open store + sqlite index + retriever. Triggers embedder model load,
    which may download from HuggingFace on first use. Use for write / search /
    reindex commands that genuinely need vector search."""
    brain = _brain_dir()
    store = ItemsStore(items_dir=brain / "items")
    embedder = _cli.get_default_embedder()
    idx = HubIndex(db_path=brain / "index.db", embedding_dim=embedder.dim)
    return store, idx, Retriever(index=idx, embedder=embedder)


def _parse_enum(enum_cls, value: str, flag: str):
    """Validate a CLI enum option, exiting cleanly with a usage message instead
    of dumping a raw ValueError traceback when the user passes e.g. --type bogus.
    """
    try:
        return enum_cls(value)
    except ValueError:
        valid = ", ".join(e.value for e in enum_cls)
        typer.echo(f"invalid {flag} {value!r}; choose from: {valid}", err=True)
        raise typer.Exit(2)


def _evict_from_index(item_id: str) -> None:
    """Remove an item from the sqlite index if the index exists.

    md is the source of truth, but a deleted item left in the FTS/vec tables
    keeps surfacing as a permanent ghost hit on search. Skip the embedder model
    load entirely — eviction only touches the index db.
    """
    db_path = _brain_dir() / "index.db"
    if not db_path.exists():
        return
    idx = None
    try:
        idx = HubIndex(db_path=db_path)
        idx.delete(item_id)
    except Exception:  # noqa: BLE001 — index eviction is best-effort
        pass
    finally:
        if idx is not None:
            idx.close()


CURRENT_SCHEMA_VERSION = _SCHEMA_COMPAT_VERSION

__all__ = ['_brain_dir', '_store_only', '_resolve_id', '_open_components', '_parse_enum', '_evict_from_index', '_doctor_offline', 'console', 'CURRENT_SCHEMA_VERSION', '_SCHEMA_COMPAT_VERSION', 'os', 'sys', 'uuid', 'datetime', 'timedelta', 'timezone', 'Path', 'typer', 'Console', 'Table', '__version__', 'list_outbound_events', 'load_merged_rules', 'load_builtin_rules', 'load_rules_from_file', 'SkillScanner', 'get_default_embedder', 'HubIndex', 'ItemsStore', 'make_item_id', 'Retriever', 'SearchFilter', 'EvolveEngine', 'EvolveReport', 'BrainStats', 'HealthScore', 'collect_stats', 'MemoryItem', 'MemoryType', 'Sensitivity']
