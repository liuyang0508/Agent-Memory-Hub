"""Tests for the storage axis (hot/warm/cold tier).

Tier is a *derived* index (plan §2.5.3 axis B): computed from age + confidence +
archived-location, never stored in the md frontmatter. cold-archiving (moving
files) is the existing `batch-archive` mechanism; rebalance only recomputes and
persists the derived tier into sqlite.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_brain.interfaces.cli import app
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.contracts.memory_item import MemoryItem, MemoryType

runner = CliRunner()
NOW = datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)


def _mk(suffix: str, *, confidence=0.7, created=None, project="hub") -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260101-000000-tier{suffix}",
        type=MemoryType.fact,
        created_at=created or NOW,
        title=f"item {suffix}",
        summary=f"summary {suffix}",
        project=project,
        confidence=confidence,
    )


# ── Pure classification ──


class TestClassifyTier:
    def test_recent_high_enough_is_hot(self):
        from agent_brain.memory.governance.tiering import Tier, classify_tier

        t = classify_tier(confidence=0.7, last_accessed=NOW - timedelta(days=5),
                           created_at=NOW - timedelta(days=400), now=NOW)
        assert t == Tier.hot

    def test_mid_age_is_warm(self):
        from agent_brain.memory.governance.tiering import Tier, classify_tier

        t = classify_tier(confidence=0.7, last_accessed=NOW - timedelta(days=90),
                           created_at=NOW - timedelta(days=400), now=NOW)
        assert t == Tier.warm

    def test_old_is_cold(self):
        from agent_brain.memory.governance.tiering import Tier, classify_tier

        t = classify_tier(confidence=0.7, last_accessed=NOW - timedelta(days=200),
                           created_at=NOW - timedelta(days=400), now=NOW)
        assert t == Tier.cold

    def test_low_confidence_is_cold_even_if_recent(self):
        from agent_brain.memory.governance.tiering import Tier, classify_tier

        t = classify_tier(confidence=0.2, last_accessed=NOW - timedelta(days=1),
                           created_at=NOW, now=NOW)
        assert t == Tier.cold

    def test_high_confidence_rescues_warm_to_hot(self):
        from agent_brain.memory.governance.tiering import Tier, classify_tier

        t = classify_tier(confidence=0.9, last_accessed=NOW - timedelta(days=90),
                           created_at=NOW - timedelta(days=400), now=NOW)
        assert t == Tier.hot

    def test_old_dominates_high_confidence(self):
        from agent_brain.memory.governance.tiering import Tier, classify_tier

        # Not accessed in 200 days → cold, even if confidence is high.
        t = classify_tier(confidence=0.95, last_accessed=NOW - timedelta(days=200),
                           created_at=NOW - timedelta(days=400), now=NOW)
        assert t == Tier.cold

    def test_archived_forces_cold(self):
        from agent_brain.memory.governance.tiering import Tier, classify_tier

        t = classify_tier(confidence=0.95, last_accessed=NOW - timedelta(days=1),
                           created_at=NOW, now=NOW, archived=True)
        assert t == Tier.cold

    def test_never_accessed_falls_back_to_created_at(self):
        from agent_brain.memory.governance.tiering import Tier, classify_tier

        # last_accessed=None, created 5 days ago → treated as recent → hot
        t = classify_tier(confidence=0.7, last_accessed=None,
                           created_at=NOW - timedelta(days=5), now=NOW)
        assert t == Tier.hot


# ── Scan over a store ──


class TestScanTiers:
    def test_scan_classifies_each_item(self, tmp_brain_dir):
        from agent_brain.memory.governance.tiering import Tier, scan_tiers

        store = ItemsStore(tmp_brain_dir / "items")
        store.write(_mk("hot", confidence=0.7, created=NOW - timedelta(days=2)), "b")
        store.write(_mk("old", confidence=0.7, created=NOW - timedelta(days=300)), "b")
        result = dict((item.id, tier) for item, tier in scan_tiers(store.items_dir, now=NOW))
        assert result["mem-20260101-000000-tierhot"] == Tier.hot
        assert result["mem-20260101-000000-tierold"] == Tier.cold

    def test_items_in_archived_subdir_are_cold(self, tmp_brain_dir):
        from agent_brain.memory.governance.tiering import Tier, scan_tiers

        store = ItemsStore(tmp_brain_dir / "items")
        archived = store.items_dir / "archived"
        archived.mkdir()
        # A freshly-created item placed under archived/ must still classify cold.
        item = _mk("arc", confidence=0.95, created=NOW)
        archived_store = ItemsStore(archived)
        archived_store.write(item, "b")
        result = dict((i.id, t) for i, t in scan_tiers(store.items_dir, now=NOW))
        assert result["mem-20260101-000000-tierarc"] == Tier.cold

    def test_distribution_counts(self, tmp_brain_dir):
        from agent_brain.memory.governance.tiering import Tier, scan_tiers, tier_distribution

        store = ItemsStore(tmp_brain_dir / "items")
        store.write(_mk("a", created=NOW - timedelta(days=2)), "b")
        store.write(_mk("b", created=NOW - timedelta(days=90)), "b")
        store.write(_mk("c", created=NOW - timedelta(days=300)), "b")
        dist = tier_distribution(t for _, t in scan_tiers(store.items_dir, now=NOW))
        assert dist[Tier.hot] == 1
        assert dist[Tier.warm] == 1
        assert dist[Tier.cold] == 1


# ── sqlite persistence ──


class TestIndexTier:
    def _index(self, tmp_brain_dir):
        from agent_brain.platform.indexing.index import HubIndex

        return HubIndex(db_path=tmp_brain_dir / "index.db")

    def test_update_and_read_tier(self, tmp_brain_dir):
        idx = self._index(tmp_brain_dir)
        item = _mk("x", confidence=0.7)
        idx.upsert(item, "body", embedding=None)
        idx.update_tier(item.id, "warm")
        counts = idx.tier_counts()
        assert counts.get("warm") == 1
        idx.close()

    def test_tier_defaults_present_after_upsert(self, tmp_brain_dir):
        # Column exists and new rows have a non-crashing default.
        idx = self._index(tmp_brain_dir)
        item = _mk("y")
        idx.upsert(item, "body", embedding=None)
        counts = idx.tier_counts()
        assert isinstance(counts, dict)
        idx.close()


# ── rebalance ──


class TestRebalance:
    def test_dry_run_reports_distribution_without_index(self, tmp_brain_dir):
        from agent_brain.memory.governance.tiering import Tier, rebalance

        store = ItemsStore(tmp_brain_dir / "items")
        store.write(_mk("a", created=NOW - timedelta(days=2)), "b")
        store.write(_mk("c", created=NOW - timedelta(days=300)), "b")
        report = rebalance(store, index=None, apply=False, now=NOW)
        assert report.applied == 0
        assert report.distribution[Tier.hot] == 1
        assert report.distribution[Tier.cold] == 1

    def test_apply_persists_tier_to_index(self, tmp_brain_dir):
        from agent_brain.platform.indexing.index import HubIndex
        from agent_brain.memory.governance.tiering import rebalance

        store = ItemsStore(tmp_brain_dir / "items")
        idx = HubIndex(db_path=tmp_brain_dir / "index.db")
        item_hot = _mk("a", created=NOW - timedelta(days=2))
        item_cold = _mk("c", created=NOW - timedelta(days=300))
        for it in (item_hot, item_cold):
            store.write(it, "b")
            idx.upsert(it, "b", embedding=None)
        report = rebalance(store, index=idx, apply=True, now=NOW)
        assert report.applied == 2
        counts = idx.tier_counts()
        assert counts.get("hot") == 1
        assert counts.get("cold") == 1
        idx.close()


# ── CLI ──


class TestTierCLI:
    def test_tier_cli_commands_are_split_from_subapps(self):
        from agent_brain.interfaces.cli.commands import subapps as subapps_mod
        from agent_brain.interfaces.cli.commands import tier as tier_mod

        assert hasattr(tier_mod, "tier_show")
        assert hasattr(tier_mod, "tier_rebalance")
        assert "tier_show" not in subapps_mod.__all__
        assert "tier_rebalance" not in subapps_mod.__all__

    @pytest.fixture
    def brain(self, tmp_brain_dir: Path):
        os.environ["BRAIN_DIR"] = str(tmp_brain_dir)
        store = ItemsStore(tmp_brain_dir / "items")
        store.write(_mk("a", created=NOW - timedelta(days=2)), "b")
        store.write(_mk("c", created=NOW - timedelta(days=300)), "b")
        yield tmp_brain_dir, store
        os.environ.pop("BRAIN_DIR", None)

    def test_tier_show_prints_distribution(self, brain):
        result = runner.invoke(app, ["tier", "show"])
        assert result.exit_code == 0
        out = result.output.lower()
        assert "hot" in out and "cold" in out

    def test_tier_rebalance_dry_run(self, brain):
        result = runner.invoke(app, ["tier", "rebalance"])
        assert result.exit_code == 0
        assert "dry-run" in result.output.lower()
