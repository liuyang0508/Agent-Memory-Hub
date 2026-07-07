"""CLI commands for raw conversation evidence."""
from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table

from agent_brain.contracts.memory_item import Sensitivity
from agent_brain.interfaces.cli._app import conversation_app
from agent_brain.interfaces.cli._shared import _brain_dir, _parse_enum, console


@conversation_app.command("ingest")
def conversation_ingest(
    transcript_path: str = typer.Argument(..., help="Path to an agent transcript JSONL"),
    agent: str = typer.Option("claude-code", "--agent", help="Source agent/runtime name"),
    session: str | None = typer.Option(None, "--session", help="Session id; defaults to transcript stem"),
    project: str | None = typer.Option(None, "--project", help="Project slug"),
    cwd: str | None = typer.Option(None, "--cwd", help="Working directory observed for the session"),
    tenant_id: str | None = typer.Option(None, "--tenant", help="Tenant id"),
    tags: str | None = typer.Option(None, "--tags", help="Comma-separated tags"),
    sensitivity: str = typer.Option("internal", "--sensitivity", help="public|internal|private|secret"),
    tier: str = typer.Option("hot", "--tier", help="hot|warm|cold|frozen"),
) -> None:
    """Snapshot transcript messages into the raw conversation evidence layer."""
    from agent_brain.contracts.conversation import ConversationTier
    from agent_brain.memory.evidence.conversation_store import ConversationStore

    sensitivity_value = _parse_enum(Sensitivity, sensitivity, "--sensitivity")
    tier_value = _parse_enum(ConversationTier, tier, "--tier")
    tag_values = [tag.strip() for tag in (tags or "").split(",") if tag.strip()]
    result = ConversationStore(_brain_dir()).ingest_transcript(
        Path(transcript_path).expanduser(),
        source_agent=agent,
        session_id=session,
        project=project,
        cwd=cwd,
        tenant_id=tenant_id,
        tags=tag_values,
        sensitivity=sensitivity_value,
        tier=tier_value,
    )
    typer.echo(
        f"conversation_id={result.conversation_id} written={result.written} skipped={result.skipped}"
    )


@conversation_app.command("list")
def conversation_list(
    agent: str | None = typer.Option(None, "--agent", help="Filter by source agent/runtime"),
    project: str | None = typer.Option(None, "--project", help="Filter by project"),
    tenant_id: str | None = typer.Option(None, "--tenant", help="Filter by tenant"),
) -> None:
    """List captured raw conversation evidence streams."""
    from agent_brain.memory.evidence.conversation_store import ConversationStore

    summaries = list(ConversationStore(_brain_dir()).iter_conversations(
        source_agent=agent,
        project=project,
        tenant_id=tenant_id,
    ))
    if not summaries:
        typer.echo("No conversations found.")
        return
    table = Table(title=f"Conversations ({len(summaries)})")
    table.add_column("conversation_id")
    table.add_column("agent")
    table.add_column("session")
    table.add_column("project")
    table.add_column("messages", justify="right")
    table.add_column("tier")
    table.add_column("last_seen")
    for summary in summaries:
        table.add_row(
            summary.conversation_id,
            summary.source_agent,
            summary.session_id or "",
            summary.project or "",
            str(summary.message_count),
            str(summary.tier),
            summary.last_observed_at.isoformat(timespec="seconds"),
        )
    console.print(table)


@conversation_app.command("read")
def conversation_read(
    conversation_id: str = typer.Argument(..., help="Conversation id from conversation list/ingest"),
    head: int = typer.Option(20, "--head", help="Maximum messages to print"),
    format: str = typer.Option("text", "--format", help="text or jsonl"),
) -> None:
    """Read bounded raw conversation evidence."""
    from agent_brain.memory.evidence.conversation_store import ConversationStore
    import json

    store = ConversationStore(_brain_dir())
    messages = list(store.iter_messages(conversation_id))
    if head >= 0:
        messages = messages[:head]
    if not messages:
        typer.echo(f"conversation not found or empty: {conversation_id}", err=True)
        raise typer.Exit(1)
    if format == "jsonl":
        for message in messages:
            typer.echo(json.dumps(message.model_dump(mode="json", exclude_none=False), ensure_ascii=False))
        store.touch_conversation(conversation_id, message_ids=[message.id for message in messages])
        return
    if format != "text":
        typer.echo("invalid --format; choose from: text, jsonl", err=True)
        raise typer.Exit(2)
    for message in messages:
        typer.echo(f"[{message.role}] {message.content_text}")
    store.touch_conversation(conversation_id, message_ids=[message.id for message in messages])


@conversation_app.command("rebalance")
def conversation_rebalance() -> None:
    """Recompute hot/warm/cold/frozen tiers for raw conversation evidence."""
    from agent_brain.contracts.conversation import ConversationTier
    from agent_brain.memory.evidence.conversation_store import ConversationStore

    report = ConversationStore(_brain_dir()).rebalance_tiers()
    table = Table(title="Conversation evidence tiers")
    table.add_column("tier")
    table.add_column("messages", justify="right")
    for tier in ConversationTier:
        table.add_row(tier.value, str(report.distribution.get(tier.value, 0)))
    console.print(table)
    typer.echo(f"rebalanced {report.scanned} message(s), updated {report.updated}")


__all__ = [
    "conversation_ingest",
    "conversation_list",
    "conversation_read",
    "conversation_rebalance",
]
