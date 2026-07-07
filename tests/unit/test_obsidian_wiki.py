"""Tests for the Obsidian wiki layer (Karpathy LLM-Wiki pattern).

Generates human-browsable overview pages — index.md (what's in the pool),
log.md (recent maintenance), health/report.md (structural checks) — on top of
the existing per-item Obsidian export. Builders are pure (content in, markdown
out) so they unit-test without touching the filesystem.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_brain.interfaces.cli import app
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Refs

runner = CliRunner()
NOW = datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)


def _mk(
    suffix: str,
    *,
    type=MemoryType.fact,
    project="hub",
    tags=None,
    confidence=0.8,
    abstraction=None,
    created=None,
    mems=None,
    urls=None,
) -> MemoryItem:
    kwargs = dict(
        id=f"mem-20260101-000000-wiki{suffix}",
        type=type,
        created_at=created or NOW,
        title=f"item {suffix}",
        summary=f"summary {suffix}",
        tags=tags if tags is not None else ["retrieval"],
        project=project,
        confidence=confidence,
        refs=Refs(mems=mems or [], urls=urls or []),
    )
    if abstraction is not None:
        kwargs["abstraction"] = abstraction
    return MemoryItem(**kwargs)


# ── index.md ──


class TestBuildIndex:
    def test_index_has_total_and_type_breakdown(self):
        from agent_brain.memory.evidence.integrations.obsidian_wiki import build_index

        items = [
            (_mk("a", type=MemoryType.fact), "b"),
            (_mk("b", type=MemoryType.fact), "b"),
            (_mk("c", type=MemoryType.decision), "b"),
        ]
        md = build_index(items, now=NOW)
        assert "3" in md  # total
        assert "fact" in md and "decision" in md
        assert "## By type" in md

    def test_index_has_abstraction_section(self):
        from agent_brain.memory.evidence.integrations.obsidian_wiki import build_index

        items = [
            (_mk("a", abstraction="L0"), "b"),
            (_mk("b", abstraction="L1"), "b"),
        ]
        md = build_index(items, now=NOW)
        assert "abstraction" in md.lower()
        assert "L0" in md and "L1" in md

    def test_index_lists_recent_titles(self):
        from agent_brain.memory.evidence.integrations.obsidian_wiki import build_index

        items = [(_mk("a"), "b")]
        md = build_index(items, now=NOW)
        assert "item a" in md

    def test_index_links_recent_items_by_exported_item_id(self):
        from agent_brain.memory.evidence.integrations.obsidian_wiki import build_index

        item = _mk("a")
        md = build_index([(item, "b")], now=NOW)
        assert f"[[{item.id}|{item.title}]]" in md

    def test_index_empty_pool_does_not_crash(self):
        from agent_brain.memory.evidence.integrations.obsidian_wiki import build_index

        md = build_index([], now=NOW)
        assert isinstance(md, str) and len(md) > 0


# ── log.md ──


class TestBuildLog:
    def test_log_lists_recent_items_newest_first(self):
        from agent_brain.memory.evidence.integrations.obsidian_wiki import build_log

        old = _mk("old", created=NOW - timedelta(days=10))
        new = _mk("new", created=NOW - timedelta(days=1))
        md = build_log([(old, "b"), (new, "b")], now=NOW)
        # newest item should appear before the older one
        assert md.index(new.id) < md.index(old.id)

    def test_log_respects_limit(self):
        from agent_brain.memory.evidence.integrations.obsidian_wiki import build_log

        items = [(_mk(str(i), created=NOW - timedelta(days=i)), "b") for i in range(10)]
        md = build_log(items, now=NOW, limit=3)
        present = sum(1 for it, _ in items if it.id in md)
        assert present == 3


# ── health/report.md ──


class TestBuildHealth:
    def test_island_item_flagged(self):
        from agent_brain.memory.evidence.integrations.obsidian_wiki import build_health

        # An item with no outbound refs and nobody pointing to it = island.
        island = _mk("island", mems=[], urls=[])
        # A linked pair so not everything is an island.
        a = _mk("a", mems=["mem-20260101-000000-wikib"])
        b = _mk("b")
        md = build_health([(island, "x"), (a, "x"), (b, "x")], now=NOW)
        assert "island" in md.lower()
        assert island.id in md

    def test_missing_source_flagged(self):
        from agent_brain.memory.evidence.integrations.obsidian_wiki import build_health

        # fact with neither urls nor mems = unsourced claim
        unsourced = _mk("nosrc", type=MemoryType.fact, mems=[], urls=[])
        md = build_health([(unsourced, "x")], now=NOW)
        assert "source" in md.lower()
        assert unsourced.id in md

    def test_stale_item_flagged(self):
        from agent_brain.memory.evidence.integrations.obsidian_wiki import build_health

        stale = _mk("stale", confidence=0.2, created=NOW - timedelta(days=400),
                    urls=["http://x"])
        md = build_health([(stale, "x")], now=NOW)
        assert "stale" in md.lower()
        assert stale.id in md


# ── write_wiki_pages ──


class TestWriteWikiPages:
    def test_writes_three_pages(self, tmp_path):
        from agent_brain.memory.evidence.integrations.obsidian_wiki import write_wiki_pages

        items = [(_mk("a"), "b"), (_mk("b", mems=["mem-20260101-000000-wikia"]), "b")]
        vault = tmp_path / "vault"
        paths = write_wiki_pages(items, vault, now=NOW)
        names = {p.name for p in paths}
        assert "index.md" in names
        assert "log.md" in names
        assert (vault / "index.md").exists()
        assert (vault / "log.md").exists()
        assert (vault / "health" / "report.md").exists()


# ── CLI ──


class TestObsidianExportWikiFlag:
    @pytest.fixture
    def brain(self, tmp_brain_dir: Path):
        os.environ["BRAIN_DIR"] = str(tmp_brain_dir)
        store = ItemsStore(tmp_brain_dir / "items")
        store.write(_mk("a"), "body a")
        store.write(_mk("b", mems=["mem-20260101-000000-wikia"]), "body b")
        yield tmp_brain_dir, store
        os.environ.pop("BRAIN_DIR", None)

    def test_export_with_wiki_flag_generates_index(self, brain, tmp_path):
        tmp, store = brain
        vault = tmp_path / "vault"
        result = runner.invoke(app, ["obsidian-export", str(vault), "--wiki"])
        assert result.exit_code == 0
        assert (vault / "index.md").exists()
