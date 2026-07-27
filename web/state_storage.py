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
CREATE TABLE IF NOT EXISTS encrypted_sync_objects (
    tenant_id TEXT NOT NULL,
    object_key TEXT NOT NULL,
    payload TEXT NOT NULL,
    device_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, object_key)
);
CREATE INDEX IF NOT EXISTS idx_sync_objects_tenant
ON encrypted_sync_objects(tenant_id, created_at, object_key);
CREATE TABLE IF NOT EXISTS organizations (
    tenant_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS organization_members (
    tenant_id TEXT NOT NULL,
    username TEXT NOT NULL,
    role TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, username)
);
CREATE INDEX IF NOT EXISTS idx_org_members_tenant_role
ON organization_members(tenant_id, role, username);
CREATE TABLE IF NOT EXISTS sync_devices (
    tenant_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    device_name TEXT NOT NULL DEFAULT '',
    client_version TEXT NOT NULL DEFAULT '',
    object_count INTEGER NOT NULL DEFAULT 0,
    last_seen TEXT NOT NULL,
    PRIMARY KEY (tenant_id, device_id)
);
CREATE INDEX IF NOT EXISTS idx_sync_devices_tenant_seen
ON sync_devices(tenant_id, last_seen DESC);
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
