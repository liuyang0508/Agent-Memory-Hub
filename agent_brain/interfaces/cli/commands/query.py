"""CLI query commands for reading and searching memory items."""
# ruff: noqa: F405
from __future__ import annotations

import hashlib
import os

from agent_brain.interfaces.cli._app import app
from agent_brain.interfaces.cli._shared import *  # noqa: F401,F403
from agent_brain.memory.context.context_loading import render_context_view, select_context_view
from agent_brain.memory.context.context_packing import ContextPack
from agent_brain.memory.context.context_firewall_types import ContextCandidate
from agent_brain.memory.context.injection_gateway import (
    HYDRATE_ERROR_REASON,
    INJECTION_EXCLUSION_REASONS,
    InjectionResult,
    _record_injection_diagnostic,
    build_injection_context,
    injection_exclusion_reason_counts,
    injection_retrieval_top_k,
    surface_injection_metrics,
)
from agent_brain.memory.context.query_signal import analyze_injection_query
import agent_brain.interfaces.cli as _cli  # noqa: E402  late binding for test-patched helpers

_CONTEXT_VERBOSITIES = {"locator", "overview", "detail", "auto"}
_NEAR_MISS_REJECTION_REASONS = {
    "missing_source",
    "negative_feedback",
    "query_not_injectable",
    "scope_mismatch",
    "sensitivity_not_allowed",
    "stale_handoff",
    "stale_negative_state",
    "stale_positive_state",
    "stale_signal",
    "stale_current_state",
    "superseded",
    "temporal_state_conflict_newer",
    "topic_recency_newer",
    "very_low_confidence",
}
_AGGREGATE_GAP_KEYS = {
    "excluded_count",
    "hydrate_error_count",
    "included_count",
    "retrieved_count",
    "source_evidence_count",
}


@app.command()
def read(
    item_id: str = typer.Argument(..., help="Full or prefix of item ID"),
    head: int | None = typer.Option(None, "--head", help="Only the first N chars of body (bounded read)"),
    view: str = typer.Option("detail", "--view", help="Context view: locator, overview, or detail"),
) -> None:
    """Read full content of one item (supports ID prefix matching).

    Default prints the whole body. ``--head N`` bounds it; large bodies print a
    stderr hint so a stray full read is visible without changing stdout.
    """
    store = _store_only()
    view = _parse_context_verbosity(view, "--view")
    full_id = _resolve_id(store, item_id)
    for item, body in store.iter_all(include_archived=True):
        if item.id == full_id:
            typer.echo(item.model_dump_json(indent=2))
            typer.echo("---")
            if view == "locator":
                typer.echo(item.context_views.locator)
                return
            if view == "overview":
                typer.echo(item.context_views.overview or item.context_views.locator)
                return
            if head is not None and len(body) > head:
                typer.echo(body[:head])
                typer.echo(f"… [+{len(body) - head} more chars; drop --head for full body]")
            else:
                if head is None and len(body) > 4000:
                    typer.echo(f"[large item: {len(body)} chars; for resume use `memory brief`, "
                               f"or `--head N` to bound this read]", err=True)
                typer.echo(body)
            return
    # _resolve_id confirmed a md file on disk, but iter_all skips files that
    # fail to parse/validate — so reaching here means the item exists yet could
    # not be loaded. Surface a diagnostic + non-zero exit instead of returning
    # silently (which looked like success with empty output).
    typer.echo(f"item not found or could not be parsed: {full_id}", err=True)
    raise typer.Exit(1)


@app.command()
def search(
    query: str = typer.Argument(...),
    top_k: int = typer.Option(10, "--top-k"),
    type: str | None = typer.Option(None, "--type", help="Filter by memory type"),
    project: str | None = typer.Option(None, "--project", help="Filter by project"),
    tags: str | None = typer.Option(None, "--tags", help="Comma-separated tags (all must match)"),
    exclude_tag: str | None = typer.Option(None, "--exclude-tag", help="Comma-separated tags to exclude"),
    since: int | None = typer.Option(None, "--since", help="Only items created within N days"),
    prefer_type: str | None = typer.Option(None, "--prefer-type", help="Comma-separated type priority for reranking"),
    output_format: str = typer.Option("table", "--format", help="Output format: table or text"),
    explain: bool = typer.Option(False, "--explain", help="Show retrieval trace in text output."),
    verbosity: str | None = typer.Option(
        None,
        "--verbosity",
        help="Context view: locator, overview, detail, or auto. Defaults to auto with --context-firewall, otherwise locator.",
    ),
    context_firewall: bool = typer.Option(
        False,
        "--context-firewall",
        help="Filter candidates for safe before-inject context.",
    ),
    include_stale_state: bool = typer.Option(
        False,
        "--include-stale-state",
        help="Include stale runtime-state observations for audit/debug searches.",
    ),
    record_injection_cohort: bool = typer.Option(
        False,
        "--record-injection-cohort",
        help="Record Gateway-approved hit IDs as an injection cohort (requires --context-firewall).",
    ),
    record_recall_gap: bool = typer.Option(
        False,
        "--record-recall-gap",
        help="Record a runtime recall gap when search emits no context.",
    ),
    adapter: str = typer.Option("unknown", "--adapter", help="Adapter name for injection cohort records"),
    session: str | None = typer.Option(None, "--session", help="Session ID for injection cohort records"),
    cwd: str | None = typer.Option(None, "--cwd", help="Working directory for injection cohort records"),
) -> None:
    """Search memory items by query (BM25 + vector RRF)."""
    if record_injection_cohort and not context_firewall:
        typer.echo(
            "--record-injection-cohort requires --context-firewall",
            err=True,
        )
        raise typer.Exit(2)
    verbosity = _parse_context_verbosity(
        verbosity or ("auto" if context_firewall else "locator"),
        "--verbosity",
    )
    store, _, retriever = _cli._open_components()
    sf = SearchFilter(
        type=type,
        project=project,
        tags=[t.strip() for t in tags.split(",") if t.strip()] if tags else [],
        exclude_tags=[t.strip() for t in exclude_tag.split(",") if t.strip()] if exclude_tag else [],
        since_days=since,
        include_stale_state=include_stale_state,
    )
    query_signal = None
    effective_query = query
    answerability_query = os.environ.get("AGENT_MEMORY_HUB_RAW_QUERY") or query
    if context_firewall:
        frame_query = answerability_query.replace("|", " ")
        query_signal = analyze_injection_query(frame_query, brain_dir=_brain_dir())
        if query_signal.injectable and query_signal.terms:
            effective_query = "|".join(query_signal.terms[:6])
    retrieval_top_k = injection_retrieval_top_k(top_k) if context_firewall else top_k
    search_kwargs = {
        "top_k": retrieval_top_k,
        "filters": sf,
        "explain": explain or record_injection_cohort,
    }
    if context_firewall:
        # Retrieval candidates are not final prompt context. Only Gateway
        # inclusions are allowed to affect access accounting.
        search_kwargs["record_access"] = False
    hits = retriever.search(effective_query, **search_kwargs)
    raw_candidate_count = len(hits)
    if not hits:
        if record_recall_gap:
            _record_search_gap(
                query=answerability_query if context_firewall else query,
                reason="empty_recall",
                evidence=(
                    _aggregate_gap_evidence(
                        retrieved_count=0,
                        included_count=0,
                        decisions=(),
                        hydrate_error_count=0,
                    )
                    if context_firewall
                    else []
                ),
                adapter=adapter,
                session=session,
                cwd=cwd,
                privacy_safe=context_firewall,
            )
        typer.echo("no matches")
        return
    items_by_id = {item.id: (item, body) for item, body in store.iter_all()}
    type_order = _parse_type_order(prefer_type)
    context_packs_by_id: dict[str, ContextPack] = {}
    injection_result: InjectionResult | None = None
    injection_metrics: dict[str, object] | None = None
    if context_firewall:
        hydrate_error_count = sum(1 for hit in hits if hit.id not in items_by_id)
        _record_injection_diagnostic(
            surface="cli-search",
            reason=HYDRATE_ERROR_REASON,
            count=hydrate_error_count,
        )
        hit_by_id = {hit.id: hit for hit in hits}
        candidates = [
            ContextCandidate(
                item=items_by_id[hit.id][0],
                body=items_by_id[hit.id][1],
                score=_context_firewall_candidate_score(
                    hit.score,
                    items_by_id[hit.id][0],
                    type_order,
                ),
                source="cli-search",
            )
            for hit in hits
            if hit.id in items_by_id
        ]
        current_scope: dict[str, str] = {}
        if cwd:
            current_scope["cwd"] = cwd
        if adapter != "unknown":
            current_scope["adapter"] = adapter
        injection_result = build_injection_context(
            candidates,
            query=answerability_query,
            query_signal=query_signal,
            brain_dir=_brain_dir(),
            requested=verbosity,
            max_items=top_k,
            current_scope=current_scope or None,
        )
        injection_metrics = surface_injection_metrics(
            injection_result,
            raw_candidate_count=raw_candidate_count,
            hydrate_error_count=hydrate_error_count,
        )
        context_packs_by_id = {
            entry.decision.candidate.item.id: entry.pack
            for entry in injection_result.included
        }
        hits = [
            hit_by_id[item_id]
            for item_id in context_packs_by_id
            if item_id in hit_by_id
        ]
        retriever.record_accesses(hits)
        if not hits:
            if record_recall_gap:
                _record_search_gap(
                    query=answerability_query,
                    reason="all_candidates_rejected",
                    evidence=_aggregate_gap_evidence(
                        retrieved_count=raw_candidate_count,
                        included_count=0,
                        decisions=injection_result.excluded,
                        hydrate_error_count=hydrate_error_count,
                    ),
                    adapter=adapter,
                    session=session,
                    cwd=cwd,
                    privacy_safe=True,
                )
            typer.echo("no matches")
            return
        if record_recall_gap:
            rejected = _significant_rejected_decisions(injection_result.excluded)
            if rejected or hydrate_error_count:
                _record_search_gap(
                    query=answerability_query,
                    reason="partial_candidates_rejected",
                    evidence=_aggregate_gap_evidence(
                        retrieved_count=raw_candidate_count,
                        included_count=len(injection_result.included),
                        decisions=injection_result.excluded,
                        hydrate_error_count=hydrate_error_count,
                    ),
                    adapter=adapter,
                    session=session,
                    cwd=cwd,
                    privacy_safe=True,
                )
    if type_order:
        hits.sort(key=lambda h: _type_priority(h.id, items_by_id, type_order))
    if record_injection_cohort:
        final_ids = [hit.id for hit in hits if hit.id in items_by_id]
        if final_ids:
            from agent_brain.memory.context.injection_cohorts import record_injection_cohort as _record_injection_cohort

            pack_metrics: dict[str, object] = {}
            if injection_metrics is not None:
                pack_metrics.update(injection_metrics)
            retrieval_trace = [
                hit.trace.to_dict()
                for hit in hits
                if hit.trace is not None and hit.id in final_ids
            ]
            if retrieval_trace and len(retrieval_trace) == len(final_ids):
                pack_metrics["retrieval_trace"] = retrieval_trace
            _record_injection_cohort(
                _brain_dir(),
                item_ids=final_ids,
                adapter=adapter,
                session_id=session,
                cwd=cwd,
                query=query,
                query_terms=_query_terms_for_injection_record(query) if context_firewall else None,
                pack_metrics=pack_metrics or None,
            )
    if output_format == "text":
        for hit in hits:
            meta = items_by_id.get(hit.id)
            if not meta:
                continue
            item, _body = meta
            for line in _render_text_hit(
                item,
                body=_body,
                include_audit_metadata=context_firewall,
                verbosity=verbosity,
                context_pack=context_packs_by_id.get(hit.id),
                retrieval_trace=hit.trace if explain else None,
            ):
                typer.echo(line)
            typer.echo("")
    else:
        table = Table()
        table.add_column("rank")
        table.add_column("id")
        table.add_column("type")
        table.add_column("title")
        for rank, hit in enumerate(hits, 1):
            meta = items_by_id.get(hit.id)
            display_title = meta[0].title if meta else "(missing)"
            display_type = str(meta[0].type) if meta else "?"
            table.add_row(str(rank), hit.id, display_type, display_title)
        console.print(table)


def _record_search_gap(
    *,
    query: str,
    reason: str,
    injected_ids: list[str] | None = None,
    rejected_ids: list[str] | None = None,
    evidence: list[str] | None = None,
    adapter: str = "unknown",
    session: str | None = None,
    cwd: str | None = None,
    privacy_safe: bool = False,
) -> None:
    from agent_brain.memory.governance.recall_events import record_gap

    if privacy_safe:
        query = "sha256:" + hashlib.sha256(query.encode("utf-8")).hexdigest()
        injected_ids = []
        rejected_ids = []
        evidence = [
            value
            for value in (evidence or [])
            if _is_aggregate_gap_evidence(value)
        ]
    record_gap(
        _brain_dir(),
        query=query,
        reason=reason,
        injected_ids=injected_ids or [],
        rejected_ids=rejected_ids or [],
        evidence=evidence or [],
        adapter=adapter,
        session_id=session,
        cwd=cwd,
    )


def _query_terms_for_injection_record(query: str) -> list[str]:
    if "|" not in query:
        stripped = query.strip()
        if stripped and len(stripped) <= 64 and not any(ch.isspace() for ch in stripped):
            return [stripped]
        return []
    return [
        term.strip()
        for term in query.split("|")
        if term.strip()
    ][:12]


def _significant_rejected_decisions(decisions) -> list:
    return [
        decision
        for decision in decisions
        if any(reason in _NEAR_MISS_REJECTION_REASONS for reason in decision.reasons)
    ]


def _aggregate_gap_evidence(
    *,
    retrieved_count: int,
    included_count: int,
    decisions,
    hydrate_error_count: int,
) -> list[str]:
    reason_counts = injection_exclusion_reason_counts(
        decisions,
        hydrate_error_count=hydrate_error_count,
    )
    evidence = [
        f"retrieved_count={retrieved_count}",
        f"included_count={included_count}",
        f"hydrate_error_count={hydrate_error_count}",
        f"excluded_count={len(decisions) + hydrate_error_count}",
    ]
    evidence.extend(
        f"excluded_reason.{reason}={count}"
        for reason, count in reason_counts.items()
    )
    return evidence


def _is_aggregate_gap_evidence(value: str) -> bool:
    key, separator, count = value.partition("=")
    if not separator or not count.isdigit():
        return False
    if key in _AGGREGATE_GAP_KEYS:
        return True
    prefix = "excluded_reason."
    return key.startswith(prefix) and key[len(prefix):] in INJECTION_EXCLUSION_REASONS


def _parse_type_order(prefer_type: str | None) -> list[str]:
    if not prefer_type:
        return []
    return [value.strip() for value in prefer_type.split(",") if value.strip()]


def _type_priority(hit_id: str, items_by_id: dict, type_order: list[str]) -> int:
    meta = items_by_id.get(hit_id)
    if not meta:
        return len(type_order)
    item_type = str(meta[0].type)
    return type_order.index(item_type) if item_type in type_order else len(type_order)


def _context_firewall_candidate_score(score: float, item, type_order: list[str]) -> float:
    if not type_order:
        return score
    try:
        priority = type_order.index(str(item.type))
    except ValueError:
        return score
    return score + float(len(type_order) - priority)


def _parse_context_verbosity(value: str, flag: str) -> str:
    normalized = value.strip().lower()
    if normalized not in _CONTEXT_VERBOSITIES:
        typer.echo(
            f"invalid {flag} {value!r}; choose from: locator, overview, detail",
            err=True,
        )
        raise typer.Exit(2)
    return normalized


def _render_text_hit(
    item,
    *,
    body: str = "",
    include_audit_metadata: bool,
    verbosity: str = "locator",
    firewall_decision=None,
    context_pack: ContextPack | None = None,
    retrieval_trace=None,
) -> list[str]:
    conf = f" conf:{item.confidence:.1f}" if item.confidence is not None and item.confidence < 1.0 else ""
    display_id = item.id if include_audit_metadata else item.id[:8]
    lines = [f"[{item.type}] **{item.title}** (id:{display_id}{conf})"]
    if include_audit_metadata:
        if context_pack is None:
            raise RuntimeError("gateway context pack required for prompt output")
        lines.append(
            "  "
            f"view={context_pack.selected_view} "
            f"packed={context_pack.packed_tokens}/{context_pack.full_tokens}t "
            f"retrieve=\"{context_pack.cli_retrieve_hint}\""
        )
        text = context_pack.text
    else:
        selection = select_context_view(
            item,
            body,
            requested=verbosity,
            firewall_decision=firewall_decision,
        )
        text = _context_view_text(item, body=body, verbosity=selection.view)
    if text:
        for text_line in text.splitlines():
            lines.append(f"  {text_line}")
    if retrieval_trace is not None:
        lines.append(f"  trace: {retrieval_trace.compact()}")
    return lines


def _context_view_text(item, *, body: str, verbosity: str) -> str:
    return render_context_view(item, body, verbosity)


def _format_injection_audit_metadata(item, *, selection=None, context_pack=None) -> str:
    parts = [f"created_at={item.created_at.isoformat()}"]
    if selection is not None:
        parts.append(f"view={selection.view}")
        parts.append(f"load_reason={','.join(selection.reasons)}")
    if context_pack is not None:
        parts.append(f"retrieve={context_pack.cli_retrieve_hint}")
        parts.append(f"packed={context_pack.packed_tokens}/{context_pack.full_tokens}t")
    if item.project:
        parts.append(f"project={item.project}")
    if item.tags:
        parts.append("tags=" + ",".join(item.tags[:6]))
    scope = _format_validity_scope(item)
    if scope:
        parts.append(f"scope={scope}")
    refs = _format_refs(item)
    if refs:
        parts.append(f"refs={refs}")
    feedback = _format_feedback(item)
    if feedback:
        parts.append(f"feedback={feedback}")
    source_kind = getattr(getattr(item, "source", None), "kind", None)
    if source_kind and str(source_kind) != "manual":
        parts.append(f"source={source_kind}")
    return " | ".join(parts)


def _format_validity_scope(item) -> str:
    validity = getattr(item, "validity", None)
    if validity is None:
        return ""
    values: list[str] = []
    for field in ("cwd", "repo", "branch", "os", "adapter"):
        value = getattr(validity, field, None)
        if value:
            values.append(f"{field}={value}")
    if getattr(validity, "ttl_hours", None):
        values.append(f"ttl_hours={validity.ttl_hours}")
    if getattr(validity, "observed_at", None):
        values.append(f"observed_at={validity.observed_at.isoformat()}")
    return " ".join(values)


def _format_refs(item) -> str:
    refs = getattr(item, "refs", None)
    if refs is None:
        return ""
    values: list[str] = []
    for field in ("urls", "files", "commits", "resources", "extractions", "mems"):
        field_values = getattr(refs, field, None) or []
        formatted = _format_limited_values(field_values)
        if formatted:
            values.append(f"{field}:{formatted}")
    return " ".join(values)


def _format_limited_values(values, *, limit: int = 2) -> str:
    items = [str(value) for value in values if str(value)]
    if not items:
        return ""
    visible = items[:limit]
    if len(items) > limit:
        visible.append(f"+{len(items) - limit}")
    return ",".join(visible)


def _format_feedback(item) -> str:
    if not (item.support_count or item.contradict_count or item.gain_score):
        return ""
    return (
        f"support:{item.support_count} "
        f"contradict:{item.contradict_count} "
        f"gain:{item.gain_score:.2f}"
    )


@app.command(name="list-recent")
def list_recent(
    count: int = typer.Option(10, "--n"),
    type: str | None = typer.Option(None, "--type"),
    project: str | None = typer.Option(None, "--project"),
    tag: str | None = typer.Option(None, "--tag", help="Filter by tag (items must have this tag)"),
    grep: str | None = typer.Option(None, "--grep", help="Filter by title substring (case-insensitive)"),
) -> None:
    """List the n most recent items, optionally filtered by type/project/tag/title."""
    store = _store_only()
    items = list(store.iter_all())
    if type:
        items = [(it, body) for it, body in items if str(it.type) == type]
    if project:
        items = [(it, body) for it, body in items if it.project == project]
    if tag:
        items = [(it, body) for it, body in items if tag in it.tags]
    if grep:
        grep_lower = grep.lower()
        items = [(it, body) for it, body in items if grep_lower in it.title.lower()]
    items.sort(key=lambda pair: pair[0].created_at, reverse=True)
    for item, _ in items[:count]:
        proj = f" @{item.project}" if item.project else ""
        typer.echo(f"{item.created_at.isoformat()}  {item.id}  [{item.type}]{proj}  {item.title}")


@app.command(name="tag-suggest")
def tag_suggest(
    text: str = typer.Argument(..., help="Text to suggest tags for"),
    max_tags: int = typer.Option(5, "--max"),
) -> None:
    """Suggest tags based on similar existing items."""
    _, idx, _ = _cli._open_components()
    embedder = _cli.get_default_embedder()
    from agent_brain.memory.recall.retrieval import suggest_tags as _suggest_tags
    suggestions = _suggest_tags(idx, embedder, text, max_tags=max_tags)
    if not suggestions:
        typer.echo("(no suggestions)")
        return
    for tag, count in suggestions:
        typer.echo(f"  {tag} ({count})")


__all__ = ["read", "search", "list_recent", "tag_suggest"]
