"""CLI commands for optional external code graph providers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rich.table import Table

from agent_brain.codegraph.provider import (
    CodeGraphInvocationError,
    CodeGraphUnavailableError,
    CodebaseMemoryMcpProvider,
    derive_codebase_memory_project,
)
from agent_brain.interfaces.cli._app import codegraph_app
from agent_brain.interfaces.cli._shared import console, typer


def _provider(binary: str | None, timeout: float) -> CodebaseMemoryMcpProvider:
    return CodebaseMemoryMcpProvider(binary=binary, timeout_s=timeout)


def _repo(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.exists() or not resolved.is_dir():
        typer.echo(f"repo path not found: {resolved}", err=True)
        raise typer.Exit(2)
    return resolved


def _emit(payload: dict[str, Any], *, format: str) -> None:
    if format == "json":
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if format != "summary":
        typer.echo("format must be json or summary", err=True)
        raise typer.Exit(2)

    table = Table(title="Code graph result")
    table.add_column("field")
    table.add_column("value")
    for key in sorted(payload):
        value = payload[key]
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)[:300]
        else:
            rendered = str(value)
        table.add_row(key, rendered)
    console.print(table)


def _handle_provider_error(exc: Exception) -> None:
    typer.echo(str(exc), err=True)
    raise typer.Exit(1)


@codegraph_app.command("project-name")
def project_name(
    repo: Path = typer.Argument(..., help="Repository path to normalize like codebase-memory-mcp"),
) -> None:
    """Print the codebase-memory-mcp project name for a repository path."""
    typer.echo(derive_codebase_memory_project(repo))


@codegraph_app.command("index")
def index_repository(
    repo: Path = typer.Option(Path.cwd(), "--repo", help="Repository to index"),
    mode: str = typer.Option("fast", "--mode", help="fast, moderate, full, or cross-repo-intelligence"),
    persistence: bool = typer.Option(
        False,
        "--persistence/--no-persistence",
        help="Write .codebase-memory/graph.db.zst via the external provider",
    ),
    binary: str | None = typer.Option(None, "--binary", help="codebase-memory-mcp binary path"),
    timeout: float = typer.Option(60.0, "--timeout", help="External provider timeout in seconds"),
    format: str = typer.Option("summary", "--format", help="summary or json"),
) -> None:
    """Index a repo with the optional codebase-memory-mcp provider."""
    try:
        payload = _provider(binary, timeout).index_repository(
            repo_path=_repo(repo),
            mode=mode,
            persistence=persistence,
        )
    except (CodeGraphUnavailableError, CodeGraphInvocationError) as exc:
        _handle_provider_error(exc)
    _emit(payload, format=format)


@codegraph_app.command("status")
def index_status(
    repo: Path = typer.Option(Path.cwd(), "--repo", help="Repository path"),
    project: str | None = typer.Option(None, "--project", help="Override codebase-memory project name"),
    binary: str | None = typer.Option(None, "--binary", help="codebase-memory-mcp binary path"),
    timeout: float = typer.Option(10.0, "--timeout", help="External provider timeout in seconds"),
    format: str = typer.Option("summary", "--format", help="summary or json"),
) -> None:
    """Show index status for a repo/project."""
    try:
        payload = _provider(binary, timeout).index_status(repo_path=_repo(repo), project=project)
    except (CodeGraphUnavailableError, CodeGraphInvocationError) as exc:
        _handle_provider_error(exc)
    _emit(payload, format=format)


@codegraph_app.command("architecture")
def architecture(
    repo: Path = typer.Option(Path.cwd(), "--repo", help="Repository path"),
    project: str | None = typer.Option(None, "--project", help="Override codebase-memory project name"),
    aspect: list[str] | None = typer.Option(None, "--aspect", help="Architecture aspect to request"),
    binary: str | None = typer.Option(None, "--binary", help="codebase-memory-mcp binary path"),
    timeout: float = typer.Option(10.0, "--timeout", help="External provider timeout in seconds"),
    format: str = typer.Option("summary", "--format", help="summary or json"),
) -> None:
    """Fetch a high-level code architecture snapshot from the external provider."""
    try:
        payload = _provider(binary, timeout).architecture(
            repo_path=_repo(repo),
            project=project,
            aspects=list(aspect or []),
        )
    except (CodeGraphUnavailableError, CodeGraphInvocationError) as exc:
        _handle_provider_error(exc)
    _emit(payload, format=format)


@codegraph_app.command("changes")
def changes(
    repo: Path = typer.Option(Path.cwd(), "--repo", help="Repository path"),
    project: str | None = typer.Option(None, "--project", help="Override codebase-memory project name"),
    scope: str = typer.Option("symbols", "--scope", help="files, symbols, or impact"),
    depth: int = typer.Option(2, "--depth", help="Blast-radius graph depth"),
    base_branch: str = typer.Option("main", "--base-branch", help="Git base branch/ref"),
    binary: str | None = typer.Option(None, "--binary", help="codebase-memory-mcp binary path"),
    timeout: float = typer.Option(10.0, "--timeout", help="External provider timeout in seconds"),
    format: str = typer.Option("summary", "--format", help="summary or json"),
) -> None:
    """Map git diff changes to affected code symbols."""
    try:
        payload = _provider(binary, timeout).detect_changes(
            repo_path=_repo(repo),
            project=project,
            scope=scope,
            depth=depth,
            base_branch=base_branch,
        )
    except (CodeGraphUnavailableError, CodeGraphInvocationError) as exc:
        _handle_provider_error(exc)
    _emit(payload, format=format)


@codegraph_app.command("search")
def search(
    query: str | None = typer.Argument(None, help="Natural-language or keyword query"),
    repo: Path = typer.Option(Path.cwd(), "--repo", help="Repository path"),
    project: str | None = typer.Option(None, "--project", help="Override codebase-memory project name"),
    name_pattern: str | None = typer.Option(None, "--name-pattern", help="Regex symbol name pattern"),
    label: str | None = typer.Option(None, "--label", help="Node label, e.g. Function or Class"),
    limit: int = typer.Option(20, "--limit", help="Max rows"),
    offset: int = typer.Option(0, "--offset", help="Pagination offset"),
    binary: str | None = typer.Option(None, "--binary", help="codebase-memory-mcp binary path"),
    timeout: float = typer.Option(10.0, "--timeout", help="External provider timeout in seconds"),
    format: str = typer.Option("summary", "--format", help="summary or json"),
) -> None:
    """Search indexed code graph symbols."""
    if not query and not name_pattern:
        typer.echo("provide a query argument or --name-pattern", err=True)
        raise typer.Exit(2)
    try:
        payload = _provider(binary, timeout).search_graph(
            repo_path=_repo(repo),
            project=project,
            query=query,
            name_pattern=name_pattern,
            label=label,
            limit=limit,
            offset=offset,
        )
    except (CodeGraphUnavailableError, CodeGraphInvocationError) as exc:
        _handle_provider_error(exc)
    _emit(payload, format=format)


__all__ = [
    "project_name",
    "index_repository",
    "index_status",
    "architecture",
    "changes",
    "search",
]
