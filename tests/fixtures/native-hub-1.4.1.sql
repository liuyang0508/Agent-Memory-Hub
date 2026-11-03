-- Hub schema from v1.4.1 (006fa153), before browser administrator sessions.

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

-- Additive indexes for bounded explicit-reference and historical-version lookups.
CREATE INDEX IF NOT EXISTS assets_source_path ON assets(
    tenant, owner_device,
    json_extract(payload,'$.source_store'),
    json_extract(payload,'$.path')
);
CREATE INDEX IF NOT EXISTS changes_asset_revision ON changes(
    tenant,
    json_extract(payload,'$.asset_id'),
    json_extract(payload,'$.revision')
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
