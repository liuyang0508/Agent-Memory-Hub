"""Tests for MCP drift/evolve tools and multi-tenant isolation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.recall.retrieval import Retriever, SearchFilter
from agent_brain.contracts.memory_item import MemoryItem, MemoryType

_DIM = 8


def _item(suffix: str, **kw) -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260528-100000-{suffix}",
        type=kw.pop("type", MemoryType.fact),
        created_at=kw.pop("created_at", datetime.now(timezone.utc)),
        title=kw.pop("title", f"Item {suffix}"),
        summary=kw.pop("summary", f"Summary {suffix}"),
        project=kw.pop("project", "testproj"),
        tags=kw.pop("tags", []),
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


# ── Drift detection via core (MCP-equivalent) ──


class TestDriftCheckCore:
    def test_drift_clean_brain(self, tmp_brain_dir: Path):
        from agent_brain.memory.governance.drift import DriftDetector
        a = _item("fresh", created_at=datetime.now(timezone.utc) - timedelta(days=5))
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        store.write(a, "Recent fact, no issues.")
        detector = DriftDetector(items_store=store, staleness_days=180)
        report = detector.detect()
        assert report.clean is True
        assert report.total_findings == 0

    def test_drift_stale_detected(self, tmp_brain_dir: Path):
        from agent_brain.memory.governance.drift import DriftDetector
        old = datetime.now(timezone.utc) - timedelta(days=200)
        a = _item("stale", created_at=old)
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        store.write(a, "Very old fact.")
        detector = DriftDetector(items_store=store, staleness_days=180)
        report = detector.detect()
        assert report.stale >= 1
        assert not report.clean


# ── Hermes hub_drift ──


class TestHermesDrift:
    def test_hub_drift_returns_summary(self, tmp_brain_dir: Path):
        old = datetime.now(timezone.utc) - timedelta(days=200)
        a = _item("hd-stale", created_at=old)
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        store.write(a, "Old fact body.")
        from agent_brain.agent_integrations.hermes.provider import hub_drift
        with _patch_hermes(tmp_brain_dir):
            result = hub_drift(staleness_days=180)
        assert result["scanned_items"] >= 1
        assert result["stale"] >= 1
        assert result["clean"] is False

    def test_hub_drift_clean(self, tmp_brain_dir: Path):
        a = _item("hd-fresh")
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        store.write(a, "Fresh fact.")
        from agent_brain.agent_integrations.hermes.provider import hub_drift
        with _patch_hermes(tmp_brain_dir):
            result = hub_drift()
        assert result["clean"] is True


# ── Hermes hub_evolve ──


class TestHermesEvolve:
    def test_hub_evolve_dry_run(self, tmp_brain_dir: Path):
        old = datetime.now(timezone.utc) - timedelta(days=40)
        a = _item("he-sig", type=MemoryType.signal, created_at=old)
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        store.write(a, "Old signal.")
        from agent_brain.agent_integrations.hermes.provider import hub_evolve
        with _patch_hermes(tmp_brain_dir):
            result = hub_evolve(apply=False)
        assert result["scanned_items"] >= 1
        assert result["executed"] == 0

    def test_hub_evolve_apply(self, tmp_brain_dir: Path):
        old = datetime.now(timezone.utc) - timedelta(days=40)
        a = _item("he-apply", type=MemoryType.signal, created_at=old)
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        idx = _seed(tmp_brain_dir, [(a, "Old signal body.")])
        from agent_brain.agent_integrations.hermes.provider import hub_evolve
        with _patch_hermes(tmp_brain_dir):
            result = hub_evolve(apply=True)
        assert result["executed"] >= 1


# ── Multi-tenant isolation ──


class TestMultiTenantIsolation:
    def test_tenant_id_persisted_in_index(self, tmp_brain_dir: Path):
        a = _item("t-a", tenant_id="tenant-1")
        idx = _seed(tmp_brain_dir, [(a, "body a")])
        row = idx.connection.execute(
            "SELECT tenant_id FROM items_meta WHERE id = ?", (a.id,)
        ).fetchone()
        assert row[0] == "tenant-1"

    def test_filter_by_tenant(self, tmp_brain_dir: Path):
        a = _item("t1", tenant_id="alpha", title="searchable alpha topic")
        b = _item("t2", tenant_id="beta", title="searchable beta topic")
        idx = _seed(tmp_brain_dir, [
            (a, "searchable alpha body"),
            (b, "searchable beta body"),
        ])
        alpha_ids = idx.filter_ids(tenant_id="alpha")
        assert alpha_ids == {a.id}
        beta_ids = idx.filter_ids(tenant_id="beta")
        assert beta_ids == {b.id}

    def test_search_filter_tenant(self, tmp_brain_dir: Path):
        a = _item("sf-t1", tenant_id="org1", title="shared keyword topic")
        b = _item("sf-t2", tenant_id="org2", title="shared keyword topic")
        idx = _seed(tmp_brain_dir, [
            (a, "shared keyword content org1"),
            (b, "shared keyword content org2"),
        ])
        emb = HashingEmbedder(dim=_DIM)
        r = Retriever(index=idx, embedder=emb, apply_decay=False, record_access=False)
        sf = SearchFilter(tenant_id="org1")
        hits = r.search("shared keyword", top_k=10, filters=sf)
        hit_ids = {h.id for h in hits}
        assert a.id in hit_ids
        assert b.id not in hit_ids

    def test_no_tenant_filter_returns_all(self, tmp_brain_dir: Path):
        a = _item("nt-1", tenant_id="org1", title="keyword topic")
        b = _item("nt-2", tenant_id="org2", title="keyword topic")
        idx = _seed(tmp_brain_dir, [
            (a, "keyword body one"),
            (b, "keyword body two"),
        ])
        emb = HashingEmbedder(dim=_DIM)
        r = Retriever(index=idx, embedder=emb, apply_decay=False, record_access=False)
        hits = r.search("keyword", top_k=10)
        hit_ids = {h.id for h in hits}
        assert a.id in hit_ids
        assert b.id in hit_ids

    def test_null_tenant_not_matched(self, tmp_brain_dir: Path):
        a = _item("null-t", tenant_id=None, title="no tenant item")
        b = _item("has-t", tenant_id="org1", title="has tenant item")
        idx = _seed(tmp_brain_dir, [
            (a, "no tenant body"),
            (b, "has tenant body"),
        ])
        org1_ids = idx.filter_ids(tenant_id="org1")
        assert org1_ids == {b.id}


# ── Hermes tenant isolation ──


class TestHermesTenantIsolation:
    def test_hub_remember_with_tenant(self, tmp_brain_dir: Path):
        from agent_brain.agent_integrations.hermes.provider import hub_remember
        with _patch_hermes(tmp_brain_dir):
            result = hub_remember(
                content="tenant content", title="tenant mem",
                tenant_id="org-x",
            )
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        for item, _ in store.iter_all():
            if item.id == result["id"]:
                assert item.tenant_id == "org-x"
                break

    def test_hub_search_with_tenant(self, tmp_brain_dir: Path):
        a = _item("hs-t1", tenant_id="orgA", title="searchable hermes")
        b = _item("hs-t2", tenant_id="orgB", title="searchable hermes")
        _seed(tmp_brain_dir, [
            (a, "searchable hermes body orgA"),
            (b, "searchable hermes body orgB"),
        ])
        from agent_brain.agent_integrations.hermes.provider import hub_search
        with _patch_hermes(tmp_brain_dir):
            results = hub_search("searchable hermes", tenant_id="orgA")
        result_ids = {r["id"] for r in results}
        assert a.id in result_ids
        assert b.id not in result_ids
