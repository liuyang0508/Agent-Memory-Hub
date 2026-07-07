"""Cross-session Causal Reasoning — temporal causal chain inference.

Answers "what decision caused this bug?" by building temporal causal chains
across sessions. Combines:
1. Explicit refs.mems edges (relation="caused_by"|"led_to")
2. Temporal proximity + semantic similarity (implicit causal candidates)
3. Project/tag overlap as scope filter

Usage:
    from agent_brain.memory.governance.reasoning.causal_chain import CausalChain
    chain = CausalChain(store, index, embedder)
    result = chain.trace_cause(bug_item_id, max_depth=5)
    # result.chain = [item5, item4, ..., item1_root_cause]
"""
from __future__ import annotations

from typing import Any

from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.platform.indexing.index import HubIndex
from agent_brain.platform.embedding import Embedder
from agent_brain.memory.governance.reasoning.causal_explicit import get_explicit_causes
from agent_brain.memory.governance.reasoning.causal_inference import find_implicit_causes, find_implicit_effects
from agent_brain.memory.governance.reasoning.causal_related import find_related_decisions as _find_related_decisions
from agent_brain.memory.governance.reasoning.causal_scoring import CausalScorer
from agent_brain.memory.governance.reasoning.causal_types import (
    TEMPORAL_WINDOW_DAYS,
    CausalCandidate,
    CausalLink,
    CausalTrace,
)
from agent_brain.contracts.memory_item import MemoryItem

find_related_decisions = _find_related_decisions


class CausalChain:
    """Cross-session causal reasoning engine."""

    def __init__(
        self,
        store: ItemsStore,
        index: HubIndex | None = None,
        embedder: Embedder | None = None,
        *,
        temporal_window_days: int = TEMPORAL_WINDOW_DAYS,
        min_similarity: float = 0.4,
    ):
        self.store = store
        self.index = index
        self.temporal_window_days = temporal_window_days
        self.scorer = CausalScorer(embedder=embedder, min_similarity=min_similarity)
        self._items_cache: dict[str, tuple[MemoryItem, str]] | None = None

    def _load_items(self) -> dict[str, tuple[MemoryItem, str]]:
        if self._items_cache is None:
            self._items_cache = {item.id: (item, body) for item, body in self.store.iter_all()}
        return self._items_cache

    def trace_cause(self, item_id: str, max_depth: int = 5) -> CausalTrace:
        """Trace backward from an item to find its causal chain.

        Combines explicit graph edges with temporal+semantic inference.
        """
        items = self._load_items()
        if item_id not in items:
            return CausalTrace(origin_id=item_id)

        trace = CausalTrace(origin_id=item_id)
        visited: set[str] = {item_id}
        current_id = item_id

        for _ in range(max_depth):
            candidates = self._find_causes(current_id, visited, items)
            if not candidates:
                break

            best = max(candidates, key=lambda c: c.score)
            link = CausalLink(
                source_id=best.item.id,
                target_id=current_id,
                relation="caused_by",
                confidence=best.score,
                reason="; ".join(best.reasons),
            )
            trace.chain.append(link)
            visited.add(best.item.id)
            current_id = best.item.id

        if trace.chain:
            trace.root_causes = [trace.chain[-1].source_id]

        return trace

    def trace_effects(self, item_id: str, max_depth: int = 5) -> CausalTrace:
        """Trace forward from a decision to find what it caused."""
        items = self._load_items()
        if item_id not in items:
            return CausalTrace(origin_id=item_id)

        trace = CausalTrace(origin_id=item_id)
        visited: set[str] = {item_id}
        current_id = item_id

        for _ in range(max_depth):
            candidates = self._find_effects(current_id, visited, items)
            if not candidates:
                break

            best = max(candidates, key=lambda c: c.score)
            link = CausalLink(
                source_id=current_id,
                target_id=best.item.id,
                relation="led_to",
                confidence=best.score,
                reason="; ".join(best.reasons),
            )
            trace.chain.append(link)
            visited.add(best.item.id)
            current_id = best.item.id

        return trace

    def find_related_decisions(
        self,
        item_id: str,
        max_results: int = 5,
    ) -> list[CausalCandidate]:
        """Find decisions that may be causally related to the given item."""
        items = self._load_items()
        return _find_related_decisions(
            item_id=item_id,
            items=items,
            scorer=self.scorer,
            max_results=max_results,
        )

    def _find_causes(
        self,
        item_id: str,
        visited: set[str],
        items: dict[str, tuple[MemoryItem, str]],
    ) -> list[CausalCandidate]:
        """Find potential causes for an item (items that happened BEFORE it)."""
        if item_id not in items:
            return []
        candidates: list[CausalCandidate] = []

        # Check explicit graph edges first
        explicit = self._get_explicit_causes(item_id, items)
        for cand in explicit:
            if cand.item.id not in visited:
                candidates.append(cand)

        # Temporal + semantic inference
        candidates.extend(
            find_implicit_causes(
                item_id=item_id,
                visited=visited,
                items=items,
                scorer=self.scorer,
                temporal_window_days=self.temporal_window_days,
            )
        )
        return candidates

    def _find_effects(
        self,
        item_id: str,
        visited: set[str],
        items: dict[str, tuple[MemoryItem, str]],
    ) -> list[CausalCandidate]:
        """Find potential effects of an item (items that happened AFTER it)."""
        return find_implicit_effects(
            item_id=item_id,
            visited=visited,
            items=items,
            scorer=self.scorer,
            temporal_window_days=self.temporal_window_days,
        )

    def _get_explicit_causes(
        self,
        item_id: str,
        items: dict[str, tuple[MemoryItem, str]],
    ) -> list[CausalCandidate]:
        """Get explicitly linked causes from refs graph."""
        return get_explicit_causes(self.index, item_id, items)

def add_causal_link(
    index: HubIndex,
    cause_id: str,
    effect_id: str,
    relation: str = "caused_by",
) -> None:
    """Explicitly record a causal relationship between two items."""
    index.add_ref(source_id=effect_id, target_id=cause_id, relation=relation)
