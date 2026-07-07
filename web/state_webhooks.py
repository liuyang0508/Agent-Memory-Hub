"""Webhook persistence helpers for the Web Admin runtime state store."""
from __future__ import annotations

import json
import threading
from sqlite3 import Connection
from typing import Any


def list_webhooks(connection: Connection, lock: threading.Lock) -> list[dict[str, Any]]:
    with lock:
        rows = connection.execute(
            "SELECT url, events_json FROM webhooks ORDER BY rowid"
        ).fetchall()
    return [{"url": row[0], "events": json.loads(row[1])} for row in rows]


def add_webhook(connection: Connection, lock: threading.Lock, url: str, events: list[str]) -> int:
    """Add or update a webhook by URL. Returns the new total count."""
    with lock:
        connection.execute(
            "INSERT OR REPLACE INTO webhooks (url, events_json) VALUES (?, ?)",
            (url, json.dumps(events)),
        )
        connection.commit()
        total = connection.execute("SELECT COUNT(*) FROM webhooks").fetchone()[0]
    return int(total)


def remove_webhook(connection: Connection, lock: threading.Lock, url: str) -> tuple[int, int]:
    """Remove a webhook by URL. Returns (removed_count, remaining_total)."""
    with lock:
        cursor = connection.execute("DELETE FROM webhooks WHERE url = ?", (url,))
        removed = cursor.rowcount
        connection.commit()
        total = connection.execute("SELECT COUNT(*) FROM webhooks").fetchone()[0]
    return int(removed), int(total)


__all__ = ["add_webhook", "list_webhooks", "remove_webhook"]
