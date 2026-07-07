"""Vector embedding operations for HubIndex."""
from __future__ import annotations

import sqlite3
import struct

from agent_brain.platform.indexing.index_types import Hit


def serialize_vec(vector: list[float]) -> bytes:
    """Serialize a float vector into sqlite-vec's binary format."""
    return struct.pack(f"{len(vector)}f", *vector)


def deserialize_vec(blob: bytes) -> list[float]:
    """Deserialize a vector from the shared binary format."""
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


class VectorIndex:
    """Operations backed by sqlite-vec or a portable SQLite blob fallback."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        embedding_dim: int,
        *,
        uses_sqlite_vec: bool = True,
    ) -> None:
        self.connection = connection
        self.embedding_dim = embedding_dim
        self.uses_sqlite_vec = uses_sqlite_vec

    def upsert(self, item_id: str, embedding: list[float]) -> None:
        """Insert or replace one embedding vector."""
        if len(embedding) != self.embedding_dim:
            raise ValueError(
                f"embedding dim {len(embedding)} != index dim {self.embedding_dim}"
            )
        self.connection.execute("DELETE FROM items_vec WHERE id = ?", (item_id,))
        self.connection.execute(
            "INSERT INTO items_vec (id, embedding) VALUES (?, ?)",
            (item_id, serialize_vec(embedding)),
        )

    def delete(self, item_id: str) -> None:
        """Remove one embedding vector."""
        self.connection.execute("DELETE FROM items_vec WHERE id = ?", (item_id,))

    def search(self, embedding: list[float], top_k: int = 10) -> list[Hit]:
        """Return nearest vectors as hits, with higher score meaning closer."""
        if not self.uses_sqlite_vec:
            if len(embedding) != self.embedding_dim:
                raise ValueError(
                    f"embedding dim {len(embedding)} != index dim {self.embedding_dim}"
                )
            rows = self.connection.execute("SELECT id, embedding FROM items_vec").fetchall()
            scored: list[Hit] = []
            for item_id, blob in rows:
                vector = deserialize_vec(blob)
                if len(vector) != self.embedding_dim:
                    continue
                distance = sum((a - b) ** 2 for a, b in zip(vector, embedding))
                scored.append(Hit(id=item_id, score=-distance))
            scored.sort(key=lambda hit: hit.score, reverse=True)
            return scored[:top_k]

        rows = self.connection.execute(
            "SELECT id, distance FROM items_vec "
            "WHERE embedding MATCH ? AND k = ? "
            "ORDER BY distance",
            (serialize_vec(embedding), top_k),
        ).fetchall()
        return [Hit(id=row[0], score=-row[1]) for row in rows]

    def get_embeddings(self, item_ids: list[str]) -> dict[str, list[float]]:
        """Return {id: embedding_vector} for the given item IDs."""
        if not item_ids:
            return {}
        placeholders = ",".join("?" for _ in item_ids)
        rows = self.connection.execute(
            f"SELECT id, embedding FROM items_vec WHERE id IN ({placeholders})",
            item_ids,
        ).fetchall()
        result: dict[str, list[float]] = {}
        for item_id, blob in rows:
            result[item_id] = deserialize_vec(blob)
        return result


__all__ = ["VectorIndex", "serialize_vec", "deserialize_vec"]
