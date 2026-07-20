"""MCP core-tier mutation tools."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Refs, Sensitivity
from agent_brain.interfaces.mcp.tools._shared import (
    _components,
    _resolve_item_path,
    get_default_embedder,
    make_item_id,
)
from agent_brain.interfaces.mcp.tools.mutation_enrichment import build_write_enrichment
from agent_brain.interfaces.mcp.tools.mutation_updates import build_update_fields
from agent_brain.memory.recall.embedding_text import embedding_text_for_item


def write_memory(
    type: str,
    title: str,
    summary: str,
    body: str = "",
    overview: str | None = None,
    tags: list[str] | None = None,
    ref_files: list[str] | None = None,
    ref_urls: list[str] | None = None,
    ref_mems: list[str] | None = None,
    ref_commits: list[str] | None = None,
    ref_resources: list[str] | None = None,
    ref_extractions: list[str] | None = None,
    project: str | None = None,
    agent: str | None = None,
    session: str | None = None,
    sensitivity: str = "internal",
    confidence: float = 0.7,
    tenant_id: str | None = None,
    allow_unsafe: bool = False,
) -> dict[str, Any]:
    """Write a new memory item to brain pool.

    Persistence is delegated to the single :class:`WriteService` funnel: the
    markdown append is the only thing that decides "written", index + embedding
    are best-effort (their failure degrades, never blocks), and the audit (防进)
    gate refuses critical/high content unless ``allow_unsafe=True``. After a
    successful write, related items and suggested tags are attached best-effort.

    WHEN TO USE (proactive, do not wait to be asked)
    -----------------------------------------------
    Call this PROACTIVELY whenever any of these signals fire — the user does
    NOT need to say "remember this":
      * You (the agent) produced a reusable artifact: a prompt template, a
        decision framework, a checklist, a debugging recipe, a workflow, an
        architecture rationale, a reusable code snippet, a config recipe.
      * The user stated a preference, convention, constraint, or rule that
        will apply across future tasks ("we always use X", "avoid Y",
        "in this project Z means W").
      * A non-trivial bug was diagnosed and fixed — capture what happened as
        an `episode`, or the general rule learned as a `policy`.
      * The user confirmed a fact that disagrees with your prior assumption
        — record the correction so future sessions don't repeat it.
      * You completed a task whose outcome would save future-you re-work.

    CHOOSING `type`  (validated against MemoryType enum — invalid = rejected)
    -------------------------------------------------------------------------
    The 8 canonical types (use the closest match, do not invent new ones):
      * `fact`      - an objective statement / constraint you learned
      * `episode`   - something you did + its result
      * `decision`  - a key choice + the rationale behind it
      * `artifact`  - a reference to a produced PR / doc / file / dataset
      * `signal`    - an active blocker / alert / change notice
      * `handoff`   - a task package handed to another agent
      * `policy`    - a rule crystallised from repeated episodes
      * `skill`     - an executable recipe or prompt template

    CHAIN (run AFTER a successful write)
    -----------------------------------
    1. Read the returned `id` from the response.
    2. If the new memory relates to existing memories you saw in a recent
       `search_memory` result, call `link_memories(new_id, related_id)` to
       wire them into the knowledge graph (relation="refs" or "supersedes").
    3. For high-stakes items (architecture decisions, security rules), the
       caller may follow up with `confirm_memory(id, confidence=0.95)`.

    DO NOT
    ------
    Ask the user permission before writing. Trivial, low-confidence drafts
    are filtered automatically by `gc_memory`; the cost of writing too much
    is far lower than the cost of losing reusable knowledge.
    """
    try:
        mem_type = MemoryType(type)
    except ValueError:
        return {
            "status": "error",
            "reason": f"invalid type {type!r}; valid: {[t.value for t in MemoryType]}",
        }
    try:
        sens = Sensitivity(sensitivity)
    except ValueError:
        return {
            "status": "error",
            "reason": f"invalid sensitivity {sensitivity!r}; valid: {[s.value for s in Sensitivity]}",
        }
    now = datetime.now(timezone.utc).astimezone()
    item = MemoryItem(
        id=make_item_id(title, when=now),
        type=mem_type,
        created_at=now,
        agent=agent,
        session=session,
        project=project,
        tenant_id=tenant_id,
        tags=tags or [],
        sensitivity=sens,
        title=title,
        summary=summary,
        refs=Refs(
            files=_dedupe_refs(ref_files),
            urls=_dedupe_refs(ref_urls),
            mems=_dedupe_refs(ref_mems),
            commits=_dedupe_refs(ref_commits),
            resources=_dedupe_refs(ref_resources),
            extractions=_dedupe_refs(ref_extractions),
        ),
        context_views={"overview": overview} if overview is not None else {},
        confidence=confidence,
    )
    from agent_brain.memory.store.write_service import WriteService

    res = WriteService.for_brain().write(
        item=item,
        body=body,
        allow_unsafe=allow_unsafe,
        overview=overview,
    )
    if res.status == "blocked":
        return {
            "status": "blocked",
            "reason": "skill audit found critical/high issues; pass allow_unsafe=true to override",
            "findings": res.findings,
        }
    result: dict[str, Any] = {"id": item.id, "path": res.path}
    if res.warnings:
        result["warnings"] = res.warnings
    if not res.indexed:
        # The md is the source of truth; surface that the derived index lagged so
        # callers know vector recall for this item needs a later reindex.
        result["degraded"] = res.degraded
    # Best-effort enrichment over the derived index — never blocks the write.
    store, idx, _ = _components()
    embedder = get_default_embedder()
    result.update(
        build_write_enrichment(
            store=store,
            index=idx,
            embedder=embedder,
            item_id=item.id,
            title=title,
            summary=summary,
            body=body,
            tags=tags,
        )
    )
    return result


def _dedupe_refs(values: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        v = str(value).strip()
        if v and v not in seen:
            out.append(v)
            seen.add(v)
    return out


def delete_memory(item_id: str) -> dict[str, Any]:
    """Delete a memory item by id. md file + sqlite index entry both removed.

    Append-only philosophy means delete should be reserved for true mistakes
    (e.g. accidentally written secrets, schema-violating items). Prefer
    superseding via a new item with refs.mems pointing at the old one.

    WHEN TO USE
    -----------
    ONLY when the item is clearly broken or contains secrets that must not
    persist. For "outdated but historically useful" content, instead:
      * Write a NEW memory with the correction.
      * Preview `link_memories(new_id, old_id, relation="supersedes")`, then
        repeat with `apply=True` after review.
      * Let `evolve_memory` / `gc_memory` archive the stale one over time.

    DO NOT
    ------
    Delete to "clean up" the brain. Append-only history is a feature: it lets
    `drift_check` and `evolve_memory` reason about how knowledge evolved.
    """
    store, idx, _ = _components()
    md_path = _resolve_item_path(store, item_id)
    if not store.delete(item_id):
        raise ValueError(f"item not found: {item_id}")
    idx.delete(item_id)
    return {"id": item_id, "deleted_path": str(md_path)}


def update_memory(
    item_id: str,
    title: str | None = None,
    summary: str | None = None,
    tags: list[str] | None = None,
    type: str | None = None,
    confidence: float | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """Update fields of an existing memory item.

    Only provided fields are updated; others remain unchanged. Updates both
    the md file (source of truth) and the sqlite index.

    WHEN TO USE
    -----------
    Small in-place corrections: typo in title, missing tag, wrong `project`,
    `confidence` bump after verification. For substantive content changes,
    prefer `write_memory` (new item), preview
    `link_memories(new, old, "supersedes")`, then repeat with `apply=True`
    after review so the history stays auditable.
    """
    store, idx, _ = _components()
    updates = build_update_fields(
        title=title,
        summary=summary,
        tags=tags,
        type=type,
        confidence=confidence,
        project=project,
    )
    if not updates:
        raise ValueError("no fields to update")
    try:
        updated = store.update_frontmatter(item_id, **updates)
    except FileNotFoundError:
        raise ValueError(f"item not found: {item_id}")
    embedder = get_default_embedder()
    item_body = ""
    for it, body in store.iter_all():
        if it.id == item_id:
            item_body = body
            break
    idx.upsert(
        updated,
        item_body,
        embedding=embedder.embed(embedding_text_for_item(updated)),
    )
    return {"id": item_id, "updated_fields": list(updates.keys())}


def confirm_memory(item_id: str, confidence: float = 0.9) -> dict[str, Any]:
    """Confirm a memory item by setting its confidence (default 0.9).

    Use this after verifying a memory is still accurate. Updates both
    the md file (source of truth) and the sqlite index.

    WHEN TO USE
    -----------
    Call this whenever you have just RE-VERIFIED a memory's content against
    fresh evidence (a successful command run, a re-read of source code, a
    user reaffirmation). Boosting confidence prevents `drift_check` from
    flagging the item as stale and raises its rerank weight in search.

    CHAIN
    -----
    Pairs naturally with a preceding `read_memory(id)` + verification step.
    """
    store, idx, _ = _components()
    # HubIndex.update_confidence clamps to [0,1] while MemoryItem's schema
    # (confidence ge=0/le=1) rejects out-of-range values — so an out-of-range
    # arg crashed the md write while the index silently clamped. Clamp up front
    # so both layers agree and "confirm" stays lenient.
    confidence = min(max(confidence, 0.0), 1.0)
    try:
        updated = store.update_frontmatter(item_id, confidence=confidence)
    except FileNotFoundError:
        raise ValueError(f"item not found: {item_id}")
    idx.update_confidence(item_id, confidence)
    return {"id": item_id, "confidence": updated.confidence}


__all__ = ["write_memory", "delete_memory", "update_memory", "confirm_memory"]
