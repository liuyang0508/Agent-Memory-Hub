"""Tests for the abstraction axis (L0→L1 consolidation).

Covers:
  - schema: new `abstraction` field (default L0), backward compat with 0.2/0.3 items
  - engine: grouping L0 facts by (project, tag), confidence threshold, greedy dedupe
  - engine: building a non-destructive L1 fact that references its sources
  - CLI: `memory consolidate` dry-run (default) vs --apply
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from agent_brain.interfaces.cli import app
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.contracts.memory_item import MemoryItem, MemoryType

runner = CliRunner()


def _mk(
    suffix: str,
    *,
    type=MemoryType.fact,
    project="hub",
    tags=None,
    confidence=0.8,
    abstraction=None,
) -> MemoryItem:
    kwargs = dict(
        id=f"mem-20260529-100000-itm{suffix}",
        type=type,
        created_at=datetime(2026, 5, 29, 10, 0, 0, tzinfo=timezone.utc),
        title=f"raw fact {suffix}",
        summary=f"summary {suffix}",
        tags=tags if tags is not None else ["retrieval"],
        project=project,
        confidence=confidence,
    )
    if abstraction is not None:
        kwargs["abstraction"] = abstraction
    return MemoryItem(**kwargs)


# ── Schema: abstraction field ──


class TestAbstractionSchema:
    def test_new_item_defaults_to_l0(self):
        item = _mk("00")
        assert item.abstraction == "L0"
        assert item.schema_version == "1"

    def test_abstraction_can_be_set_to_l1(self):
        item = _mk("01", abstraction="L1")
        assert item.abstraction == "L1"

    def test_invalid_abstraction_rejected(self):
        with pytest.raises(ValidationError):
            _mk("02", abstraction="L9")

    def test_historical_item_without_abstraction_defaults_l0(self):
        """A 0.2 item written before the abstraction axis must still load."""
        historical = {
            "id": "mem-20260515-142857-old",
            "schema_version": "0.2",
            "type": "fact",
            "created_at": "2026-05-15T14:28:57+0800",
            "title": "old fact",
            "summary": "written before abstraction axis",
        }
        item = MemoryItem.model_validate(historical)
        assert item.abstraction == "L0"
        assert item.schema_version == "0.2"


# ── Engine: grouping ──


class TestFindGroups:
    def test_three_l0_facts_same_project_tag_form_one_group(self):
        from agent_brain.memory.governance.consolidation import find_consolidation_groups

        items = [(_mk(f"1{i}", tags=["retrieval"]), "body") for i in range(3)]
        groups = find_consolidation_groups(items, min_group=3, min_confidence=0.6)
        assert len(groups) == 1
        assert groups[0].project == "hub"
        assert groups[0].tag == "retrieval"
        assert len(groups[0].source_ids) == 3

    def test_two_items_below_min_group_yields_nothing(self):
        from agent_brain.memory.governance.consolidation import find_consolidation_groups

        items = [(_mk(f"2{i}", tags=["retrieval"]), "body") for i in range(2)]
        groups = find_consolidation_groups(items, min_group=3)
        assert groups == []

    def test_low_confidence_items_excluded(self):
        from agent_brain.memory.governance.consolidation import find_consolidation_groups

        items = [(_mk(f"3{i}", tags=["retrieval"], confidence=0.3), "b") for i in range(3)]
        groups = find_consolidation_groups(items, min_group=3, min_confidence=0.6)
        assert groups == []

    def test_non_fact_types_excluded(self):
        from agent_brain.memory.governance.consolidation import find_consolidation_groups

        items = [(_mk(f"4{i}", type=MemoryType.episode, tags=["retrieval"]), "b") for i in range(3)]
        groups = find_consolidation_groups(items, min_group=3)
        assert groups == []

    def test_already_l1_items_excluded(self):
        from agent_brain.memory.governance.consolidation import find_consolidation_groups

        items = [(_mk(f"5{i}", tags=["retrieval"], abstraction="L1"), "b") for i in range(3)]
        groups = find_consolidation_groups(items, min_group=3)
        assert groups == []

    def test_items_without_project_excluded(self):
        from agent_brain.memory.governance.consolidation import find_consolidation_groups

        items = [(_mk(f"6{i}", project=None, tags=["retrieval"]), "b") for i in range(3)]
        groups = find_consolidation_groups(items, min_group=3)
        assert groups == []

    def test_greedy_dedupe_each_item_used_once(self):
        """An item sharing two tags must land in only one group."""
        from agent_brain.memory.governance.consolidation import find_consolidation_groups

        # 3 items share tag 'a'; 3 items share tag 'b'; one item is in both.
        items = [
            (_mk("70", tags=["a"]), "b"),
            (_mk("71", tags=["a"]), "b"),
            (_mk("72", tags=["a", "b"]), "b"),
            (_mk("73", tags=["b"]), "b"),
            (_mk("74", tags=["b"]), "b"),
        ]
        groups = find_consolidation_groups(items, min_group=3)
        all_used = [sid for g in groups for sid in g.source_ids]
        assert len(all_used) == len(set(all_used))  # no item counted twice

    def test_tag_filter_limits_to_one_tag(self):
        from agent_brain.memory.governance.consolidation import find_consolidation_groups

        items = [(_mk(f"8{i}", tags=["a"]), "b") for i in range(3)] + [
            (_mk(f"9{i}", tags=["b"]), "b") for i in range(3)
        ]
        groups = find_consolidation_groups(items, min_group=3, tag="a")
        assert len(groups) == 1
        assert groups[0].tag == "a"


# ── Engine: building the L1 item ──


class TestBuildConsolidated:
    def test_consolidation_builder_is_split_and_reexported(self):
        from agent_brain.memory.governance import consolidation
        from agent_brain.memory.governance.consolidation_builder import build_consolidated_item

        assert consolidation.build_consolidated_item is build_consolidated_item

    def _group(self):
        from agent_brain.memory.governance.consolidation import find_consolidation_groups

        items = [(_mk(f"1{i}", tags=["retrieval"], confidence=0.8), "raw body") for i in range(3)]
        return find_consolidation_groups(items, min_group=3)[0]

    def test_built_item_is_l1_fact(self):
        from agent_brain.memory.governance.consolidation import build_consolidated_item

        item, body = build_consolidated_item(self._group())
        assert item.type == MemoryType.fact
        assert item.abstraction == "L1"
        assert item.schema_version == "1"

    def test_built_item_references_sources(self):
        from agent_brain.memory.governance.consolidation import build_consolidated_item

        group = self._group()
        item, body = build_consolidated_item(group)
        assert set(item.refs.mems) == set(group.source_ids)

    def test_built_item_id_is_valid(self):
        from agent_brain.memory.governance.consolidation import build_consolidated_item

        # Must satisfy MemoryItem's id pattern (round-trips through validation).
        item, body = build_consolidated_item(self._group())
        reparsed = MemoryItem.model_validate(item.model_dump(mode="json"))
        assert reparsed.id == item.id

    def test_built_item_confidence_is_mean_of_sources(self):
        from agent_brain.memory.governance.consolidation import build_consolidated_item, find_consolidation_groups

        items = [
            (_mk("a0", tags=["retrieval"], confidence=0.6), "b"),
            (_mk("a1", tags=["retrieval"], confidence=0.8), "b"),
            (_mk("a2", tags=["retrieval"], confidence=1.0), "b"),
        ]
        group = find_consolidation_groups(items, min_group=3)[0]
        item, _ = build_consolidated_item(group)
        assert item.confidence == pytest.approx(0.8, abs=0.01)

    def test_body_mentions_each_source(self):
        from agent_brain.memory.governance.consolidation import build_consolidated_item

        group = self._group()
        _, body = build_consolidated_item(group)
        for src, _b in group.sources:
            assert src.id in body


# ── Engine: consolidate() over a store ──


class TestConsolidateStore:
    def _populate(self, brain: Path, n=3, tags=("retrieval",)):
        store = ItemsStore(brain / "items")
        for i in range(n):
            store.write(_mk(f"c{i}", tags=list(tags), confidence=0.8), f"raw body {i}")
        return store

    def test_dry_run_writes_nothing(self, tmp_brain_dir):
        from agent_brain.memory.governance.consolidation import consolidate

        store = self._populate(tmp_brain_dir)
        before = len(list(store.items_dir.glob("*.md")))
        report = consolidate(store, min_group=3, apply=False)
        after = len(list(store.items_dir.glob("*.md")))
        assert after == before
        assert len(report.groups) == 1
        assert report.created == []

    def test_apply_writes_l1_and_keeps_sources(self, tmp_brain_dir):
        from agent_brain.memory.governance.consolidation import consolidate

        store = self._populate(tmp_brain_dir)
        source_ids = [p.stem for p in store.items_dir.glob("*.md")]
        report = consolidate(store, min_group=3, apply=True)
        # sources untouched
        for sid in source_ids:
            assert (store.items_dir / f"{sid}.md").exists()
        # exactly one new L1 item created
        assert len(report.created) == 1
        new_item, _ = store.get(report.created[0].id)
        assert new_item.abstraction == "L1"
        assert new_item.id not in source_ids


# ── CLI ──


class TestConsolidateCLI:
    @pytest.fixture
    def brain_with_facts(self, tmp_brain_dir: Path):
        os.environ["BRAIN_DIR"] = str(tmp_brain_dir)
        store = ItemsStore(tmp_brain_dir / "items")
        for i in range(3):
            store.write(_mk(f"d{i}", tags=["retrieval"], confidence=0.8), f"raw {i}")
        yield tmp_brain_dir, store
        os.environ.pop("BRAIN_DIR", None)

    def test_cli_dry_run_default_writes_nothing(self, brain_with_facts):
        tmp, store = brain_with_facts
        before = len(list(store.items_dir.glob("*.md")))
        result = runner.invoke(app, ["consolidate"])
        assert result.exit_code == 0
        assert len(list(store.items_dir.glob("*.md"))) == before
        assert "dry-run" in result.output.lower()

    def test_cli_apply_creates_l1(self, brain_with_facts):
        tmp, store = brain_with_facts
        before = len(list(store.items_dir.glob("*.md")))
        result = runner.invoke(app, ["consolidate", "--apply"])
        assert result.exit_code == 0
        assert len(list(store.items_dir.glob("*.md"))) == before + 1
        l1 = [
            i
            for i, _ in (store.get(p.stem) for p in store.items_dir.glob("*.md"))
            if i.abstraction == "L1"
        ]
        assert len(l1) == 1
