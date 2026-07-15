"""Metadata, retention, tier, and filter operations for HubIndex."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone


class MetadataIndex:
    """Operations backed by the ``items_meta`` table."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get_confidence_data(self, item_ids: list[str]) -> dict[str, tuple[float, str, str | None]]:
        """Return {id: (confidence, decay_class, last_accessed_iso)} for given ids."""
        if not item_ids:
            return {}
        placeholders = ",".join("?" for _ in item_ids)
        rows = self.connection.execute(
            f"SELECT id, confidence, decay_class, last_accessed FROM items_meta WHERE id IN ({placeholders})",
            item_ids,
        ).fetchall()
        return {row[0]: (row[1], row[2], row[3]) for row in rows}

    def get_decay_data(
        self,
        item_ids: list[str],
    ) -> dict[str, tuple[float, str, str | None, str | None, int, int, int, float]]:
        """Return metadata needed by the multi-axis decay coefficient.

        Tuple layout:
        ``(confidence, decay_class, last_accessed, created_at, access_count,
        support_count, contradict_count, gain_score)``.
        """
        if not item_ids:
            return {}
        placeholders = ",".join("?" for _ in item_ids)
        rows = self.connection.execute(
            "SELECT id, confidence, decay_class, last_accessed, created_at, access_count, "
            "support_count, contradict_count, gain_score FROM items_meta "
            f"WHERE id IN ({placeholders})",
            item_ids,
        ).fetchall()
        return {
            row[0]: (
                float(row[1]),
                row[2],
                row[3],
                row[4],
                int(row[5] or 0),
                int(row[6] or 0),
                int(row[7] or 0),
                float(row[8] or 0.0),
            )
            for row in rows
        }

    def get_search_metadata(self, item_ids: list[str]) -> dict[str, dict[str, object]]:
        """Return lightweight metadata used by post-retrieval scoring."""
        if not item_ids:
            return {}
        placeholders = ",".join("?" for _ in item_ids)
        rows = self.connection.execute(
            "SELECT id, type, tags_json, title, summary, created_at FROM items_meta "
            f"WHERE id IN ({placeholders})",
            item_ids,
        ).fetchall()
        return {
            row[0]: {
                "type": row[1],
                "tags": json.loads(row[2] or "[]"),
                "title": row[3] or "",
                "summary": row[4] or "",
                "created_at": row[5],
            }
            for row in rows
        }

    def get_projects(self, item_ids: Sequence[str]) -> dict[str, str | None]:
        """Return the stored project value for each existing item ID."""
        if not item_ids:
            return {}
        ids = list(item_ids)
        placeholders = ",".join("?" for _ in ids)
        rows = self.connection.execute(
            f"SELECT id, project FROM items_meta WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
        return {str(row[0]): row[1] for row in rows}

    def get_feedback_data(self, item_ids: list[str]) -> dict[str, tuple[int, int, float]]:
        """Return {id: (support_count, contradict_count, gain_score)} for ids."""
        if not item_ids:
            return {}
        placeholders = ",".join("?" for _ in item_ids)
        rows = self.connection.execute(
            "SELECT id, support_count, contradict_count, gain_score FROM items_meta "
            f"WHERE id IN ({placeholders})",
            item_ids,
        ).fetchall()
        return {row[0]: (int(row[1]), int(row[2]), float(row[3])) for row in rows}

    def record_access(self, item_id: str, accessed_at: str) -> None:
        """Increment access_count and update last_accessed for an item."""
        self.connection.execute(
            "UPDATE items_meta SET access_count = access_count + 1, last_accessed = ? WHERE id = ?",
            (accessed_at, item_id),
        )
        self.connection.commit()

    def record_access_many(self, item_ids: list[str], accessed_at: str) -> None:
        """Increment access_count for multiple items in one transaction."""
        if not item_ids:
            return
        self.connection.executemany(
            "UPDATE items_meta SET access_count = access_count + 1, last_accessed = ? WHERE id = ?",
            [(accessed_at, item_id) for item_id in item_ids],
        )
        self.connection.commit()

    def update_confidence(self, item_id: str, confidence: float) -> None:
        """Set confidence for an item in the index."""
        self.connection.execute(
            "UPDATE items_meta SET confidence = ? WHERE id = ?",
            (min(max(confidence, 0.0), 1.0), item_id),
        )
        self.connection.commit()

    def update_feedback_stats(
        self,
        item_id: str,
        *,
        support_count: int | None = None,
        contradict_count: int | None = None,
        gain_score: float | None = None,
    ) -> None:
        """Set feedback value stats for an existing item row."""
        updates: list[str] = []
        params: list[object] = []
        if support_count is not None:
            updates.append("support_count = ?")
            params.append(max(0, int(support_count)))
        if contradict_count is not None:
            updates.append("contradict_count = ?")
            params.append(max(0, int(contradict_count)))
        if gain_score is not None:
            updates.append("gain_score = ?")
            params.append(float(gain_score))
        if not updates:
            return
        params.append(item_id)
        self.connection.execute(
            f"UPDATE items_meta SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        self.connection.commit()

    def update_tier(self, item_id: str, tier: str) -> None:
        """Set the derived storage tier (hot/warm/cold) for an item."""
        self.connection.execute(
            "UPDATE items_meta SET tier = ? WHERE id = ?",
            (tier, item_id),
        )
        self.connection.commit()

    def tier_counts(self) -> dict[str, int]:
        """Return {tier: count} over items that have a tier assigned."""
        rows = self.connection.execute(
            "SELECT tier, COUNT(*) FROM items_meta WHERE tier IS NOT NULL GROUP BY tier"
        ).fetchall()
        return {row[0]: row[1] for row in rows}

    def filter_ids(
        self,
        type: str | None = None,
        project: str | None = None,
        tags: list[str] | None = None,
        exclude_tags: list[str] | None = None,
        since_days: int | None = None,
        tenant_id: str | None = None,
        include_superseded: bool = True,
    ) -> set[str] | None:
        """Return matching item IDs, or None if no filters are active."""
        clauses: list[str] = []
        params: list[object] = []
        if not include_superseded:
            clauses.append("(superseded_by IS NULL OR superseded_by = '')")
        if type is not None:
            clauses.append("type = ?")
            params.append(type)
        if project is not None:
            clauses.append("project = ?")
            params.append(project)
        if since_days is not None:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat()
            clauses.append("created_at >= ?")
            params.append(cutoff)
        if tenant_id is not None:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        if not clauses and not tags and not exclude_tags:
            return None
        sql = "SELECT id, tags_json FROM items_meta"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        rows = self.connection.execute(sql, params).fetchall()
        rows_by_id = {row[0]: set(json.loads(row[1])) for row in rows}
        result_ids = set(rows_by_id.keys())
        if tags:
            tag_set = set(tags)
            result_ids = {rid for rid in result_ids if tag_set.issubset(rows_by_id[rid])}
        if exclude_tags:
            exclude_set = set(exclude_tags)
            result_ids = {rid for rid in result_ids if not exclude_set.intersection(rows_by_id[rid])}
        return result_ids

    def all_ids(self) -> set[str]:
        """Return the set of item IDs currently present in items_meta."""
        rows = self.connection.execute("SELECT id FROM items_meta").fetchall()
        return {row[0] for row in rows}


__all__ = ["MetadataIndex"]
