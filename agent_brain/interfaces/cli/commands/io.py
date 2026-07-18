"""CLI io commands. Bodies moved verbatim from cli.py (decorators kept →
Typer self-registers on import)."""
# ruff: noqa: F405
from __future__ import annotations

from agent_brain.interfaces.cli._app import app
from agent_brain.interfaces.cli._shared import *  # noqa: F401,F403  (imports, helpers, console, CURRENT_SCHEMA_VERSION)
from agent_brain.memory.evidence.import_service import import_records
from agent_brain.interfaces.mcp.tools.io_export import build_export_payload
import agent_brain.interfaces.cli as _cli  # noqa: E402  late binding for test-patched helpers


@app.command()
def export(
    type: str | None = typer.Option(None, "--type", help="Filter by memory type"),
    project: str | None = typer.Option(None, "--project", help="Filter by project"),
    tenant_id: str | None = typer.Option(None, "--tenant", help="Filter by tenant"),
    format: str = typer.Option("jsonl", "--format", help="Output format: json or jsonl"),
    output: str | None = typer.Option(None, "-o", "--output", help="Output file (default: stdout)"),
) -> None:
    """Export memory items as JSON or JSONL."""
    import json
    import sys

    store = _store_only()
    payload = build_export_payload(
        list(store.iter_all()),
        type=type,
        project=project,
        tenant_id=tenant_id,
        format=format,
    )

    out = open(output, "w", encoding="utf-8") if output else sys.stdout
    try:
        if format == "jsonl":
            if payload["data"]:
                out.write(payload["data"] + "\n")
        else:
            json.dump(payload["items"], out, indent=2, ensure_ascii=False)
            out.write("\n")
    finally:
        if output:
            out.close()

    if output:
        typer.echo(f"Exported {payload['count']} items to {output}")
    else:
        typer.echo(f"\n# Exported {payload['count']} items", err=True)


@app.command(name="import")
def import_items(
    input_file: str = typer.Argument(..., help="Path to JSON or JSONL file (or '-' for stdin)"),
    format: str = typer.Option("jsonl", "--format", help="Input format: json or jsonl"),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite existing items with same ID"),
) -> None:
    """Import memory items from JSON or JSONL (reverse of export)."""
    import json
    import sys

    embedder = _cli.get_default_embedder()

    if input_file == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(input_file).expanduser().read_text(encoding="utf-8")

    if format == "json":
        records = json.loads(raw)
        if isinstance(records, dict) and "items" in records:
            records = records["items"]
    else:
        # Keep raw lines; parse each one inside the per-record handler below so a
        # single malformed JSONL line is counted as an error instead of crashing
        # the whole import before any valid record is written.
        records = [line for line in raw.strip().splitlines() if line.strip()]

    with _cli._managed_components() as (store, idx, _):
        result = import_records(
            records,
            store=store,
            index=idx,
            embedder=embedder,
            overwrite=overwrite,
        )
    for error in result.errors:
        typer.echo(f"  error: {error}", err=True)

    typer.echo(f"Imported {result.imported}, skipped {result.skipped}, errors {len(result.errors)}")
    if result.errors:
        raise typer.Exit(1)


@app.command(name="obsidian-export")
def obsidian_export(
    vault_dir: str = typer.Argument(..., help="Path to Obsidian vault directory"),
    project: str | None = typer.Option(None, "--project", help="Filter by project"),
    type: str | None = typer.Option(None, "--type", help="Filter by memory type"),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite existing files"),
    wiki: bool = typer.Option(False, "--wiki", help="Also generate index.md / log.md / health report (LLM-Wiki layer)"),
) -> None:
    """Export brain pool items to an Obsidian vault as markdown files."""
    from agent_brain.memory.evidence.integrations.obsidian import ObsidianSync

    store = _store_only()
    vault = Path(vault_dir).expanduser()
    sync = ObsidianSync(items_store=store, vault_dir=vault)
    report = sync.export_all(project=project, type=type, overwrite=overwrite)
    typer.echo(f"Exported {report.exported} items, skipped {report.skipped}")

    if wiki:
        from agent_brain.memory.governance.entities import write_entity_pages
        from agent_brain.memory.evidence.integrations.obsidian_wiki import write_wiki_pages

        items = list(store.iter_all())
        paths = write_wiki_pages(items, vault)
        ent_paths = write_entity_pages(items, vault)
        typer.echo(
            f"Generated wiki layer: {', '.join(p.name for p in paths)} "
            f"+ {len(ent_paths)} entity page(s)"
        )

    if report.errors:
        for err in report.errors:
            typer.echo(f"  error: {err}", err=True)
        raise typer.Exit(1)


@app.command(name="obsidian-import")
def obsidian_import(
    vault_dir: str = typer.Argument(..., help="Path to Obsidian vault directory"),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite existing items"),
) -> None:
    """Import Obsidian markdown files back into brain pool."""
    from agent_brain.memory.evidence.integrations.obsidian import ObsidianSync

    store = _store_only()
    sync = ObsidianSync(items_store=store, vault_dir=Path(vault_dir).expanduser())
    report = sync.import_from_vault(overwrite=overwrite)
    typer.echo(f"Imported {report.exported} items, skipped {report.skipped}")
    if report.errors:
        for err in report.errors:
            typer.echo(f"  error: {err}", err=True)
        raise typer.Exit(1)


__all__ = ['export', 'import_items', 'obsidian_export', 'obsidian_import']
