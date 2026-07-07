from __future__ import annotations

import threading
from collections.abc import Callable
from sqlite3 import Connection


def _link_row(row) -> dict[str, str]:
    return {
        "source": row[0],
        "target": row[1],
        "relation": row[2],
        "created_by": row[3],
        "created_at": row[4],
    }


def link_exists(
    connection: Connection,
    lock: threading.Lock,
    source: str,
    target: str,
) -> bool:
    with lock:
        row = connection.execute(
            "SELECT 1 FROM item_links WHERE source = ? AND target = ?",
            (source, target),
        ).fetchone()
    return row is not None


def add_link(
    connection: Connection,
    lock: threading.Lock,
    source: str,
    target: str,
    relation: str,
    created_by: str,
    *,
    now: Callable[[], str],
) -> dict[str, str]:
    created_at = now()
    with lock:
        connection.execute(
            "INSERT INTO item_links (source, target, relation, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (source, target, relation, created_by, created_at),
        )
        connection.commit()
    return {
        "source": source,
        "target": target,
        "relation": relation,
        "created_by": created_by,
        "created_at": created_at,
    }


def links_for(
    connection: Connection,
    lock: threading.Lock,
    item_id: str,
) -> list[dict[str, str]]:
    with lock:
        rows = connection.execute(
            "SELECT source, target, relation, created_by, created_at FROM item_links "
            "WHERE source = ? OR target = ? ORDER BY rowid",
            (item_id, item_id),
        ).fetchall()
    return [_link_row(row) for row in rows]


def remove_link(
    connection: Connection,
    lock: threading.Lock,
    source: str,
    target: str,
) -> int:
    with lock:
        cursor = connection.execute(
            "DELETE FROM item_links WHERE source = ? AND target = ?",
            (source, target),
        )
        removed = cursor.rowcount
        connection.commit()
    return int(removed)


__all__ = ["add_link", "link_exists", "links_for", "remove_link"]
