"""CLI review queue commands for unverified memory candidates."""

from __future__ import annotations

import json

from agent_brain.interfaces.cli._app import review_app
from agent_brain.interfaces.cli._shared import HubIndex, Table, _brain_dir, _resolve_id, _store_only, console, typer


@review_app.command(name="status")
def review_status(
    output_format: str = typer.Option(
        "table",
        "--format",
        help="Output format: table or json",
    ),
) -> None:
    """Summarize review and pending queue backlog without changing data."""
    from agent_brain.memory.governance.review_queue import list_review_candidates
    from agent_brain.memory.store.pending import PendingQueue

    review = list_review_candidates(_store_only())
    queue = PendingQueue()
    pending_dead_dir = _brain_dir() / "pending" / "dead"
    pending_dead = len(list(pending_dead_dir.glob("*.jsonl"))) if pending_dead_dir.exists() else 0
    recommended_next = (
        "review list --format json"
        if review.total
        else (
            "memory sync-pending --format json"
            if queue.depth() or pending_dead
            else "none"
        )
    )
    data = {
        "review_total": review.total,
        "pending_depth": queue.depth(),
        "pending_dead": pending_dead,
        "recommended_next": recommended_next,
    }
    if output_format == "json":
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
        return

    table = Table(title="Memory Review Status")
    table.add_column("metric")
    table.add_column("value", justify="right")
    table.add_row("review_total", str(data["review_total"]))
    table.add_row("pending_depth", str(data["pending_depth"]))
    table.add_row("pending_dead", str(data["pending_dead"]))
    table.add_row("recommended_next", str(data["recommended_next"]))
    console.print(table)


@review_app.command(name="list")
def review_list(
    output_format: str = typer.Option(
        "table",
        "--format",
        help="Output format: table or json",
    ),
) -> None:
    """List active needs-review memory candidates."""
    from agent_brain.memory.governance.review_queue import list_review_candidates

    report = list_review_candidates(_store_only())
    data = report.to_dict()
    if output_format == "json":
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
        return

    table = Table(title="Memory Review Queue")
    table.add_column("id")
    table.add_column("confidence", justify="right")
    table.add_column("tags")
    table.add_column("title")
    for candidate in report.candidates:
        table.add_row(
            candidate.id,
            f"{candidate.confidence:.2f}",
            ",".join(candidate.tags),
            candidate.title,
        )
    console.print(table)


@review_app.command(name="approve")
def review_approve(
    item_id: str = typer.Argument(..., help="Memory item ID or prefix"),
    confidence: float = typer.Option(0.7, "--confidence", help="Confidence after approval"),
) -> None:
    """Approve a needs-review candidate so it can participate in normal recall."""
    from agent_brain.memory.governance.review_queue import approve_review_candidate

    store = _store_only()
    item_id = _resolve_id(store, item_id)
    updated = approve_review_candidate(store, item_id, confidence=confidence)
    _update_index_confidence(item_id, updated.confidence)
    typer.echo(f"approved: {item_id} confidence={updated.confidence:.2f}")


@review_app.command(name="reject")
def review_reject(
    item_id: str = typer.Argument(..., help="Memory item ID or prefix"),
    confidence: float = typer.Option(0.1, "--confidence", help="Confidence after rejection"),
) -> None:
    """Reject a needs-review candidate and keep it quarantined from injection."""
    from agent_brain.memory.governance.review_queue import reject_review_candidate

    store = _store_only()
    item_id = _resolve_id(store, item_id)
    updated = reject_review_candidate(store, item_id, confidence=confidence)
    _update_index_confidence(item_id, updated.confidence)
    typer.echo(f"rejected: {item_id} confidence={updated.confidence:.2f}")


def _update_index_confidence(item_id: str, confidence: float) -> None:
    try:
        idx = HubIndex(db_path=_brain_dir() / "index.db")
        try:
            idx.update_confidence(item_id, confidence)
        finally:
            idx.close()
    except Exception:
        pass


__all__ = ["review_approve", "review_list", "review_reject", "review_status"]


@review_app.command(name="generate-semantic")
def review_generate_semantic(
    output_format: str = typer.Option(
        "table",
        "--format",
        help="Output format: table or json",
    ),
    limit: int = typer.Option(50, "--limit", help="Max recent source items to scan"),
) -> None:
    """Generate semantic proactive candidates into the review sidecar."""
    from agent_brain.product.proactive_memory import generate_semantic_candidates

    result = generate_semantic_candidates(_brain_dir(), limit=limit)
    if output_format == "json":
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return
    table = Table(title=f"Semantic memory candidates ({result['created']} created)")
    table.add_column("candidate")
    table.add_column("type")
    table.add_column("summary")
    for candidate in result["candidates"]:
        table.add_row(
            candidate["candidate_id"],
            candidate["type"],
            candidate["summary"],
        )
    console.print(table)


__all__.append("review_generate_semantic")
