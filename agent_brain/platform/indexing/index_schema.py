"""SQLite schema and migration helpers for the rebuildable hub index."""
from __future__ import annotations

import sqlite3

from agent_brain.platform.indexing.text_scripts import is_cjk_search_char


def segment_cjk(text: str) -> str:
    """Insert spaces around CJK characters before FTS indexing."""
    if not text:
        return text
    out: list[str] = []
    for ch in text:
        if is_cjk_search_char(ch):
            out.append(" ")
            out.append(ch)
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def init_index_schema(
    conn: sqlite3.Connection,
    *,
    embedding_dim: int,
    use_sqlite_vec: bool = True,
) -> None:
    """Create and migrate all SQLite tables used by HubIndex."""
    vector_table_sql = (
        "CREATE VIRTUAL TABLE IF NOT EXISTS items_vec USING vec0("
        f"id TEXT PRIMARY KEY, embedding FLOAT[{embedding_dim}]);"
        if use_sqlite_vec
        else """
        CREATE TABLE IF NOT EXISTS items_vec (
            id TEXT PRIMARY KEY,
            embedding BLOB NOT NULL
        );
        """
    )
    conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS items_meta (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            project TEXT,
            created_at TEXT NOT NULL,
            tags_json TEXT NOT NULL DEFAULT '[]',
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0.7,
            decay_class TEXT NOT NULL DEFAULT 'fact',
            last_accessed TEXT,
            access_count INTEGER NOT NULL DEFAULT 0,
            sensitivity TEXT NOT NULL DEFAULT 'internal',
            tenant_id TEXT,
            support_count INTEGER NOT NULL DEFAULT 0,
            contradict_count INTEGER NOT NULL DEFAULT 0,
            gain_score REAL NOT NULL DEFAULT 0.0,
            superseded_by TEXT,
            maturity TEXT,
            context_views_json TEXT NOT NULL DEFAULT '{{}}'
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
            id UNINDEXED, title, summary, body, tokenize='unicode61'
        );
        {vector_table_sql}
        CREATE TABLE IF NOT EXISTS refs_graph (
            source_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            relation TEXT NOT NULL DEFAULT 'refs',
            PRIMARY KEY (source_id, target_id, relation)
        );
    """)
    conn.commit()
    migrate_meta_columns(conn)
    migrate_refs_graph(conn)


def migrate_refs_graph(conn: sqlite3.Connection) -> None:
    """Ensure the refs graph table exists for older index databases."""
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    if "refs_graph" not in tables:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS refs_graph (
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                relation TEXT NOT NULL DEFAULT 'refs',
                PRIMARY KEY (source_id, target_id, relation)
            )
        """)
        conn.commit()


def migrate_meta_columns(conn: sqlite3.Connection) -> None:
    """Add metadata columns introduced after the original index schema."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(items_meta)").fetchall()}
    migrations = [
        ("confidence", "REAL NOT NULL DEFAULT 0.7"),
        ("decay_class", "TEXT NOT NULL DEFAULT 'fact'"),
        ("last_accessed", "TEXT"),
        ("access_count", "INTEGER NOT NULL DEFAULT 0"),
        ("sensitivity", "TEXT NOT NULL DEFAULT 'internal'"),
        ("tenant_id", "TEXT"),
        ("tier", "TEXT"),
        ("support_count", "INTEGER NOT NULL DEFAULT 0"),
        ("contradict_count", "INTEGER NOT NULL DEFAULT 0"),
        ("gain_score", "REAL NOT NULL DEFAULT 0.0"),
        ("superseded_by", "TEXT"),
        ("maturity", "TEXT"),
        ("context_views_json", "TEXT NOT NULL DEFAULT '{}'"),
    ]
    for col, typedef in migrations:
        if col not in existing:
            conn.execute(f"ALTER TABLE items_meta ADD COLUMN {col} {typedef}")
    _backfill_context_loading_columns(conn)
    conn.commit()


def _backfill_context_loading_columns(conn: sqlite3.Connection) -> None:
    """Backfill maturity/context view metadata for older index rows."""
    rows = conn.execute(
        "SELECT id, summary, maturity, context_views_json FROM items_meta"
    ).fetchall()
    for item_id, summary, maturity, context_views_json in rows:
        updates: list[str] = []
        values: list[str] = []
        if maturity is None:
            updates.append("maturity = ?")
            values.append("raw")
        if not context_views_json or context_views_json == "{}":
            updates.append("context_views_json = ?")
            values.append(
                '{"locator": '
                + _json_string(summary or "")
                + ', "overview": "", "detail_uri": '
                + _json_string(f"memory://items/{item_id}/body")
                + "}"
            )
        if updates:
            values.append(item_id)
            conn.execute(
                f"UPDATE items_meta SET {', '.join(updates)} WHERE id = ?",
                values,
            )


def _json_string(value: str) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


__all__ = [
    "init_index_schema",
    "migrate_meta_columns",
    "migrate_refs_graph",
    "segment_cjk",
]
