"""Tests for the LLM-Wiki style CLI surface."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from typer.testing import CliRunner

from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Refs
from agent_brain.interfaces.cli import app
from agent_brain.memory.store.items_store import ItemsStore

runner = CliRunner()
NOW = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)


def _item(
    suffix: str,
    *,
    project: str = "agent-memory-hub",
    tags: list[str] | None = None,
    refs: Refs | None = None,
) -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260704-120000-wiki-cli-{suffix}",
        type=MemoryType.fact,
        created_at=NOW,
        title=f"Wiki CLI {suffix}",
        summary=f"summary {suffix}",
        tags=tags or ["obsidian", "wiki"],
        project=project,
        refs=refs or Refs(),
    )


def test_wiki_compile_generates_workbench_pages_and_schema(
    tmp_brain: Path,
    tmp_path: Path,
) -> None:
    store = ItemsStore(tmp_brain / "items")
    store.write(_item("a", refs=Refs(urls=["https://example.com/a"])), "body a")
    store.write(
        _item("b", refs=Refs(mems=["mem-20260704-120000-wiki-cli-a"])),
        "body b",
    )

    vault = tmp_path / "BrainVault"
    result = runner.invoke(app, ["wiki", "compile", str(vault), "--overwrite"])

    assert result.exit_code == 0, result.output
    assert (vault / "index.md").exists()
    assert (vault / "log.md").exists()
    assert (vault / "health" / "report.md").exists()
    assert (vault / "entities" / "index.md").exists()
    assert (vault / "AGENTS.md").exists()
    assert (vault / "raw").is_dir()
    assert (vault / "output").is_dir()

    schema = (vault / "AGENTS.md").read_text(encoding="utf-8")
    assert "LLM-Wiki" in schema
    assert "raw/" in schema
    assert "output/" in schema
    assert "items are exported from Agent Memory Hub" in schema


def test_wiki_compile_project_filter_scopes_item_export_and_wiki_index(
    tmp_brain: Path,
    tmp_path: Path,
) -> None:
    store = ItemsStore(tmp_brain / "items")
    store.write(_item("amh", project="agent-memory-hub"), "body amh")
    store.write(_item("other", project="other-project"), "body other")

    vault = tmp_path / "BrainVault"
    result = runner.invoke(
        app,
        [
            "wiki",
            "compile",
            str(vault),
            "--project",
            "agent-memory-hub",
            "--overwrite",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (vault / "mem-20260704-120000-wiki-cli-amh.md").exists()
    assert not (vault / "mem-20260704-120000-wiki-cli-other.md").exists()

    index = (vault / "index.md").read_text(encoding="utf-8")
    assert "Wiki CLI amh" in index
    assert "Wiki CLI other" not in index


def test_wiki_query_save_writes_output_snapshot_with_memory_links(
    tmp_brain: Path,
    tmp_path: Path,
) -> None:
    store = ItemsStore(tmp_brain / "items")
    item = _item("query", project="agent-memory-hub", tags=["compiler-loop"])
    store.write(item, "Karpathy inspired compiler loop for Obsidian workbench.")

    vault = tmp_path / "BrainVault"
    result = runner.invoke(
        app,
        [
            "wiki",
            "query",
            str(vault),
            "compiler loop",
            "--project",
            "agent-memory-hub",
            "--save",
        ],
    )

    assert result.exit_code == 0, result.output
    output_files = sorted((vault / "output").glob("*.md"))
    assert len(output_files) == 1

    content = output_files[0].read_text(encoding="utf-8")
    assert "# Query: compiler loop" in content
    assert f"[[{item.id}|{item.title}]]" in content
    assert "Karpathy inspired compiler loop" in content
    assert "Saved wiki query output" in result.output


def test_wiki_query_without_save_prints_snapshot_without_writing_output(
    tmp_brain: Path,
    tmp_path: Path,
) -> None:
    store = ItemsStore(tmp_brain / "items")
    item = _item("print", project="agent-memory-hub", tags=["brainvault"])
    store.write(item, "BrainVault output should not be written without --save.")

    vault = tmp_path / "BrainVault"
    result = runner.invoke(
        app,
        [
            "wiki",
            "query",
            str(vault),
            "BrainVault",
            "--project",
            "agent-memory-hub",
        ],
    )

    assert result.exit_code == 0, result.output
    assert f"[[{item.id}|{item.title}]]" in result.output
    assert not (vault / "output").exists()


def test_wiki_lint_save_writes_read_only_fix_plan(
    tmp_brain: Path,
    tmp_path: Path,
) -> None:
    store = ItemsStore(tmp_brain / "items")
    item = _item("unsourced", project="agent-memory-hub")
    store.write(item, "This fact intentionally has no source refs.")

    vault = tmp_path / "BrainVault"
    result = runner.invoke(
        app,
        [
            "wiki",
            "lint",
            str(vault),
            "--project",
            "agent-memory-hub",
            "--save",
        ],
    )

    assert result.exit_code == 0, result.output
    plan = vault / "health" / "fix-plan.md"
    assert plan.exists()
    content = plan.read_text(encoding="utf-8")
    assert "# Wiki Fix Plan" in content
    assert "read-only" in content
    assert "source_missing" in content
    assert "add_source_reference" in content
    assert f"[[{item.id}|{item.title}]]" in content


def test_wiki_lint_reports_broken_vault_wikilinks(
    tmp_brain: Path,
    tmp_path: Path,
) -> None:
    vault = tmp_path / "BrainVault"
    vault.mkdir()
    (vault / "note.md").write_text("Broken link: [[missing-page|Missing Page]]\n", encoding="utf-8")

    result = runner.invoke(app, ["wiki", "lint", str(vault)])

    assert result.exit_code == 0, result.output
    assert "broken_wikilink" in result.output
    assert "note.md" in result.output
    assert "missing-page" in result.output
    assert not (vault / "health" / "fix-plan.md").exists()


def test_wiki_lint_ignores_previous_generated_fix_plan(
    tmp_brain: Path,
    tmp_path: Path,
) -> None:
    vault = tmp_path / "BrainVault"
    health = vault / "health"
    health.mkdir(parents=True)
    (health / "fix-plan.md").write_text(
        "Previous generated plan with [[missing-from-old-plan]].\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["wiki", "lint", str(vault)])

    assert result.exit_code == 0, result.output
    assert "missing-from-old-plan" not in result.output
    assert "broken_wikilink" not in result.output
