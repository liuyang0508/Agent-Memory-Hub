"""Tests for confidence + retention decay system (v1.3)."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.memory.recall.retrieval import retention_factor
from agent_brain.contracts.memory_item import (
    DECAY_HALF_LIFE_DAYS,
    TYPE_TO_DECAY_CLASS,
    DecayClass,
    MemoryItem,
    MemoryType,
    Retention,
)


# ── Schema tests ──


class TestSchemaV03Fields:
    def test_new_item_has_confidence(self):
        item = MemoryItem(
            id="mem-20260528-100000-test",
            type=MemoryType.fact,
            created_at=datetime.now(timezone.utc),
            title="t",
            summary="s",
        )
        assert item.confidence == 0.7
        assert item.schema_version == "1"

    def test_confidence_range_validation(self):
        with pytest.raises(Exception):
            MemoryItem(
                id="mem-20260528-100000-bad",
                type=MemoryType.fact,
                created_at=datetime.now(timezone.utc),
                title="t",
                summary="s",
                confidence=1.5,
            )

    def test_retention_defaults_auto_mapped(self):
        item = MemoryItem(
            id="mem-20260528-100000-ret",
            type=MemoryType.decision,
            created_at=datetime.now(timezone.utc),
            title="t",
            summary="s",
        )
        assert item.retention.access_count == 0
        assert item.retention.last_accessed is None
        assert item.retention.decay_class == "decision"  # auto-mapped from type

    def test_explicit_retention(self):
        now = datetime.now(timezone.utc)
        item = MemoryItem(
            id="mem-20260528-100000-exp",
            type=MemoryType.fact,
            created_at=now,
            title="t",
            summary="s",
            retention=Retention(
                last_accessed=now,
                access_count=5,
                decay_class=DecayClass.architecture,
            ),
        )
        assert item.retention.access_count == 5
        assert item.retention.decay_class == "architecture"

    def test_confidence_explicit_set(self):
        item = MemoryItem(
            id="mem-20260528-100000-conf",
            type=MemoryType.fact,
            created_at=datetime.now(timezone.utc),
            title="t",
            summary="s",
            confidence=0.95,
        )
        assert item.confidence == 0.95


class TestV02Migration:
    def test_v02_item_gets_defaults(self):
        data = {
            "id": "mem-20260101-120000-old",
            "schema_version": "0.2",
            "type": "fact",
            "created_at": "2026-01-01T12:00:00+08:00",
            "title": "old item",
            "summary": "from v0.2",
        }
        item = MemoryItem.model_validate(data)
        assert item.confidence == 0.7
        assert item.retention.decay_class == "fact"
        assert item.retention.access_count == 0

    def test_v02_signal_gets_ephemeral_decay(self):
        data = {
            "id": "mem-20260101-120000-sig",
            "schema_version": "0.2",
            "type": "signal",
            "created_at": "2026-01-01T12:00:00+08:00",
            "title": "signal",
            "summary": "s",
        }
        item = MemoryItem.model_validate(data)
        assert item.retention.decay_class == "ephemeral"

    def test_v02_artifact_gets_architecture_decay(self):
        data = {
            "id": "mem-20260101-120000-art",
            "schema_version": "0.2",
            "type": "artifact",
            "created_at": "2026-01-01T12:00:00+08:00",
            "title": "artifact",
            "summary": "s",
        }
        item = MemoryItem.model_validate(data)
        assert item.retention.decay_class == "architecture"

    def test_type_to_decay_mapping_complete(self):
        for mt in MemoryType:
            assert mt.value in TYPE_TO_DECAY_CLASS


# ── Decay formula tests ──


class TestRetentionFactor:
    def test_retriever_delegates_decay_strategy(self, tmp_brain_dir: Path):
        from agent_brain.platform.indexing.index import HubIndex
        from agent_brain.memory.recall.retrieval import Retriever
        from agent_brain.memory.recall.retrieval_decay import RetrievalDecay

        idx = HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=8)
        retriever = Retriever(index=idx, embedder=HashingEmbedder(dim=8))

        assert isinstance(retriever.decay, RetrievalDecay)

    def test_zero_days_returns_one(self):
        assert retention_factor("fact", 0) == 1.0

    def test_at_half_life_returns_half(self):
        for dc, hl in DECAY_HALF_LIFE_DAYS.items():
            rf = retention_factor(dc, hl)
            assert abs(rf - 0.5) < 1e-9, f"{dc}: expected 0.5 at {hl} days, got {rf}"

    def test_double_half_life_returns_quarter(self):
        rf = retention_factor("fact", 120)  # 2 × 60
        assert abs(rf - 0.25) < 1e-9

    def test_ephemeral_decays_fast(self):
        rf_eph = retention_factor("ephemeral", 14)  # 2 half-lives
        rf_arch = retention_factor("architecture", 14)
        assert rf_eph < rf_arch

    def test_negative_days_returns_one(self):
        assert retention_factor("fact", -5) == 1.0

    def test_unknown_decay_class_uses_60_days(self):
        rf = retention_factor("unknown_class", 60)
        assert abs(rf - 0.5) < 1e-9

    def test_very_old_item_near_zero(self):
        rf = retention_factor("ephemeral", 365)
        assert rf < 0.001

    def test_decay_coefficient_combines_time_access_and_feedback(self):
        from agent_brain.memory.recall.retrieval_decay import decay_coefficient

        unused = decay_coefficient(
            decay_class="fact",
            days_since_reference=90,
            access_count=0,
            support_count=0,
            contradict_count=0,
            gain_score=0.0,
        )
        reinforced = decay_coefficient(
            decay_class="fact",
            days_since_reference=90,
            access_count=12,
            support_count=4,
            contradict_count=0,
            gain_score=0.5,
        )
        contradicted = decay_coefficient(
            decay_class="fact",
            days_since_reference=90,
            access_count=0,
            support_count=0,
            contradict_count=4,
            gain_score=-0.2,
        )

        assert reinforced > unused
        assert contradicted < unused
        assert 0.0 < contradicted < 1.0


# ── Retrieval integration tests ──


def _build_index(tmp: Path, items: list[tuple[str, str, str, float, str]]):
    """items = list of (suffix, title, body, confidence, decay_class)."""
    from agent_brain.platform.indexing.index import HubIndex

    idx = HubIndex(db_path=tmp / "index.db", embedding_dim=8)
    emb = HashingEmbedder(dim=8)
    for suffix, title, body, conf, dc in items:
        item = MemoryItem(
            id=f"mem-20260528-100000-{suffix}",
            type=MemoryType.fact,
            created_at=datetime.now(timezone.utc),
            title=title,
            summary=body[:60],
            confidence=conf,
            retention=Retention(decay_class=DecayClass(dc)),
        )
        idx.upsert(item, body, embedding=emb.embed(f"{title} {body}"))
    return idx


class TestEffectiveScoreIntegration:
    def test_high_confidence_ranks_higher(self, tmp_brain_dir: Path):
        from agent_brain.memory.recall.retrieval import Retriever

        idx = _build_index(tmp_brain_dir, [
            ("hi", "Redis caching", "fast store redis", 0.95, "architecture"),
            ("lo", "Redis config", "redis configuration setup", 0.2, "architecture"),
        ])
        r = Retriever(index=idx, embedder=HashingEmbedder(dim=8), apply_decay=True, record_access=False)
        hits = r.search("redis", top_k=2)
        assert hits[0].id.endswith("hi")

    def test_decay_disabled_ignores_confidence(self, tmp_brain_dir: Path):
        from agent_brain.memory.recall.retrieval import Retriever

        idx = _build_index(tmp_brain_dir, [
            ("hi", "Redis caching", "fast store redis", 0.95, "architecture"),
            ("lo", "Redis config", "redis configuration setup", 0.2, "architecture"),
        ])
        r_decay = Retriever(index=idx, embedder=HashingEmbedder(dim=8), apply_decay=True, record_access=False)
        r_nodecay = Retriever(index=idx, embedder=HashingEmbedder(dim=8), apply_decay=False, record_access=False)
        hits_decay = r_decay.search("redis", top_k=2)
        hits_nodecay = r_nodecay.search("redis", top_k=2)
        assert hits_decay[0].score != hits_nodecay[0].score

    def test_search_records_access(self, tmp_brain_dir: Path):
        from agent_brain.memory.recall.retrieval import Retriever

        idx = _build_index(tmp_brain_dir, [
            ("a", "unique xylophone", "xylophone content", 0.7, "fact"),
        ])
        r = Retriever(index=idx, embedder=HashingEmbedder(dim=8), record_access=True, apply_decay=False)
        r.search("xylophone", top_k=1)
        r.search("xylophone", top_k=1)

        row = idx.connection.execute(
            "SELECT access_count, last_accessed FROM items_meta WHERE id = ?",
            ("mem-20260528-100000-a",),
        ).fetchone()
        assert row[0] >= 2
        assert row[1] is not None

    def test_retrieval_decay_uses_created_at_and_reuse_signals_when_never_accessed(
        self,
        tmp_brain_dir: Path,
    ):
        from agent_brain.platform.indexing.index import HubIndex
        from agent_brain.memory.recall.retrieval_decay import RetrievalDecay
        from agent_brain.memory.recall.retrieval_types import RetrievedItem

        idx = HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=8)
        old = datetime.now(timezone.utc) - timedelta(days=120)
        unused = MemoryItem(
            id="mem-20260528-100000-old-unused",
            type=MemoryType.fact,
            created_at=old,
            title="same relevance",
            summary="same relevance",
            confidence=1.0,
            retention=Retention(decay_class=DecayClass.fact, access_count=0),
        )
        reused = MemoryItem(
            id="mem-20260528-100000-old-reused",
            type=MemoryType.fact,
            created_at=old,
            title="same relevance",
            summary="same relevance",
            confidence=1.0,
            retention=Retention(decay_class=DecayClass.fact, access_count=10),
            support_count=3,
            gain_score=0.4,
        )
        idx.upsert(unused, "body", embedding=[1.0] + [0.0] * 7)
        idx.upsert(reused, "body", embedding=[1.0] + [0.0] * 7)

        rescored = RetrievalDecay(idx).apply([
            RetrievedItem(id=unused.id, score=1.0, bm25_rank=1, vector_rank=None),
            RetrievedItem(id=reused.id, score=1.0, bm25_rank=2, vector_rank=None),
        ])

        assert rescored[0].id == reused.id
        assert rescored[0].score > rescored[1].score


# ── Index confidence operations ──


class TestIndexConfidenceOps:
    def test_update_confidence(self, tmp_brain_dir: Path):
        from agent_brain.platform.indexing.index import HubIndex

        idx = HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=8)
        item = MemoryItem(
            id="mem-20260528-100000-uc",
            type=MemoryType.fact,
            created_at=datetime.now(timezone.utc),
            title="t",
            summary="s",
            confidence=0.5,
        )
        idx.upsert(item, "body", embedding=[0.0] * 8)
        idx.update_confidence("mem-20260528-100000-uc", 0.9)
        data = idx.get_confidence_data(["mem-20260528-100000-uc"])
        assert data["mem-20260528-100000-uc"][0] == 0.9

    def test_update_confidence_clamps(self, tmp_brain_dir: Path):
        from agent_brain.platform.indexing.index import HubIndex

        idx = HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=8)
        item = MemoryItem(
            id="mem-20260528-100000-cl",
            type=MemoryType.fact,
            created_at=datetime.now(timezone.utc),
            title="t",
            summary="s",
        )
        idx.upsert(item, "body", embedding=[0.0] * 8)
        idx.update_confidence("mem-20260528-100000-cl", 5.0)
        data = idx.get_confidence_data(["mem-20260528-100000-cl"])
        assert data["mem-20260528-100000-cl"][0] == 1.0

    def test_record_access_increments(self, tmp_brain_dir: Path):
        from agent_brain.platform.indexing.index import HubIndex

        idx = HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=8)
        item = MemoryItem(
            id="mem-20260528-100000-ra",
            type=MemoryType.fact,
            created_at=datetime.now(timezone.utc),
            title="t",
            summary="s",
        )
        idx.upsert(item, "body", embedding=[0.0] * 8)
        idx.record_access("mem-20260528-100000-ra", "2026-05-28T12:00:00Z")
        idx.record_access("mem-20260528-100000-ra", "2026-05-28T13:00:00Z")
        row = idx.connection.execute(
            "SELECT access_count, last_accessed FROM items_meta WHERE id = ?",
            ("mem-20260528-100000-ra",),
        ).fetchone()
        assert row[0] == 2
        assert row[1] == "2026-05-28T13:00:00Z"


# ── ItemsStore update_frontmatter ──


class TestUpdateFrontmatter:
    def test_updates_confidence(self, tmp_brain_dir: Path):
        from agent_brain.memory.store.items_store import ItemsStore

        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        item = MemoryItem(
            id="mem-20260528-100000-upd",
            type=MemoryType.fact,
            created_at=datetime.now(timezone.utc),
            title="updatable",
            summary="s",
            confidence=0.5,
        )
        store.write(item, "body content")
        updated = store.update_frontmatter("mem-20260528-100000-upd", confidence=0.9)
        assert updated.confidence == 0.9

        reloaded = list(store.iter_all())
        found = [it for it, _ in reloaded if it.id == "mem-20260528-100000-upd"]
        assert found[0].confidence == 0.9

    def test_updates_retention_nested(self, tmp_brain_dir: Path):
        from agent_brain.memory.store.items_store import ItemsStore

        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        item = MemoryItem(
            id="mem-20260528-100000-nest",
            type=MemoryType.fact,
            created_at=datetime.now(timezone.utc),
            title="nested",
            summary="s",
        )
        store.write(item, "body")
        updated = store.update_frontmatter(
            "mem-20260528-100000-nest",
            **{"retention.access_count": 10},
        )
        assert updated.retention.access_count == 10

    def test_update_nonexistent_raises(self, tmp_brain_dir: Path):
        from agent_brain.memory.store.items_store import ItemsStore

        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        with pytest.raises(FileNotFoundError):
            store.update_frontmatter("mem-20260528-100000-nope", confidence=0.5)

    def test_preserves_body(self, tmp_brain_dir: Path):
        from agent_brain.memory.store.items_store import ItemsStore

        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        item = MemoryItem(
            id="mem-20260528-100000-body",
            type=MemoryType.fact,
            created_at=datetime.now(timezone.utc),
            title="body check",
            summary="s",
        )
        store.write(item, "important body content\nwith multiple lines")
        store.update_frontmatter("mem-20260528-100000-body", confidence=0.1)

        for it, body in store.iter_all():
            if it.id == "mem-20260528-100000-body":
                assert "important body content" in body
                assert "multiple lines" in body
                break
