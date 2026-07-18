"""Lazy local components used by the Python SDK."""
from __future__ import annotations

from pathlib import Path
from typing import Any


class ClientComponents:
    """Cache ItemsStore, HubIndex, embedder, retriever, and feedback objects."""

    def __init__(self, brain_dir: Path) -> None:
        self.brain_dir = Path(brain_dir)
        self._store: Any = None
        self._index: Any = None
        self._embedder: Any = None
        self._retriever: Any = None
        self._feedback: Any = None

    def get_store(self):
        if self._store is None:
            from agent_brain.memory.store.items_store import ItemsStore

            items_dir = self.brain_dir / "items"
            items_dir.mkdir(parents=True, exist_ok=True)
            self._store = ItemsStore(items_dir=items_dir)
        return self._store

    def get_index(self):
        if self._index is None:
            from agent_brain.platform.indexing.index import HubIndex

            self._index = HubIndex(
                db_path=self.brain_dir / "index.db",
                embedding_dim=64,
            )
        return self._index

    def get_embedder(self):
        if self._embedder is None:
            from agent_brain.platform.embedding import HashingEmbedder

            self._embedder = HashingEmbedder(dim=64)
        return self._embedder

    def get_retriever(self):
        if self._retriever is None:
            from agent_brain.memory.recall.retrieval import Retriever

            self._retriever = Retriever(
                index=self.get_index(),
                embedder=self.get_embedder(),
            )
        return self._retriever

    def get_feedback(self):
        if self._feedback is None:
            from agent_brain.memory.governance.feedback import ConfidenceFeedback

            self._feedback = ConfidenceFeedback(
                index=self.get_index(),
                items_store=self.get_store(),
            )
        return self._feedback

    def close(self) -> None:
        if self._index is not None:
            self._index.close()
        self._feedback = None
        self._retriever = None
        self._index = None


__all__ = ["ClientComponents"]
