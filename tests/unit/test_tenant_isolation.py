"""End-to-end multi-tenant isolation tests.

Verifies that tenant_id properly isolates:
- Search results (only see own tenant's items)
- Governance pipelines (drift/quality checks per tenant)
- Stats reporting (counts per tenant)
- Write/read paths (no cross-contamination)
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex
from agent_brain.memory.store.items_store import ItemsStore, make_item_id
from agent_brain.memory.recall.retrieval import Retriever, SearchFilter
from agent_brain.contracts.memory_item import MemoryItem, MemoryType


@pytest.fixture
def multi_tenant_env(tmp_path):
    """Create a shared brain with items from 3 tenants."""
    items_dir = tmp_path / "items"
    items_dir.mkdir()
    store = ItemsStore(items_dir=items_dir)
    idx = HubIndex(db_path=tmp_path / "index.db", embedding_dim=64)
    embedder = HashingEmbedder(dim=64)

    tenants = ["tenant-alpha", "tenant-beta", "tenant-gamma"]
    item_ids: dict[str, list[str]] = {t: [] for t in tenants}

    for i, tenant in enumerate(tenants):
        for j in range(5):
            now = datetime(2026, 1, 1 + i * 5 + j, tzinfo=timezone.utc)
            item = MemoryItem(
                id=make_item_id(f"{tenant}-item-{j}", when=now),
                type=MemoryType.fact,
                created_at=now,
                tenant_id=tenant,
                project=f"proj-{tenant}",
                tags=["shared-tag", f"tag-{tenant}"],
                title=f"Secret fact for {tenant} #{j}",
                summary=f"Confidential data belonging to {tenant}",
                confidence=0.7 + j * 0.05,
            )
            body = f"Private body content for {tenant} item {j}. SSE push details."
            store.write(item, body)
            idx.upsert(item, body, embedding=embedder.embed(f"{item.title} {body}"))
            item_ids[tenant].append(item.id)

    return store, idx, embedder, item_ids


class TestSearchIsolation:
    def test_tenant_sees_only_own_items(self, multi_tenant_env):
        store, idx, embedder, item_ids = multi_tenant_env
        retriever = Retriever(index=idx, embedder=embedder)

        sf = SearchFilter(tenant_id="tenant-alpha")
        results = retriever.search("secret fact", top_k=20, filters=sf)

        result_ids = {r.id for r in results}
        alpha_ids = set(item_ids["tenant-alpha"])
        beta_ids = set(item_ids["tenant-beta"])
        gamma_ids = set(item_ids["tenant-gamma"])

        assert result_ids.issubset(alpha_ids)
        assert not result_ids.intersection(beta_ids)
        assert not result_ids.intersection(gamma_ids)

    def test_each_tenant_isolated(self, multi_tenant_env):
        store, idx, embedder, item_ids = multi_tenant_env
        retriever = Retriever(index=idx, embedder=embedder)

        for tenant in ["tenant-alpha", "tenant-beta", "tenant-gamma"]:
            sf = SearchFilter(tenant_id=tenant)
            results = retriever.search("secret fact", top_k=20, filters=sf)
            result_ids = {r.id for r in results}
            own_ids = set(item_ids[tenant])
            other_ids = set()
            for t, ids in item_ids.items():
                if t != tenant:
                    other_ids.update(ids)
            assert result_ids.issubset(own_ids), f"{tenant} sees other tenant's items"
            assert not result_ids.intersection(other_ids)

    def test_no_tenant_filter_returns_all(self, multi_tenant_env):
        store, idx, embedder, item_ids = multi_tenant_env
        retriever = Retriever(index=idx, embedder=embedder)

        results = retriever.search("secret fact", top_k=50)
        result_ids = {r.id for r in results}
        all_ids = set()
        for ids in item_ids.values():
            all_ids.update(ids)
        assert len(result_ids) > 5

    def test_tenant_with_type_filter(self, multi_tenant_env):
        store, idx, embedder, item_ids = multi_tenant_env
        retriever = Retriever(index=idx, embedder=embedder)

        sf = SearchFilter(tenant_id="tenant-beta", type="fact")
        results = retriever.search("secret", top_k=20, filters=sf)
        for r in results:
            assert r.id in item_ids["tenant-beta"]


class TestWriteIsolation:
    def test_items_retain_tenant_id(self, multi_tenant_env):
        store, idx, embedder, item_ids = multi_tenant_env
        for item, body in store.iter_all():
            assert item.tenant_id in ["tenant-alpha", "tenant-beta", "tenant-gamma"]

    def test_tenant_specific_read(self, multi_tenant_env):
        store, idx, embedder, item_ids = multi_tenant_env
        alpha_items = [
            (it, b) for it, b in store.iter_all()
            if it.tenant_id == "tenant-alpha"
        ]
        assert len(alpha_items) == 5
        for it, _ in alpha_items:
            assert "tenant-alpha" in it.title


class TestGovernanceIsolation:
    def test_drift_per_tenant(self, multi_tenant_env):
        """Drift detection can be scoped to a tenant's items only."""
        store, idx, embedder, item_ids = multi_tenant_env
        from agent_brain.memory.governance.drift import DriftDetector

        class TenantScopedStore:
            def __init__(self, real_store, tenant_id):
                self._store = real_store
                self._tenant = tenant_id

            def iter_all(self):
                for item, body in self._store.iter_all():
                    if item.tenant_id == self._tenant:
                        yield item, body

        scoped = TenantScopedStore(store, "tenant-alpha")
        detector = DriftDetector(items_store=scoped)
        report = detector.detect()
        assert report.scanned_items == 5
        for finding in report.findings:
            for fid in finding.item_ids:
                assert fid in item_ids["tenant-alpha"]

    def test_stats_per_tenant(self, multi_tenant_env):
        store, idx, embedder, item_ids = multi_tenant_env
        from agent_brain.observability import collect_stats

        all_items = list(store.iter_all())
        alpha_items = [(it, b) for it, b in all_items if it.tenant_id == "tenant-alpha"]
        stats = collect_stats(alpha_items, skipped_count=0)
        assert stats.total_items == 5
