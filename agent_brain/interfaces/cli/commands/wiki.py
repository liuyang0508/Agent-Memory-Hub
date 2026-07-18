"""CLI commands for the LLM-Wiki style Obsidian workbench."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re

import typer

from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.contracts.memory_enums import memory_enum_value
from agent_brain.interfaces.cli._app import wiki_app
from agent_brain.interfaces.cli._shared import _managed_components, _store_only, SearchFilter

ItemBody = tuple[MemoryItem, str]


@dataclass
class WikiCompileReport:
    exported: int
    skipped: int
    wiki_pages: int
    entity_pages: int
    schema_path: Path
    errors: list[str]


@dataclass
class WikiQueryHit:
    item: MemoryItem
    body: str
    score: float


@dataclass(frozen=True)
class WikiFixFinding:
    issue_type: str
    severity: str
    target: str
    suggested_action: str
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class WikiFixPlan:
    generated_at: datetime
    findings: list[WikiFixFinding]


def _matches_scope(
    item: MemoryItem,
    *,
    project: str | None,
    memory_type: str | None,
) -> bool:
    if project and item.project != project:
        return False
    if memory_type and str(item.type) != memory_type:
        return False
    return True


def _scoped_items(
    items: list[ItemBody],
    *,
    project: str | None,
    memory_type: str | None,
) -> list[ItemBody]:
    return [
        (item, body)
        for item, body in items
        if _matches_scope(item, project=project, memory_type=memory_type)
    ]


def render_vault_agents_schema() -> str:
    """Return the AGENTS.md schema that makes the vault usable by coding agents."""
    return """# Agent Memory Hub LLM-Wiki Vault

This vault is a human review workbench for Agent Memory Hub.

## Layers

- `raw/` is the inbox for source material humans want agents to inspect.
- `output/` is where high-value query answers, reports, charts, and decks can be saved.
- `index.md`, `log.md`, `health/report.md`, and `entities/` are generated wiki views.
- Memory items are exported from Agent Memory Hub as notes at the vault root.

## Rules For Agents

- Treat `~/.agent-memory-hub/items/` as the source of truth for memory items.
- Treat generated wiki views as derived files that can be rebuilt.
- Do not edit exported memory item notes unless the user asks for an Obsidian import workflow.
- When ingesting new sources from `raw/`, summarize evidence first, then write durable memory
  items through Agent Memory Hub before regenerating the wiki views.
- When a query answer is worth keeping, save it under `output/` and link the relevant memory
  item ids or wiki pages.
- Run wiki lint or health checks before trusting old claims.
"""


def write_vault_agents_schema(vault_dir: Path) -> Path:
    path = vault_dir / "AGENTS.md"
    path.write_text(render_vault_agents_schema(), encoding="utf-8")
    return path


def _markdown_link(item: MemoryItem) -> str:
    return f"[[{item.id}|{item.title}]]"


def _query_terms(query: str) -> list[str]:
    return [term.lower() for term in query.split() if term.strip()]


def _lexical_score(query: str, item: MemoryItem, body: str) -> float:
    terms = _query_terms(query)
    if not terms:
        return 0.0
    haystacks = [
        item.title.lower(),
        item.summary.lower(),
        " ".join(item.tags).lower(),
        body.lower(),
    ]
    score = 0.0
    for term in terms:
        score += haystacks[0].count(term) * 4
        score += haystacks[1].count(term) * 3
        score += haystacks[2].count(term) * 2
        score += haystacks[3].count(term)
    return score


def search_wiki_query_hits(
    query: str,
    *,
    top_k: int = 5,
    project: str | None = None,
    memory_type: str | None = None,
) -> list[WikiQueryHit]:
    """Search memory for a wiki query, falling back to local Markdown scanning."""
    store = _store_only()
    retrieval_hits = []
    try:
        with _managed_components() as (managed_store, _index, retriever):
            store = managed_store
            filters = SearchFilter(type=memory_type, project=project)
            retrieval_hits = retriever.search(
                query,
                top_k=top_k,
                filters=None if filters.is_empty else filters,
            )
    except Exception:
        retrieval_hits = []

    items_by_id = {
        item.id: (item, body)
        for item, body in store.iter_all()
        if _matches_scope(item, project=project, memory_type=memory_type)
    }

    results: list[WikiQueryHit] = []
    for hit in retrieval_hits:
        if hit.id in items_by_id:
            item, body = items_by_id[hit.id]
            results.append(WikiQueryHit(item=item, body=body, score=hit.score))

    if results:
        return results[:top_k]

    lexical: list[WikiQueryHit] = []
    for item, body in items_by_id.values():
        score = _lexical_score(query, item, body)
        if score > 0:
            lexical.append(WikiQueryHit(item=item, body=body, score=score))
    lexical.sort(key=lambda hit: (-hit.score, hit.item.created_at, hit.item.id))
    return lexical[:top_k]


def _body_snippet(body: str, limit: int = 240) -> str:
    snippet = " ".join(body.split())
    if len(snippet) <= limit:
        return snippet
    return snippet[: limit - 1].rstrip() + "..."


def render_wiki_query_snapshot(
    query: str,
    hits: list[WikiQueryHit],
    *,
    generated_at: datetime | None = None,
) -> str:
    generated_at = generated_at or datetime.now(timezone.utc)
    lines = [
        f"# Query: {query}",
        "",
        f"> Generated {generated_at:%Y-%m-%d %H:%M} UTC · {len(hits)} retrieved memories",
        "",
        "## Retrieved Memories",
    ]
    if not hits:
        lines.append("- no matches")
    for rank, hit in enumerate(hits, 1):
        item = hit.item
        lines += [
            f"{rank}. [[{item.id}|{item.title}]]",
            f"   - type: {memory_enum_value(item.type)}",
            f"   - project: {item.project or '(none)'}",
            f"   - score: {hit.score:.4f}",
            f"   - summary: {item.summary}",
        ]
        snippet = _body_snippet(hit.body)
        if snippet:
            lines.append(f"   - snippet: {snippet}")
    lines += [
        "",
        "## Next Actions",
        "- Review the retrieved memories before treating this as a final answer.",
        "- If this becomes durable knowledge, write an Agent Memory Hub item and re-run `memory wiki compile`.",
    ]
    return "\n".join(lines) + "\n"


def write_wiki_query_output(
    vault_dir: Path,
    query: str,
    hits: list[WikiQueryHit],
    *,
    generated_at: datetime | None = None,
) -> Path:
    from agent_brain.memory.evidence.integrations.obsidian import _slugify

    generated_at = generated_at or datetime.now(timezone.utc)
    output_dir = vault_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = _slugify(query) or "query"
    path = output_dir / f"{generated_at:%Y%m%d-%H%M%S}-{stem}.md"
    counter = 2
    while path.exists():
        path = output_dir / f"{generated_at:%Y%m%d-%H%M%S}-{stem}-{counter}.md"
        counter += 1
    path.write_text(
        render_wiki_query_snapshot(query, hits, generated_at=generated_at),
        encoding="utf-8",
    )
    return path


_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
_GENERATED_LINT_PATHS = {"health/fix-plan.md"}


def _normalize_wikilink_target(raw_target: str) -> str:
    target = raw_target.split("|", 1)[0].split("#", 1)[0].strip()
    if target.endswith(".md"):
        target = target[:-3]
    return target


def _vault_targets(vault_dir: Path) -> set[str]:
    targets: set[str] = set()
    if not vault_dir.exists():
        return targets
    for path in vault_dir.rglob("*.md"):
        rel = path.relative_to(vault_dir).with_suffix("")
        normalized_rel = rel.as_posix()
        targets.add(normalized_rel)
        targets.add(path.stem)
    return targets


def _broken_wikilink_findings(vault_dir: Path) -> list[WikiFixFinding]:
    targets = _vault_targets(vault_dir)
    findings: list[WikiFixFinding] = []
    if not vault_dir.exists():
        return findings
    for path in sorted(vault_dir.rglob("*.md")):
        rel = path.relative_to(vault_dir).as_posix()
        if rel in _GENERATED_LINT_PATHS:
            continue
        text = path.read_text(encoding="utf-8")
        for match in _WIKILINK_RE.finditer(text):
            target = _normalize_wikilink_target(match.group(1))
            if not target or target in targets:
                continue
            findings.append(WikiFixFinding(
                issue_type="broken_wikilink",
                severity="warning",
                target=f"{rel} -> [[{target}]]",
                suggested_action="create_or_correct_wikilink_target",
                evidence=(f"file:{rel}", f"missing_link:{target}"),
            ))
    return findings


def build_wiki_fix_plan(
    vault_dir: Path,
    *,
    project: str | None = None,
    issue_type: str | None = None,
    limit: int | None = None,
    generated_at: datetime | None = None,
) -> WikiFixPlan:
    from agent_brain.memory.governance.knowledge_lint import KnowledgeLinter

    report = KnowledgeLinter(_store_only()).run(project=project, issue_type=issue_type)
    findings = [
        WikiFixFinding(
            issue_type=finding.issue_type,
            severity=finding.severity,
            target=f"[[{finding.item_id}|{finding.title}]]",
            suggested_action=finding.suggested_action,
            evidence=(*finding.reasons, *finding.evidence),
        )
        for finding in report.findings
    ]
    if issue_type in {None, "broken_wikilink"}:
        findings.extend(_broken_wikilink_findings(vault_dir))
    if issue_type is not None:
        findings = [finding for finding in findings if finding.issue_type == issue_type]
    if limit is not None:
        findings = findings[:limit]
    return WikiFixPlan(
        generated_at=generated_at or datetime.now(timezone.utc),
        findings=findings,
    )


def render_wiki_fix_plan(plan: WikiFixPlan) -> str:
    counts = Counter(finding.issue_type for finding in plan.findings)
    lines = [
        "# Wiki Fix Plan",
        "",
        f"> Generated {plan.generated_at:%Y-%m-%d %H:%M} UTC · read-only · "
        f"{len(plan.findings)} findings",
        "",
        "## Summary",
    ]
    if not counts:
        lines.append("- no findings")
    for issue, count in sorted(counts.items()):
        lines.append(f"- {issue}: {count}")

    lines += ["", "## Findings"]
    if not plan.findings:
        lines.append("- no findings")
    for index, finding in enumerate(plan.findings, 1):
        lines += [
            f"### {index}. [{finding.severity}] {finding.issue_type}",
            "",
            f"- target: {finding.target}",
            f"- action: {finding.suggested_action}",
        ]
        if finding.evidence:
            lines.append("- evidence:")
            for evidence in finding.evidence:
                lines.append(f"  - `{evidence}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_wiki_fix_plan(vault_dir: Path, plan: WikiFixPlan) -> Path:
    health_dir = vault_dir / "health"
    health_dir.mkdir(parents=True, exist_ok=True)
    path = health_dir / "fix-plan.md"
    path.write_text(render_wiki_fix_plan(plan), encoding="utf-8")
    return path


def compile_wiki_workbench(
    vault_dir: Path,
    *,
    project: str | None = None,
    memory_type: str | None = None,
    overwrite: bool = False,
) -> WikiCompileReport:
    from agent_brain.memory.evidence.integrations.obsidian import ObsidianSync
    from agent_brain.memory.evidence.integrations.obsidian_wiki import write_wiki_pages
    from agent_brain.memory.governance.entities import write_entity_pages

    store = _store_only()
    vault_dir.mkdir(parents=True, exist_ok=True)
    (vault_dir / "raw").mkdir(exist_ok=True)
    (vault_dir / "output").mkdir(exist_ok=True)

    sync = ObsidianSync(items_store=store, vault_dir=vault_dir)
    export_report = sync.export_all(project=project, type=memory_type, overwrite=overwrite)

    items = _scoped_items(
        list(store.iter_all()),
        project=project,
        memory_type=memory_type,
    )
    wiki_paths = write_wiki_pages(items, vault_dir)
    entity_paths = write_entity_pages(items, vault_dir)
    schema_path = write_vault_agents_schema(vault_dir)

    return WikiCompileReport(
        exported=export_report.exported,
        skipped=export_report.skipped,
        wiki_pages=len(wiki_paths),
        entity_pages=len(entity_paths),
        schema_path=schema_path,
        errors=export_report.errors,
    )


@wiki_app.command("compile")
def wiki_compile(
    vault_dir: str = typer.Argument(..., help="Path to Obsidian vault directory"),
    project: str | None = typer.Option(None, "--project", help="Filter by project"),
    type: str | None = typer.Option(None, "--type", help="Filter by memory type"),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite existing item notes"),
) -> None:
    """Compile memory items into an Obsidian LLM-Wiki workbench."""
    vault = Path(vault_dir).expanduser()
    report = compile_wiki_workbench(
        vault,
        project=project,
        memory_type=type,
        overwrite=overwrite,
    )
    typer.echo(
        "Compiled wiki workbench: "
        f"exported {report.exported} items, skipped {report.skipped}, "
        f"generated {report.wiki_pages} wiki pages, "
        f"{report.entity_pages} entity pages, schema {report.schema_path.name}"
    )
    if report.errors:
        for err in report.errors:
            typer.echo(f"  error: {err}", err=True)
        raise typer.Exit(1)


@wiki_app.command("lint")
def wiki_lint(
    vault_dir: str = typer.Argument(..., help="Path to Obsidian vault directory"),
    project: str | None = typer.Option(None, "--project", help="Filter memory findings by project"),
    issue_type: str | None = typer.Option(None, "--issue-type", help="Only show one issue type"),
    limit: int | None = typer.Option(None, "--limit", help="Maximum findings to show"),
    save: bool = typer.Option(False, "--save", help="Write the read-only fix plan to health/fix-plan.md"),
) -> None:
    """Build a read-only Obsidian workbench fix plan."""
    if limit is not None and limit < 0:
        typer.echo("limit must be non-negative", err=True)
        raise typer.Exit(2)
    vault = Path(vault_dir).expanduser()
    plan = build_wiki_fix_plan(
        vault,
        project=project,
        issue_type=issue_type,
        limit=limit,
    )
    if save:
        path = write_wiki_fix_plan(vault, plan)
        typer.echo(f"Saved wiki fix plan: {path} ({len(plan.findings)} findings)")
        return
    typer.echo(render_wiki_fix_plan(plan))


@wiki_app.command("query")
def wiki_query(
    vault_dir: str = typer.Argument(..., help="Path to Obsidian vault directory"),
    query: str = typer.Argument(..., help="Question or search query to run against memory"),
    project: str | None = typer.Option(None, "--project", help="Filter by project"),
    type: str | None = typer.Option(None, "--type", help="Filter by memory type"),
    top_k: int = typer.Option(5, "--top-k", help="Maximum retrieved memories"),
    save: bool = typer.Option(False, "--save", help="Write the query snapshot to output/"),
) -> None:
    """Query memory for the LLM-Wiki workbench and optionally save a snapshot."""
    vault = Path(vault_dir).expanduser()
    hits = search_wiki_query_hits(
        query,
        top_k=top_k,
        project=project,
        memory_type=type,
    )
    if save:
        path = write_wiki_query_output(vault, query, hits)
        typer.echo(f"Saved wiki query output: {path} ({len(hits)} hits)")
        return
    typer.echo(render_wiki_query_snapshot(query, hits))


__all__ = [
    "WikiCompileReport",
    "WikiFixFinding",
    "WikiFixPlan",
    "WikiQueryHit",
    "build_wiki_fix_plan",
    "compile_wiki_workbench",
    "render_vault_agents_schema",
    "render_wiki_fix_plan",
    "render_wiki_query_snapshot",
    "search_wiki_query_hits",
    "wiki_compile",
    "wiki_lint",
    "wiki_query",
    "write_wiki_fix_plan",
    "write_wiki_query_output",
    "write_vault_agents_schema",
]
