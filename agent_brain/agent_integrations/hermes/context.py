from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


def build_active_recall_payload(
    retriever: Any,
    task_hint: str,
    project: str | None = None,
    recall_factory: Callable[[Any], Any] | None = None,
) -> list[dict[str, Any]]:
    if recall_factory is None:
        from agent_brain.memory.governance.evolve.active_recall import ActiveRecall

        recall_factory = ActiveRecall

    try:
        recall = recall_factory(retriever)
        recall_result = recall.before_task(task_hint, project=project)
        return [
            {
                "id": item.id,
                "type": str(item.type),
                "title": item.title,
                "summary": item.summary,
                "support_count": item.support_count,
                "gain_score": item.gain_score,
                "version": item.version,
            }
            for item in recall_result.items
        ]
    except Exception:
        logger.warning(
            "Failed to build Hermes active recall context",
            exc_info=True,
        )
        return []


__all__ = ["build_active_recall_payload"]
