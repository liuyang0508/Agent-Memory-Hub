"""CLI crud commands. Bodies moved verbatim from cli.py (decorators kept →
Typer self-registers on import)."""
# ruff: noqa: F401,F405
from __future__ import annotations

from agent_brain.interfaces.cli._app import (
    app, audit_app, govern_app, tier_app, entity_app, adapter_app,
)
from agent_brain.interfaces.cli._shared import *  # noqa: F401,F403  (imports, helpers, console, CURRENT_SCHEMA_VERSION)
from agent_brain.interfaces.cli.crud_updates import build_cli_update_fields
from agent_brain.interfaces.cli.commands.links import link, unlink
from agent_brain.interfaces.cli.commands.query import list_recent, read, search, tag_suggest
from agent_brain.memory.recall.embedding_text import embedding_text_for_item
from agent_brain.contracts.memory_item import Refs
import agent_brain.interfaces.cli as _cli  # noqa: E402  late binding for test-patched helpers


@app.command()
def write(
    type: str = typer.Option(..., "--type"),
    title: str = typer.Option(..., "--title"),
    summary: str = typer.Option(..., "--summary"),
    overview: str | None = typer.Option(None, "--overview"),
    body: str = typer.Option("", "--body"),
    tags: str = typer.Option("", "--tags", help="comma-separated"),
    project: str | None = typer.Option(None, "--project"),
    tenant_id: str | None = typer.Option(None, "--tenant-id", "--tenant", help="Tenant id"),
    agent: str | None = typer.Option(None, "--agent"),
    session: str | None = typer.Option(None, "--session"),
    cwd: str | None = typer.Option(None, "--cwd", help="Validity scope cwd for runtime-state observations"),
    adapter: str | None = typer.Option(None, "--adapter", help="Validity scope adapter for runtime-state observations"),
    validity_cwd: str | None = typer.Option(None, "--validity-cwd"),
    validity_repo: str | None = typer.Option(None, "--validity-repo"),
    validity_branch: str | None = typer.Option(None, "--validity-branch"),
    validity_os: str | None = typer.Option(None, "--validity-os"),
    validity_adapter: str | None = typer.Option(None, "--validity-adapter"),
    validity_ttl_hours: int | None = typer.Option(None, "--validity-ttl-hours"),
    ref_file: list[str] = typer.Option([], "--ref-file", help="Source file path; repeatable"),
    ref_url: list[str] = typer.Option([], "--ref-url", help="Source URL; repeatable"),
    ref_mem: list[str] = typer.Option([], "--ref-mem", help="Referenced memory id; repeatable"),
    ref_commit: list[str] = typer.Option([], "--ref-commit", help="Source commit/ref; repeatable"),
    ref_resource: list[str] = typer.Option([], "--ref-resource", help="Resource sidecar id; repeatable"),
    ref_extraction: list[str] = typer.Option([], "--ref-extraction", help="Extraction sidecar id; repeatable"),
    sensitivity: str = typer.Option("internal", "--sensitivity"),
    allow_unsafe: bool = typer.Option(
        False, "--allow-unsafe",
        help="bypass the audit (防进) gate on critical/high findings",
    ),
) -> None:
    """Write a new memory item.

    Persistence is delegated to the single WriteService funnel: the markdown
    append is the only "written" verdict, the sqlite index is repaired
    best-effort, and the audit (防进) gate refuses critical/high content unless
    --allow-unsafe is given. Prints the md path; notes index degradation (if any)
    on stderr so the success path on stdout stays a single clean path line.
    """
    if not body:
        body = sys.stdin.read() if not sys.stdin.isatty() else ""
    now = datetime.now(timezone.utc).astimezone()
    validity = {
        key: value
        for key, value in {
            "cwd": validity_cwd or cwd,
            "repo": validity_repo,
            "branch": validity_branch,
            "os": validity_os,
            "adapter": validity_adapter or adapter,
            "ttl_hours": validity_ttl_hours,
        }.items()
        if value is not None
    }
    item = MemoryItem(
        id=make_item_id(title, when=now),
        type=_parse_enum(MemoryType, type, "--type"),
        created_at=now,
        agent=agent,
        session=session,
        project=project,
        tenant_id=tenant_id,
        tags=[t.strip() for t in tags.split(",") if t.strip()],
        sensitivity=_parse_enum(Sensitivity, sensitivity, "--sensitivity"),
        title=title,
        summary=summary,
        refs=Refs(
            files=_dedupe_refs(ref_file),
            urls=_dedupe_refs(ref_url),
            mems=_dedupe_refs(ref_mem),
            commits=_dedupe_refs(ref_commit),
            resources=_dedupe_refs(ref_resource),
            extractions=_dedupe_refs(ref_extraction),
        ),
        context_views={"overview": overview} if overview is not None else {},
        validity=validity,
    )
    from agent_brain.memory.store.write_service import WriteService

    res = WriteService.for_brain().write(
        item=item,
        body=body,
        allow_unsafe=allow_unsafe,
        overview=overview,
    )
    if res.status == "blocked":
        typer.echo(
            "blocked: skill audit found critical/high issues; pass --allow-unsafe to override",
            err=True,
        )
        for f in res.findings or []:
            typer.echo(f"  [{f['severity']}] {f['rule_id']}: {f['description']}", err=True)
        raise typer.Exit(2)
    typer.echo(res.path)
    for warning in res.warnings:
        typer.echo(f"warning: {warning}", err=True)
    if not res.indexed:
        typer.echo(
            f"degraded: {', '.join(res.degraded)} (md written; reindex pending)",
            err=True,
        )


def _dedupe_refs(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        v = value.strip()
        if v and v not in seen:
            out.append(v)
            seen.add(v)
    return out


@app.command()
def update(
    item_id: str = typer.Argument(...),
    title: str | None = typer.Option(None, "--title"),
    summary: str | None = typer.Option(None, "--summary"),
    add_tags: str | None = typer.Option(None, "--add-tags", help="Comma-separated tags to add"),
    confidence: float | None = typer.Option(None, "--confidence"),
    project: str | None = typer.Option(None, "--project"),
) -> None:
    """Update fields of an existing memory item (supports ID prefix matching)."""
    with _cli._managed_components() as (store, idx, _):
        item_id = _resolve_id(store, item_id)
        current_tags: list[str] | None = None
        if add_tags:
            for item, _ in store.iter_all():
                if item.id == item_id:
                    current_tags = item.tags
                    break
        updates = build_cli_update_fields(
            title=title,
            summary=summary,
            add_tags=add_tags,
            current_tags=current_tags,
            confidence=confidence,
            project=project,
        )
        if not updates:
            typer.echo("no fields to update", err=True)
            raise typer.Exit(1)
        try:
            updated = store.update_frontmatter(item_id, **updates)
        except FileNotFoundError:
            typer.echo(f"item not found: {item_id}", err=True)
            raise typer.Exit(1)
        embedder = _cli.get_default_embedder()
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
    typer.echo(f"updated: {item_id} ({', '.join(updates.keys())})")


@app.command()
def delete(item_id: str = typer.Argument(..., help="Full or prefix of item ID")) -> None:
    """Delete a memory item by ID (supports ID prefix matching)."""
    store = _store_only()
    full_id = _resolve_id(store, item_id)
    with store.locked_catalog():
        if not store.delete(full_id):
            # _resolve_id also walks subdirectories such as archived/. Those
            # files are outside the active-item lock namespace, but still need
            # the catalog lock so pending classification cannot race deletion.
            md_path = next(iter(store.items_dir.rglob(f"{full_id}.md")), None)
            if md_path is None:
                raise FileNotFoundError(f"Item {full_id} not found")
            md_path.unlink()
    _evict_from_index(full_id)
    typer.echo(f"deleted: {full_id}")


@app.command()
def confirm(
    item_id: str = typer.Argument(..., help="Item ID to confirm (resets confidence to 0.9)"),
) -> None:
    """Confirm a memory item — resets its confidence to 0.9 (supports ID prefix)."""
    store = _store_only()
    item_id = _resolve_id(store, item_id)
    try:
        store.update_frontmatter(item_id, confidence=0.9)
    except FileNotFoundError:
        typer.echo(f"Error: Item '{item_id}' not found", err=True)
        raise typer.Exit(1)

    # Also update index if available
    try:
        brain = _brain_dir()
        from agent_brain.platform.indexing.index import HubIndex
        idx = HubIndex(db_path=brain / "index.db")
        idx.update_confidence(item_id, 0.9)
        idx.close()
    except Exception:
        pass

    typer.echo(f"Confirmed {item_id} — confidence set to 0.9")


@app.command(name="injection-feedback")
def injection_feedback(
    injected: str = typer.Option("", "--injected", help="Comma-separated injected item IDs"),
    latest: bool = typer.Option(False, "--latest", help="Use the latest recorded injection cohort"),
    adapter: str | None = typer.Option(None, "--adapter", help="Filter latest cohort by adapter"),
    session: str | None = typer.Option(None, "--session", help="Filter latest cohort by session ID"),
    adopted: str = typer.Option("", "--adopted", help="Comma-separated adopted item IDs"),
    rejected: str = typer.Option("", "--rejected", help="Comma-separated rejected item IDs"),
) -> None:
    """Apply feedback for an injected memory cohort.

    Adopted items are reinforced; rejected items are penalized; injected but
    unmentioned items are left unchanged.
    """
    from agent_brain.memory.context.injection_feedback import InjectionFeedback

    injected_ids = _split_ids(injected)
    cohort = None
    if latest:
        from agent_brain.memory.context.injection_cohorts import latest_injection_cohort

        cohort = latest_injection_cohort(_brain_dir(), adapter=adapter, session_id=session)
        if cohort is None:
            typer.echo("no recorded injection cohort found", err=True)
            raise typer.Exit(2)
        injected_ids = list(cohort.item_ids)
    if not injected_ids:
        typer.echo("missing injected cohort: pass --injected or --latest", err=True)
        raise typer.Exit(2)

    with _cli._managed_components() as (store, idx, _):
        try:
            report = InjectionFeedback(items_store=store, index=idx).apply(
                injected_ids=injected_ids,
                adopted_ids=_split_ids(adopted),
                rejected_ids=_split_ids(rejected),
            )
        except ValueError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(2)
    _record_injection_feedback_outcome(report, cohort=cohort, adapter=adapter, session=session)
    _record_only_rejected_gap_if_needed(report, cohort=cohort, adapter=adapter, session=session)
    typer.echo(
        "injection feedback applied: "
        f"adopted={len(report.adopted)} "
        f"rejected={len(report.rejected)} "
        f"ignored={len(report.ignored)}"
    )


def _record_injection_feedback_outcome(report, *, cohort=None, adapter=None, session=None) -> None:
    from agent_brain.memory.governance.recall_events import (
        record_task_outcome,
        record_task_outcome_feedback_application,
    )

    feedback_signals = ["injection_feedback"]
    if report.rejected:
        feedback_signals.append("user_correction")
    if report.adopted:
        feedback_signals.append("explicit_user_confirmed")
    outcome = "corrected" if report.rejected else "success"
    cohort_id = cohort.cohort_id if cohort is not None else "manual"
    task_outcome = record_task_outcome(
        _brain_dir(),
        task_id=f"injection-feedback:{cohort_id}",
        question=f"injection cohort {cohort_id}",
        outcome=outcome,
        feedback_signals=feedback_signals,
        confidence=1.0,
        injected_ids=list(report.injected),
        adopted_ids=list(report.adopted),
        rejected_ids=list(report.rejected),
        adapter=(cohort.adapter if cohort is not None else adapter) or "unknown",
        session_id=(cohort.session_id if cohort is not None else session),
        cwd=(cohort.cwd if cohort is not None else None),
    )
    record_task_outcome_feedback_application(
        _brain_dir(),
        outcome_id=task_outcome.outcome_id,
        applied=True,
        adopted_ids=list(report.adopted),
        rejected_ids=list(report.rejected),
        adapter=(cohort.adapter if cohort is not None else adapter) or "unknown",
        session_id=(cohort.session_id if cohort is not None else session),
    )


def _record_only_rejected_gap_if_needed(report, *, cohort=None, adapter=None, session=None) -> None:
    if report.adopted or report.ignored:
        return
    if set(report.rejected) != set(report.injected):
        return
    from agent_brain.memory.governance.recall_events import record_gap

    evidence = ["injection_feedback_all_rejected"]
    if cohort is not None:
        evidence.append(f"cohort_id:{cohort.cohort_id}")
        if cohort.query_sha256:
            evidence.append(f"query_sha256:{cohort.query_sha256}")
    record_gap(
        _brain_dir(),
        query=f"injection cohort {cohort.cohort_id} rejected" if cohort else "injection cohort rejected",
        reason="only_rejected",
        rejected_ids=list(report.rejected),
        evidence=evidence,
        adapter=(cohort.adapter if cohort is not None else adapter) or "unknown",
        session_id=(cohort.session_id if cohort is not None else session),
        cwd=(cohort.cwd if cohort is not None else None),
    )


def _split_ids(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


__all__ = [
    'write', 'read', 'update', 'delete', 'confirm', 'injection_feedback',
    'search', 'list_recent', 'tag_suggest', 'link', 'unlink',
]
