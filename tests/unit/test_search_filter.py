"""Tests for search filter functionality (type, project, tags, since_days)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.recall.retrieval import Retriever, SearchFilter
from agent_brain.contracts.memory_item import MemoryItem, MemoryType


def _seed_index(tmp: Path, items: list[dict]) -> HubIndex:
    """Seed an index with items. Each dict has: suffix, title, body, type, project, tags."""
    idx = HubIndex(db_path=tmp / "index.db", embedding_dim=8)
    emb = HashingEmbedder(dim=8)
    for it in items:
        created = it.get("created_at", datetime.now(timezone.utc))
        item = MemoryItem(
            id=f"mem-20260528-100000-{it['suffix']}",
            type=MemoryType(it.get("type", "fact")),
            created_at=created,
            title=it["title"],
            summary=it["body"][:60],
            project=it.get("project"),
            tags=it.get("tags", []),
            superseded_by=it.get("superseded_by"),
        )
        idx.upsert(item, it["body"], embedding=emb.embed(f"{it['title']} {it['body']}"))
    return idx


# ── SearchFilter dataclass ──


class TestSearchFilterBasics:
    def test_empty_filter(self):
        sf = SearchFilter()
        assert sf.is_empty

    def test_non_empty_type(self):
        sf = SearchFilter(type="decision")
        assert not sf.is_empty

    def test_non_empty_project(self):
        sf = SearchFilter(project="myproj")
        assert not sf.is_empty

    def test_non_empty_tags(self):
        sf = SearchFilter(tags=["redis"])
        assert not sf.is_empty

    def test_non_empty_since(self):
        sf = SearchFilter(since_days=7)
        assert not sf.is_empty

    def test_empty_tags_list_is_empty(self):
        sf = SearchFilter(tags=[])
        assert sf.is_empty

    def test_non_empty_exclude_tags(self):
        sf = SearchFilter(exclude_tags=["noise"])
        assert not sf.is_empty

    def test_empty_exclude_tags_is_empty(self):
        sf = SearchFilter(exclude_tags=[])
        assert sf.is_empty


# ── Index-level filter_ids ──


class TestIndexFilterIds:
    def test_no_filters_returns_none(self, tmp_brain_dir: Path):
        idx = _seed_index(tmp_brain_dir, [
            {"suffix": "a", "title": "hello", "body": "world"},
        ])
        assert idx.filter_ids() is None

    def test_filter_by_type(self, tmp_brain_dir: Path):
        idx = _seed_index(tmp_brain_dir, [
            {"suffix": "a", "title": "redis", "body": "cache", "type": "decision"},
            {"suffix": "b", "title": "postgres", "body": "db", "type": "fact"},
            {"suffix": "c", "title": "mysql", "body": "rds", "type": "decision"},
        ])
        ids = idx.filter_ids(type="decision")
        assert ids == {
            "mem-20260528-100000-a",
            "mem-20260528-100000-c",
        }

    def test_filter_by_project(self, tmp_brain_dir: Path):
        idx = _seed_index(tmp_brain_dir, [
            {"suffix": "a", "title": "redis", "body": "cache", "project": "hub"},
            {"suffix": "b", "title": "postgres", "body": "db", "project": "web"},
        ])
        ids = idx.filter_ids(project="hub")
        assert ids == {"mem-20260528-100000-a"}

    def test_filter_by_tags(self, tmp_brain_dir: Path):
        idx = _seed_index(tmp_brain_dir, [
            {"suffix": "a", "title": "redis", "body": "cache", "tags": ["infra", "cache"]},
            {"suffix": "b", "title": "postgres", "body": "db", "tags": ["infra", "sql"]},
            {"suffix": "c", "title": "mysql", "body": "rds", "tags": ["sql"]},
        ])
        ids = idx.filter_ids(tags=["infra"])
        assert ids == {
            "mem-20260528-100000-a",
            "mem-20260528-100000-b",
        }

    def test_filter_by_multiple_tags_requires_all(self, tmp_brain_dir: Path):
        idx = _seed_index(tmp_brain_dir, [
            {"suffix": "a", "title": "redis", "body": "cache", "tags": ["infra", "cache"]},
            {"suffix": "b", "title": "postgres", "body": "db", "tags": ["infra", "sql"]},
        ])
        ids = idx.filter_ids(tags=["infra", "cache"])
        assert ids == {"mem-20260528-100000-a"}

    def test_filter_by_since_days(self, tmp_brain_dir: Path):
        now = datetime.now(timezone.utc)
        idx = _seed_index(tmp_brain_dir, [
            {"suffix": "new", "title": "new item", "body": "fresh", "created_at": now},
            {"suffix": "old", "title": "old item", "body": "stale", "created_at": now - timedelta(days=30)},
        ])
        ids = idx.filter_ids(since_days=7)
        assert ids == {"mem-20260528-100000-new"}

    def test_combined_filters(self, tmp_brain_dir: Path):
        idx = _seed_index(tmp_brain_dir, [
            {"suffix": "a", "title": "redis", "body": "cache", "type": "decision", "project": "hub"},
            {"suffix": "b", "title": "postgres", "body": "db", "type": "decision", "project": "web"},
            {"suffix": "c", "title": "mysql", "body": "rds", "type": "fact", "project": "hub"},
        ])
        ids = idx.filter_ids(type="decision", project="hub")
        assert ids == {"mem-20260528-100000-a"}

    def test_filter_no_matches_returns_empty_set(self, tmp_brain_dir: Path):
        idx = _seed_index(tmp_brain_dir, [
            {"suffix": "a", "title": "redis", "body": "cache", "type": "fact"},
        ])
        ids = idx.filter_ids(type="decision")
        assert ids == set()

    def test_filter_exclude_tags(self, tmp_brain_dir: Path):
        idx = _seed_index(tmp_brain_dir, [
            {"suffix": "a", "title": "redis", "body": "cache", "tags": ["infra", "session-end"]},
            {"suffix": "b", "title": "postgres", "body": "db", "tags": ["infra"]},
            {"suffix": "c", "title": "mysql", "body": "rds", "tags": ["auto-captured"]},
        ])
        ids = idx.filter_ids(exclude_tags=["session-end", "auto-captured"])
        assert ids == {"mem-20260528-100000-b"}

    def test_filter_exclude_tags_with_type(self, tmp_brain_dir: Path):
        idx = _seed_index(tmp_brain_dir, [
            {"suffix": "a", "title": "redis", "body": "cache", "type": "signal", "tags": ["session-end"]},
            {"suffix": "b", "title": "postgres", "body": "db", "type": "signal", "tags": []},
            {"suffix": "c", "title": "mysql", "body": "rds", "type": "fact", "tags": []},
        ])
        ids = idx.filter_ids(type="signal", exclude_tags=["session-end"])
        assert ids == {"mem-20260528-100000-b"}

    def test_filter_can_exclude_superseded_items(self, tmp_brain_dir: Path):
        idx = _seed_index(tmp_brain_dir, [
            {
                "suffix": "old",
                "title": "browser status",
                "body": "standard browser is limited",
                "superseded_by": "mem-20260528-100000-current",
            },
            {
                "suffix": "current",
                "title": "browser status fixed",
                "body": "fingerprint browser works now",
            },
        ])

        ids = idx.filter_ids(include_superseded=False)

        assert ids == {"mem-20260528-100000-current"}


# ── Retriever integration with filters ──


class TestRetrieverWithFilters:
    def test_filter_by_type_limits_results(self, tmp_brain_dir: Path):
        idx = _seed_index(tmp_brain_dir, [
            {"suffix": "a", "title": "Redis caching", "body": "fast redis store", "type": "decision"},
            {"suffix": "b", "title": "Redis config", "body": "redis setup config", "type": "fact"},
        ])
        r = Retriever(index=idx, embedder=HashingEmbedder(dim=8), apply_decay=False, record_access=False)
        hits = r.search("redis", top_k=10, filters=SearchFilter(type="decision"))
        assert len(hits) == 1
        assert hits[0].id.endswith("a")

    def test_filter_by_project_limits_results(self, tmp_brain_dir: Path):
        idx = _seed_index(tmp_brain_dir, [
            {"suffix": "a", "title": "Redis caching", "body": "fast redis store", "project": "hub"},
            {"suffix": "b", "title": "Redis config", "body": "redis setup config", "project": "web"},
        ])
        r = Retriever(index=idx, embedder=HashingEmbedder(dim=8), apply_decay=False, record_access=False)
        hits = r.search("redis", top_k=10, filters=SearchFilter(project="web"))
        assert len(hits) == 1
        assert hits[0].id.endswith("b")

    def test_empty_filter_returns_all(self, tmp_brain_dir: Path):
        idx = _seed_index(tmp_brain_dir, [
            {"suffix": "a", "title": "Redis caching", "body": "fast redis store"},
            {"suffix": "b", "title": "Redis config", "body": "redis setup config"},
        ])
        r = Retriever(index=idx, embedder=HashingEmbedder(dim=8), apply_decay=False, record_access=False)
        hits_no_filter = r.search("redis", top_k=10)
        hits_empty_filter = r.search("redis", top_k=10, filters=SearchFilter())
        assert len(hits_no_filter) == len(hits_empty_filter)

    def test_filter_no_match_returns_empty(self, tmp_brain_dir: Path):
        idx = _seed_index(tmp_brain_dir, [
            {"suffix": "a", "title": "Redis caching", "body": "fast redis store", "type": "fact"},
        ])
        r = Retriever(index=idx, embedder=HashingEmbedder(dim=8), apply_decay=False, record_access=False)
        hits = r.search("redis", top_k=10, filters=SearchFilter(type="decision"))
        assert hits == []

    def test_filter_by_tags(self, tmp_brain_dir: Path):
        idx = _seed_index(tmp_brain_dir, [
            {"suffix": "a", "title": "Redis caching", "body": "fast redis store", "tags": ["infra"]},
            {"suffix": "b", "title": "Redis config", "body": "redis setup config", "tags": ["ops"]},
        ])
        r = Retriever(index=idx, embedder=HashingEmbedder(dim=8), apply_decay=False, record_access=False)
        hits = r.search("redis", top_k=10, filters=SearchFilter(tags=["infra"]))
        assert len(hits) == 1
        assert hits[0].id.endswith("a")

    def test_filter_combined_type_and_project(self, tmp_brain_dir: Path):
        idx = _seed_index(tmp_brain_dir, [
            {"suffix": "a", "title": "Redis caching", "body": "fast redis store", "type": "decision", "project": "hub"},
            {"suffix": "b", "title": "Redis config", "body": "redis setup config", "type": "decision", "project": "web"},
            {"suffix": "c", "title": "Redis ops", "body": "redis ops setup", "type": "fact", "project": "hub"},
        ])
        r = Retriever(index=idx, embedder=HashingEmbedder(dim=8), apply_decay=False, record_access=False)
        hits = r.search("redis", top_k=10, filters=SearchFilter(type="decision", project="hub"))
        assert len(hits) == 1
        assert hits[0].id.endswith("a")

    def test_filter_exclude_tags_in_retriever(self, tmp_brain_dir: Path):
        idx = _seed_index(tmp_brain_dir, [
            {"suffix": "a", "title": "Redis caching", "body": "fast redis store", "tags": ["infra"]},
            {"suffix": "b", "title": "Redis config", "body": "redis setup config", "tags": ["noise", "infra"]},
        ])
        r = Retriever(index=idx, embedder=HashingEmbedder(dim=8), apply_decay=False, record_access=False)
        hits = r.search("redis", top_k=10, filters=SearchFilter(exclude_tags=["noise"]))
        assert len(hits) == 1
        assert hits[0].id.endswith("a")

    def test_retriever_excludes_superseded_items_by_default(self, tmp_brain_dir: Path):
        idx = _seed_index(tmp_brain_dir, [
            {
                "suffix": "old",
                "title": "Browser status",
                "body": "browser browser standard browser is limited",
                "superseded_by": "mem-20260528-100000-current",
            },
            {
                "suffix": "current",
                "title": "Browser status",
                "body": "browser fingerprint browser works now",
            },
        ])
        r = Retriever(index=idx, embedder=HashingEmbedder(dim=8), apply_decay=False, record_access=False)

        hits = r.search("browser status", top_k=10)

        assert hits
        assert "mem-20260528-100000-old" not in {hit.id for hit in hits}

    def test_retriever_can_include_superseded_items_for_audit(self, tmp_brain_dir: Path):
        idx = _seed_index(tmp_brain_dir, [
            {
                "suffix": "old",
                "title": "Browser status",
                "body": "browser browser standard browser is limited",
                "superseded_by": "mem-20260528-100000-current",
            },
            {
                "suffix": "current",
                "title": "Browser status",
                "body": "browser fingerprint browser works now",
            },
        ])
        r = Retriever(index=idx, embedder=HashingEmbedder(dim=8), apply_decay=False, record_access=False)

        hits = r.search("browser status", top_k=10, filters=SearchFilter(include_superseded=True))

        assert "mem-20260528-100000-old" in {hit.id for hit in hits}

    def test_retriever_excludes_md_superseded_item_even_when_index_meta_is_stale(
        self,
        tmp_brain_dir: Path,
    ):
        store = ItemsStore(tmp_brain_dir / "items")
        idx = HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=8)
        emb = HashingEmbedder(dim=8)
        old = MemoryItem(
            id="mem-20260528-100000-stale-index-old",
            type=MemoryType.fact,
            created_at=datetime.now(timezone.utc),
            title="Browser limitation",
            summary="browser browser permission limitation",
        )
        current = MemoryItem(
            id="mem-20260528-100000-stale-index-current",
            type=MemoryType.fact,
            created_at=datetime.now(timezone.utc),
            title="Browser fixed",
            summary="browser fingerprint browser available",
        )
        for item in (old, current):
            store.write(item, item.summary)
            idx.upsert(item, item.summary, embedding=emb.embed(f"{item.title} {item.summary}"))
        store.update_frontmatter(old.id, superseded_by=current.id)
        r = Retriever(index=idx, embedder=emb, apply_decay=False, record_access=False)

        hits = r.search("browser", top_k=10)

        hit_ids = {hit.id for hit in hits}
        assert old.id not in hit_ids
        assert current.id in hit_ids

    def test_retriever_excludes_stale_runtime_state_by_default(self, tmp_brain_dir: Path):
        idx = _seed_index(tmp_brain_dir, [
            {
                "suffix": "old-browser-state",
                "title": "Browser currently limited",
                "body": "browser browser standard browser is unavailable due to permission denied",
                "tags": ["browser", "runtime"],
                "created_at": datetime.now(timezone.utc) - timedelta(days=5),
            },
            {
                "suffix": "current-browser-state",
                "title": "Browser current status",
                "body": "browser fingerprint browser is available now",
                "tags": ["browser", "runtime"],
            },
        ])
        r = Retriever(index=idx, embedder=HashingEmbedder(dim=8), apply_decay=False, record_access=False)

        hits = r.search("browser status", top_k=10)

        hit_ids = {hit.id for hit in hits}
        assert "mem-20260528-100000-old-browser-state" not in hit_ids
        assert "mem-20260528-100000-current-browser-state" in hit_ids

    def test_retriever_keeps_old_stable_facts(self, tmp_brain_dir: Path):
        idx = _seed_index(tmp_brain_dir, [
            {
                "suffix": "old-stable",
                "title": "SQLite migration design",
                "body": "sqlite migration schema history remains valid",
                "tags": ["architecture"],
                "created_at": datetime.now(timezone.utc) - timedelta(days=90),
            },
        ])
        r = Retriever(index=idx, embedder=HashingEmbedder(dim=8), apply_decay=False, record_access=False)

        hits = r.search("sqlite migration", top_k=10)

        assert "mem-20260528-100000-old-stable" in {hit.id for hit in hits}

    def test_retriever_can_include_stale_state_for_audit(self, tmp_brain_dir: Path):
        idx = _seed_index(tmp_brain_dir, [
            {
                "suffix": "old-browser-state",
                "title": "Browser currently limited",
                "body": "browser browser standard browser is unavailable due to permission denied",
                "tags": ["browser", "runtime"],
                "created_at": datetime.now(timezone.utc) - timedelta(days=5),
            },
        ])
        r = Retriever(index=idx, embedder=HashingEmbedder(dim=8), apply_decay=False, record_access=False)

        hits = r.search(
            "browser status",
            top_k=10,
            filters=SearchFilter(include_stale_state=True),
        )

        assert "mem-20260528-100000-old-browser-state" in {hit.id for hit in hits}
