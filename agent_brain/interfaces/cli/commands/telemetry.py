"""Commands for explicit anonymous product-telemetry consent."""

from __future__ import annotations

import json

import typer

from agent_brain.interfaces.cli._app import telemetry_app
from agent_brain.platform.product_telemetry import disable, emit, enable, status


@telemetry_app.command("status")  # type: ignore[untyped-decorator]
def telemetry_status() -> None:
    """Show consent and the last successful-attempt timestamps."""

    typer.echo(json.dumps(status(), ensure_ascii=False, indent=2, sort_keys=True))


@telemetry_app.command("enable")  # type: ignore[untyped-decorator]
def telemetry_enable() -> None:
    """Enable bounded anonymous install/activity reporting."""

    config = enable()
    emit("install", channel="cli")
    typer.echo(f"Anonymous telemetry enabled: anon-{config['anonymous_id'][:8]}")


@telemetry_app.command("disable")  # type: ignore[untyped-decorator]
def telemetry_disable() -> None:
    """Disable all future product-telemetry network calls."""

    disable()
    typer.echo("Anonymous telemetry disabled.")


__all__ = ["telemetry_disable", "telemetry_enable", "telemetry_status"]
