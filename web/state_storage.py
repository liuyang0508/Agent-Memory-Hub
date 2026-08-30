"""SQLite connection and schema helpers for web-admin runtime state."""
from __future__ import annotations

import sqlite3
from pathlib import Path


WEB_STATE_DDL = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    user TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS webhooks (
    url TEXT PRIMARY KEY,
    events_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS item_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    frontmatter_json TEXT NOT NULL,
    body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_item ON item_snapshots(item_id, id);
CREATE TABLE IF NOT EXISTS item_links (
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    relation TEXT NOT NULL DEFAULT 'related',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (source, target)
);
"""


def open_web_state_connection(db_path: Path) -> sqlite3.Connection:
    """Open a web-state SQLite connection and ensure schema exists."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), check_same_thread=False)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
    except sqlite3.Error:
        pass
    connection.executescript(WEB_STATE_DDL)
    connection.commit()
    return connection


__all__ = ["WEB_STATE_DDL", "open_web_state_connection"]
