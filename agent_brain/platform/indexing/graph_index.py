from __future__ import annotations

import sqlite3


class GraphIndex:
    """Knowledge-graph operations backed by the ``refs_graph`` table."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def neighbors(self, item_id: str, depth: int = 1) -> set[str]:
        """Return item IDs reachable from item_id within depth hops."""
        visited: set[str] = set()
        frontier: set[str] = {item_id}
        for _ in range(depth):
            if not frontier:
                break
            placeholders = ",".join("?" for _ in frontier)
            rows = self.connection.execute(
                f"SELECT source_id, target_id FROM refs_graph "
                f"WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})",
                list(frontier) + list(frontier),
            ).fetchall()
            next_frontier: set[str] = set()
            for src, tgt in rows:
                if src not in visited and src != item_id:
                    next_frontier.add(src)
                if tgt not in visited and tgt != item_id:
                    next_frontier.add(tgt)
            visited.update(frontier)
            frontier = next_frontier - visited
        visited.update(frontier)
        visited.discard(item_id)
        return visited

    def add_ref(self, source_id: str, target_id: str, relation: str = "refs") -> None:
        """Add a single edge."""
        self.connection.execute(
            "INSERT OR IGNORE INTO refs_graph (source_id, target_id, relation) "
            "VALUES (?, ?, ?)",
            (source_id, target_id, relation),
        )
        self.connection.commit()

    def remove_ref(self, source_id: str, target_id: str) -> int:
        """Remove an edge. Returns rows deleted."""
        cur = self.connection.execute(
            "DELETE FROM refs_graph WHERE source_id = ? AND target_id = ?",
            (source_id, target_id),
        )
        self.connection.commit()
        return cur.rowcount

    def refs_for(self, item_id: str) -> list[tuple[str, str, str]]:
        """Return all edges involving item_id."""
        rows = self.connection.execute(
            "SELECT source_id, target_id, relation FROM refs_graph "
            "WHERE source_id = ? OR target_id = ?",
            (item_id, item_id),
        ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]


__all__ = ["GraphIndex"]
