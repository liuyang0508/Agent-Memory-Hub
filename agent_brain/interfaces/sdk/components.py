"""Lazy local components used by the Python SDK."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_brain.memory.governance.feedback import ConfidenceFeedback
    from agent_brain.memory.recall.retrieval import Retriever
    from agent_brain.memory.store.items_store import ItemsStore
    from agent_brain.platform.embedding import Embedder
    from agent_brain.platform.indexing.index import HubIndex


class ClientComponents:
    """Cache ItemsStore, HubIndex, embedder, retriever, and feedback objects."""

    def __init__(self, brain_dir: Path) -> None:
        self.brain_dir = Path(brain_dir)
        self._store: ItemsStore | None = None
        self._index: HubIndex | None = None
        self._embedder: Embedder | None = None
        self._retriever: Retriever | None = None
        self._feedback: ConfidenceFeedback | None = None

    def get_store(self) -> ItemsStore:
        if self._store is None:
            from agent_brain.memory.store.items_store import ItemsStore

            items_dir = self.brain_dir / "items"
            items_dir.mkdir(parents=True, exist_ok=True)
            self._store = ItemsStore(items_dir=items_dir)
        return self._store

    def get_index(self) -> HubIndex:
        if self._index is None:
            from agent_brain.platform.indexing.index import HubIndex

            self._index = HubIndex(
                db_path=self.brain_dir / "index.db",
                embedding_dim=64,
            )
        return self._index

    def get_embedder(self) -> Embedder:
        if self._embedder is None:
            from agent_brain.platform.embedding import HashingEmbedder

            self._embedder = HashingEmbedder(dim=64)
        return self._embedder

    def get_retriever(self) -> Retriever:
        if self._retriever is None:
            from agent_brain.memory.recall.retrieval import Retriever

            self._retriever = Retriever(
                index=self.get_index(),
                embedder=self.get_embedder(),
            )
        return self._retriever

    def get_feedback(self) -> ConfidenceFeedback:
        if self._feedback is None:
            from agent_brain.memory.governance.feedback import ConfidenceFeedback

            self._feedback = ConfidenceFeedback(  # type: ignore[no-untyped-call]
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
