"""Query-side helpers for the lightweight Python SDK."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SearchResult:
    """A single search result from the brain pool."""
    id: str
    title: str
    summary: str
    score: float
    type: str
    confidence: float
    snippet: str = ""
    context_pack: dict[str, Any] | None = None
    retrieval_trace: dict[str, Any] | None = None
    firewall: dict[str, Any] | None = None
    resource_context: list[dict[str, Any]] = field(default_factory=list)


def search_items(
    *,
    query: str,
    top_k: int,
    type: str | None,
    project: str | None,
    tags: list[str] | None,
    default_project: str | None,
    retriever: Any,
    store: Any,
    brain_dir: Path | None = None,
    verbosity: str = "locator",
    include_trace: bool = False,
    context_firewall: bool = True,
    include_resources: bool = False,
) -> list[SearchResult]:
    """Search stored memory items and convert retriever hits into SDK results.

    The default path returns only Gateway-authorized prompt context. Pass
    ``context_firewall=False`` only for explicit raw retrieval diagnostics; raw
    results preserve metadata and snippets but never include a context pack.
    """
    from agent_brain.memory.recall.retrieval import SearchFilter
    from agent_brain.memory.context.context_firewall_types import ContextCandidate
    from agent_brain.memory.context.injection_gateway import (
        _record_injection_diagnostic,
        build_injection_context,
    )

    parsed_verbosity = _parse_verbosity(verbosity)
    items_by_id: dict[str, tuple[Any, str]] = {}
    for item, body in store.iter_all():
        items_by_id[item.id] = (item, body)

    search_filter = SearchFilter(
        type=type,
        project=project or default_project,
        tags=tags or [],
    )

    old_record_access = getattr(retriever, "record_access", None)
    if context_firewall and old_record_access is not None:
        retriever.record_access = False
    try:
        hits = retriever.search(
            query,
            top_k=top_k * 3 if context_firewall else top_k,
            filters=search_filter if not search_filter.is_empty else None,
            explain=include_trace,
        )
    finally:
        if context_firewall and old_record_access is not None:
            retriever.record_access = old_record_access

    packed_by_id: dict[str, Any] = {}
    firewall_by_id: dict[str, Any] = {}
    if context_firewall:
        _record_injection_diagnostic(
            surface="sdk-search",
            reason="hydrate_error",
            count=sum(1 for hit in hits if hit.id not in items_by_id),
        )
        hit_by_id = {hit.id: hit for hit in hits}
        injection = build_injection_context(
            [
                ContextCandidate(
                    item=items_by_id[hit.id][0],
                    body=items_by_id[hit.id][1],
                    score=hit.score,
                    source="sdk-search",
                )
                for hit in hits
                if hit.id in items_by_id
            ],
            query=query,
            requested=parsed_verbosity,
            max_items=top_k,
        )
        packed_by_id = {
            entry.decision.candidate.item.id: entry.pack
            for entry in injection.included
        }
        firewall_by_id = {
            entry.decision.candidate.item.id: entry.decision
            for entry in injection.included
        }
        hits = [
            hit_by_id[item_id]
            for item_id in packed_by_id
            if item_id in hit_by_id
        ]
    else:
        hits = hits[:top_k]

    results: list[SearchResult] = []
    for hit in hits:
        if hit.id not in items_by_id:
            continue
        item, body = items_by_id[hit.id]
        pack = packed_by_id.get(hit.id)
        firewall_decision = firewall_by_id.get(hit.id)
        results.append(
            SearchResult(
                id=hit.id,
                title=item.title,
                summary=item.summary,
                score=hit.score,
                type=str(item.type),
                confidence=item.confidence,
                snippet=pack.text if pack is not None else body[:200],
                context_pack=pack.to_dict() if pack is not None else None,
                retrieval_trace=hit.trace.to_dict() if getattr(hit, "trace", None) else None,
                firewall=_firewall_to_dict(firewall_decision) if firewall_decision else None,
                resource_context=_resource_context_for_item(
                    brain_dir,
                    item,
                    include_resources=include_resources,
                ),
            )
        )

    return results


def _parse_verbosity(value: str) -> str:
    normalized = (value or "locator").strip().lower()
    if normalized not in {"locator", "overview", "detail", "auto"}:
        raise ValueError("verbosity must be one of: locator, overview, detail, auto")
    return normalized


def _firewall_to_dict(decision: Any) -> dict[str, Any]:
    return {
        "action": decision.action,
        "reasons": list(decision.reasons),
        "score": decision.score,
        "effective_score": decision.effective_score,
    }


def _resource_context_for_item(
    brain_dir: Path | None,
    item: Any,
    *,
    include_resources: bool,
) -> list[dict[str, Any]]:
    if not include_resources or brain_dir is None:
        return []
    from agent_brain.memory.evidence.resource_reading import read_resource_context
    from agent_brain.memory.evidence.resource_store import ResourceStore

    store = ResourceStore(brain_dir)
    contexts: list[dict[str, Any]] = []
    for resource_id in getattr(item.refs, "resources", []):
        for entry in read_resource_context(store, resource_id, max_tokens=80):
            contexts.append({
                "resource_id": entry.resource_id,
                "level": entry.level,
                "status": entry.status,
                "content_text": entry.content_text,
                "extraction_id": entry.extraction_id,
                "source_locator": entry.source_locator,
                "confidence": entry.confidence,
            })
    return contexts


def read_item(store: Any, item_id: str) -> dict[str, Any] | None:
    """Read a single item by ID. Returns dict with 'item' and 'body' or None."""
    for item, body in store.iter_all():
        if item.id == item_id:
            return {
                "item": item.model_dump(mode="json"),
                "body": body,
            }
    return None


def list_recent_items(store: Any, n: int = 10, type: str | None = None) -> list[dict[str, Any]]:
    """List recent items, optionally filtered by type."""
    items = list(store.iter_all())
    if type:
        items = [(item, body) for item, body in items if str(item.type) == type]
    items.sort(key=lambda pair: pair[0].created_at, reverse=True)
    return [
        {
            "id": item.id,
            "type": str(item.type),
            "title": item.title,
            "confidence": item.confidence,
            "created_at": item.created_at.isoformat(),
        }
        for item, _ in items[:n]
    ]


def build_brief_payload(
    store: Any,
    *,
    project: str | None,
    budget_tokens: int,
) -> dict[str, Any]:
    """Build the token-budgeted SDK brief payload."""
    from agent_brain.memory.recall.brief import build_brief

    brief = build_brief(store, project=project, budget_tokens=budget_tokens)
    return {
        "total_shown": brief.total_shown,
        "total_withheld": brief.total_withheld,
        "tiers": [
            {
                "name": tier.name,
                "items": [{"id": item.id, "title": item.title} for item in tier.shown],
            }
            for tier in brief.tiers
        ],
    }


__all__ = [
    "SearchResult",
    "build_brief_payload",
    "list_recent_items",
    "read_item",
    "search_items",
]
