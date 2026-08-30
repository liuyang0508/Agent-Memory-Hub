from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.table import Table

from agent_brain.interfaces.cli._app import loop_app
from agent_brain.interfaces.cli._shared import _brain_dir, console
from agent_brain.memory.loops.loop_contract import contract_digest, parse_loop_contract
from agent_brain.memory.loops.loop_contract_validator import validate_loop_contract
from agent_brain.memory.loops.loop_feedback import format_feedback_view
from agent_brain.memory.loops.loop_orchestrator import LoopOrchestrator, LoopRunSummary
from agent_brain.memory.loops.loop_store import LoopStore
from agent_brain.memory.loops.loop_types import LoopNotFoundError, LoopRun, LoopTransitionError
from agent_brain.memory.loops.loop_verifier import LoopVerifier

loop_contract_app = typer.Typer(help="Loop Contract commands")
loop_app.add_typer(loop_contract_app, name="contract")
loop_gate_app = typer.Typer(help="Loop human gate commands")
loop_app.add_typer(loop_gate_app, name="gate")


def _store() -> LoopStore:
    return LoopStore(_brain_dir())


def _create_loop_from_contract(
    path: Path,
    *,
    adapter: str | None,
    session_id: str | None,
    start: bool,
    format: str,
) -> LoopRun:
    try:
        contract = parse_loop_contract(path)
    except Exception as exc:
        typer.echo(f"failed to parse contract: {exc}", err=True)
        raise typer.Exit(2)

    validation = validate_loop_contract(contract)
    if not validation.valid:
        payload = validation.to_dict()
        payload["digest"] = ""
        _print_contract_validation(payload, format="json" if format == "json" else "text")
        raise typer.Exit(2)

    digest = contract_digest(contract)
    return _store().create(
        goal=contract.goal.statement,
        project=contract.scope.project,
        adapter=adapter,
        session_id=session_id,
        cwd=contract.scope.repo,
        verification_plan=[verifier.command for verifier in contract.verifiers if verifier.required],
        budget=_contract_budget(contract),
        context={
            "contract_title": contract.title,
            "contract_state": contract.to_dict()["state"],
        },
        metadata={
            "contract_id": contract.id,
            "contract_schema_version": contract.schema_version,
            "contract_digest": digest,
            "contract_source_path": str(path),
            "contract_verifiers": [
                {"id": verifier.id, "command": verifier.command, "required": verifier.required}
                for verifier in contract.verifiers
            ],
            "contract_human_gates": [
                {"id": gate.id, "trigger": gate.trigger, "reason": gate.reason}
                for gate in contract.human_gates
            ],
        },
        start=start,
    )


def _contract_budget(contract: Any) -> dict[str, Any]:
    return {
        "max_iterations": contract.budget.max_iterations,
        "max_verifier_runs": contract.budget.max_verifier_runs,
        "timeout_per_action_seconds": contract.budget.timeout_per_action_seconds,
        "max_parallel_agents": contract.budget.max_parallel_agents,
        "token_budget_hint": contract.budget.token_budget_hint,
    }


@loop_contract_app.command("validate")
def loop_contract_validate(
    path: Path = typer.Argument(..., help="Loop Contract YAML or JSON path"),
    format: str = typer.Option("text", "--format", help="Output format: text or json"),
) -> None:
    """Validate a Loop Contract without creating or running a loop."""
    try:
        contract = parse_loop_contract(path)
    except Exception as exc:
        payload = {
            "valid": False,
            "contract_id": "",
            "schema_version": "",
            "digest": "",
            "errors": [{"path": str(path), "code": "parse_error", "message": str(exc)}],
            "warnings": [],
        }
        _print_contract_validation(payload, format=format)
        raise typer.Exit(2)

    result = validate_loop_contract(contract)
    payload = result.to_dict()
    payload["digest"] = contract_digest(contract)
    _print_contract_validation(payload, format=format)
    if not result.valid:
        raise typer.Exit(2)


@loop_app.command("create")
def loop_create(
    goal: str | None = typer.Option(None, "--goal", help="Loop goal"),
    contract: Path | None = typer.Option(None, "--contract", help="Loop Contract YAML or JSON path"),
    project: str | None = typer.Option(None, "--project", help="Project slug"),
    adapter: str | None = typer.Option(None, "--adapter", help="Adapter name"),
    session_id: str | None = typer.Option(None, "--session", help="Session id"),
    cwd: str | None = typer.Option(None, "--cwd", help="Working directory"),
    verifier: list[str] | None = typer.Option(
        None,
        "--verifier",
        help="Verification command or evidence plan",
    ),
    budget_iterations: int | None = typer.Option(
        None,
        "--budget-iterations",
        help="Iteration budget",
    ),
    start: bool = typer.Option(False, "--start", help="Start loop immediately"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
) -> None:
    """Create a LoopRun ledger entry."""
    if contract is not None:
        if any([goal, project, cwd, verifier, budget_iterations is not None]):
            typer.echo("cannot combine --contract with manual loop fields", err=True)
            raise typer.Exit(2)
        loop = _create_loop_from_contract(
            contract,
            adapter=adapter,
            session_id=session_id,
            start=start,
            format=format,
        )
        _print_loop(loop, format=format)
        return

    if not goal:
        typer.echo("--goal is required unless --contract is provided", err=True)
        raise typer.Exit(2)

    budget: dict[str, Any] = {}
    if budget_iterations is not None:
        budget["iterations"] = budget_iterations
    loop = _store().create(
        goal=goal,
        project=project,
        adapter=adapter,
        session_id=session_id,
        cwd=cwd,
        verification_plan=list(verifier or []),
        budget=budget,
        start=start,
    )
    _print_loop(loop, format=format)


@loop_app.command("run")
def loop_run(
    contract: Path = typer.Option(..., "--contract", help="Loop Contract YAML or JSON path"),
    timeout: int = typer.Option(60, "--timeout", help="Timeout per verifier command in seconds"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
) -> None:
    """Run the non-LLM Loop Contract orchestrator once."""
    try:
        summary = LoopOrchestrator(_brain_dir()).run_contract(contract, timeout=timeout)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2)
    except LoopTransitionError as exc:
        _exit_for_loop_error(exc)
    _print_run_summary(summary, format=format)


@loop_app.command("status")
def loop_status(
    loop_id: str = typer.Argument(..., help="Loop id"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
) -> None:
    """Show one LoopRun."""
    try:
        loop = _store().get(loop_id)
    except LoopNotFoundError as exc:
        _exit_for_loop_error(exc)
    _print_loop(loop, format=format)


@loop_app.command("list")
def loop_list(
    status: str | None = typer.Option(None, "--status", help="Filter by status"),
    project: str | None = typer.Option(None, "--project", help="Filter by project"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
) -> None:
    """List LoopRun records."""
    rows = _store().list(status=status, project=project)
    if format == "json":
        typer.echo(json.dumps([row.to_dict() for row in rows], ensure_ascii=False, indent=2))
        return
    if format != "table":
        typer.echo("format must be table or json", err=True)
        raise typer.Exit(2)
    table = Table(title=f"Loop runs ({len(rows)})")
    table.add_column("id")
    table.add_column("status")
    table.add_column("goal")
    table.add_column("updated")
    for row in rows:
        table.add_row(row.loop_id, row.status, row.goal, row.updated_at)
    console.print(table)


@loop_app.command("checkpoint")
def loop_checkpoint(
    loop_id: str = typer.Argument(..., help="Loop id"),
    note: str = typer.Option(..., "--note", help="Checkpoint note"),
    artifact: str | None = typer.Option(
        None,
        "--artifact",
        help="Artifact path, URL, commit, or summary",
    ),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
) -> None:
    """Append a checkpoint to a LoopRun."""
    try:
        loop = _store().checkpoint(loop_id, note=note, artifact=artifact)
    except (LoopNotFoundError, LoopTransitionError) as exc:
        _exit_for_loop_error(exc)
    _print_loop(loop, format=format)


@loop_app.command("complete")
def loop_complete(
    loop_id: str = typer.Argument(..., help="Loop id"),
    evidence: str | None = typer.Option(None, "--evidence", help="Verification evidence summary"),
    artifact: str | None = typer.Option(
        None,
        "--artifact",
        help="Artifact path, URL, commit, or summary",
    ),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
) -> None:
    """Mark a LoopRun completed. Requires verification evidence."""
    try:
        loop = _store().complete(loop_id, evidence=evidence, artifact=artifact)
    except (LoopNotFoundError, LoopTransitionError) as exc:
        _exit_for_loop_error(exc)
    _print_loop(loop, format=format)


@loop_app.command("fail")
def loop_fail(
    loop_id: str = typer.Argument(..., help="Loop id"),
    reason: str = typer.Option(..., "--reason", help="Failure reason"),
    evidence: str | None = typer.Option(None, "--evidence", help="Verification evidence summary"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
) -> None:
    """Mark a LoopRun failed."""
    try:
        loop = _store().fail(loop_id, reason=reason, evidence=evidence)
    except (LoopNotFoundError, LoopTransitionError) as exc:
        _exit_for_loop_error(exc)
    _print_loop(loop, format=format)


@loop_app.command("verify")
def loop_verify(
    loop_id: str = typer.Argument(..., help="Loop id"),
    limit: int | None = typer.Option(None, "--limit", help="Maximum verification commands to run"),
    timeout: int = typer.Option(60, "--timeout", help="Timeout per command in seconds"),
    cwd: str | None = typer.Option(None, "--cwd", help="Override loop cwd for this verification run"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
) -> None:
    """Run allowlisted verification commands and append structured feedback."""
    try:
        summary = LoopVerifier(_brain_dir()).verify(loop_id, limit=limit, timeout=timeout, cwd=cwd)
    except LoopNotFoundError as exc:
        _exit_for_loop_error(exc)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2)
    if format == "json":
        typer.echo(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
        return
    if format != "table":
        typer.echo("format must be table or json", err=True)
        raise typer.Exit(2)
    table = Table(title="Loop verification")
    table.add_column("command")
    table.add_column("status")
    table.add_column("category")
    table.add_column("exit")
    table.add_column("duration_ms")
    for item in summary.feedback:
        table.add_row(
            item.command,
            item.status,
            item.category,
            "" if item.exit_code is None else str(item.exit_code),
            str(item.duration_ms),
        )
    console.print(table)


@loop_app.command("feedback")
def loop_feedback(
    loop_id: str = typer.Argument(..., help="Loop id"),
    limit: int = typer.Option(10, "--limit", help="Maximum feedback rows to show"),
    format: str = typer.Option("text", "--format", help="Output format: text or json"),
) -> None:
    """Show recent structured loop feedback for the next agent iteration."""
    try:
        loop = _store().get(loop_id)
    except LoopNotFoundError as exc:
        _exit_for_loop_error(exc)
    if format == "json":
        rows = [row for row in loop.verification_results if row.get("feedback_id")][-limit:]
        typer.echo(json.dumps({"loop_id": loop_id, "feedback": rows}, ensure_ascii=False, indent=2))
        return
    if format != "text":
        typer.echo("format must be text or json", err=True)
        raise typer.Exit(2)
    typer.echo(format_feedback_view(loop_id, loop.verification_results, limit=limit, metadata=loop.metadata))


@loop_gate_app.command("list")
def loop_gate_list(
    loop_id: str = typer.Argument(..., help="Loop id"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
) -> None:
    """List open and resolved human gates for a LoopRun."""
    try:
        loop = _store().get(loop_id)
    except LoopNotFoundError as exc:
        _exit_for_loop_error(exc)
    payload = _human_gate_payload(loop)
    if format == "json":
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if format != "table":
        typer.echo("format must be table or json", err=True)
        raise typer.Exit(2)
    table = Table(title="Loop human gates")
    table.add_column("state")
    table.add_column("id")
    table.add_column("reason/note")
    for row in payload["open"]:
        table.add_row("open", str(row.get("id") or ""), str(row.get("reason") or ""))
    for row in payload["resolved"]:
        table.add_row(str(row.get("decision") or "resolved"), str(row.get("id") or ""), str(row.get("note") or ""))
    console.print(table)


@loop_gate_app.command("open")
def loop_gate_open(
    loop_id: str = typer.Argument(..., help="Loop id"),
    gate_id: str = typer.Option(..., "--gate", help="Human gate id"),
    reason: str = typer.Option(..., "--reason", help="Reason the gate is open"),
    trigger: str | None = typer.Option(None, "--trigger", help="Optional trigger name"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
) -> None:
    """Open a human gate on a LoopRun."""
    try:
        loop = _store().open_human_gate(
            loop_id,
            gate_id=gate_id,
            reason=reason,
            trigger=trigger,
        )
    except (LoopNotFoundError, LoopTransitionError) as exc:
        _exit_for_loop_error(exc)
    _print_loop(loop, format=format)


@loop_gate_app.command("approve")
def loop_gate_approve(
    loop_id: str = typer.Argument(..., help="Loop id"),
    gate_id: str = typer.Option(..., "--gate", help="Human gate id"),
    note: str = typer.Option(..., "--note", help="Approval note"),
    evidence: str | None = typer.Option(None, "--evidence", help="Optional evidence path or summary"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
) -> None:
    """Approve and close an open human gate."""
    try:
        loop = _store().approve_human_gate(
            loop_id,
            gate_id=gate_id,
            note=note,
            evidence=evidence,
        )
    except (LoopNotFoundError, LoopTransitionError) as exc:
        _exit_for_loop_error(exc)
    _print_loop(loop, format=format)


@loop_gate_app.command("reject")
def loop_gate_reject(
    loop_id: str = typer.Argument(..., help="Loop id"),
    gate_id: str = typer.Option(..., "--gate", help="Human gate id"),
    reason: str = typer.Option(..., "--reason", help="Rejection reason"),
    evidence: str | None = typer.Option(None, "--evidence", help="Optional evidence path or summary"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
) -> None:
    """Reject and close an open human gate."""
    try:
        loop = _store().reject_human_gate(
            loop_id,
            gate_id=gate_id,
            reason=reason,
            evidence=evidence,
        )
    except (LoopNotFoundError, LoopTransitionError) as exc:
        _exit_for_loop_error(exc)
    _print_loop(loop, format=format)


def _human_gate_payload(loop: LoopRun) -> dict[str, Any]:
    open_rows = loop.metadata.get("open_human_gates")
    resolved_rows = loop.metadata.get("resolved_human_gates")
    return {
        "loop_id": loop.loop_id,
        "open": [dict(row) for row in open_rows if isinstance(row, dict)]
        if isinstance(open_rows, list)
        else [],
        "resolved": [dict(row) for row in resolved_rows if isinstance(row, dict)]
        if isinstance(resolved_rows, list)
        else [],
    }


def _print_loop(loop: LoopRun, *, format: str) -> None:
    if format == "json":
        typer.echo(json.dumps(loop.to_dict(), ensure_ascii=False, indent=2))
        return
    if format != "table":
        typer.echo("format must be table or json", err=True)
        raise typer.Exit(2)
    table = Table(title="Loop run")
    table.add_column("field")
    table.add_column("value")
    table.add_row("id", loop.loop_id)
    table.add_row("status", loop.status)
    table.add_row("goal", loop.goal)
    table.add_row("updated", loop.updated_at)
    table.add_row("verifications", str(len(loop.verification_results)))
    console.print(table)


def _print_run_summary(summary: LoopRunSummary, *, format: str) -> None:
    if format == "json":
        typer.echo(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
        return
    if format != "table":
        typer.echo("format must be table or json", err=True)
        raise typer.Exit(2)
    table = Table(title="Loop run summary")
    table.add_column("field")
    table.add_column("value")
    table.add_row("loop_id", summary.loop_id)
    table.add_row("contract_id", summary.contract_id)
    table.add_row("decision", summary.decision)
    table.add_row("readiness", summary.completion_readiness)
    table.add_row("status", summary.status)
    table.add_row("open_human_gates", ", ".join(summary.open_human_gates) or "-")
    table.add_row("verification", json.dumps(summary.verification, ensure_ascii=False))
    console.print(table)


def _print_contract_validation(payload: dict[str, Any], *, format: str) -> None:
    if format == "json":
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if format != "text":
        typer.echo("format must be text or json", err=True)
        raise typer.Exit(2)
    status = "valid" if payload["valid"] else "invalid"
    typer.echo(f"contract: {payload['contract_id'] or 'unknown'}")
    typer.echo(f"status: {status}")
    if payload.get("digest"):
        typer.echo(f"digest: {payload['digest']}")
    for error in payload.get("errors", []):
        typer.echo(f"error: {error['path']} {error['code']} - {error['message']}")
    for warning in payload.get("warnings", []):
        typer.echo(f"warning: {warning['path']} {warning['code']} - {warning['message']}")


def _exit_for_loop_error(exc: Exception) -> None:
    typer.echo(str(exc), err=True)
    if isinstance(exc, LoopNotFoundError):
        raise typer.Exit(1)
    raise typer.Exit(2)
