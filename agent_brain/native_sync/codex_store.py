"""Which of Codex's two memory stores this machine actually uses.

Codex keeps v1 memory in ``~/.codex/memories`` and v2 in ``~/.codex/memories_v2``
(codex-rs/protocol/src/memory_version.rs). Which one a machine writes and reads
follows a rule in codex-rs/core/src/config/mod.rs, unchanged from its 2026-09-08
introduction through rust-v0.167.0 and stated in the configuration reference:

* ``[memories] version`` in config.toml wins when set;
* otherwise an existing ``memories/`` keeps the machine on v1, and so does an
  explicit ``dual_write = false``;
* otherwise (a fresh home) Codex uses v2.

``dual_write = true`` with v1 selected makes Codex write both stores while still
reading v1, so the store it reads is the one worth sharing. Nothing here decides
for Codex; it mirrors the decision Codex will make, also on a machine where Codex
has not written memory yet, so that installation never creates the one directory
(``memories/``) whose mere existence would move Codex back to v1.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

STORES = ("memories", "memories_v2")
CONFIG = "config.toml"

# Generated artifacts inside a store that are not memory: the v1 extraction scratch
# file and the v2 consolidation prompt artifact (deleted and rewritten each pass).
GENERATED = {("raw_memories.md",), ("phase2_workspace_diff.md",)}

_cache: dict[str, tuple[tuple[object, bool], Path]] = {}


def is_store(root: Path) -> bool:
    """A Codex memory store root; other products name theirs ``memory``."""
    return Path(root).name in STORES


def settings(codex_home: Path) -> tuple[str | None, bool | None]:
    """(version, dual_write) from config.toml; unreadable or malformed reads as unset."""
    try:
        with open(Path(codex_home) / CONFIG, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, ValueError):
        return None, None
    memories = data.get("memories")
    if not isinstance(memories, dict):
        return None, None
    version = memories.get("version")
    version = version.strip().lower() if isinstance(version, str) else None
    dual_write = memories.get("dual_write")
    return (
        version if version in ("v1", "v2") else None,
        dual_write if isinstance(dual_write, bool) else None,
    )


def _state(codex_home: Path) -> tuple[object, bool]:
    try:
        stat = os.stat(codex_home / CONFIG)
        config: object = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
    except OSError:
        config = None
    return config, (codex_home / "memories").exists()


def active(codex_home: Path) -> Path:
    """The store Codex reads on this machine, by Codex's own rule."""
    codex_home = Path(codex_home)
    key = _state(codex_home)
    cached = _cache.get(str(codex_home))
    if cached and cached[0] == key:
        return cached[1]
    version, dual_write = settings(codex_home)
    if version == "v2":
        store = codex_home / "memories_v2"
    elif version == "v1" or key[1] or dual_write is False:
        store = codex_home / "memories"
    else:
        store = codex_home / "memories_v2"
    _cache[str(codex_home)] = (key, store)
    return store


def roots(codex_home: Path) -> list[Path]:
    """The active store first, then the other one when it may be watched too.

    ``memories_v2`` is always listed so a later ``version = "v2"`` is picked up
    without reinstalling; ``memories`` only when it already exists, because
    creating it would change what Codex does (see :func:`may_create`).
    """
    codex_home = Path(codex_home)
    chosen = active(codex_home)
    others = [
        codex_home / name
        for name in STORES
        if name != chosen.name and (name != "memories" or (codex_home / name).is_dir())
    ]
    return [chosen, *others]


def may_create(root: Path) -> bool:
    """Whether installation may create this memory root when it is missing.

    An empty ``memories_v2/`` is inert to Codex (it initializes the directory
    itself when selected). An empty ``memories/`` is not: Codex reads its
    existence as "this machine is on v1".
    """
    root = Path(root)
    return root.name != "memories" or active(root.parent) == root


def withheld(root: Path, rel: Path | str) -> bool:
    """Files in a Codex store that stay on this machine.

    The whole store Codex no longer reads is withheld: copies already on the Hub
    stay, edits stop, and the store Codex does read takes over. Inside the active
    store, generated artifacts and ``extensions/skysight/`` (what Computer History
    derives from what a person did on screen) are withheld.
    """
    root = Path(root)
    if active(root.parent) != root:
        return True
    parts = Path(rel).parts
    return parts in GENERATED or parts[:2] == ("extensions", "skysight")
