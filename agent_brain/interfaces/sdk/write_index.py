from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from agent_brain.memory.recall.embedding_text import embedding_text_for_item

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClientWriteIndexer:
    index_getter: Callable[[], Any]
    embedder_getter: Callable[[], Any]

    def index(self, item: Any, body: str) -> bool:
        try:
            idx = self.index_getter()
            emb = self.embedder_getter()
            idx.upsert(
                item,
                body,
                embedding=emb.embed(embedding_text_for_item(item)),
            )
            return True
        except Exception:
            logger.warning(
                "Failed to index SDK-written item %s",
                item.id,
                exc_info=True,
            )
            return False


__all__ = ["ClientWriteIndexer"]
