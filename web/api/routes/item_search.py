"""Search-related routes for item data."""

from __future__ import annotations

import re as _re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from agent_brain.memory.context.context_firewall_types import ContextCandidate
from agent_brain.memory.context.injection_gateway import (
    build_injection_context,
    injection_retrieval_top_k,
)
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
_PROMPT_RESOURCE_SENSITIVITIES = ("public", "internal")


@router.get("/api/search")
def search_items(
    q: str = Query(..., min_length=1),
    top_k: int = Query(10, le=50),
    type: str | None = None,
    project: str | None = None,
    exclude_tags: str | None = None,
    verbosity: str = Query("locator", pattern="^(locator|overview|detail|auto)$"),
    include_trace: bool = False,
    context_firewall: bool = True,
    include_resources: bool = False,
    user: CurrentUser = Depends(get_current_user),
):
    if not context_firewall and not user.is_admin:
        raise HTTPException(status_code=403, detail="raw search diagnostics are admin only")

    store, _, retriever, _ = _components()
    sf = SearchFilter(
        type=type,
        project=project,
        exclude_tags=[tag.strip() for tag in (exclude_tags or "").split(",") if tag.strip()],
    )
    search_kwargs: dict[str, Any] = {
        "top_k": injection_retrieval_top_k(top_k) if context_firewall else top_k,
        "filters": sf if not sf.is_empty else None,
        "explain": include_trace,
    }
    if context_firewall:
        search_kwargs["record_access"] = False
    hits = retriever.search(q, **search_kwargs)

    items_by_id: dict[str, tuple[Any, str]] = {}
    for item, body in store.iter_all():
        if _visible(item, user):
            items_by_id[item.id] = (item, body)
    context_packs_by_id: dict[str, Any] = {}
    firewall_by_id: dict[str, Any] = {}
    if context_firewall:
        hit_by_id = {hit.id: hit for hit in hits}
        candidates = [
            ContextCandidate(
                item=items_by_id[hit.id][0],
                body=items_by_id[hit.id][1],
                score=hit.score,
                source="web-search",
            )
            for hit in hits
            if hit.id in items_by_id
        ]
        injection = build_injection_context(
            candidates,
            query=q,
            brain_dir=_brain_dir(),
            requested=verbosity,
            max_items=top_k,
        )
        context_packs_by_id = {
            entry.decision.candidate.item.id: entry.pack
            for entry in injection.included
        }
        firewall_by_id = {
            entry.decision.candidate.item.id: entry.decision
            for entry in injection.included
        }
        hits = [
            hit_by_id[item_id]
            for item_id in context_packs_by_id
            if item_id in hit_by_id
        ]
        record_accesses = getattr(retriever, "record_accesses", None)
        if callable(record_accesses):
            record_accesses(hits)
    else:
        hits = hits[:top_k]

    results = []
    for h in hits:
        if h.id not in items_by_id:
            continue
        item, body = items_by_id[h.id]
        firewall_decision = firewall_by_id.get(h.id)
        context_pack = context_packs_by_id.get(h.id)
        results.append(
            {
                "id": h.id,
                "type": str(item.type),
                "title": item.title,
                "summary": item.summary,
                "score": h.score,
                "confidence": item.confidence,
                "snippet": context_pack.text if context_pack is not None else body[:200],
                "context_pack": (
                    context_pack.to_dict() if context_pack is not None else None
                ),
                "retrieval_trace": h.trace.to_dict() if h.trace is not None else None,
                "firewall": _firewall_to_dict(firewall_decision) if firewall_decision else None,
                "resource_context": (
                    _resource_context_for_item(item, user=user)
                    if context_firewall and include_resources
                    else []
                ),
            }
        )
    resource_results = (
        _resource_results(q, project=project, top_k=top_k, user=user)
        if context_firewall and include_resources
        else []
    )
    return {
        "results": results,
        "query": q,
        "diagnostics": {
            "retrieval_trace": include_trace,
            "context_firewall": context_firewall,
            "resource_sidecar": context_firewall and include_resources,
            "verbosity": verbosity,
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


def _resource_context_for_item(
    item,
    *,
    user: CurrentUser,
) -> list[dict[str, Any]]:
    store = ResourceStore(_brain_dir())
    contexts: list[dict[str, Any]] = []
    for resource_id in item.refs.resources:
        try:
            resource = store.get_resource(resource_id)
            if not _resource_visible_to(resource, user):
                continue
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


def _resource_results(
    query: str,
    *,
    project: str | None,
    top_k: int,
    user: CurrentUser,
) -> list[dict[str, Any]]:
    store = ResourceStore(_brain_dir())
    hits = search_resource(
        store,
        query,
        top_k=top_k,
        filters=ResourceSearchFilter(
            project=project,
            tenant_ids=None if user.is_admin else (None, user.tenant_id),
            allowed_sensitivities=_PROMPT_RESOURCE_SENSITIVITIES,
        ),
    )
    rows: list[dict[str, Any]] = []
    for hit in hits:
        if not _resource_visible_to(hit.resource, user):
            continue
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


def _resource_visible_to(resource, user: CurrentUser) -> bool:
    sensitivity = str(getattr(resource.sensitivity, "value", resource.sensitivity))
    if sensitivity not in _PROMPT_RESOURCE_SENSITIVITIES:
        return False
    return bool(
        user.is_admin
        or resource.tenant_id is None
        or resource.tenant_id == user.tenant_id
    )


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
