"""Recall drift CLI commands."""

from __future__ import annotations

import json

from agent_brain.interfaces.cli._app import recall_drift_app
from agent_brain.interfaces.cli._shared import Table, _brain_dir, console, typer
import agent_brain.interfaces.cli as _cli


@recall_drift_app.command(name="report")
def recall_drift_report(
    output_format: str = typer.Option(
        "table",
        "--format",
        help="Output format: table or json",
    ),
) -> None:
    """Show read-only recall drift summary from runtime sidecar records."""
    from agent_brain.memory.governance.recall_drift_report import build_recall_drift_report

    report = build_recall_drift_report(_brain_dir())
    data = {
        "gap_count": report.gap_count,
        "task_outcome_count": report.task_outcome_count,
        "gaps_by_reason": report.gaps_by_reason,
        "gaps_by_family": report.gaps_by_family,
        "task_outcomes_by_status": report.task_outcomes_by_status,
        "task_outcome_feedback_applied_count": report.task_outcome_feedback_applied_count,
        "task_outcome_feedback_skipped_count": report.task_outcome_feedback_skipped_count,
        "implicit_positive_count": report.implicit_positive_count,
        "explicit_correction_count": report.explicit_correction_count,
    }
    if output_format == "json":
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
        return

    table = Table(title="Recall Drift Report")
    table.add_column("metric")
    table.add_column("value", justify="right")
    table.add_row("gap_count", str(report.gap_count))
    table.add_row("task_outcome_count", str(report.task_outcome_count))
    table.add_row(
        "task_outcome_feedback_applied_count",
        str(report.task_outcome_feedback_applied_count),
    )
    table.add_row(
        "task_outcome_feedback_skipped_count",
        str(report.task_outcome_feedback_skipped_count),
    )
    table.add_row("implicit_positive_count", str(report.implicit_positive_count))
    table.add_row("explicit_correction_count", str(report.explicit_correction_count))
    for reason, count in sorted(report.gaps_by_reason.items()):
        table.add_row(f"gap:{reason}", str(count))
    for family, count in sorted(report.gaps_by_family.items()):
        table.add_row(f"gap_family:{family}", str(count))
    for status, count in sorted(report.task_outcomes_by_status.items()):
        table.add_row(f"outcome:{status}", str(count))
    console.print(table)


@recall_drift_app.command(name="apply-outcomes")
def recall_drift_apply_outcomes(
    output_format: str = typer.Option(
        "table",
        "--format",
        help="Output format: table or json",
    ),
    force: bool = typer.Option(False, "--force", help="Re-apply outcomes even if already recorded"),
) -> None:
    """Apply explicit task outcome adopted/rejected ids to memory feedback."""
    from agent_brain.memory.governance.outcome_feedback import apply_task_outcome_feedback_batch

    store, index, _retriever = _cli._open_components()
    report = apply_task_outcome_feedback_batch(
        _brain_dir(),
        items_store=store,
        index=index,
        force=force,
    )
    data = report.to_dict()
    if output_format == "json":
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
        return

    table = Table(title="Task Outcome Feedback")
    table.add_column("metric")
    table.add_column("value", justify="right")
    table.add_row("applied_count", str(report.applied_count))
    table.add_row("skipped_count", str(report.skipped_count))
    table.add_row("already_applied_count", str(report.already_applied_count))
    console.print(table)


@recall_drift_app.command(name="gap-clusters")
def recall_drift_gap_clusters(
    output_format: str = typer.Option(
        "table",
        "--format",
        help="Output format: table or json",
    ),
    top_n: int | None = typer.Option(None, "--top-n", help="Limit returned clusters"),
    min_size: int = typer.Option(1, "--min-size", help="Only include clusters with at least N gaps"),
) -> None:
    """Cluster recall gap records into operational root-cause buckets."""
    from agent_brain.memory.governance.recall_gap_clustering import build_gap_cluster_report

    report = build_gap_cluster_report(
        _brain_dir(),
        top_n=top_n,
        min_size=min_size,
    )
    data = report.to_dict()
    if output_format == "json":
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
        return

    table = Table(title="Recall Gap Clusters", expand=True)
    table.add_column("rank", no_wrap=True)
    table.add_column("size", justify="right", no_wrap=True)
    table.add_column("risk", no_wrap=True)
    table.add_column("owner", no_wrap=True)
    table.add_column("root_cause", overflow="fold")
    table.add_column("labels", overflow="fold")
    table.add_column("title", overflow="fold")
    for rank, cluster in enumerate(report.clusters, 1):
        table.add_row(
            str(rank),
            str(cluster.size),
            cluster.profile.risk_level,
            cluster.profile.suggested_owner,
            cluster.profile.root_cause,
            ",".join(cluster.labels),
            cluster.title,
        )
    console.print(table)


@recall_drift_app.command(name="replay-cohort")
def recall_drift_replay_cohort(
    root_cause: str = typer.Option(
        "query_gate_underqualified",
        "--root-cause",
        help="Operational root cause to export from gap clusters",
    ),
    limit: int | None = typer.Option(None, "--limit", help="Maximum deduped cases to return"),
    output_format: str = typer.Option(
        "table",
        "--format",
        help="Output format: table or json",
    ),
) -> None:
    """Export deduped recall gap prompts for regression replay."""
    from agent_brain.memory.governance.recall_gap_clustering import build_gap_replay_cohort

    cohort = build_gap_replay_cohort(
        _brain_dir(),
        root_cause=root_cause,
        limit=limit,
    )
    data = cohort.to_dict()
    if output_format == "json":
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
        return

    table = Table(title=f"Recall Gap Replay Cohort: {root_cause}", expand=True)
    table.add_column("gap", overflow="fold")
    table.add_column("reason", no_wrap=True)
    table.add_column("owner", no_wrap=True)
    table.add_column("risk", no_wrap=True)
    table.add_column("query", overflow="fold")
    for case in cohort.cases:
        table.add_row(
            case.gap_id,
            case.reason,
            case.expected_owner,
            case.expected_risk,
            case.query,
        )
    console.print(table)
    console.print(
        f"matched_gap_count={cohort.matched_gap_count} "
        f"deduped_query_count={cohort.deduped_query_count}"
    )


__all__ = [
    "recall_drift_apply_outcomes",
    "recall_drift_gap_clusters",
    "recall_drift_replay_cohort",
    "recall_drift_report",
]
