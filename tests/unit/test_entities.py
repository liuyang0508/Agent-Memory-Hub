"""Tests for the entity/persona derived layer.

Entities are *derived* (not stored, no new schema type, no LLM): a name that
recurs as a project, an agent, or a frequent tag becomes an entity page that
aggregates every item about it. Fills the gap that jinchenma (HerName),
Tencent (Persona) and Karpathy LLM-Wiki (entities/) all independently have.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_brain.interfaces.cli import app
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.contracts.memory_item import MemoryItem, MemoryType

runner = CliRunner()
NOW = datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)


def _mk(suffix, *, project="hub", agent="claude-code", tags=None, type=MemoryType.fact):
    return MemoryItem(
        id=f"mem-20260101-000000-ent{suffix}",
        type=type,
        created_at=NOW,
        title=f"item {suffix}",
        summary=f"summary {suffix}",
        project=project,
        agent=agent,
        tags=tags if tags is not None else [],
    )


class TestExtractEntities:
    def test_project_becomes_entity(self):
        from agent_brain.memory.governance.entities import extract_entities

        items = [(_mk("a", project="wukong"), "b"), (_mk("b", project="wukong"), "b")]
        ents = {e.name: e for e in extract_entities(items, min_tag_count=99)}
        assert "wukong" in ents
        assert ents["wukong"].kind == "project"
        assert len(ents["wukong"].item_ids) == 2

    def test_agent_becomes_entity(self):
        from agent_brain.memory.governance.entities import extract_entities

        items = [(_mk("a", agent="codex", project=None), "b")]
        ents = {e.name: e for e in extract_entities(items, min_tag_count=99)}
        assert "codex" in ents
        assert ents["codex"].kind == "agent"

    def test_frequent_tag_becomes_entity(self):
        from agent_brain.memory.governance.entities import extract_entities

        items = [(_mk(str(i), project=None, agent=None, tags=["tencent"]), "b") for i in range(3)]
        ents = {e.name: e for e in extract_entities(items, min_tag_count=3)}
        assert "tencent" in ents
        assert ents["tencent"].kind == "tag"

    def test_rare_tag_excluded(self):
        from agent_brain.memory.governance.entities import extract_entities

        items = [(_mk("a", project=None, agent=None, tags=["rare"]), "b")]
        ents = {e.name: e for e in extract_entities(items, min_tag_count=3)}
        assert "rare" not in ents

    def test_related_entities_cooccur(self):
        from agent_brain.memory.governance.entities import extract_entities

        # project=hub items also tagged retrieval → hub and retrieval co-occur
        items = [
            (_mk("a", project="hub", agent=None, tags=["retrieval"]), "b"),
            (_mk("b", project="hub", agent=None, tags=["retrieval"]), "b"),
            (_mk("c", project="hub", agent=None, tags=["retrieval"]), "b"),
        ]
        ents = {e.name: e for e in extract_entities(items, min_tag_count=3)}
        related_names = [name for name, _ in ents["hub"].related]
        assert "retrieval" in related_names


class TestBuildPages:
    def _entities(self):
        from agent_brain.memory.governance.entities import extract_entities

        items = [(_mk(str(i), project="hub", agent="claude-code", tags=["retrieval"]), "b") for i in range(3)]
        return extract_entities(items, min_tag_count=3), {it.id: it for it, _ in items}

    def test_entity_page_has_name_and_links(self):
        from agent_brain.memory.governance.entities import build_entity_page

        ents, by_id = self._entities()
        hub = next(e for e in ents if e.name == "hub")
        md = build_entity_page(hub, by_id)
        assert "hub" in md
        assert str(len(hub.item_ids)) in md
        # references at least one item id
        assert any(iid in md for iid in hub.item_ids)

    def test_entity_page_links_items_by_exported_item_id(self):
        from agent_brain.memory.governance.entities import build_entity_page

        ents, by_id = self._entities()
        hub = next(e for e in ents if e.name == "hub")
        md = build_entity_page(hub, by_id)
        item = by_id[hub.item_ids[0]]
        assert f"[[{item.id}|{item.title}]]" in md

    def test_index_lists_entities(self):
        from agent_brain.memory.governance.entities import build_entities_index

        ents, _ = self._entities()
        md = build_entities_index(ents)
        assert "hub" in md
        assert "claude-code" in md


class TestWriteEntityPages:
    def test_writes_index_and_per_entity(self, tmp_path):
        from agent_brain.memory.governance.entities import write_entity_pages

        items = [(_mk(str(i), project="hub"), "b") for i in range(2)]
        vault = tmp_path / "vault"
        paths = write_entity_pages(items, vault, min_tag_count=99)
        assert (vault / "entities" / "index.md").exists()
        # at least the project entity page exists
        assert any(p.parent.name == "entities" and p.name != "index.md" for p in paths)

    def test_slug_collisions_do_not_overwrite_entity_pages(self, tmp_path):
        from agent_brain.memory.governance.entities import write_entity_pages

        items = [
            (_mk("a", project="foo bar"), "b"),
            (_mk("b", project="foo-bar"), "b"),
        ]
        vault = tmp_path / "vault"
        paths = write_entity_pages(items, vault, min_tag_count=99)
        entity_files = sorted((vault / "entities").glob("*.md"))

        assert len(entity_files) == len(paths)
        assert len({p.name for p in entity_files}) == len(entity_files)
        assert (vault / "entities" / "index.md").exists()

    def test_entity_named_index_does_not_overwrite_entities_index(self, tmp_path):
        from agent_brain.memory.governance.entities import write_entity_pages

        items = [(_mk("a", project="index"), "b")]
        vault = tmp_path / "vault"
        paths = write_entity_pages(items, vault, min_tag_count=99)
        index = (vault / "entities" / "index.md").read_text(encoding="utf-8")

        assert index.startswith("# Entities")
        assert any(p.name.startswith("index-") for p in paths)

    def test_rewrite_prunes_stale_entity_pages(self, tmp_path):
        from agent_brain.memory.governance.entities import write_entity_pages

        vault = tmp_path / "vault"
        stale = vault / "entities" / "stale.md"
        stale.parent.mkdir(parents=True)
        stale.write_text("# stale\n", encoding="utf-8")

        write_entity_pages([(_mk("a", project="hub"), "b")], vault, min_tag_count=99)

        assert not stale.exists()


class TestEntityCLI:
    @pytest.fixture
    def brain(self, tmp_brain_dir: Path):
        os.environ["BRAIN_DIR"] = str(tmp_brain_dir)
        store = ItemsStore(tmp_brain_dir / "items")
        store.write(_mk("a", project="wukong"), "b")
        store.write(_mk("b", project="wukong"), "b")
        yield tmp_brain_dir, store
        os.environ.pop("BRAIN_DIR", None)

    def test_entity_list(self, brain):
        result = runner.invoke(app, ["entity", "list"])
        assert result.exit_code == 0
        assert "wukong" in result.output

    def test_entity_show(self, brain):
        result = runner.invoke(app, ["entity", "show", "wukong"])
        assert result.exit_code == 0
        assert "wukong" in result.output

    def test_entity_show_unknown(self, brain):
        result = runner.invoke(app, ["entity", "show", "nonexistent-entity"])
        assert result.exit_code != 0


class TestWikiFlagEmitsEntities:
    @pytest.fixture
    def brain(self, tmp_brain_dir: Path):
        os.environ["BRAIN_DIR"] = str(tmp_brain_dir)
        store = ItemsStore(tmp_brain_dir / "items")
        store.write(_mk("a", project="hub"), "b")
        yield tmp_brain_dir, store
        os.environ.pop("BRAIN_DIR", None)

    def test_export_wiki_emits_entities_index(self, brain, tmp_path):
        tmp, store = brain
        vault = tmp_path / "vault"
        result = runner.invoke(app, ["obsidian-export", str(vault), "--wiki"])
        assert result.exit_code == 0
        assert (vault / "entities" / "index.md").exists()
