"""Write operations for HubIndex tables."""

from __future__ import annotations

import json
import sqlite3
from datetime import timezone

from agent_brain.platform.indexing.index_schema import segment_cjk
from agent_brain.platform.indexing.vector_index import VectorIndex
from agent_brain.contracts.memory_item import MemoryItem


class IndexWriter:
    """Coordinates item upsert/delete writes across meta, FTS, vector, and refs tables."""

    def __init__(self, connection: sqlite3.Connection, vector: VectorIndex) -> None:
        self.connection = connection
        self.vector = vector

    def upsert(
        self,
        item: MemoryItem,
        body: str,
        embedding: list[float] | None,
    ) -> None:
        conn = self.connection
        created_at = item.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        created_at_utc = created_at.astimezone(timezone.utc).isoformat()
        last_acc = None
        if hasattr(item, "retention") and item.retention and item.retention.last_accessed:
            last_acc = item.retention.last_accessed.isoformat()
        decay_cls = "fact"
        access_cnt = 0
        if hasattr(item, "retention") and item.retention:
            decay_cls = str(item.retention.decay_class)
            access_cnt = item.retention.access_count
        confidence = getattr(item, "confidence", 0.7)
        sensitivity = str(getattr(item, "sensitivity", "internal"))
        tenant_id = getattr(item, "tenant_id", None)
        support_count = getattr(item, "support_count", 0)
        contradict_count = getattr(item, "contradict_count", 0)
        gain_score = getattr(item, "gain_score", 0.0)
        superseded_by = getattr(item, "superseded_by", None)
        context_views = item.context_views
        context_views_json = json.dumps(context_views.model_dump(mode="json"), ensure_ascii=False)
        maturity = str(getattr(item, "maturity", "raw"))
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO items_meta "
                "(id, type, project, created_at, tags_json, title, summary, "
                " confidence, decay_class, last_accessed, access_count, sensitivity, tenant_id, "
                " support_count, contradict_count, gain_score, superseded_by, maturity, "
                " context_views_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item.id,
                    str(item.type),
                    item.project,
                    created_at_utc,
                    json.dumps(item.tags),
                    item.title,
                    item.summary,
                    confidence,
                    decay_cls,
                    last_acc,
                    access_cnt,
                    sensitivity,
                    tenant_id,
                    support_count,
                    contradict_count,
                    gain_score,
                    superseded_by,
                    maturity,
                    context_views_json,
                ),
            )
            conn.execute("DELETE FROM items_fts WHERE id = ?", (item.id,))
            # Segment CJK so the unicode61 tokenizer emits one token per char,
            # matching the query-side tokenizer. This text feeds the FTS index
            # only; display content is read from the md source of truth.
            conn.execute(
                "INSERT INTO items_fts (id, title, summary, body) VALUES (?, ?, ?, ?)",
                (
                    item.id,
                    segment_cjk(item.title),
                    segment_cjk(context_views.locator),
                    segment_cjk(context_views.overview),
                ),
            )
            if embedding is not None:
                self.vector.upsert(item.id, embedding)
            conn.execute("DELETE FROM refs_graph WHERE source_id = ?", (item.id,))
            if hasattr(item, "refs") and item.refs and item.refs.mems:
                for target_id in item.refs.mems:
                    row = conn.execute(
                        "SELECT superseded_by FROM items_meta WHERE id = ?",
                        (target_id,),
                    ).fetchone()
                    relation = (
                        "supersedes" if row and row[0] == item.id else "refs"
                    )
                    conn.execute(
                        "INSERT OR IGNORE INTO refs_graph (source_id, target_id, relation) "
                        "VALUES (?, ?, ?)",
                        (item.id, target_id, relation),
                    )
            conn.execute(
                "DELETE FROM refs_graph WHERE target_id = ? AND relation = 'supersedes'",
                (item.id,),
            )
            if item.superseded_by:
                conn.execute(
                    "DELETE FROM refs_graph "
                    "WHERE source_id = ? AND target_id = ? AND relation = 'refs'",
                    (item.superseded_by, item.id),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO refs_graph "
                    "(source_id, target_id, relation) VALUES (?, ?, 'supersedes')",
                    (item.superseded_by, item.id),
                )

    def delete(self, item_id: str) -> None:
        """Remove an item from all index tables."""
        conn = self.connection
        with conn:
            conn.execute("DELETE FROM items_meta WHERE id = ?", (item_id,))
            conn.execute("DELETE FROM items_fts WHERE id = ?", (item_id,))
            self.vector.delete(item_id)
            conn.execute("DELETE FROM refs_graph WHERE source_id = ? OR target_id = ?", (item_id, item_id))


__all__ = ["IndexWriter"]
