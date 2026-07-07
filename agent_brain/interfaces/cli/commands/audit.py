"""CLI audit subcommands."""
from __future__ import annotations

from agent_brain.interfaces.cli._app import audit_app
from agent_brain.interfaces.cli._shared import *  # noqa: F401,F403


@audit_app.command(name="skill")
def audit_skill(
    path: str = typer.Argument(..., help="Path to skill file or directory"),
    rules: str | None = typer.Option(None, "--rules", help="Custom rules file path"),
    format: str = typer.Option("markdown", "--format", help="Output format: json or markdown"),
) -> None:
    """Audit a skill file or directory for security issues."""
    target_path = Path(path)

    if not target_path.exists():
        typer.echo(f"Error: Path does not exist: {path}", err=True)
        raise typer.Exit(1)

    try:
        if rules:
            rule_set = load_rules_from_file(rules)
        else:
            rule_set = load_merged_rules()
    except Exception as e:
        typer.echo(f"Error loading rules: {e}", err=True)
        raise typer.Exit(1)

    scanner = SkillScanner(rules=rule_set)

    if target_path.is_file():
        report = scanner.scan_directory(target_path.parent, glob=target_path.name)
    else:
        report = scanner.scan_directory(target_path)

    if format == "json":
        import json

        typer.echo(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        typer.echo(report.to_markdown())

    if not report.passed:
        raise typer.Exit(1)


@audit_app.command(name="outbound")
def audit_outbound(
    since: int = typer.Option(30, "--since", help="Number of days to look back"),
    format: str = typer.Option("markdown", "--format", help="Output format: json or markdown"),
) -> None:
    """View audit log of all outbound events from this machine."""
    events = list_outbound_events(since_days=since)

    if not events:
        typer.echo("No outbound events recorded.")
        return

    if format == "json":
        import json

        data = [event.to_dict() for event in events]
        typer.echo(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        lines = []
        lines.append("# Outbound Events Audit Log")
        lines.append("")
        lines.append(f"Showing events from the last {since} days.")
        lines.append("")
        lines.append(f"**Total Events**: {len(events)}")
        lines.append("")
        lines.append("## Events")
        lines.append("")

        for i, event in enumerate(events, 1):
            lines.append(f"### {i}. {event.destination}")
            lines.append("")
            lines.append(f"- **Timestamp**: {event.timestamp}")
            lines.append(f"- **Payload Type**: {event.payload_type}")
            lines.append(f"- **Size**: {event.size_bytes} bytes")
            lines.append(f"- **Source Tool**: {event.source_tool}")
            if event.approved_by:
                lines.append(f"- **Approved By**: {event.approved_by}")
            lines.append("")

        typer.echo("\n".join(lines))


__all__ = ["audit_skill", "audit_outbound"]
