"""CLI adapter management commands."""

from __future__ import annotations

from agent_brain.interfaces.cli._app import adapter_app
from agent_brain.interfaces.cli._shared import Table, _brain_dir, console, typer


@adapter_app.command("list")
def adapter_list(
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
) -> None:
    """List all available agent adapters (auto-discovered) and their status."""
    import json

    from agent_brain.agent_integrations.capabilities import capabilities_for_all

    rows = [cap.to_dict() for cap in capabilities_for_all(_brain_dir())]

    if format == "json":
        typer.echo(json.dumps(rows, indent=2, ensure_ascii=False))
        return

    if not rows:
        typer.echo("No adapters discovered.")
        return

    table = Table(title=f"Agent Adapters ({len(rows)} discovered)")
    table.add_column("name")
    table.add_column("support")
    table.add_column("status")
    table.add_column("modes")
    table.add_column("inject")
    table.add_column("evidence")
    table.add_column("limits")
    for r in rows:
        support = str(r["support_level"])
        color = "green" if support == "verified" else "cyan" if support == "install-ready" else "yellow"
        evidence_level = r.get("evidence_level")
        evidence_paths = r.get("evidence_paths") or []
        evidence = "none"
        if evidence_level:
            count = len(evidence_paths) if isinstance(evidence_paths, list) else 0
            noun = "path" if count == 1 else "paths"
            evidence = f"{evidence_level}: {count} {noun}"
        aliases = r.get("aliases", [])
        name = str(r["name"])
        if isinstance(aliases, list) and aliases:
            name = f"{name} ({', '.join(str(alias) for alias in aliases)})"
        table.add_row(
            name,
            f"[{color}]{support}[/{color}]",
            str(r["status"]),
            ", ".join(r["integration_modes"]),
            str(r["inject_method"]),
            evidence,
            "; ".join(r["limitations"]),
        )
    console.print(table)


@adapter_app.command("install")
def adapter_install(
    name: str = typer.Argument(..., help="Adapter name (see 'memory adapter list')"),
    format: str = typer.Option("text", "--format", help="Output format: text or json"),
) -> None:
    """Install an agent adapter — wires the brain pool into that agent's config."""
    import json

    from agent_brain.agent_integrations import discover_adapters
    from agent_brain.agent_integrations.registry import get_adapter, resolve_adapter_name

    if format not in {"text", "json"}:
        typer.echo("format must be text or json", err=True)
        raise typer.Exit(2)

    discover_adapters()
    try:
        canonical_name, alias_used = resolve_adapter_name(name)
        adapter = get_adapter(canonical_name, _brain_dir())
    except ValueError as e:
        if format == "json":
            typer.echo(json.dumps(
                _adapter_install_payload(
                    adapter=name,
                    requested_adapter=name,
                    status="unknown_adapter",
                    message=str(e),
                ),
                indent=2,
                ensure_ascii=False,
            ))
            raise typer.Exit(1)
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    try:
        msg = adapter.install()
    except NotImplementedError as e:
        payload = _adapter_install_payload(
            adapter=canonical_name,
            requested_adapter=name,
            alias=alias_used,
            status="adapter_wip",
            message=str(e),
        )
        if format == "json":
            typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            typer.echo(_format_adapter_install_failure(payload), err=True)
        raise typer.Exit(1)
    except (FileNotFoundError, RuntimeError) as e:
        payload = _adapter_install_payload(
            adapter=canonical_name,
            requested_adapter=name,
            alias=alias_used,
            status=_adapter_install_error_status(e),
            message=str(e),
        )
        if format == "json":
            typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            typer.echo(_format_adapter_install_failure(payload), err=True)
        raise typer.Exit(1)
    payload = _adapter_install_payload(
        adapter=canonical_name,
        requested_adapter=name,
        alias=alias_used,
        status="configured",
        message=msg,
    )
    if format == "json":
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        typer.echo(msg)


def _adapter_install_error_status(exc: Exception) -> str:
    message = str(exc).lower()
    if "malformed" in message:
        return "malformed_config"
    if (
        isinstance(exc, FileNotFoundError)
        or "not found" in message
        or "config directory not found" in message
        or "cli not found" in message
        or "no such file" in message
        or "not on path" in message
    ):
        return "needs_client"
    return "failed"


def _adapter_install_payload(
    *,
    adapter: str,
    requested_adapter: str,
    status: str,
    message: str,
    alias: str | None = None,
) -> dict[str, object]:
    from agent_brain.platform.install_repair import CORE_HOOK_ADAPTERS

    optional = adapter not in CORE_HOOK_ADAPTERS
    core_impact = "none" if optional or status == "configured" else "core_adapter_degraded"
    repair_command = (
        "memory doctor --fix"
        if core_impact != "none"
        else f"memory adapter install {adapter}"
    )
    payload: dict[str, object] = {
        "adapter": adapter,
        "requested_adapter": requested_adapter,
        "status": status,
        "optional": optional,
        "core_impact": core_impact,
        "message": message,
        "repair_command": repair_command,
        "next_step": _adapter_install_next_step(status, repair_command),
    }
    if alias:
        payload["alias"] = alias
    return payload


def _adapter_install_next_step(status: str, repair_command: str) -> str:
    if status == "configured":
        return "run adapter doctor or start the target agent to observe runtime evidence"
    if status == "needs_client":
        return f"install the target client or CLI, then run: {repair_command}"
    if status == "malformed_config":
        return f"repair the malformed client config by hand, then run: {repair_command}"
    if status == "adapter_wip":
        return "this adapter is registered but its install path is not implemented yet"
    if status == "unknown_adapter":
        return "run: memory adapter list"
    return f"inspect the error, then retry or run: {repair_command}"


def _format_adapter_install_failure(payload: dict[str, object]) -> str:
    adapter = payload.get("requested_adapter") or payload.get("adapter")
    status = payload.get("status")
    message = payload.get("message")
    next_step = payload.get("next_step")
    if status in {"needs_client", "malformed_config", "adapter_wip"}:
        headline = f"{adapter}: not configured — {message}"
    else:
        headline = f"{adapter}: install failed — {message}"
    return f"{headline}\nnext: {next_step}"


@adapter_app.command("uninstall")
def adapter_uninstall(
    name: str = typer.Argument(..., help="Adapter name to uninstall"),
) -> None:
    """Uninstall an agent adapter — removes only hub-owned config entries."""
    from agent_brain.agent_integrations import discover_adapters
    from agent_brain.agent_integrations.registry import get_adapter, resolve_adapter_name

    discover_adapters()
    try:
        canonical_name, _alias_used = resolve_adapter_name(name)
        adapter = get_adapter(canonical_name, _brain_dir())
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    uninstall = getattr(adapter, "uninstall", None)
    if not callable(uninstall):
        typer.echo(
            f"{name}: adapter has no uninstall path "
            f"(WIP stub — nothing was installed)",
            err=True,
        )
        raise typer.Exit(1)
    try:
        typer.echo(uninstall())
    except (FileNotFoundError, RuntimeError) as e:
        typer.echo(f"{name}: uninstall failed — {e}", err=True)
        raise typer.Exit(1)


@adapter_app.command("doctor")
def adapter_doctor(
    name: str = typer.Argument(..., help="Adapter name to diagnose"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
) -> None:
    """Diagnose an installed agent adapter without modifying user config."""
    import json

    from agent_brain.agent_integrations import discover_adapters
    from agent_brain.agent_integrations.registry import get_adapter, resolve_adapter_name

    discover_adapters()
    try:
        canonical_name, alias_used = resolve_adapter_name(name)
        adapter = get_adapter(canonical_name, _brain_dir())
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)

    diagnose = getattr(adapter, "diagnose", None)
    if not callable(diagnose):
        typer.echo(f"{name}: adapter doctor not implemented", err=True)
        raise typer.Exit(1)

    report = diagnose()
    data = report.to_dict()
    data["requested_adapter"] = name
    data["adapter"] = canonical_name
    if alias_used:
        data["alias"] = alias_used

    if format == "json":
        typer.echo(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        title = f"Adapter doctor: {canonical_name}"
        if alias_used:
            title += f" (requested alias: {alias_used})"
        table = Table(title=title)
        table.add_column("check")
        table.add_column("status")
        table.add_column("detail")
        table.add_column("fix")
        for check in data["checks"]:
            status = str(check["status"])
            color = "green" if status == "ok" else "yellow" if status == "warn" else "red"
            table.add_row(
                str(check["name"]),
                f"[{color}]{status}[/{color}]",
                str(check["detail"]),
                str(check["fix"]),
            )
        console.print(table)

    if report.overall_status == "error":
        raise typer.Exit(1)


@adapter_app.command("verify")
def adapter_verify(
    name: str = typer.Argument(..., help="Adapter name to verify"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
    verifier: str = typer.Option("cli", "--verifier", help="Verifier label stored in evidence"),
    note: str | None = typer.Option(None, "--note", help="Optional verification note"),
    context_probe: bool = typer.Option(
        False,
        "--context-probe/--no-context-probe",
        help="Require transcript-level evidence that injected AMH context reached the model session.",
    ),
) -> None:
    """Record verified evidence after adapter doctor and runtime checks pass."""
    import json

    from agent_brain.product.adapter_onboarding import verify_adapter

    try:
        payload = verify_adapter(
            _brain_dir(),
            name,
            verifier=verifier,
            note=note,
            context_probe=context_probe,
        )
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        table = Table(title=f"Adapter verify: {payload.get('adapter', name)}")
        table.add_column("field")
        table.add_column("value")
        table.add_row("status", str(payload.get("status")))
        blockers = payload.get("blockers") or []
        table.add_row("blockers", "; ".join(str(blocker) for blocker in blockers) if blockers else "none")
        evidence = payload.get("evidence") or []
        table.add_row("evidence", "; ".join(str(item) for item in evidence))
        mcp_probe = payload.get("mcp_probe")
        if isinstance(mcp_probe, dict):
            table.add_row("mcp_probe", f"{mcp_probe.get('status')}: {mcp_probe.get('detail')}")
        context_probe_payload = payload.get("context_probe")
        if isinstance(context_probe_payload, dict):
            table.add_row(
                "context_probe",
                f"{context_probe_payload.get('status')}: {context_probe_payload.get('detail')}",
            )
        console.print(table)
    if payload.get("status") != "passed":
        raise typer.Exit(1)


@adapter_app.command("install-verify")
def adapter_install_verify(
    name: str = typer.Argument(..., help="Adapter name to install and verify"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
    verifier: str = typer.Option("cli", "--verifier", help="Verifier label stored in evidence"),
    uninstall_check: bool = typer.Option(
        False,
        "--uninstall-check/--no-uninstall-check",
        help="Also prove hub-owned uninstall works. Passed records are not persisted in this mode.",
    ),
    context_probe: bool = typer.Option(
        False,
        "--context-probe/--no-context-probe",
        help="Require transcript-level evidence that injected AMH context reached the model session.",
    ),
) -> None:
    """Run a one-command install + doctor/runtime verification transaction."""

    import json

    from agent_brain.product.adapter_onboarding import install_verify_adapter

    try:
        payload = install_verify_adapter(
            _brain_dir(),
            name,
            verifier=verifier,
            uninstall_check=uninstall_check,
            context_probe=context_probe,
        )
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)

    if format == "json":
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        table = Table(title=f"Adapter install-verify: {payload.get('adapter', name)}")
        table.add_column("field")
        table.add_column("value")
        table.add_row("status", str(payload.get("status")))
        table.add_row("install", str((payload.get("install") or {}).get("status", "-")))
        verification = payload.get("verification") or {}
        if isinstance(verification, dict):
            table.add_row("verification", str(verification.get("status", "-")))
            table.add_row("doctor", str(verification.get("doctor_status", "-")))
            table.add_row("runtime_events", str(verification.get("runtime_events", "-")))
        if uninstall_check:
            uninstall = payload.get("uninstall") or {}
            if isinstance(uninstall, dict):
                table.add_row("uninstall", str(uninstall.get("status", "-")))
                table.add_row("uninstall_doctor", str(uninstall.get("doctor_after_status", "-")))
            table.add_row("recorded", str(payload.get("persistent_verification_recorded")))
        blockers = payload.get("blockers") or []
        table.add_row("blockers", "; ".join(str(blocker) for blocker in blockers) if blockers else "none")
        console.print(table)

    if payload.get("status") != "passed":
        raise typer.Exit(1)


__all__ = [
    "adapter_list",
    "adapter_install",
    "adapter_install_verify",
    "adapter_uninstall",
    "adapter_doctor",
    "adapter_verify",
]
