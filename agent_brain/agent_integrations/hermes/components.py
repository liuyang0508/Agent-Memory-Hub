from __future__ import annotations

from pathlib import Path
from typing import Callable

from agent_brain.platform.embedding import Embedder


def build_store(brain_dir: Path):
    from agent_brain.memory.store.items_store import ItemsStore

    return ItemsStore(items_dir=brain_dir / "items")


def build_components(
    brain_dir: Path,
    embedder_factory: Callable[[], Embedder],
) -> tuple:
    from agent_brain.memory.recall.retrieval import Retriever
    from agent_brain.platform.indexing.index import HubIndex

    store = build_store(brain_dir)
    embedder = embedder_factory()
    index = HubIndex(db_path=brain_dir / "index.db", embedding_dim=embedder.dim)
    return store, index, Retriever(index=index, embedder=embedder)


__all__ = ["build_components", "build_store"]
