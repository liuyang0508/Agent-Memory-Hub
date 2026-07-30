"""CLI review queue commands for unverified memory candidates."""

from __future__ import annotations

import json
from datetime import datetime, timezone
import re
from urllib.parse import urlsplit

from agent_brain.contracts.memory_item import is_valid_memory_item_id
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
    queue = PendingQueue(brain=_brain_dir())
    pending_depth = queue.depth()
    pending_preview = queue.preview(limit=max(pending_depth, 1))
    pending_ages = [
        record.age_seconds
        for record in pending_preview.records
        if record.age_seconds is not None
    ]
    pending_oldest_age_seconds = max(pending_ages, default=None)
    review_oldest_age_seconds = _review_oldest_age_seconds(review)
    pending_dead_dir = _brain_dir() / "pending" / "dead"
    pending_dead = len(list(pending_dead_dir.glob("*.jsonl"))) if pending_dead_dir.exists() else 0
    alerts = _review_alerts(
        review_oldest_age_seconds=review_oldest_age_seconds,
        pending_oldest_age_seconds=pending_oldest_age_seconds,
        pending_dead=pending_dead,
    )
    recommended_next = (
        "review list --format json"
        if review.total
        else (
            "memory sync-pending --format json"
            if pending_depth or pending_dead
            else "none"
        )
    )
    data = {
        "status": "warn" if alerts else "ok",
        "review_total": review.total,
        "review_oldest_age_seconds": review_oldest_age_seconds,
        "pending_depth": pending_depth,
        "pending_oldest_age_seconds": pending_oldest_age_seconds,
        "pending_dead": pending_dead,
        "alerts": alerts,
        "recommended_next": recommended_next,
    }
    if output_format == "json":
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
        return

    table = Table(title="Memory Review Status")
    table.add_column("metric")
    table.add_column("value", justify="right")
    table.add_row("review_total", str(data["review_total"]))
    table.add_row("review_oldest_age", _format_age(review_oldest_age_seconds))
    table.add_row("pending_depth", str(data["pending_depth"]))
    table.add_row("pending_oldest_age", _format_age(pending_oldest_age_seconds))
    table.add_row("pending_dead", str(data["pending_dead"]))
    table.add_row("status", str(data["status"]))
    table.add_row("recommended_next", str(data["recommended_next"]))
    console.print(table)
    for alert in alerts:
        typer.echo(f"alert: {alert}")


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
    table.add_column("reason")
    table.add_column("next")
    table.add_column("title")
    for candidate in report.candidates:
        table.add_row(
            candidate.id,
            f"{candidate.confidence:.2f}",
            ",".join(candidate.tags),
            candidate.review_reason,
            candidate.recommended_action,
            candidate.title,
        )
    console.print(table)


@review_app.command(name="attach-source")
def review_attach_source(
    item_id: str = typer.Argument(..., help="Memory item ID or prefix"),
    source_files: list[str] = typer.Option([], "--file", help="Source file; repeatable"),
    source_urls: list[str] = typer.Option([], "--url", help="HTTPS source URL; repeatable"),
    source_commits: list[str] = typer.Option(
        [],
        "--commit",
        help="Git commit SHA; repeatable",
    ),
    source_mems: list[str] = typer.Option(
        [],
        "--memory",
        help="Source memory ID; repeatable",
    ),
) -> None:
    """Attach explicit source references without changing review state."""
    from agent_brain.memory.governance.review_queue import is_active_review_candidate

    store = _store_only()
    item_id = _resolve_id(store, item_id)
    item, _body = store.get(item_id)
    if not is_active_review_candidate(item):
        typer.echo(f"not an active review candidate: {item_id}", err=True)
        raise typer.Exit(2)
    if not any((source_files, source_urls, source_commits, source_mems)):
        typer.echo("attach-source requires at least one source", err=True)
        raise typer.Exit(2)
    if any(not _bounded_source(value) for value in source_files):
        typer.echo("--file contains an invalid source", err=True)
        raise typer.Exit(2)
    if any(not _valid_source_url(value) for value in source_urls):
        typer.echo("--url requires a bounded https URL", err=True)
        raise typer.Exit(2)
    if any(re.fullmatch(r"[0-9a-fA-F]{7,64}", value) is None for value in source_commits):
        typer.echo("--commit requires a 7-64 character hexadecimal SHA", err=True)
        raise typer.Exit(2)
    if any(not is_valid_memory_item_id(value) for value in source_mems):
        typer.echo("--memory requires a canonical memory ID", err=True)
        raise typer.Exit(2)
    refs = item.refs.model_copy(
        update={
            "files": _merge_sources(item.refs.files, source_files),
            "urls": _merge_sources(item.refs.urls, source_urls),
            "commits": _merge_sources(item.refs.commits, source_commits),
            "mems": _merge_sources(item.refs.mems, source_mems),
        }
    )
    store.update_frontmatter(item_id, refs=refs)
    typer.echo(
        f"attached sources: {item_id} "
        f"(explicit={len(refs.files) + len(refs.urls) + len(refs.commits) + len(refs.mems)})"
    )


@review_app.command(name="resolve")
def review_resolve(
    item_id: str = typer.Argument(..., help="Memory item ID or prefix"),
    action: str = typer.Option(..., "--action", help="approve or reject"),
    confidence: float | None = typer.Option(None, "--confidence"),
    apply: bool = typer.Option(False, "--apply", help="Apply exact preview digest"),
    expected_sha256: str | None = typer.Option(
        None,
        "--expected-sha256",
        help="Exact digest emitted by preview; required with --apply",
    ),
) -> None:
    """Preview or apply one digest-bound, receipted review resolution."""
    from agent_brain.memory.governance.review_transactions import (
        ReviewResolutionAction,
        resolve_review_candidate,
    )

    if action not in {"approve", "reject"}:
        typer.echo("--action must be approve or reject", err=True)
        raise typer.Exit(2)
    if apply and (
        expected_sha256 is None
        or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
    ):
        typer.echo("--apply requires --expected-sha256 from preview", err=True)
        raise typer.Exit(2)
    typed_action: ReviewResolutionAction = (
        "approve" if action == "approve" else "reject"
    )
    store = _store_only()
    item_id = _resolve_id(store, item_id)
    target_confidence = confidence
    if target_confidence is None:
        target_confidence = 0.7 if action == "approve" else 0.1
    result = resolve_review_candidate(
        brain_dir=_brain_dir(),
        store=store,
        item_id=item_id,
        action=typed_action,
        confidence=target_confidence,
        apply=apply,
        expected_sha256=expected_sha256,
    )
    typer.echo(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    if result.status in {"blocked", "failed"}:
        raise typer.Exit(1)


@review_app.command(name="cases")
def review_cases(
    output_format: str = typer.Option(
        "table",
        "--format",
        help="Output format: table or json",
    ),
    include_resolved: bool = typer.Option(
        False,
        "--include-resolved",
        help="Include digest-matched resolved and deferred cases",
    ),
) -> None:
    """List current contradiction cases and their adjudication state."""
    from agent_brain.memory.governance.contradiction_resolution import (
        build_contradiction_case_inventory,
    )

    inventory = build_contradiction_case_inventory(
        brain_dir=_brain_dir(),
        store=_store_only(),
    )
    visible_cases = [
        case for case in inventory.cases
        if include_resolved or case.status == "open"
    ]
    data = inventory.to_dict()
    data["cases"] = [case.to_dict() for case in visible_cases]
    if output_format == "json":
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
        return
    table = Table(title="Contradiction Cases")
    table.add_column("case")
    table.add_column("status")
    table.add_column("items", justify="right")
    table.add_column("pairs", justify="right")
    table.add_column("confidence", justify="right")
    table.add_column("resolution")
    for case in visible_cases:
        table.add_row(
            case.case_id,
            case.status,
            str(len(case.item_ids)),
            str(case.pair_count),
            f"{case.confidence:.2f}",
            case.resolution_action or "-",
        )
    console.print(table)


@review_app.command(name="resolve-signal")
def review_resolve_signal(
    item_id: str = typer.Argument(..., help="Signal memory ID or prefix"),
    action: str = typer.Option(
        ...,
        "--action",
        help="resolve, obsolete, defer, or reopen",
    ),
    resolution_item_id: str | None = typer.Option(
        None,
        "--resolution-item-id",
        help="Decision/artifact/fact/episode memory proving resolution",
    ),
    defer_days: int | None = typer.Option(None, "--defer-days"),
    reason: str | None = typer.Option(None, "--reason"),
    apply: bool = typer.Option(False, "--apply", help="Apply exact preview intent"),
    expected_intent_sha256: str | None = typer.Option(
        None,
        "--expected-intent-sha256",
        help="Exact intent digest emitted by preview; required with --apply",
    ),
) -> None:
    """Preview or apply one recoverable Signal lifecycle transition."""
    from agent_brain.memory.governance.signal_resolution import (
        SignalTransitionAction,
        transition_signal_state,
    )

    if action not in {"resolve", "obsolete", "defer", "reopen"}:
        typer.echo(
            "--action must be resolve, obsolete, defer, or reopen",
            err=True,
        )
        raise typer.Exit(2)
    if apply and (
        expected_intent_sha256 is None
        or re.fullmatch(r"[0-9a-f]{64}", expected_intent_sha256) is None
    ):
        typer.echo(
            "--apply requires --expected-intent-sha256 from preview",
            err=True,
        )
        raise typer.Exit(2)
    store = _store_only()
    item_id = _resolve_id(store, item_id)
    if resolution_item_id is not None:
        resolution_item_id = _resolve_id(store, resolution_item_id)
    typed_action: SignalTransitionAction
    if action == "resolve":
        typed_action = "resolve"
    elif action == "obsolete":
        typed_action = "obsolete"
    elif action == "defer":
        typed_action = "defer"
    else:
        typed_action = "reopen"
    index = HubIndex(db_path=_brain_dir() / "index.db") if apply else None
    try:
        result = transition_signal_state(
            brain_dir=_brain_dir(),
            store=store,
            item_id=item_id,
            action=typed_action,
            apply=apply,
            expected_intent_sha256=expected_intent_sha256,
            resolution_item_id=resolution_item_id,
            defer_days=defer_days,
            reason=reason,
            index=index,
        )
    finally:
        if index is not None:
            index.close()
    typer.echo(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    if result.status in {"blocked", "failed"}:
        raise typer.Exit(1)


@review_app.command(name="resolve-case")
def review_resolve_case(
    case_id: str = typer.Argument(..., help="Stable contradiction case ID"),
    action: str = typer.Option(
        ...,
        "--action",
        help="select-authority, merge, coexist, dismiss/not-conflict, or defer",
    ),
    target_item_id: str | None = typer.Option(
        None,
        "--target-item-id",
        help="Authority item or approved merged item",
    ),
    defer_days: int | None = typer.Option(None, "--defer-days"),
    apply: bool = typer.Option(False, "--apply", help="Apply exact preview intent"),
    expected_intent_sha256: str | None = typer.Option(
        None,
        "--expected-intent-sha256",
        help="Exact intent digest emitted by preview; required with --apply",
    ),
) -> None:
    """Preview or apply one recoverable contradiction-case resolution."""
    from agent_brain.memory.governance.contradiction_resolution import (
        ContradictionResolutionAction,
        resolve_contradiction_case,
    )

    action_aliases = {
        "select-authority": "select_authority",
        "select_authority": "select_authority",
        "merge": "merge",
        "coexist": "coexist",
        "dismiss": "dismiss",
        "not-conflict": "dismiss",
        "not_conflict": "dismiss",
        "defer": "defer",
    }
    normalized_action = action_aliases.get(action)
    if normalized_action is None:
        typer.echo(
            "--action must be select-authority, merge, coexist, "
            "dismiss/not-conflict, or defer",
            err=True,
        )
        raise typer.Exit(2)
    if apply and (
        expected_intent_sha256 is None
        or re.fullmatch(r"[0-9a-f]{64}", expected_intent_sha256) is None
    ):
        typer.echo(
            "--apply requires --expected-intent-sha256 from preview",
            err=True,
        )
        raise typer.Exit(2)
    typed_action: ContradictionResolutionAction
    if normalized_action == "select_authority":
        typed_action = "select_authority"
    elif normalized_action == "merge":
        typed_action = "merge"
    elif normalized_action == "coexist":
        typed_action = "coexist"
    elif normalized_action == "dismiss":
        typed_action = "dismiss"
    else:
        typed_action = "defer"
    index = HubIndex(db_path=_brain_dir() / "index.db") if apply else None
    try:
        result = resolve_contradiction_case(
            brain_dir=_brain_dir(),
            store=_store_only(),
            case_id=case_id,
            action=typed_action,
            target_item_id=target_item_id,
            defer_days=defer_days,
            apply=apply,
            expected_intent_sha256=expected_intent_sha256,
            index=index,
        )
    finally:
        if index is not None:
            index.close()
    typer.echo(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    if result.status in {"blocked", "failed"}:
        raise typer.Exit(1)


@review_app.command(name="recover-case")
def review_recover_case(
    transaction_id: str = typer.Argument(..., help="Incomplete transaction ID"),
    apply: bool = typer.Option(False, "--apply"),
) -> None:
    """Preview or restore one incomplete contradiction-case transaction."""
    from agent_brain.memory.governance.contradiction_resolution import (
        recover_contradiction_case_transaction,
    )

    if re.fullmatch(r"[0-9a-f]{32}", transaction_id) is None:
        typer.echo("transaction_id must be 32 lowercase hexadecimal characters", err=True)
        raise typer.Exit(2)
    result = recover_contradiction_case_transaction(
        brain_dir=_brain_dir(),
        store=_store_only(),
        transaction_id=transaction_id,
        apply=apply,
    )
    typer.echo(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    if result.status == "blocked":
        raise typer.Exit(1)


@review_app.command(name="recover-containment")
def review_recover_containment(
    transaction_id: str = typer.Argument(..., help="Incomplete containment transaction ID"),
    apply: bool = typer.Option(False, "--apply"),
) -> None:
    """Preview or restore one incomplete contradiction containment."""
    from agent_brain.memory.governance.contradiction_containment import (
        recover_containment_transaction,
    )

    if re.fullmatch(r"[0-9a-f]{32}", transaction_id) is None:
        typer.echo(
            "transaction_id must be 32 lowercase hexadecimal characters",
            err=True,
        )
        raise typer.Exit(2)
    result = recover_containment_transaction(
        brain_dir=_brain_dir(),
        store=_store_only(),
        transaction_id=transaction_id,
        apply=apply,
    )
    typer.echo(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    if result.status == "blocked":
        raise typer.Exit(1)


@review_app.command(name="recover-signal")
def review_recover_signal(
    transaction_id: str = typer.Argument(
        ..., help="Incomplete Signal transition transaction ID"
    ),
    apply: bool = typer.Option(False, "--apply"),
) -> None:
    """Preview or restore one incomplete Signal transition."""
    from agent_brain.memory.governance.signal_resolution import (
        recover_signal_transaction,
    )

    if re.fullmatch(r"[0-9a-f]{32}", transaction_id) is None:
        typer.echo(
            "transaction_id must be 32 lowercase hexadecimal characters",
            err=True,
        )
        raise typer.Exit(2)
    result = recover_signal_transaction(
        brain_dir=_brain_dir(),
        store=_store_only(),
        transaction_id=transaction_id,
        apply=apply,
    )
    typer.echo(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    if result.status == "blocked":
        raise typer.Exit(1)


@review_app.command(name="approve")
def review_approve(
    item_id: str = typer.Argument(..., help="Memory item ID or prefix"),
    confidence: float = typer.Option(0.7, "--confidence", help="Confidence after approval"),
) -> None:
    """Approve a needs-review candidate so it can participate in normal recall."""
    from agent_brain.memory.governance.review_queue import (
        approve_review_candidate,
        is_active_review_candidate,
    )

    store = _store_only()
    item_id = _resolve_id(store, item_id)
    item, _body = store.get(item_id)
    if not is_active_review_candidate(item):
        typer.echo(f"not an active review candidate: {item_id}", err=True)
        raise typer.Exit(2)
    updated = approve_review_candidate(store, item_id, confidence=confidence)
    _update_index_confidence(item_id, updated.confidence)
    typer.echo(f"approved: {item_id} confidence={updated.confidence:.2f}")


@review_app.command(name="reject")
def review_reject(
    item_id: str = typer.Argument(..., help="Memory item ID or prefix"),
    confidence: float = typer.Option(0.1, "--confidence", help="Confidence after rejection"),
) -> None:
    """Reject a needs-review candidate and keep it quarantined from injection."""
    from agent_brain.memory.governance.review_queue import (
        is_active_review_candidate,
        reject_review_candidate,
    )

    store = _store_only()
    item_id = _resolve_id(store, item_id)
    item, _body = store.get(item_id)
    if not is_active_review_candidate(item):
        typer.echo(f"not an active review candidate: {item_id}", err=True)
        raise typer.Exit(2)
    updated = reject_review_candidate(store, item_id, confidence=confidence)
    _update_index_confidence(item_id, updated.confidence)
    typer.echo(f"rejected: {item_id} confidence={updated.confidence:.2f}")


@review_app.command(name="approve-many")
def review_approve_many(
    item_ids: list[str] = typer.Argument(..., help="Memory item IDs or prefixes"),
    confidence: float = typer.Option(0.7, "--confidence", help="Confidence after approval"),
) -> None:
    """Approve an explicit batch after resolving every ID before mutation."""

    _review_many(item_ids, action="approve", confidence=confidence)


@review_app.command(name="reject-many")
def review_reject_many(
    item_ids: list[str] = typer.Argument(..., help="Memory item IDs or prefixes"),
    confidence: float = typer.Option(0.1, "--confidence", help="Confidence after rejection"),
) -> None:
    """Reject an explicit batch after resolving every ID before mutation."""

    _review_many(item_ids, action="reject", confidence=confidence)


def _review_many(item_ids: list[str], *, action: str, confidence: float) -> None:
    from agent_brain.memory.governance.review_queue import (
        approve_review_candidate,
        is_active_review_candidate,
        reject_review_candidate,
    )

    store = _store_only()
    resolved = list(dict.fromkeys(_resolve_id(store, item_id) for item_id in item_ids))
    mutate = approve_review_candidate if action == "approve" else reject_review_candidate
    for item_id in resolved:
        item, _body = store.get(item_id)
        if not is_active_review_candidate(item):
            typer.echo(f"not an active review candidate: {item_id}", err=True)
            raise typer.Exit(2)
    for item_id in resolved:
        updated = mutate(store, item_id, confidence=confidence)
        _update_index_confidence(item_id, updated.confidence)
    typer.echo(f"{action}d={len(resolved)}")
    for item_id in resolved:
        typer.echo(item_id)


def _review_oldest_age_seconds(review: object) -> int | None:
    candidates = getattr(review, "candidates", ())
    now = datetime.now(timezone.utc)
    ages: list[int] = []
    for candidate in candidates:
        try:
            created = datetime.fromisoformat(candidate.created_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        ages.append(max(0, int((now - created.astimezone(timezone.utc)).total_seconds())))
    return max(ages, default=None)


def _review_alerts(
    *,
    review_oldest_age_seconds: int | None,
    pending_oldest_age_seconds: int | None,
    pending_dead: int,
) -> list[str]:
    alerts: list[str] = []
    if review_oldest_age_seconds is not None and review_oldest_age_seconds >= 7 * 86400:
        alerts.append("review queue oldest candidate exceeds 7d SLA")
    if pending_oldest_age_seconds is not None and pending_oldest_age_seconds >= 24 * 3600:
        alerts.append("pending queue oldest record exceeds 24h SLA")
    if pending_dead:
        alerts.append(f"pending dead-letter queue contains {pending_dead} record(s)")
    return alerts


def _format_age(age_seconds: int | None) -> str:
    if age_seconds is None:
        return "-"
    if age_seconds >= 86400:
        return f"{age_seconds / 86400:.1f}d"
    if age_seconds >= 3600:
        return f"{age_seconds / 3600:.1f}h"
    return f"{age_seconds}s"


def _bounded_source(value: str) -> bool:
    return (
        bool(value.strip())
        and len(value.encode("utf-8")) <= 2048
        and not any(ord(character) < 32 for character in value)
    )


def _valid_source_url(value: str) -> bool:
    if not _bounded_source(value):
        return False
    parsed = urlsplit(value)
    return parsed.scheme == "https" and bool(parsed.netloc) and not parsed.username


def _merge_sources(existing: list[str], added: list[str]) -> list[str]:
    return list(dict.fromkeys([*existing, *(value.strip() for value in added)]))


def _update_index_confidence(item_id: str, confidence: float) -> None:
    try:
        idx = HubIndex(db_path=_brain_dir() / "index.db")
        try:
            idx.update_confidence(item_id, confidence)
        finally:
            idx.close()
    except Exception:
        pass


__all__ = [
    "review_approve",
    "review_approve_many",
    "review_attach_source",
    "review_cases",
    "review_list",
    "review_recover_case",
    "review_recover_containment",
    "review_recover_signal",
    "review_reject",
    "review_reject_many",
    "review_resolve",
    "review_resolve_case",
    "review_resolve_signal",
    "review_status",
]


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
