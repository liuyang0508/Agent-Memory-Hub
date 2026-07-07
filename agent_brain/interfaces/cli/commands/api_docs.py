"""CLI command for listing Web Admin API endpoints."""

from __future__ import annotations

from importlib import import_module as _import_module
from typing import Iterable

import typer
from rich.console import Console
from rich.table import Table

from agent_brain.interfaces.cli._app import app


EndpointRow = tuple[str, str, str]


def discover_api_endpoints(import_module=_import_module) -> list[EndpointRow]:
    """Discover the same API/WS route surface served by the Web Admin."""

    try:
        web_app = import_module("web.app").app
    except ModuleNotFoundError:
        return []

    rows: list[EndpointRow] = []
    for route in _iter_routes(web_app):
        path = getattr(route, "path", "")
        if not path or not (path.startswith("/api") or path.startswith("/ws")):
            continue
        methods = getattr(route, "methods", None) or {"WS"}
        description = _description(route)
        for method in sorted(methods):
            if method in {"HEAD", "OPTIONS"}:
                continue
            rows.append((method, path, description))
    return sorted(rows, key=lambda row: (row[1], row[0]))


def _iter_routes(router) -> Iterable[object]:
    for route in router.routes:
        original_router = getattr(route, "original_router", None)
        if original_router is not None:
            yield from _iter_routes(original_router)
            continue
        yield route


def _description(route: object) -> str:
    endpoint = getattr(route, "endpoint", None)
    doc = getattr(endpoint, "__doc__", None)
    if doc:
        first_line = doc.strip().splitlines()[0].strip()
        if first_line:
            return first_line
    return str(getattr(route, "name", "") or "Web Admin route")


API_ENDPOINTS: list[EndpointRow] = discover_api_endpoints()


@app.command("api-docs")
def api_docs() -> None:
    """List all available Web Admin API endpoints."""
    endpoints = discover_api_endpoints()
    if not endpoints:
        typer.echo("Web Admin API docs unavailable: install with `agent-memory-hub[web]`.", err=True)
        raise typer.Exit(1)
    console = Console(width=160)
    table = Table(title="Agent Memory Hub API Endpoints")
    table.add_column("Method", style="bold cyan", width=8)
    table.add_column("Path", style="green")
    table.add_column("Description")
    for method, path, desc in endpoints:
        table.add_row(method, path, desc)
    console.print(table)
    typer.echo(f"\nTotal: {len(endpoints)} endpoints")
    typer.echo("Docs: http://localhost:8765/docs (when server is running)")


__all__ = ["API_ENDPOINTS", "api_docs", "discover_api_endpoints"]
