"""Organization-scoped immutable revisions, identity and deterministic full-text recall."""

from __future__ import annotations

from typing import Any
import sqlite3

import json
import secrets
import time
import uuid
from pathlib import Path
from .common import database, digest, tokens
from .models import AssetInput


class Hub:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        with database(self.path) as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS members(id TEXT PRIMARY KEY,tenant TEXT NOT NULL,name TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS invites(hash TEXT PRIMARY KEY,member TEXT NOT NULL,expires REAL NOT NULL,used INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY,member TEXT NOT NULL,token_hash TEXT UNIQUE NOT NULL,name TEXT NOT NULL,revoked INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS assets(tenant TEXT NOT NULL,id TEXT NOT NULL,revision INTEGER NOT NULL,owner_device TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(tenant,id));
            CREATE TABLE IF NOT EXISTS changes(seq INTEGER PRIMARY KEY AUTOINCREMENT,tenant TEXT NOT NULL,payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS changes_tenant ON changes(tenant,seq);
            CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(tenant UNINDEXED,asset_id UNINDEXED,terms);
            """)

    def invite(
        self, tenant: str, name: str, *, member_id: str | None = None, ttl: int = 86400
    ) -> str:
        if not tenant.strip() or not name.strip() or len(tenant) > 128 or len(name) > 160:
            raise ValueError("tenant and member name required")
        token = secrets.token_urlsafe(32)
        with database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            if member_id:
                row = db.execute("SELECT tenant FROM members WHERE id=?", (member_id,)).fetchone()
                if row is None or row["tenant"] != tenant:
                    raise ValueError("member not found in this organization")
            else:
                member_id = "mem_" + uuid.uuid4().hex
                db.execute("INSERT INTO members VALUES(?,?,?)", (member_id, tenant, name))
            db.execute(
                "INSERT INTO invites VALUES(?,?,?,0)", (digest(token), member_id, time.time() + ttl)
            )
        return token

    def enroll(self, invite: str, device_name: str) -> dict[str, Any]:
        with database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT m.*,i.used,i.expires FROM invites i JOIN members m ON i.member=m.id WHERE i.hash=?",
                (digest(invite),),
            ).fetchone()
            if row is None or row["used"] or row["expires"] < time.time():
                raise ValueError("invitation invalid, expired or used")
            db.execute("UPDATE invites SET used=1 WHERE hash=?", (digest(invite),))
            device, token = "dev_" + uuid.uuid4().hex, secrets.token_urlsafe(32)
            db.execute(
                "INSERT INTO devices VALUES(?,?,?,?,0)",
                (device, row["id"], digest(token), device_name),
            )
            return dict(
                tenant_id=row["tenant"],
                member_id=row["id"],
                member_name=row["name"],
                device_id=device,
                token=token,
            )

    @staticmethod
    def _identity(db: sqlite3.Connection, token: str) -> dict[str, Any]:
        row = db.execute(
            "SELECT d.id device_id,m.id member_id,m.name,m.tenant FROM devices d JOIN members m ON m.id=d.member WHERE d.token_hash=? AND d.revoked=0",
            (digest(token),),
        ).fetchone()
        if row is None:
            raise PermissionError("device credential invalid or revoked")
        return dict(row)

    def identity(self, token: str) -> dict[str, Any]:
        with database(self.path) as db:
            return self._identity(db, token)

    def revoke(self, device_id: str) -> None:
        with database(self.path) as db:
            db.execute("UPDATE devices SET revoked=1 WHERE id=?", (device_id,))

    def put(self, token: str, value: dict[str, Any]) -> dict[str, Any]:
        item = AssetInput.model_validate(value).model_dump()
        # Hash content and its meaning, not the transport's base revision.
        content_hash = digest(
            json.dumps(
                {k: v for k, v in item.items() if k != "base_revision"},
                sort_keys=True,
                ensure_ascii=False,
            )
        )
        with database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            who = self._identity(db, token)
            old = db.execute(
                "SELECT * FROM assets WHERE tenant=? AND id=?", (who["tenant"], item["asset_id"])
            ).fetchone()
            previous: dict[str, Any] | None = json.loads(old["payload"]) if old else None
            if old and old["owner_device"] != who["device_id"]:
                raise ValueError("asset belongs to another source device")
            if previous and previous["content_hash"] == content_hash:
                return previous
            if item["base_revision"] != (old["revision"] if old else 0):
                raise ValueError("revision conflict; fetch current revision before retry")
            if previous and (
                previous["source_store"],
                previous["product"],
                previous["producer_unknown"],
            ) != (item["source_store"], item["product"], item["producer_unknown"]):
                raise ValueError("source identity is immutable")
            for ref in item["upstream"]:
                if not db.execute(
                    "SELECT 1 FROM assets WHERE tenant=? AND id=?", (who["tenant"], ref)
                ).fetchone():
                    raise ValueError("upstream asset missing in organization")
            producer = None if item["producer_unknown"] else who["member_id"]
            record = {
                **item,
                "tenant_id": who["tenant"],
                "revision": item["base_revision"] + 1,
                "producer_member_id": producer,
                "producer_name": "未知" if producer is None else who["name"],
                "uploader_member_id": who["member_id"],
                "uploader_name": who["name"],
                "source_device_id": who["device_id"],
                "content_hash": content_hash,
                "body_hash": digest(item["body"]),
                "observed_at": time.time(),
            }
            encoded = json.dumps(record, ensure_ascii=False)
            db.execute(
                "INSERT OR REPLACE INTO assets VALUES(?,?,?,?,?)",
                (who["tenant"], item["asset_id"], record["revision"], who["device_id"], encoded),
            )
            db.execute("INSERT INTO changes(tenant,payload) VALUES(?,?)", (who["tenant"], encoded))
            db.execute(
                "DELETE FROM search_index WHERE tenant=? AND asset_id=?",
                (who["tenant"], item["asset_id"]),
            )
            if not item["deleted"] and item["encoding"] == "utf8":
                indexed = (
                    item["index_text"]
                    if item["kind"] == "transcript" and item["index_text"] is not None
                    else item["body"]
                )
                words = tokens(item["title"] + "\n" + indexed)
                db.execute(
                    "INSERT INTO search_index VALUES(?,?,?)",
                    (who["tenant"], item["asset_id"], " ".join(words)),
                )
            return record

    def changes(self, token: str, cursor: int, limit: int = 100) -> dict[str, Any]:
        with database(self.path) as db:
            who = self._identity(db, token)
            rows = db.execute(
                "SELECT seq,payload FROM changes WHERE tenant=? AND seq>? ORDER BY seq LIMIT ?",
                (who["tenant"], max(0, cursor), min(max(1, limit), 100)),
            ).fetchall()
            return {
                "items": [json.loads(r["payload"]) for r in rows],
                "cursor": rows[-1]["seq"] if rows else cursor,
            }

    def search(
        self,
        token: str,
        query: str,
        *,
        project_hint: str = "",
        project_filter: str | None = None,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        terms = tokens(query)[:64]
        with database(self.path) as db:
            who = self._identity(db, token)
            if not terms:
                return []
            match = " OR ".join('"' + t + '"' for t in terms)
            rows = db.execute(
                "SELECT a.payload,bm25(search_index) score FROM search_index JOIN assets a ON a.tenant=search_index.tenant AND a.id=search_index.asset_id WHERE search_index MATCH ? AND search_index.tenant=? AND (? IS NULL OR json_extract(a.payload, '$.project')=?) ORDER BY score LIMIT 100",
                (match, who["tenant"], project_filter, project_filter),
            ).fetchall()
            results = []
            ready: dict[tuple[str, str, str], bool] = {}
            for row in rows:
                item = json.loads(row["payload"])
                if item.get("document_id"):
                    key = (item["source_device_id"], item["document_id"], item["document_hash"])
                    if key not in ready:
                        from .materialize import assembled

                        parts = db.execute(
                            "SELECT payload FROM assets WHERE tenant=? AND owner_device=? AND json_extract(payload,'$.document_id')=? AND json_extract(payload,'$.document_hash')=?",
                            (who["tenant"], *key),
                        ).fetchall()
                        ready[key] = bool(assembled([json.loads(p["payload"]) for p in parts]))
                    if not ready[key]:
                        continue
                if project_filter is not None and item["project"] != project_filter:
                    continue
                # The project is a bounded boost, never a visibility filter.
                item["score"] = -row["score"] * (
                    1.05 if project_hint and item["project"] == project_hint else 1
                )
                results.append(item)
            return sorted(results, key=lambda x: x["score"], reverse=True)[:limit]
