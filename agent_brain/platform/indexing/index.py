from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path

import sqlite_vec

from agent_brain.platform.indexing.graph_index import GraphIndex
from agent_brain.platform.indexing.index_schema import init_index_schema, segment_cjk
from agent_brain.platform.indexing.index_types import Hit
from agent_brain.platform.indexing.index_writer import IndexWriter
from agent_brain.platform.indexing.metadata_index import MetadataIndex
from agent_brain.platform.indexing.vector_index import VectorIndex
from agent_brain.contracts.memory_item import MemoryItem

_segment_cjk = segment_cjk
_FALLBACK_SQLITE_VARIABLE_LIMIT = 900


class HubIndex:
    """Shadow index over items_dir. md is source of truth; this is rebuildable."""

    def __init__(self, db_path: Path, embedding_dim: int = 384) -> None:
        self.db_path = db_path
        self.embedding_dim = embedding_dim
        self.connection = self._connect()
        self.uses_sqlite_vec = self._load_sqlite_vec()
        existing_vector_schema = self._vector_table_schema()
        if existing_vector_schema and "using vec0" not in existing_vector_schema.lower():
            self.uses_sqlite_vec = False
        elif existing_vector_schema and not self.uses_sqlite_vec:
            # index.db is rebuildable from memory items. If this Python cannot
            # load sqlite-vec, a previous vec0 table would be unreadable here.
            self._reset_index_db()
            self.uses_sqlite_vec = False
        # WAL + busy_timeout let multiple agents share the pool without
        # "database is locked" crashes when one writes while another reads.
        self._init_schema()
        self.graph = GraphIndex(self.connection)
        self.metadata = MetadataIndex(self.connection)
        self.vector = VectorIndex(
            self.connection,
            embedding_dim=embedding_dim,
            uses_sqlite_vec=self.uses_sqlite_vec,
        )
        self.writer = IndexWriter(self.connection, self.vector)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path), check_same_thread=False)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=5000")
        except sqlite3.Error:
            pass
        return connection

    def _load_sqlite_vec(self) -> bool:
        enable_load_extension = getattr(self.connection, "enable_load_extension", None)
        if not callable(enable_load_extension):
            return False
        try:
            enable_load_extension(True)
            sqlite_vec.load(self.connection)
            return True
        except Exception:
            return False
        finally:
            try:
                enable_load_extension(False)
            except Exception:
                pass

    def _vector_table_schema(self) -> str | None:
        try:
            row = self.connection.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'items_vec'"
            ).fetchone()
        except sqlite3.Error:
            return None
        return str(row[0]) if row and row[0] else None

    def _reset_index_db(self) -> None:
        self.connection.close()
        for suffix in ("", "-wal", "-shm"):
            path = Path(f"{self.db_path}{suffix}")
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        self.connection = self._connect()

    def _init_schema(self) -> None:
        init_index_schema(
            self.connection,
            embedding_dim=self.embedding_dim,
            use_sqlite_vec=self.uses_sqlite_vec,
        )

    def upsert(
        self,
        item: MemoryItem,
        body: str,
        embedding: list[float] | None,
    ) -> None:
        self.writer.upsert(item, body, embedding)

    def delete(self, item_id: str) -> None:
        """Remove an item from all index tables (meta + FTS + vec + refs_graph)."""
        self.writer.delete(item_id)

    def bm25_search(
        self,
        query: str,
        top_k: int = 10,
        *,
        allowed_ids: set[str] | None = None,
        excluded_ids: set[str] | None = None,
    ) -> list[Hit]:
        if allowed_ids is not None and excluded_ids is not None:
            raise ValueError("allowed_ids and excluded_ids are mutually exclusive")
        if allowed_ids is None:
            if excluded_ids is not None:
                if top_k <= 0:
                    return []
                fetch_k = top_k + len(excluded_ids)
                rows = self.connection.execute(
                    "SELECT id, bm25(items_fts) AS score "
                    "FROM items_fts WHERE items_fts MATCH ? "
                    f"ORDER BY score LIMIT {fetch_k}",
                    (query,),
                ).fetchall()
                hits = [
                    Hit(id=row[0], score=-row[1])
                    for row in rows
                    if row[0] not in excluded_ids
                ]
                return hits[:top_k]
            rows = self.connection.execute(
                "SELECT id, bm25(items_fts) AS score "
                "FROM items_fts WHERE items_fts MATCH ? "
                "ORDER BY score LIMIT ?",
                (query, top_k),
            ).fetchall()
            # bm25() returns lower=better; invert so higher=better for caller
            return [Hit(id=row[0], score=-row[1]) for row in rows]
        if not allowed_ids or top_k <= 0:
            return []

        variable_limit = _sqlite_variable_limit(self.connection)
        if variable_limit < 2:
            setlimit = getattr(self.connection, "setlimit", None)
            previous_limit = None
            if callable(setlimit):
                previous_limit = setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 2)
            try:
                rows = self.connection.execute(
                    "SELECT id, bm25(items_fts) AS score "
                    "FROM items_fts WHERE items_fts MATCH ? ORDER BY score",
                    (query,),
                ).fetchall()
            finally:
                if previous_limit is not None:
                    setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, previous_limit)
            hits = [
                Hit(id=row[0], score=-row[1])
                for row in rows
                if row[0] in allowed_ids
            ]
            return hits[:top_k]
        chunk_size = variable_limit - 1  # query is the only fixed bind
        hits: list[Hit] = []
        for chunk in _chunked(sorted(allowed_ids), chunk_size):
            placeholders = ",".join("?" for _ in chunk)
            rows = self.connection.execute(
                "SELECT id, bm25(items_fts) AS score "
                "FROM items_fts WHERE items_fts MATCH ? "
                f"AND id IN ({placeholders}) "
                f"ORDER BY score LIMIT {top_k}",
                [query, *chunk],
            ).fetchall()
            hits.extend(Hit(id=row[0], score=-row[1]) for row in rows)
        hits.sort(key=lambda hit: (-hit.score, hit.id))
        return hits[:top_k]

    def vector_search(
        self,
        embedding: list[float],
        top_k: int = 10,
        *,
        allowed_ids: set[str] | None = None,
        excluded_ids: set[str] | None = None,
    ) -> list[Hit]:
        if allowed_ids is not None and excluded_ids is not None:
            raise ValueError("allowed_ids and excluded_ids are mutually exclusive")
        if excluded_ids is not None:
            if top_k <= 0:
                return []
            hits = self.vector.search(
                embedding,
                top_k=top_k + len(excluded_ids),
            )
            return [hit for hit in hits if hit.id not in excluded_ids][:top_k]
        if allowed_ids is None:
            return self.vector.search(embedding, top_k=top_k)
        if not allowed_ids or top_k <= 0:
            return []
        if len(embedding) != self.embedding_dim:
            raise ValueError(
                f"embedding dim {len(embedding)} != index dim {self.embedding_dim}"
            )
        variable_limit = _sqlite_variable_limit(self.connection)
        embeddings: dict[str, list[float]] = {}
        for chunk in _chunked(sorted(allowed_ids), variable_limit):
            embeddings.update(self.vector.get_embeddings(chunk))
        scored = [
            Hit(
                id=item_id,
                score=-sum((value - query_value) ** 2 for value, query_value in zip(vector, embedding)),
            )
            for item_id, vector in embeddings.items()
            if len(vector) == self.embedding_dim
        ]
        scored.sort(key=lambda hit: (-hit.score, hit.id))
        return scored[:top_k]

    def get_confidence_data(self, item_ids: list[str]) -> dict[str, tuple[float, str, str | None]]:
        """Return {id: (confidence, decay_class, last_accessed_iso)} for given ids."""
        return self.metadata.get_confidence_data(item_ids)

    def get_decay_data(
        self,
        item_ids: list[str],
    ) -> dict[str, tuple[float, str, str | None, str | None, int, int, int, float]]:
        """Return confidence, retention, and feedback fields for decay scoring."""
        return self.metadata.get_decay_data(item_ids)

    def get_search_metadata(self, item_ids: list[str]) -> dict[str, dict[str, object]]:
        """Return lightweight metadata used by post-retrieval scoring."""
        return self.metadata.get_search_metadata(item_ids)

    def get_projects(self, item_ids: Sequence[str]) -> dict[str, str | None]:
        """Return the stored project value for each existing item ID."""
        return self.metadata.get_projects(item_ids)

    def get_superseded_ids(self) -> set[str]:
        """Return IDs whose metadata marks them as superseded."""
        return self.metadata.get_superseded_ids()

    def get_feedback_data(self, item_ids: list[str]) -> dict[str, tuple[int, int, float]]:
        """Return {id: (support_count, contradict_count, gain_score)} for ids."""
        return self.metadata.get_feedback_data(item_ids)

    def record_access(self, item_id: str, accessed_at: str) -> None:
        """Increment access_count and update last_accessed for an item."""
        self.metadata.record_access(item_id, accessed_at)

    def record_access_many(self, item_ids: list[str], accessed_at: str) -> None:
        """Increment access_count for multiple items in one transaction."""
        self.metadata.record_access_many(item_ids, accessed_at)

    def update_confidence(self, item_id: str, confidence: float) -> None:
        """Set confidence for an item in the index."""
        self.metadata.update_confidence(item_id, confidence)

    def update_feedback_stats(
        self,
        item_id: str,
        *,
        support_count: int | None = None,
        contradict_count: int | None = None,
        gain_score: float | None = None,
    ) -> None:
        """Set feedback value stats for an item in the index."""
        self.metadata.update_feedback_stats(
            item_id,
            support_count=support_count,
            contradict_count=contradict_count,
            gain_score=gain_score,
        )

    def update_tier(self, item_id: str, tier: str) -> None:
        """Set the derived storage tier (hot/warm/cold) for an item."""
        self.metadata.update_tier(item_id, tier)

    def tier_counts(self) -> dict[str, int]:
        """Return {tier: count} over items that have a tier assigned."""
        return self.metadata.tier_counts()

    def get_text(self, item_id: str) -> str | None:
        """Return concatenated title+summary+body from FTS for a single item."""
        row = self.connection.execute(
            "SELECT title, summary, body FROM items_fts WHERE id = ?",
            (item_id,),
        ).fetchone()
        if row is None:
            return None
        return f"{row[0]} {row[1]} {row[2]}"

    def get_texts(self, item_ids: list[str]) -> dict[str, str]:
        """Batch-fetch texts for multiple items."""
        if not item_ids:
            return {}
        placeholders = ",".join("?" for _ in item_ids)
        rows = self.connection.execute(
            f"SELECT id, title, summary, body FROM items_fts WHERE id IN ({placeholders})",
            item_ids,
        ).fetchall()
        return {row[0]: f"{row[1]} {row[2]} {row[3]}" for row in rows}

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
        """Return the set of item IDs matching all given filters, or None if no filters are active."""
        return self.metadata.filter_ids(
            type=type,
            project=project,
            tags=tags,
            exclude_tags=exclude_tags,
            since_days=since_days,
            tenant_id=tenant_id,
            include_superseded=include_superseded,
        )

    def graph_neighbors(self, item_id: str, depth: int = 1) -> set[str]:
        """Return item IDs reachable from item_id within depth hops (bidirectional)."""
        return self.graph.neighbors(item_id, depth=depth)

    def add_ref(self, source_id: str, target_id: str, relation: str = "refs") -> None:
        """Add a single edge to the refs graph."""
        self.graph.add_ref(source_id, target_id, relation)

    def remove_ref(self, source_id: str, target_id: str) -> int:
        """Remove an edge from the refs graph. Returns rows deleted (0 or 1)."""
        return self.graph.remove_ref(source_id, target_id)

    def get_refs(self, item_id: str) -> list[tuple[str, str, str]]:
        """Return all edges involving item_id: list of (source, target, relation)."""
        return self.graph.refs_for(item_id)

    def get_embeddings(self, item_ids: list[str]) -> dict[str, list[float]]:
        """Return {id: embedding_vector} for the given item IDs."""
        return self.vector.get_embeddings(item_ids)

    def all_ids(self) -> set[str]:
        """Return the set of item IDs currently present in the index (items_meta).

        md is the source of truth; this lets a reconcile/prune pass diff the
        index against the md store and drop orphan rows whose md no longer
        exists (deleted or archived items that would otherwise stay as ghost
        search hits).
        """
        return self.metadata.all_ids()

    def close(self) -> None:
        self.connection.close()


def _sqlite_variable_limit(connection: sqlite3.Connection) -> int:
    getlimit = getattr(connection, "getlimit", None)
    if callable(getlimit):
        try:
            limit = int(getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER))
        except (AttributeError, sqlite3.Error, TypeError, ValueError):
            pass
        else:
            if limit > 0:
                return limit
    return _FALLBACK_SQLITE_VARIABLE_LIMIT


def _chunked(values: list[str], chunk_size: int) -> list[list[str]]:
    size = max(1, chunk_size)
    return [values[offset : offset + size] for offset in range(0, len(values), size)]
