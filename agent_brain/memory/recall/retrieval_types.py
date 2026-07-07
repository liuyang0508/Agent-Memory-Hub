from __future__ import annotations

from dataclasses import dataclass

from agent_brain.memory.recall.retrieval_trace import RetrievalTrace


@dataclass(frozen=True)
class RetrievedItem:
    """A retrieval hit with fused score and original rank provenance."""

    id: str
    score: float
    bm25_rank: int | None
    vector_rank: int | None
    trace: RetrievalTrace | None = None


__all__ = ["RetrievedItem"]
