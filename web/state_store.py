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
from datetime import datetime, timezone
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
