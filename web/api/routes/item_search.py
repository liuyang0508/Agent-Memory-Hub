"""Search-related routes for item data."""

from __future__ import annotations

import re as _re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall
from agent_brain.memory.context.context_packing import build_context_pack
from agent_brain.memory.context.recall_policy import search_governance_warnings
from agent_brain.memory.evidence.resource_reading import (
    ResourceSearchFilter,
    read_resource_context,
    search_resource,
)
from agent_brain.memory.evidence.resource_store import ResourceStore
from agent_brain.memory.recall.retrieval import SearchFilter
from web._base import _brain_dir, _components, _visible
from web.auth import CurrentUser, get_current_user


router = APIRouter()


@router.get("/api/search")
async def search_items(
    q: str = Query(..., min_length=1),
    top_k: int = Query(10, le=50),
    type: str | None = None,
    project: str | None = None,
    exclude_tags: str | None = None,
    verbosity: str = Query("locator", pattern="^(locator|overview|detail|auto)$"),
    include_trace: bool = False,
    context_firewall: bool = False,
    include_resources: bool = False,
    user: CurrentUser = Depends(get_current_user),
):
    governance_warnings = search_governance_warnings(
        verbosity=verbosity,
        top_k=top_k,
    )
    store, _, retriever, _ = _components()
    sf = SearchFilter(
        type=type,
        project=project,
        exclude_tags=[tag.strip() for tag in (exclude_tags or "").split(",") if tag.strip()],
    )
    old_record_access = retriever.record_access
    if context_firewall:
        retriever.record_access = False
    try:
        hits = retriever.search(
            q,
            top_k=top_k * 3 if context_firewall else top_k,
            filters=sf if not sf.is_empty else None,
            explain=include_trace,
        )
    finally:
        retriever.record_access = old_record_access
    items_by_id: dict[str, tuple[Any, str]] = {}
    for item, body in store.iter_all():
        if _visible(item, user):
            items_by_id[item.id] = (item, body)
    firewall_by_id: dict[str, Any] = {}
    if context_firewall:
        hit_by_id = {hit.id: hit for hit in hits}
        candidates = [
            ContextCandidate(
                item=items_by_id[hit.id][0],
                body=items_by_id[hit.id][1],
                score=hit.score,
            )
            for hit in hits
            if hit.id in items_by_id
        ]
        firewall_result = ContextFirewall().filter(candidates, query=q, max_items=top_k)
        firewall_by_id = {
            decision.candidate.item.id: decision
            for decision in firewall_result.decisions
        }
        hits = [
            hit_by_id[decision.candidate.item.id]
            for decision in firewall_result.included
            if decision.candidate.item.id in hit_by_id
        ]
    else:
        hits = hits[:top_k]
    results = []
    for h in hits:
        if h.id not in items_by_id:
            continue
        item, body = items_by_id[h.id]
        firewall_decision = firewall_by_id.get(h.id)
        context_pack = build_context_pack(
            item,
            body,
            requested=verbosity,
            firewall_decision=firewall_decision,
        )
        results.append(
            {
                "id": h.id,
                "type": str(item.type),
                "title": item.title,
                "summary": item.summary,
                "score": h.score,
                "confidence": item.confidence,
                "snippet": context_pack.text[:200],
                "context_pack": context_pack.to_dict(),
                "retrieval_trace": h.trace.to_dict() if h.trace is not None else None,
                "firewall": _firewall_to_dict(firewall_decision) if firewall_decision else None,
                "resource_context": _resource_context_for_item(item) if include_resources else [],
            }
        )
    resource_results = _resource_results(q, project=project, top_k=top_k) if include_resources else []
    return {
        "results": results,
        "query": q,
        "diagnostics": {
            "retrieval_trace": include_trace,
            "context_firewall": context_firewall,
            "resource_sidecar": include_resources,
            "verbosity": verbosity,
            "governance_warnings": list(governance_warnings),
        },
        "resource_results": resource_results,
    }


def _firewall_to_dict(decision) -> dict[str, Any]:
    return {
        "action": decision.action,
        "reasons": list(decision.reasons),
        "score": decision.score,
        "effective_score": decision.effective_score,
    }


def _resource_context_for_item(item) -> list[dict[str, Any]]:
    store = ResourceStore(_brain_dir())
    contexts: list[dict[str, Any]] = []
    for resource_id in item.refs.resources:
        try:
            entries = read_resource_context(store, resource_id, max_tokens=80)
        except FileNotFoundError:
            continue
        for entry in entries:
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


def _resource_results(query: str, *, project: str | None, top_k: int) -> list[dict[str, Any]]:
    store = ResourceStore(_brain_dir())
    hits = search_resource(
        store,
        query,
        top_k=top_k,
        filters=ResourceSearchFilter(project=project),
    )
    rows: list[dict[str, Any]] = []
    for hit in hits:
        context = []
        try:
            for entry in read_resource_context(store, hit.resource.id, max_tokens=80):
                context.append({
                    "level": entry.level,
                    "status": entry.status,
                    "content_text": entry.content_text,
                    "extraction_id": entry.extraction_id,
                    "confidence": entry.confidence,
                })
        except FileNotFoundError:
            context = []
        rows.append({
            "id": hit.resource.id,
            "title": hit.resource.title,
            "kind": str(hit.resource.kind),
            "uri": hit.resource.uri,
            "score": hit.score,
            "matched_extractions": hit.matched_extractions,
            "context": context,
        })
    return rows


@router.get("/api/items/{item_id}/related")
async def related_items(
    item_id: str,
    top_k: int = Query(5, le=20),
    user: CurrentUser = Depends(get_current_user),
):
    """Find items semantically similar to the given item."""
    store, _, retriever, _ = _components()
    try:
        item, body = store.get(item_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="item not found")
    query_text = f"{item.title} {item.summary} {body[:500]}"
    hits = retriever.search(query_text, top_k=top_k + 1)
    results = []
    for h in hits:
        if h.id == item_id:
            continue
        try:
            rel_item, _ = store.get(h.id)
        except (FileNotFoundError, Exception):
            continue
        if not _visible(rel_item, user):
            continue
        results.append(
            {
                "id": h.id,
                "type": str(rel_item.type),
                "title": rel_item.title,
                "summary": rel_item.summary,
                "score": round(h.score, 3),
                "confidence": rel_item.confidence,
            }
        )
        if len(results) >= top_k:
            break
    return {"item_id": item_id, "related": results}


@router.get("/api/search/fulltext")
async def fulltext_search(
    q: str = Query(..., min_length=1),
    type: str | None = None,
    project: str | None = None,
    limit: int = Query(20, le=100),
    highlight: bool = Query(False, description="Wrap matches in <mark> tags"),
    user: CurrentUser = Depends(get_current_user),
):
    """Search within the body text of items (substring match)."""
    store, _, _, _ = _components()
    results = []
    q_lower = q.lower()
    for item, body in store.iter_all():
        if not _visible(item, user):
            continue
        if type and str(item.type) != type:
            continue
        if project and item.project != project:
            continue
        body_lower = body.lower()
        pos = body_lower.find(q_lower)
        if pos == -1:
            title_lower = item.title.lower()
            summary_lower = (item.summary or "").lower()
            if q_lower not in title_lower and q_lower not in summary_lower:
                continue
            snippet = body[:200]
        else:
            start = max(0, pos - 50)
            snippet = body[start : start + 200]
        if highlight:
            snippet = _re.sub(
                _re.escape(q),
                lambda m: f"<mark>{m.group()}</mark>",
                snippet,
                flags=_re.IGNORECASE,
            )
        results.append(
            {
                "id": item.id,
                "type": str(item.type),
                "title": item.title,
                "summary": item.summary,
                "confidence": item.confidence,
                "snippet": snippet,
            }
        )
        if len(results) >= limit:
            break
    return {"results": results, "query": q, "count": len(results)}
