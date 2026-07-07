"""Active Recall: proactive memory retrieval before task execution.

Upgrades the system from passive injection (hook pushes top-K on every prompt)
to active recall (agent explicitly pulls the most relevant policies/skills
before starting work). Ranks by gain_score * confidence, filtered to
policy/skill types for maximum signal-to-noise.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.recall.retrieval import Retriever, SearchFilter
from agent_brain.contracts.memory_item import MemoryItem, MemoryType


@dataclass
class RecallResult:
    items: list[MemoryItem] = field(default_factory=list)
    scores: list[float] = field(default_factory=list)


class ActiveRecall:
    """Proactive retrieval of policies and skills relevant to a task context."""

    def __init__(
        self,
        retriever: Retriever,
        *,
        items_store: Optional[ItemsStore] = None,
        top_k: int = 5,
        min_gain: float = -0.5,
    ):
        self.retriever = retriever
        self.items_store = items_store
        self.top_k = top_k
        self.min_gain = min_gain

    def before_task(
        self,
        task_context: str,
        *,
        project: Optional[str] = None,
        include_facts: bool = False,
    ) -> RecallResult:
        """Retrieve the most relevant policies/skills for a given task context.

        Called by agents before starting work to proactively load applicable
        strategies and skills — not just raw facts.
        """
        allowed_types = {MemoryType.policy, MemoryType.skill}
        if include_facts:
            allowed_types.add(MemoryType.fact)

        filt = SearchFilter(project=project)
        raw_hits = self.retriever.search(
            query=task_context,
            top_k=self.top_k * 3,
            filters=filt,
        )

        hits: list[tuple[MemoryItem, float]] = []
        for hit in raw_hits:
            if isinstance(hit, tuple):
                hits.append(hit)
            elif self.items_store is not None:
                try:
                    item, _ = self.items_store.get(hit.id)
                    hits.append((item, hit.score))
                except FileNotFoundError:
                    continue
            else:
                continue

        scored: list[tuple[MemoryItem, float, float]] = []
        for item, relevance_score in hits:
            if item.type not in allowed_types:
                continue
            if item.gain_score < self.min_gain:
                continue
            if item.superseded_by is not None:
                continue
            active_score = relevance_score * (1.0 + item.gain_score) * item.confidence
            scored.append((item, active_score, relevance_score))

        scored.sort(key=lambda x: -x[1])
        top = scored[: self.top_k]

        return RecallResult(
            items=[item for item, _, _ in top],
            scores=[score for _, score, _ in top],
        )

    def format_context(self, result: RecallResult) -> str:
        """Format recall results as injectable context for the agent."""
        if not result.items:
            return ""
        lines = ["## Active Recall — Relevant Policies & Skills", ""]
        for item, score in zip(result.items, result.scores):
            type_label = "POLICY" if item.type == MemoryType.policy else "SKILL"
            lines.append(
                f"- [{type_label}] **{item.title}** "
                f"(support={item.support_count}, gain={item.gain_score:.2f}, v{item.version})"
            )
            lines.append(f"  {item.summary}")
            lines.append("")
        return "\n".join(lines)
