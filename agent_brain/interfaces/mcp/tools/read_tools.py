"""MCP read/list/brief tool implementations."""
from __future__ import annotations

from agent_brain.interfaces.mcp.tools._shared import *  # noqa: F401,F403


def read_memory(item_id: str, head: int | None = None, view: str = "detail") -> dict[str, Any]:
    """Read one memory item. Default returns the full body; pass head=N for a
    bounded body (adds body_truncated + full_chars) to save context.

    WHEN TO USE
    -----------
    Call this AFTER `search_memory(..., verbosity="auto")` shows a relevant
    context_pack and you need exact evidence, code, logs, stack traces, or
    original wording. Prefer `read_memory(id, head=2000, view="detail")` first
    to keep context budget small; widen only if the bounded body ends
    mid-thought.

    DO NOT
    ------
    Bulk-read many items to "browse" the brain. Use `brief_memory` or
    `list_recent` for browsing; `read_memory` is for committed deep-reads.
    """
    view = _parse_context_view(view)
    store, _, _ = _components()
    for item, body in store.iter_all():
        if item.id == item_id:
            out: dict[str, Any] = {"frontmatter": item.model_dump(mode="json")}
            if view == "locator":
                out["locator"] = item.context_views.locator
                return out
            if view == "overview":
                out["locator"] = item.context_views.locator
                out["overview"] = item.context_views.overview
                return out
            if head is not None and len(body) > head:
                out["body"] = body[:head]
                out["body_truncated"] = True
                out["full_chars"] = len(body)
            else:
                out["body"] = body
            return out
    raise ValueError(f"item not found: {item_id}")


def _parse_context_view(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {"locator", "overview", "detail"}:
        raise ValueError("view must be one of: locator, overview, detail")
    return normalized


def brief_memory(project: str | None = None, budget_tokens: int = 1500,
                 query: str | None = None) -> dict[str, Any]:
    """Token-budgeted resume briefing (summaries only). Call this first on resume
    instead of bulk-reading items; then read_memory only the few ids you need.

    WHEN TO USE
    -----------
    Call this AT THE START of any session that resumes work on a project:
      * User says "continue", "resume", "接着", "接着上次".
      * First message of a new session that mentions a known project name.
      * Before answering a question that requires "current state" of a project.

    The tiered summary gives you situational awareness without burning the
    context budget on full bodies.

    CHAIN
    -----
    Brief → spot the 1-3 items most relevant to the task → `read_memory` only
    those ids.
    """
    from agent_brain.memory.recall.brief import build_brief

    store, _, _ = _components()
    b = build_brief(store, project=project, budget_tokens=budget_tokens, query=query)
    return {
        "tiers": [
            {"name": t.name,
             "items": [{"type": i.type, "title": i.title, "id": i.id, "summary": i.summary}
                       for i in t.shown],
             "withheld": t.withheld}
            for t in b.tiers
        ],
        "total_shown": b.total_shown,
        "total_withheld": b.total_withheld,
        "budget_tokens": b.budget_tokens,
        "footer": b.footer,
    }


def list_recent(n: int = 10, type: str | None = None) -> list:
    """List recent memory items, optionally filtered by type.

    WHEN TO USE
    -----------
    Quick reconnaissance: "what has been captured today / this week",
    debugging whether a recent `write_memory` actually persisted, or
    auditing recent agent activity. NOT a substitute for `search_memory` —
    use search whenever the user query has semantic intent.
    """
    store, _, _ = _components()
    items = list(store.iter_all())
    if type:
        items = [(it, b) for it, b in items if str(it.type) == type]
    items.sort(key=lambda pair: pair[0].created_at, reverse=True)
    return [
        {
            "id": it.id,
            "type": str(it.type),
            "title": it.title,
            "confidence": it.confidence,
            "created_at": it.created_at.isoformat(),
        }
        for it, _ in items[:n]
    ]


__all__ = ["read_memory", "brief_memory", "list_recent"]
