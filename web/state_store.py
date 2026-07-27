"""Persistent, brain-dir-scoped store for web-admin runtime state.

P2-11: the web admin kept its audit log, webhooks, item version snapshots and
manual item links in module-level Python globals (``_audit_log``, ``_webhooks``,
``_item_snapshots``, ``_item_links``). That state was RAM-only — lost on every
restart and *not shared* across uvicorn worker processes (each worker had its
own globals, so a webhook added on worker A was invisible to worker B and an
audit entry written on B never appeared on A).

This module persists all four to ``<brain>/web_state.db`` (a small sqlite db,
opened WAL + busy_timeout like ``core.index.HubIndex``) so the state survives
restarts and is shared by every worker pointed at the same brain dir. Stores are
cached per brain dir, mirroring ``web.app._components()``.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from web.state_storage import open_web_state_connection
from web.state_links import (
    add_link,
    link_exists,
    links_for,
    remove_link,
)
from web.state_webhooks import (
    add_webhook,
    list_webhooks,
    remove_webhook,
)

# Match the caps the old in-memory globals enforced.
AUDIT_MAX = 500
MAX_SNAPSHOTS_PER_ITEM = 20
_MAX_STATE_CACHE_ENTRIES = 8


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WebStateStore:
    """SQLite-backed persistence for web-admin audit/webhooks/snapshots/links."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        # check_same_thread=False because FastAPI/Starlette may dispatch requests
        # from different worker threads; a Lock serializes access. WAL +
        # busy_timeout mirror core.index.HubIndex so concurrent workers sharing
        # the file don't hit "database is locked".
        self.connection = open_web_state_connection(self.db_path)
        self._lock = threading.Lock()
        self._closed = False

    def close(self) -> None:
        """Close the underlying sqlite connection once."""
        with self._lock:
            if self._closed:
                return
            self.connection.close()
            self._closed = True

    # ─── Audit log ───

    def add_audit(self, user: str, action: str, detail: str = "") -> None:
        with self._lock:
            self.connection.execute(
                "INSERT INTO audit_log (ts, user, action, detail) VALUES (?, ?, ?, ?)",
                (_now(), user, action, detail),
            )
            # Keep only the most recent AUDIT_MAX rows (old global popped from front).
            self.connection.execute(
                "DELETE FROM audit_log WHERE id NOT IN "
                "(SELECT id FROM audit_log ORDER BY id DESC LIMIT ?)",
                (AUDIT_MAX,),
            )
            self.connection.commit()

    def list_audit(self, limit: int = 50) -> tuple[list[dict[str, Any]], int]:
        """Return (entries newest-first, total) — matches the old endpoint shape."""
        with self._lock:
            rows = self.connection.execute(
                "SELECT ts, user, action, detail FROM audit_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            total = self.connection.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        entries = [
            {"ts": r[0], "user": r[1], "action": r[2], "detail": r[3]} for r in rows
        ]
        return entries, int(total)

    # ─── Webhooks ───

    def list_webhooks(self) -> list[dict[str, Any]]:
        return list_webhooks(self.connection, self._lock)

    def add_webhook(self, url: str, events: list[str]) -> int:
        """Add (or update) a webhook by url. Returns the new total count."""
        return add_webhook(self.connection, self._lock, url, events)

    def remove_webhook(self, url: str) -> tuple[int, int]:
        """Returns (removed_count, remaining_total)."""
        return remove_webhook(self.connection, self._lock, url)

    # ─── Item version snapshots ───

    def add_snapshot(self, item_id: str, frontmatter: dict, body: str) -> None:
        with self._lock:
            self.connection.execute(
                "INSERT INTO item_snapshots (item_id, ts, frontmatter_json, body) "
                "VALUES (?, ?, ?, ?)",
                (item_id, _now(), json.dumps(frontmatter), body),
            )
            # Retain only the most recent MAX_SNAPSHOTS_PER_ITEM per item.
            self.connection.execute(
                "DELETE FROM item_snapshots WHERE item_id = ? AND id NOT IN "
                "(SELECT id FROM item_snapshots WHERE item_id = ? ORDER BY id DESC LIMIT ?)",
                (item_id, item_id, MAX_SNAPSHOTS_PER_ITEM),
            )
            self.connection.commit()

    def list_snapshots(self, item_id: str) -> list[dict[str, Any]]:
        """Oldest-first, matching the old in-memory list order (index 0 = oldest)."""
        with self._lock:
            rows = self.connection.execute(
                "SELECT ts, frontmatter_json, body FROM item_snapshots "
                "WHERE item_id = ? ORDER BY id ASC",
                (item_id,),
            ).fetchall()
        return [
            {"timestamp": r[0], "frontmatter": json.loads(r[1]), "body": r[2]}
            for r in rows
        ]

    # ─── Manual item links ───

    def link_exists(self, source: str, target: str) -> bool:
        return link_exists(self.connection, self._lock, source, target)

    def add_link(self, source: str, target: str, relation: str, created_by: str) -> dict[str, str]:
        return add_link(self.connection, self._lock, source, target, relation, created_by, now=_now)

    def links_for(self, item_id: str) -> list[dict[str, str]]:
        return links_for(self.connection, self._lock, item_id)

    def remove_link(self, source: str, target: str) -> int:
        return remove_link(self.connection, self._lock, source, target)

    # ─── Opaque end-to-end encrypted sync objects ───

    def add_sync_objects(
        self,
        tenant_id: str,
        rows: list[dict[str, str]],
    ) -> tuple[int, int]:
        accepted = existing = 0
        now = _now()
        device_ids: set[str] = set()
        with self._lock:
            for row in rows:
                device_ids.add(row["device_id"])
                cursor = self.connection.execute(
                    "INSERT OR IGNORE INTO encrypted_sync_objects "
                    "(tenant_id, object_key, payload, device_id, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        tenant_id,
                        row["object_key"],
                        row["payload"],
                        row["device_id"],
                        now,
                    ),
                )
                if cursor.rowcount:
                    accepted += 1
                else:
                    existing += 1
                self.connection.execute(
                    "INSERT INTO sync_devices "
                    "(tenant_id, device_id, object_count, last_seen) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(tenant_id, device_id) DO UPDATE SET "
                    "last_seen = excluded.last_seen",
                    (tenant_id, row["device_id"], 0, now),
                )
            for device_id in device_ids:
                count = int(
                    self.connection.execute(
                        "SELECT COUNT(*) FROM encrypted_sync_objects "
                        "WHERE tenant_id = ? AND device_id = ?",
                        (tenant_id, device_id),
                    ).fetchone()[0]
                )
                self.connection.execute(
                    "UPDATE sync_devices SET object_count = ? "
                    "WHERE tenant_id = ? AND device_id = ?",
                    (count, tenant_id, device_id),
                )
            self.connection.commit()
        return accepted, existing

    def list_sync_objects(
        self,
        tenant_id: str,
        *,
        limit: int = 10_000,
    ) -> list[dict[str, str]]:
        with self._lock:
            rows = self.connection.execute(
                "SELECT object_key, payload, device_id, created_at "
                "FROM encrypted_sync_objects WHERE tenant_id = ? "
                "ORDER BY created_at, object_key LIMIT ?",
                (tenant_id, limit),
            ).fetchall()
        return [
            {
                "object_key": str(row[0]),
                "payload": str(row[1]),
                "device_id": str(row[2]),
                "created_at": str(row[3]),
            }
            for row in rows
        ]

    def touch_sync_device(
        self,
        tenant_id: str,
        *,
        device_id: str,
        device_name: str = "",
        client_version: str = "",
        object_count: int = 0,
    ) -> None:
        with self._lock:
            self.connection.execute(
                "INSERT INTO sync_devices "
                "(tenant_id, device_id, device_name, client_version, object_count, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(tenant_id, device_id) DO UPDATE SET "
                "device_name = excluded.device_name, "
                "client_version = excluded.client_version, "
                "object_count = excluded.object_count, "
                "last_seen = excluded.last_seen",
                (
                    tenant_id,
                    device_id,
                    device_name,
                    client_version,
                    max(0, object_count),
                    _now(),
                ),
            )
            self.connection.commit()

    def list_sync_devices(self, tenant_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.connection.execute(
                "SELECT device_id, device_name, client_version, object_count, last_seen "
                "FROM sync_devices WHERE tenant_id = ? ORDER BY last_seen DESC, device_id",
                (tenant_id,),
            ).fetchall()
        return [
            {
                "device_id": str(row[0]),
                "device_name": str(row[1]),
                "client_version": str(row[2]),
                "object_count": int(row[3]),
                "last_seen": str(row[4]),
            }
            for row in rows
        ]

    def sync_stats(self, tenant_id: str) -> dict[str, Any]:
        active_after = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()
        with self._lock:
            object_count = int(
                self.connection.execute(
                    "SELECT COUNT(*) FROM encrypted_sync_objects WHERE tenant_id = ?",
                    (tenant_id,),
                ).fetchone()[0]
            )
            row = self.connection.execute(
                "SELECT COUNT(*), "
                "SUM(CASE WHEN last_seen >= ? THEN 1 ELSE 0 END), "
                "MAX(last_seen) FROM sync_devices WHERE tenant_id = ?",
                (active_after, tenant_id),
            ).fetchone()
        return {
            "objects": object_count,
            "devices": int(row[0] or 0),
            "active_24h": int(row[1] or 0),
            "last_seen": str(row[2]) if row[2] else None,
        }

    # ─── Organization directory and live role assignments ───

    def ensure_organization(
        self,
        tenant_id: str,
        *,
        created_by: str,
        name: str | None = None,
    ) -> dict[str, str]:
        now = _now()
        org_name = (name or tenant_id).strip() or tenant_id
        with self._lock:
            self.connection.execute(
                "INSERT OR IGNORE INTO organizations "
                "(tenant_id, name, created_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (tenant_id, org_name, created_by, now, now),
            )
            self.connection.commit()
            row = self.connection.execute(
                "SELECT tenant_id, name, created_by, created_at, updated_at "
                "FROM organizations WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
        return {
            "tenant_id": str(row[0]),
            "name": str(row[1]),
            "created_by": str(row[2]),
            "created_at": str(row[3]),
            "updated_at": str(row[4]),
        }

    def rename_organization(self, tenant_id: str, name: str) -> dict[str, str]:
        with self._lock:
            self.connection.execute(
                "UPDATE organizations SET name = ?, updated_at = ? WHERE tenant_id = ?",
                (name, _now(), tenant_id),
            )
            self.connection.commit()
        return self.ensure_organization(tenant_id, created_by="", name=name)

    def get_org_member(self, tenant_id: str, username: str) -> dict[str, str] | None:
        with self._lock:
            row = self.connection.execute(
                "SELECT username, role, created_at, updated_at "
                "FROM organization_members WHERE tenant_id = ? AND username = ?",
                (tenant_id, username),
            ).fetchone()
        if row is None:
            return None
        return {
            "username": str(row[0]),
            "role": str(row[1]),
            "created_at": str(row[2]),
            "updated_at": str(row[3]),
        }

    def set_org_member(self, tenant_id: str, username: str, role: str) -> dict[str, str]:
        now = _now()
        with self._lock:
            self.connection.execute(
                "INSERT INTO organization_members "
                "(tenant_id, username, role, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(tenant_id, username) DO UPDATE SET "
                "role = excluded.role, updated_at = excluded.updated_at",
                (tenant_id, username, role, now, now),
            )
            self.connection.commit()
        member = self.get_org_member(tenant_id, username)
        assert member is not None
        return member

    def list_org_members(self, tenant_id: str) -> list[dict[str, str]]:
        with self._lock:
            rows = self.connection.execute(
                "SELECT username, role, created_at, updated_at "
                "FROM organization_members WHERE tenant_id = ? AND role != 'revoked' "
                "ORDER BY CASE role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 "
                "WHEN 'member' THEN 2 ELSE 3 END, username",
                (tenant_id,),
            ).fetchall()
        return [
            {
                "username": str(row[0]),
                "role": str(row[1]),
                "created_at": str(row[2]),
                "updated_at": str(row[3]),
            }
            for row in rows
        ]

    def remove_org_member(self, tenant_id: str, username: str) -> bool:
        with self._lock:
            cursor = self.connection.execute(
                "UPDATE organization_members SET role = 'revoked', updated_at = ? "
                "WHERE tenant_id = ? AND username = ? AND role != 'revoked'",
                (_now(), tenant_id, username),
            )
            self.connection.commit()
        return bool(cursor.rowcount)

    def count_org_owners(self, tenant_id: str) -> int:
        with self._lock:
            row = self.connection.execute(
                "SELECT COUNT(*) FROM organization_members "
                "WHERE tenant_id = ? AND role = 'owner'",
                (tenant_id,),
            ).fetchone()
        return int(row[0])


class _StateStoreCache(OrderedDict[str, WebStateStore]):
    def _close_store(self, store: WebStateStore) -> None:
        store.close()

    def __delitem__(self, key: str) -> None:
        store = self[key]
        super().__delitem__(key)
        self._close_store(store)

    def clear(self) -> None:
        stores = list(self.values())
        super().clear()
        for store in stores:
            self._close_store(store)

    def pop(self, key: str, default: Any = None) -> WebStateStore | Any:
        if key not in self:
            return default
        store = super().pop(key)
        self._close_store(store)
        return store

    def popitem(self, last: bool = True) -> tuple[str, WebStateStore]:
        key, store = super().popitem(last=last)
        self._close_store(store)
        return key, store


_state_cache: _StateStoreCache = _StateStoreCache()
_state_cache_lock = threading.Lock()


def close_state_store_cache() -> None:
    with _state_cache_lock:
        _state_cache.clear()


def get_state_store(brain_dir: Path) -> WebStateStore:
    """Return a WebStateStore for ``brain_dir``, cached per brain dir."""
    key = str(brain_dir)
    with _state_cache_lock:
        store = _state_cache.get(key)
        if store is None:
            store = WebStateStore(Path(brain_dir) / "web_state.db")
            _state_cache[key] = store
            while len(_state_cache) > max(1, _MAX_STATE_CACHE_ENTRIES):
                _state_cache.popitem(last=False)
        else:
            _state_cache.move_to_end(key)
        return store
