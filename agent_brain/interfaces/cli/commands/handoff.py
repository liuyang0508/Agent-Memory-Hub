"""Structured handoff and resume commands."""

from __future__ import annotations

import os

from agent_brain.contracts.memory_enums import MemoryType, Sensitivity
from agent_brain.contracts.memory_item import ContextViews, MemoryItem, Refs, Validity
from agent_brain.interfaces.cli._app import app
from agent_brain.interfaces.cli._shared import (
    Path,
    _brain_dir,
    _parse_enum,
    _store_only,
    datetime,
    make_item_id,
    timezone,
    typer,
)
from agent_brain.product.handoff import (
    capture_git_snapshot,
    latest_resumable_handoff,
    render_code_handoff,
)


@app.command()
def handoff(
    objective: str | None = typer.Option(None, "--objective", help="One-sentence task objective"),
    next_action: list[str] = typer.Option([], "--next", help="Required next action; repeatable"),
    verify: list[str] = typer.Option([], "--verify", help="Verification command/outcome; repeatable"),
    completed: list[str] = typer.Option([], "--done", help="Completed task state; repeatable"),
    pending: list[str] = typer.Option([], "--pending", help="Unfinished task state; repeatable"),
    decision: list[str] = typer.Option([], "--decision", help="Decision | reason | reversal cost; repeatable"),
    blocker: list[str] = typer.Option([], "--blocker", help="Active blocker; repeatable"),
    project: str | None = typer.Option(None, "--project"),
    repo: Path = typer.Option(Path.cwd(), "--repo", help="Repository to snapshot"),
    source_agent: str | None = typer.Option(None, "--agent", "--source-agent"),
    target_agent: str = typer.Option("any", "--target-agent"),
    session: str | None = typer.Option(None, "--session"),
    sensitivity: str = typer.Option("internal", "--sensitivity"),
    loop_id: str | None = typer.Option(None, "--loop", help="Derive state from a LoopRun"),
) -> None:
    """Persist one complete, resumable cross-agent checkpoint."""

    task_state: list[str] = []
    if loop_id:
        from agent_brain.memory.loops.loop_store import LoopStore
        from agent_brain.memory.loops.loop_types import LoopNotFoundError

        try:
            loop = LoopStore(_brain_dir()).get(loop_id)
        except LoopNotFoundError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(2)
        objective = objective or loop.goal
        derived_done = [
            str(step.get("title") or step.get("id"))
            for step in loop.steps
            if step.get("status") in {"completed", "verified"}
        ]
        derived_pending = [
            f"{step.get('title') or step.get('id')} [{step.get('status')}]"
            for step in loop.steps
            if step.get("status") not in {"completed", "verified", "cancelled"}
        ]
        completed = [*completed, *derived_done]
        pending = [*pending, *derived_pending]
        blocker = [
            *blocker,
            *[
                str(row.get("reason") or row.get("id"))
                for row in loop.blockers
                if row.get("status") == "open"
            ],
        ]
        if not next_action:
            next_action = [
                str(step.get("title") or step.get("id"))
                for step in loop.steps
                if step.get("status") in {"pending", "running", "blocked"}
            ][:1]
        if not verify:
            verify = list(loop.verification_plan)
        task_state = [
            f"`{step.get('id')}` {step.get('status')}: {step.get('title')}"
            for step in loop.steps
        ]
        if not task_state:
            task_state = [f"`{loop.loop_id}` has no steps"]

    missing: list[str] = []
    if not objective:
        missing.append("--objective or --loop")
    if not completed and not pending:
        missing.append("--done or --pending")
    if not next_action:
        missing.append("--next")
    if not verify:
        missing.append("--verify")
    if missing:
        typer.echo("incomplete handoff; required: " + ", ".join(missing), err=True)
        raise typer.Exit(2)

    source_agent = source_agent or os.environ.get("AGENT_MEMORY_HUB_ADAPTER") or "unknown"
    snapshot = capture_git_snapshot(repo)
    now = datetime.now(timezone.utc).astimezone()
    body = render_code_handoff(
        objective=objective,
        snapshot=snapshot,
        completed=completed,
        pending=pending,
        decisions=decision,
        next_actions=next_action,
        verification=verify,
        blockers=blocker,
        source_agent=source_agent,
        target_agent=target_agent,
        task_state=task_state,
    )
    title = f"Handoff: {objective}"
    next_summary = next_action[0]
    summary = f"{objective} Next: {next_summary}"
    item = MemoryItem(
        id=make_item_id(title, when=now),
        type=MemoryType.handoff,
        created_at=now,
        agent=source_agent,
        session=session,
        project=project,
        tags=["handoff", "resume", f"target:{target_agent}"],
        sensitivity=_parse_enum(Sensitivity, sensitivity, "--sensitivity"),
        title=title,
        summary=summary,
        refs=Refs(commits=[] if snapshot.head == "NO_COMMIT" else [snapshot.head]),
        validity=Validity(
            observed_at=now,
            ttl_hours=720,
            cwd=str(repo.resolve()),
            repo=snapshot.repo,
            branch=snapshot.branch,
            adapter=source_agent,
        ),
        context_views=ContextViews(
            overview=f"{summary} Resume with `memory resume`."
        ),
    )

    from agent_brain.memory.store.write_service import WriteService

    with WriteService.for_brain(_brain_dir()) as service:
        result = service.write(item=item, body=body)
    if result.status == "blocked":
        typer.echo("handoff blocked by sensitive-content audit", err=True)
        for finding in result.findings or []:
            typer.echo(
                f"  [{finding['severity']}] {finding['rule_id']}: {finding['description']}",
                err=True,
            )
        raise typer.Exit(2)
    typer.echo(f"handoff_written={item.id}")
    typer.echo(f"path={result.path}")
    typer.echo(
        "resume="
        + " ".join(
            [
                "memory resume",
                *(["--project", project] if project else []),
            ]
        )
    )
    for warning in result.warnings:
        typer.echo(f"warning: {warning}", err=True)


@app.command()
def resume(
    project: str | None = typer.Option(None, "--project", help="Only this project"),
    query: str | None = typer.Option(None, "--query", help="Bias toward this task/topic"),
    fail_empty: bool = typer.Option(
        False,
        "--fail-empty",
        help="Exit with code 3 when no resumable handoff exists",
    ),
) -> None:
    """Load the full latest gateway-approved handoff."""

    selected = latest_resumable_handoff(_store_only(), project=project, query=query)
    if selected is None:
        typer.echo("no resumable handoff; run `memory brief` for signals and decisions")
        if fail_empty:
            raise typer.Exit(3)
        return
    item, body = selected
    typer.echo(f"resuming={item.id}")
    typer.echo(f"project={item.project or '-'}")
    typer.echo(f"source_agent={item.agent or 'unknown'}")
    typer.echo("---")
    typer.echo(body)


__all__ = ["handoff", "resume"]
