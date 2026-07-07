"""CLI graph inspection command."""
from __future__ import annotations

from agent_brain.interfaces.cli._app import app
from agent_brain.interfaces.cli._shared import *  # noqa: F401,F403


@app.command()
def graph(
    item_id: str = typer.Argument(..., help="Item ID to show graph connections for"),
    depth: int = typer.Option(1, "--depth", help="Max hops from item (1-3)"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
) -> None:
    """Show knowledge graph connections for a memory item (supports ID prefix)."""
    brain = _brain_dir()
    idx = HubIndex(db_path=brain / "index.db")
    store = _store_only()
    item_id = _resolve_id(store, item_id)

    edges = idx.get_refs(item_id)
    neighbors = idx.graph_neighbors(item_id, depth=min(depth, 3))
    idx.close()

    items_by_id = {it.id: it for it, _ in store.iter_all()}

    if format == "json":
        import json

        data = {
            "item_id": item_id,
            "edges": [{"source": s, "target": t, "relation": r} for s, t, r in edges],
            "neighbors": sorted(neighbors),
            "neighbor_details": [
                {"id": nid, "type": str(items_by_id[nid].type), "title": items_by_id[nid].title}
                for nid in sorted(neighbors)
                if nid in items_by_id
            ],
        }
        typer.echo(json.dumps(data, indent=2, ensure_ascii=False))
        return

    if not edges and not neighbors:
        typer.echo(f"No graph connections for {item_id}")
        return

    source_item = items_by_id.get(item_id)
    if source_item:
        typer.echo(f"\n[{source_item.type}] {source_item.title}")
    typer.echo(f"Item: {item_id}\n")

    if edges:
        table = Table(title="Direct Edges")
        table.add_column("direction")
        table.add_column("related_id")
        table.add_column("relation")
        table.add_column("type")
        table.add_column("title")
        for src, tgt, rel in edges:
            other = tgt if src == item_id else src
            direction = "->" if src == item_id else "<-"
            other_item = items_by_id.get(other)
            table.add_row(
                direction,
                other,
                rel,
                str(other_item.type) if other_item else "?",
                other_item.title[:50] if other_item else "(not found)",
            )
        console.print(table)

    if neighbors:
        typer.echo(f"\nReachable within {depth} hop(s): {len(neighbors)} items")
        for nid in sorted(neighbors):
            n = items_by_id.get(nid)
            if n:
                typer.echo(f"  {nid}  [{n.type}]  {n.title}")
            else:
                typer.echo(f"  {nid}  (not in store)")


__all__ = ["graph"]
