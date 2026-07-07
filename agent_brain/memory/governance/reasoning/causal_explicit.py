"""Explicit causal candidate extraction from graph and item refs."""
from __future__ import annotations

from agent_brain.platform.indexing.index import HubIndex
from agent_brain.memory.governance.reasoning.causal_types import CAUSAL_RELATIONS, CausalCandidate
from agent_brain.contracts.memory_item import MemoryItem


def get_explicit_causes(
    index: HubIndex | None,
    item_id: str,
    items: dict[str, tuple[MemoryItem, str]],
) -> list[CausalCandidate]:
    """Get explicitly linked causes from index refs and item metadata."""
    causes: list[CausalCandidate] = []

    if index is not None:
        try:
            edges = index.get_refs(item_id)
            for src, tgt, rel in edges:
                if rel in CAUSAL_RELATIONS:
                    cause_id = src if tgt == item_id else tgt
                    if cause_id in items:
                        cause_item, _ = items[cause_id]
                        causes.append(CausalCandidate(
                            item=cause_item,
                            score=0.9,
                            reasons=[f"explicit edge: {rel}"],
                        ))
        except Exception:
            pass

    if item_id not in items:
        return causes

    item, _ = items[item_id]
    cause_ids = {candidate.item.id for candidate in causes}

    for mem_ref in item.refs.mems:
        if mem_ref in items and mem_ref not in cause_ids:
            ref_item, _ = items[mem_ref]
            if ref_item.created_at < item.created_at:
                causes.append(CausalCandidate(
                    item=ref_item,
                    score=0.7,
                    reasons=["refs.mems link + temporal precedence"],
                ))
                cause_ids.add(mem_ref)

    for evolved_id in getattr(item, "evolved_from", []):
        if evolved_id in items and evolved_id not in cause_ids:
            evolved_item, _ = items[evolved_id]
            causes.append(CausalCandidate(
                item=evolved_item,
                score=0.8,
                reasons=["evolved_from link"],
            ))
            cause_ids.add(evolved_id)

    return causes


__all__ = ["get_explicit_causes"]
