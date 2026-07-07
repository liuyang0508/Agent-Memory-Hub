from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from agent_brain.memory.recall.retrieval_types import RetrievedItem

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievalAccessRecorder:
    """Best-effort writer for retrieval access metadata and confidence rewards."""

    index: object
    reinforce_confidence: bool = False
    confidence_reward: float = 0.01

    def record(
        self,
        results: list[RetrievedItem],
        accessed_at: str | None = None,
    ) -> list[RetrievedItem]:
        """Record that retrieved items were accessed, then return them unchanged."""
        timestamp = accessed_at or datetime.now(timezone.utc).isoformat()
        if not self.reinforce_confidence and hasattr(self.index, "record_access_many"):
            try:
                self.index.record_access_many([item.id for item in results], timestamp)
                return results
            except Exception:
                logger.warning(
                    "Failed to batch record retrieval access",
                    exc_info=True,
                )
        for item in results:
            try:
                self.index.record_access(item.id, timestamp)
                if self.reinforce_confidence:
                    from agent_brain.memory.governance.feedback import ConfidenceFeedback

                    ConfidenceFeedback(index=self.index).on_access(
                        item.id,
                        reward=self.confidence_reward,
                    )
            except Exception:
                logger.warning(
                    "Failed to record retrieval access for %s",
                    item.id,
                    exc_info=True,
                )
        return results


__all__ = ["RetrievalAccessRecorder"]
