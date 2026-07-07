from __future__ import annotations

import json

import typer
from rich.table import Table

from agent_brain.contracts.memory_enums import MemoryType, Sensitivity
from agent_brain.interfaces.cli._app import resource_app
from agent_brain.interfaces.cli._shared import _brain_dir, _parse_enum, console
from agent_brain.memory.evidence.extraction_promotion import promote_extraction_to_memory
from agent_brain.memory.store.items_store import ItemsStore


@resource_app.command("promote-extraction")
def resource_promote_extraction(
    extraction_id: str = typer.Argument(..., help="Extraction sidecar id"),
    memory_type: str = typer.Option("fact", "--type", help="Memory type for the promoted item"),
    title: str | None = typer.Option(None, "--title", help="Promoted memory title"),
    summary: str | None = typer.Option(None, "--summary", help="Promoted memory summary"),
    body: str | None = typer.Option(None, "--body", help="Override promoted memory body"),
    tag: list[str] = typer.Option([], "--tag", help="Additional tag; repeatable"),
    agent: str | None = typer.Option(None, "--agent", help="Writer agent/runtime"),
    session: str | None = typer.Option(None, "--session", help="Session id"),
    project: str | None = typer.Option(None, "--project", help="Project slug"),
    tenant_id: str | None = typer.Option(None, "--tenant", help="Tenant id"),
    sensitivity: str | None = typer.Option(None, "--sensitivity", help="public|internal|private|secret"),
    confidence: float | None = typer.Option(None, "--confidence", min=0.0, max=1.0),
    allow_unsafe: bool = typer.Option(False, "--allow-unsafe", help="Bypass audit gate"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
) -> None:
    """Promote parsed multimodal evidence into a governed MemoryItem."""
    mem_type = _parse_enum(MemoryType, memory_type, "--type")
    sensitivity_value = (
        _parse_enum(Sensitivity, sensitivity, "--sensitivity")
        if sensitivity is not None
        else None
    )
    try:
        result = promote_extraction_to_memory(
            brain_dir=_brain_dir(),
            extraction_id=extraction_id,
            memory_type=mem_type,
            title=title,
            summary=summary,
            body=body,
            tags=tag,
            agent=agent,
            session=session,
            project=project,
            tenant_id=tenant_id,
            sensitivity=sensitivity_value,
            confidence=confidence,
            allow_unsafe=allow_unsafe,
        )
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    if result.status == "blocked":
        typer.echo("blocked: skill audit found critical/high issues", err=True)
        raise typer.Exit(2)
    if not result.item_id:
        typer.echo("write did not return item_id", err=True)
        raise typer.Exit(1)

    item, _body = ItemsStore(_brain_dir() / "items").get(result.item_id)
    payload = {
        "status": result.status,
        "item_id": result.item_id,
        "path": result.path,
        "indexed": result.indexed,
        "degraded": result.degraded,
        "warnings": result.warnings,
        "refs": item.refs.model_dump(mode="json"),
        "tags": item.tags,
    }
    if format == "json":
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if format != "table":
        typer.echo("format must be table or json", err=True)
        raise typer.Exit(2)
    table = Table(title="Promoted extraction")
    table.add_column("field")
    table.add_column("value")
    table.add_row("status", result.status)
    table.add_row("item_id", result.item_id)
    table.add_row("resources", ", ".join(item.refs.resources))
    table.add_row("extractions", ", ".join(item.refs.extractions))
    table.add_row("tags", ", ".join(item.tags))
    table.add_row("indexed", str(result.indexed))
    if result.degraded:
        table.add_row("degraded", ", ".join(result.degraded))
    console.print(table)


__all__ = ["resource_promote_extraction"]
