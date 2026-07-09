"""Hook runtime diagnostics commands."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.table import Table

from agent_brain.interfaces.cli._app import hook_app
from agent_brain.interfaces.cli._shared import _brain_dir, console
from agent_brain.memory.context.injection_cohorts import iter_injection_cohorts
from agent_brain.memory.governance.recall_events import iter_gap_records, iter_task_outcomes


@hook_app.command("recent")
def hook_recent(
    limit: int = typer.Option(10, "--limit", help="Max recent hook records to show"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
    adapter: str | None = typer.Option(None, "--adapter", help="Filter by adapter"),
    session: str | None = typer.Option(None, "--session", help="Filter by session id"),
) -> None:
    """Show recent injection, gap, and timeout records from hook runtime logs."""
    brain = _brain_dir()
    rows = _recent_hook_rows(brain, adapter=adapter, session=session, limit=limit)
    if format == "json":
        typer.echo(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    if format != "table":
        typer.echo("format must be table or json", err=True)
        raise typer.Exit(2)
    if not rows:
        typer.echo("No hook runtime records found.")
        return

    table = Table(title=f"Recent Hook Runtime Records ({len(rows)})")
    table.add_column("time")
    table.add_column("kind")
    table.add_column("adapter")
    table.add_column("session")
    table.add_column("status")
    table.add_column("detail")
    for row in rows:
        table.add_row(
            _short_time(str(row["timestamp"])),
            str(row["kind"]),
            str(row.get("adapter") or "-"),
            _short(str(row.get("session_id") or "-"), 18),
            str(row.get("status") or "-"),
            _short(str(row.get("detail") or "-"), 80),
        )
    console.print(table)


def _recent_hook_rows(
    brain: Path,
    *,
    adapter: str | None = None,
    session: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for cohort in iter_injection_cohorts(brain, adapter=adapter, session_id=session):
        keywords = "|".join(cohort.query_terms)
        detail = ",".join(cohort.item_ids[:3])
        if keywords:
            detail = f"keywords={keywords} | ids={detail}"
        rows.append({
            "timestamp": cohort.timestamp,
            "kind": "injection",
            "adapter": cohort.adapter,
            "session_id": cohort.session_id,
            "cwd": cohort.cwd,
            "status": f"injected:{len(cohort.item_ids)}",
            "detail": detail,
            "keywords": keywords,
            "item_ids": list(cohort.item_ids),
            "cohort_id": cohort.cohort_id,
        })

    for gap in iter_gap_records(brain):
        if adapter and gap.adapter != adapter:
            continue
        if session and gap.session_id != session:
            continue
        detail_parts = [gap.normalized_query]
        if gap.rejected_ids:
            detail_parts.append("rejected=" + ",".join(gap.rejected_ids[:3]))
        if gap.evidence:
            detail_parts.append("evidence=" + ";".join(gap.evidence[:2]))
        rows.append({
            "timestamp": gap.timestamp,
            "kind": "recall_gap",
            "adapter": gap.adapter,
            "session_id": gap.session_id,
            "cwd": gap.cwd,
            "status": gap.reason,
            "detail": " | ".join(detail_parts),
            "gap_id": gap.gap_id,
            "injected_ids": list(gap.injected_ids),
            "rejected_ids": list(gap.rejected_ids),
            "evidence": list(gap.evidence),
        })

    for outcome in iter_task_outcomes(brain):
        if adapter and outcome.adapter != adapter:
            continue
        if session and outcome.session_id != session:
            continue
        injected_ids = list(outcome.injected_ids)
        adopted_ids = list(outcome.adopted_ids)
        rejected_ids = list(outcome.rejected_ids)
        handled_ids = set(adopted_ids) | set(rejected_ids)
        ignored_ids = [item_id for item_id in injected_ids if item_id not in handled_ids]
        cohort_id = _cohort_id_from_task_outcome(outcome.task_id, outcome.question)
        usage = {
            "injected": len(injected_ids),
            "adopted": len(adopted_ids),
            "rejected": len(rejected_ids),
            "ignored": len(ignored_ids),
        }
        rows.append({
            "timestamp": outcome.timestamp,
            "kind": "outcome",
            "adapter": outcome.adapter,
            "session_id": outcome.session_id,
            "cwd": outcome.cwd,
            "status": outcome.outcome,
            "detail": (
                f"adopted={usage['adopted']} "
                f"rejected={usage['rejected']} "
                f"ignored={usage['ignored']}"
            ),
            "outcome_id": outcome.outcome_id,
            "task_id": outcome.task_id,
            "cohort_id": cohort_id,
            "usage": usage,
            "injected_ids": injected_ids,
            "adopted_ids": adopted_ids,
            "rejected_ids": rejected_ids,
            "ignored_ids": ignored_ids,
            "feedback_signals": list(outcome.feedback_signals),
            "value_tags": list(outcome.value_tags),
        })

    for latency in _iter_hook_latency(brain):
        if adapter and latency.get("adapter") != adapter:
            continue
        if session and latency.get("session_id") != session:
            continue
        rows.append({
            "timestamp": str(latency.get("timestamp") or ""),
            "kind": "latency",
            "adapter": latency.get("adapter") or "unknown",
            "session_id": latency.get("session_id"),
            "cwd": latency.get("cwd"),
            "status": latency.get("status") or "-",
            "detail": latency.get("detail") or latency.get("stage") or "-",
            "stage": latency.get("stage"),
            "timeout_seconds": latency.get("timeout_seconds"),
        })

    rows.sort(key=lambda row: str(row.get("timestamp") or ""))
    if limit > 0:
        rows = rows[-limit:]
    return rows


def _cohort_id_from_task_outcome(task_id: str, question: str) -> str | None:
    prefix = "injection-feedback:"
    if task_id.startswith(prefix):
        return task_id[len(prefix):] or None
    question_prefix = "injection cohort "
    normalized_question = question.strip()
    if normalized_question.startswith(question_prefix):
        return normalized_question[len(question_prefix):].strip() or None
    return None


def _iter_hook_latency(brain: Path) -> list[dict[str, Any]]:
    path = brain / "runtime" / "hook-latency.jsonl"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                rows.append(data)
    return rows


def _short_time(value: str) -> str:
    if "T" not in value:
        return value
    return value.replace("+00:00", "Z").replace("T", " ")


def _short(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


__all__ = ["hook_recent"]
