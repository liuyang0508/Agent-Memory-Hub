"""SQLite persistence boundary for two separate databases, never the legacy brain.

Schemas are initialization-only and preserve existing table/column order. A future
incompatible change needs an explicit versioned migration and restore test; CREATE
TABLE IF NOT EXISTS must never be used as an implicit migration mechanism.

Hub: identity, current assets, append-only change log and transactional FTS index.
Client: source selection, coalescing outbox, replica and observed context coverage.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator

DB_TIMEOUT: ContextVar[float] = ContextVar("native_db_timeout", default=10.0)


@contextmanager
def database(path: Path, *, create: bool = True) -> Iterator[sqlite3.Connection]:
    """Commit on success, roll back on error, always close (including setup failures).

    Connections belong to one operation/thread. Callers use BEGIN IMMEDIATE for
    read/modify/write invariants; no network or retry sleeps belong inside them.
    Hook/readiness contexts can lower DB_TIMEOUT without affecting other threads.
    """
    if create:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Readiness must report a missing database, not create an empty replacement.
    location = str(path) if create else path.resolve().as_uri() + "?mode=rw"
    db = sqlite3.connect(location, timeout=DB_TIMEOUT.get(), uri=not create)
    try:
        os.chmod(path, 0o600)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        with db:
            yield db
    finally:
        db.close()


HUB_SCHEMA = """
CREATE TABLE IF NOT EXISTS members(
    id TEXT PRIMARY KEY,
    tenant TEXT NOT NULL,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS devices(
    id TEXT PRIMARY KEY,
    member TEXT NOT NULL,
    token_hash TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    revoked INTEGER DEFAULT 0
);

-- Additive runtime-pairing state; existing invitations/devices retain their schema.
CREATE TABLE IF NOT EXISTS runtime_invites(
    hash TEXT PRIMARY KEY,
    member TEXT NOT NULL,
    sponsor_device TEXT NOT NULL,
    expires REAL NOT NULL,
    used INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS assets(
    tenant TEXT NOT NULL,
    id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    owner_device TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY(tenant,id)
);

-- Complete-version lookups must not scan every JSON payload in the tenant.
CREATE INDEX IF NOT EXISTS assets_document_version ON assets(
    tenant, owner_device,
    json_extract(payload,'$.source_store'),
    json_extract(payload,'$.document_id'),
    json_extract(payload,'$.document_hash')
);

CREATE TABLE IF NOT EXISTS changes(
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS changes_tenant ON changes(
    tenant,
    seq
);

-- Additive enterprise-login state; disabled instances never populate it.
CREATE TABLE IF NOT EXISTS feishu_members(
    open_id TEXT PRIMARY KEY,
    member TEXT NOT NULL
);

-- New identities carry their provider namespace. Legacy mappings stay intact
-- until a host administrator explicitly scopes them; names are never keys.
CREATE TABLE IF NOT EXISTS feishu_accounts(
    app_id TEXT NOT NULL,
    tenant_key TEXT NOT NULL,
    open_id TEXT NOT NULL,
    member TEXT NOT NULL,
    PRIMARY KEY(app_id,tenant_key,open_id)
);
CREATE TABLE IF NOT EXISTS feishu_states(
    hash TEXT PRIMARY KEY,
    port INTEGER NOT NULL,
    expires REAL NOT NULL,
    used INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS feishu_tickets(
    hash TEXT PRIMARY KEY,
    member TEXT NOT NULL,
    expires REAL NOT NULL,
    used INTEGER DEFAULT 0
);

-- Additive operator surface; an instance without an admin credential serves no admin page.
CREATE TABLE IF NOT EXISTS admins(
    hash TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    created REAL NOT NULL,
    revoked INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
    tenant UNINDEXED,
    asset_id UNINDEXED,
    terms
);
"""


CLIENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources(
    id TEXT PRIMARY KEY,
    root TEXT,
    product TEXT,
    project TEXT,
    kind TEXT,
    file_source INTEGER NOT NULL DEFAULT 0,
    UNIQUE(root,kind)
);

CREATE TABLE IF NOT EXISTS snapshots(
    source TEXT,
    path TEXT,
    hash TEXT,
    selected INTEGER,
    asset TEXT,
    revision INTEGER DEFAULT 0,
    unknown INTEGER DEFAULT 0,
    PRIMARY KEY(source,path)
);

CREATE TABLE IF NOT EXISTS source_parts(
    base TEXT,
    asset TEXT PRIMARY KEY,
    payload TEXT,
    revision INTEGER
);

CREATE TABLE IF NOT EXISTS outbox(
    asset TEXT PRIMARY KEY,
    payload TEXT,
    hash TEXT
);

CREATE TABLE IF NOT EXISTS lineage_records(
    asset TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS replica(
    asset TEXT PRIMARY KEY,
    payload TEXT
);

CREATE TABLE IF NOT EXISTS meta(
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS coverage(
    session TEXT,
    epoch TEXT,
    key TEXT,
    PRIMARY KEY(session,epoch,key)
);

CREATE TABLE IF NOT EXISTS history_hashes(
    hash TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS dynamic_targets(
    root TEXT PRIMARY KEY,
    product TEXT
);

CREATE TABLE IF NOT EXISTS file_identity(
    source TEXT,
    path TEXT,
    device INTEGER,
    inode INTEGER,
    PRIMARY KEY(source,path)
);

CREATE TABLE IF NOT EXISTS aliases(
    source TEXT,
    path TEXT,
    refs TEXT,
    status TEXT,
    PRIMARY KEY(source,path)
);

CREATE TABLE IF NOT EXISTS native_proofs(
    session TEXT,
    epoch TEXT,
    source TEXT,
    path TEXT,
    hash TEXT,
    PRIMARY KEY(session,epoch,source,path)
);

CREATE TABLE IF NOT EXISTS recall_sources(
    asset TEXT,
    revision INTEGER,
    payload TEXT,
    PRIMARY KEY(asset,revision)
);

CREATE TABLE IF NOT EXISTS emitted(
    session TEXT,
    marker TEXT,
    keys_json TEXT,
    created REAL,
    PRIMARY KEY(session,marker)
);
"""
