"""Tests for governance pipeline exposed via MCP and Hermes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.contracts.memory_item import MemoryItem, MemoryType

_DIM = 8


def _item(suffix: str, **kw) -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260528-200000-{suffix}",
        type=kw.pop("type", MemoryType.fact),
        created_at=kw.pop("created_at", datetime.now(timezone.utc)),
        title=kw.pop("title", f"Item {suffix}"),
        summary=kw.pop("summary", f"Summary for {suffix} with enough characters"),
        project=kw.pop("project", "govproj"),
        tags=kw.pop("tags", ["test"]),
        tenant_id=kw.pop("tenant_id", None),
    )


def _seed(brain_dir: Path, items: list[tuple[MemoryItem, str]]) -> HubIndex:
    store = ItemsStore(items_dir=brain_dir / "items")
    idx = HubIndex(db_path=brain_dir / "index.db", embedding_dim=_DIM)
    emb = HashingEmbedder(dim=_DIM)
    for item, body in items:
        store.write(item, body)
        idx.upsert(item, body, embedding=emb.embed(f"{item.title} {body}"))
    return idx


def _patch_hermes(brain_dir: Path):
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(patch("agent_brain.agent_integrations.hermes.provider._brain_dir", return_value=brain_dir))
    stack.enter_context(patch(
        "agent_brain.agent_integrations.hermes.provider.get_default_embedder",
        return_value=HashingEmbedder(dim=_DIM),
    ))
    return stack


class TestGovernancePipelineCore:
    def test_clean_brain(self, tmp_brain_dir: Path):
        a = _item("gov-clean")
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        store.write(a, "Good content with enough detail.")
        from agent_brain.memory.governance.pipeline import GovernancePipeline
        pipeline = GovernancePipeline(items_store=store)
        report = pipeline.run()
        assert report.scanned_items == 1
        assert report.healthy is True

    def test_duplicate_detected(self, tmp_brain_dir: Path):
        a = _item("gov-dup-a", title="Same title here", summary="Same summary content")
        b = _item("gov-dup-b", title="Same title here", summary="Same summary content")
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        store.write(a, "body a")
        store.write(b, "body b")
        from agent_brain.memory.governance.pipeline import GovernancePipeline
        pipeline = GovernancePipeline(items_store=store)
        report = pipeline.run()
        assert report.duplicates >= 1

    def test_expired_detected(self, tmp_brain_dir: Path):
        old = datetime.now(timezone.utc) - timedelta(days=100)
        a = _item("gov-exp", created_at=old)
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        store.write(a, "Old content.")
        from agent_brain.memory.governance.pipeline import GovernancePipeline
        pipeline = GovernancePipeline(items_store=store, ttl_days=90)
        report = pipeline.run()
        assert report.expired >= 1

    def test_noise_detected(self, tmp_brain_dir: Path):
        a = _item("gov-noise", summary="short")
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        store.write(a, "body")
        from agent_brain.memory.governance.pipeline import GovernancePipeline
        pipeline = GovernancePipeline(items_store=store)
        report = pipeline.run()
        assert report.noise >= 1

    def test_low_quality_no_tags(self, tmp_brain_dir: Path):
        a = _item("gov-notag", tags=[])
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        store.write(a, "body")
        from agent_brain.memory.governance.pipeline import GovernancePipeline
        pipeline = GovernancePipeline(items_store=store)
        report = pipeline.run()
        assert report.low_quality >= 1


class TestHermesGovern:
    def test_hub_govern_clean(self, tmp_brain_dir: Path):
        a = _item("hg-clean")
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        store.write(a, "Good content.")
        from agent_brain.agent_integrations.hermes.provider import hub_govern
        with _patch_hermes(tmp_brain_dir):
            result = hub_govern()
        assert result["scanned_items"] == 1
        assert result["healthy"] is True

    def test_hub_govern_finds_issues(self, tmp_brain_dir: Path):
        old = datetime.now(timezone.utc) - timedelta(days=100)
        a = _item("hg-exp", created_at=old, tags=[])
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        store.write(a, "Old content.")
        from agent_brain.agent_integrations.hermes.provider import hub_govern
        with _patch_hermes(tmp_brain_dir):
            result = hub_govern(ttl_days=90)
        assert result["total_issues"] >= 1
        assert result["healthy"] is True or result["healthy"] is False
        assert len(result["issues"]) >= 1
        issue = result["issues"][0]
        assert "item_id" in issue
        assert "issue_type" in issue
        assert "suggestion" in issue

    def test_hub_govern_registered(self):
        from agent_brain.agent_integrations.hermes.provider import register_hermes_tools
        registered = []

        class FakeMCP:
            def tool(self):
                def decorator(fn):
                    registered.append(fn.__name__)
                    return fn
                return decorator

        register_hermes_tools(FakeMCP())
        assert "hub_govern" in registered


class TestHermesStats:
    def test_hub_stats_reports_health_summary(self, tmp_brain_dir: Path):
        a = _item("hs-a", type=MemoryType.fact, tags=["stats", "alpha"], project="stats-proj")
        b = _item("hs-b", type=MemoryType.decision, tags=["stats"], project="stats-proj")
        _seed(tmp_brain_dir, [(a, "Stats fact body."), (b, "Stats decision body.")])

        from agent_brain.agent_integrations.hermes.provider import hub_stats

        with _patch_hermes(tmp_brain_dir):
            result = hub_stats(project="stats-proj")

        assert result["total_items"] == 2
        assert result["type_counts"]["fact"] == 1
        assert result["type_counts"]["decision"] == 1
        assert result["project_counts"]["stats-proj"] == 2
        assert result["tag_counts"]["stats"] == 2
        assert result["health_grade"] in {"A", "B", "C", "D", "F"}
        assert isinstance(result["healthy"], bool)


def test_mcp_governance_batch_helpers_are_split_and_reexported():
    from agent_brain.interfaces.mcp.tools import governance
    from agent_brain.interfaces.mcp.tools.governance_batch import batch_archive, batch_confirm

    assert governance.batch_confirm is batch_confirm
    assert governance.batch_archive is batch_archive
