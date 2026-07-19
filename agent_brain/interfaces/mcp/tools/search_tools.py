"""MCP core-tier search and tag suggestion tools."""
# ruff: noqa: F405
from __future__ import annotations

from agent_brain.memory.context.context_firewall_types import ContextCandidate
from agent_brain.memory.context.injection_gateway import (
    _record_injection_diagnostic,
    build_injection_context,
    injection_retrieval_top_k,
)
from agent_brain.memory.context.recall_policy import search_governance_warnings
from agent_brain.interfaces.mcp.tools._shared import *  # noqa: F401,F403


def tag_suggest(
    text: str,
    max_tags: int = 5,
) -> dict[str, Any]:
    """Suggest tags for content based on similar existing items.

    Finds the most similar items in the brain pool and returns their
    most common tags, ranked by frequency.

    WHEN TO USE
    -----------
    Call this RIGHT BEFORE `write_memory` whenever the caller has not provided
    explicit tags. Using suggested tags keeps the brain's tag vocabulary
    convergent (a few well-used tags instead of long-tail singletons) and
    boosts later recall via tag-filtered `search_memory`.

    SKIP IF
    -------
    Caller already provided 2+ tags that match the project's established
    convention; or the new item is so unique that no similar items exist.
    """
    _, idx, _ = _components()
    embedder = get_default_embedder()
    suggestions = _suggest_tags(idx, embedder, text, max_tags=max_tags)
    return {
        "suggestions": [{"tag": tag, "frequency": freq} for tag, freq in suggestions],
    }


def search_memory(
    query: str,
    top_k: int = 10,
    type: str | None = None,
    project: str | None = None,
    tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
    since_days: int | None = None,
    graph_expand: bool = False,
    tenant_id: str | None = None,
    mmr_lambda: float | None = None,
    verbosity: str = "locator",
    include_trace: bool = False,
) -> list:
    """Search memory items (BM25 + vector RRF) with optional filters.

    When graph_expand=True, search results are expanded with knowledge-graph
    neighbors (items linked via refs.mems). tenant_id isolates to a single tenant.
    mmr_lambda (0.0-1.0) enables MMR diversity re-ranking: 1.0 = pure relevance,
    0.5 = balanced relevance+diversity, lower = more diverse.
    exclude_tags filters out items containing any of the specified tags.
    include_trace returns an optional retrieval_trace object for debugging why
    a hit was selected; leave it false for normal prompt injection.

    WHEN TO USE (proactive, before answering)
    -----------------------------------------
    Call this FIRST whenever any of these triggers fire, BEFORE you start
    reasoning or generating an answer:
      * The user mentions a project, tool, framework, person, decision,
        convention, error, command, or any named entity.
      * The user references prior work ("earlier", "last time", "as before",
        "that thing we did", "还记得", "上次", "之前").
      * The task overlaps with a domain the brain may already contain
        (architecture, code review, debugging recipes, prompts, decisions).
      * You are about to claim something from your own knowledge that the
        user's brain might contradict or refine.

    Doing this prevents reinventing wheels and grounds your answer in the
    user's own previously confirmed knowledge.

    CHAIN
    -----
    1. Pass the full task description, `top_k=5`, and `verbosity="auto"`
       for agent context. Use `brief_memory` to recover the overall project
       state and this search tool for the current concrete task; neither is a
       fallback for the other. `project` is a hard filter: set it only when the
       user explicitly names the project or the cwd mapping is certain, never
       from a natural-language guess.
    2. If recall is weak, broaden to synonyms or enable `graph_expand=True`.
    3. Inspect each hit["context_pack"] before deep reading. The pack includes
       selected_view, text, packed/full token estimates, and retrieve hints.
    4. If context_pack.text is enough, answer from packed context. Call
       `read_memory(id, head=2000, view="detail")` only when needed for
       evidence, code, logs, stack traces, or exact wording. Cite the id in
       your response so the user can audit.

    DO NOT
    ------
    Skip search just because the question "looks general". Most user questions
    have project-specific context the brain already encodes.
    Do not bulk-read bodies to browse; browse with `brief_memory` or packed
    `search_memory(..., verbosity="auto")` results.
    """
    verbosity = _parse_context_verbosity(verbosity)
    governance_warnings = search_governance_warnings(
        verbosity=verbosity,
        top_k=top_k,
    )
    store, idx, _ = _components()
    embedder = get_default_embedder()
    retriever = Retriever(
        index=idx, embedder=embedder,
        graph_expand=graph_expand, graph_depth=1,
        mmr_lambda=mmr_lambda,
    )
    items_by_id: dict[str, MemoryItem] = {}
    bodies_by_id: dict[str, str] = {}
    for it, body in store.iter_all():
        items_by_id[it.id] = it
        bodies_by_id[it.id] = body
    sf = SearchFilter(
        type=type, project=project, tags=tags or [],
        exclude_tags=exclude_tags or [],
        since_days=since_days, tenant_id=tenant_id,
    )
    raw_top_k = injection_retrieval_top_k(top_k)
    hits = retriever.search(
        query,
        top_k=raw_top_k,
        filters=sf,
        explain=include_trace,
        record_access=False,
    )
    _record_injection_diagnostic(
        surface="mcp-search",
        reason="hydrate_error",
        count=sum(1 for hit in hits if hit.id not in items_by_id),
    )
    hit_by_id = {hit.id: hit for hit in hits}
    candidates = [
        ContextCandidate(
            item=items_by_id[hit.id],
            body=bodies_by_id.get(hit.id, ""),
            score=hit.score,
            source="mcp-search",
        )
        for hit in hits
        if hit.id in items_by_id
    ]
    injection = build_injection_context(
        candidates,
        query=query,
        brain_dir=_brain_dir(),
        requested=verbosity,
        max_items=top_k,
    )
    retriever.record_accesses([
        hit_by_id[entry.decision.candidate.item.id]
        for entry in injection.included
        if entry.decision.candidate.item.id in hit_by_id
    ])
    results = []
    for entry in injection.included:
        decision = entry.decision
        item = decision.candidate.item
        pack = entry.pack
        hit = hit_by_id.get(item.id)
        result = {
            "id": item.id,
            "title": item.title,
            "type": str(item.type),
            "summary": item.summary,
            "confidence": item.confidence,
            "score": hit.score if hit is not None else decision.score,
            "context_pack": pack.to_dict(),
            "locator": item.context_views.locator,
        }
        if verbosity == "auto":
            result["selected_view"] = pack.selected_view
            result["load_reason"] = list(pack.load_reason)
            if pack.selected_view == "detail":
                result["overview"] = item.context_views.overview
                result["body"] = pack.text
            elif pack.selected_view == "overview":
                result["overview"] = pack.text
            else:
                result["snippet"] = pack.text
        elif verbosity == "overview":
            result["overview"] = pack.text
        elif verbosity == "detail":
            result["overview"] = item.context_views.overview
            result["body"] = pack.text
        else:
            result["snippet"] = pack.text
        if governance_warnings:
            result["governance_warnings"] = list(governance_warnings)
        if hit is not None and hit.trace is not None:
            result["retrieval_trace"] = hit.trace.to_dict()
        results.append(result)
    return results


def _parse_context_verbosity(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {"locator", "overview", "detail", "auto"}:
        raise ValueError("verbosity must be one of: locator, overview, detail, auto")
    return normalized


__all__ = ["tag_suggest", "search_memory"]
