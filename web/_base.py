"""Agent Memory Hub Web Admin — infrastructure (state, helpers, models, middleware).

Split out of app.py so the route handlers (which stay in app.py) import this via
`from web._base import *`. Holds: SSE/WS subscriber state + rate-limit
state + component cache, the broadcast/visibility/audit/snapshot helpers, the
Pydantic request models, the lifespan, and the two middleware functions (registered
onto `app` back in app.py). Behaviour-identical to the pre-split module.
"""
from __future__ import annotations

import os
import threading
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from fastapi import FastAPI, HTTPException
from agent_brain.memory.store.write_service import WriteService
from web.auth import CurrentUser
from web.state_store import WebStateStore, close_state_store_cache, get_state_store
from web.live_events import (
    _event_visible_to,
    _sse_subscribers,
    _ws_send_and_reap,
    _ws_subscribers,
    broadcast_live_event,
)
from web.visibility import (
    require_visible as _require_visible,
    safe_item_id as _safe_item_id,
    visible as _visible,
)



@asynccontextmanager
async def _lifespan(application: FastAPI):
    try:
        _components()
    except Exception:
        pass
    try:
        yield
    finally:
        with _components_cache_lock:
            _components_cache.clear()
        close_state_store_cache()
_STATIC_DIR = Path(__file__).parent / "static"
_TEMPLATES_DIR = Path(__file__).parent / "templates"
def _audit(user: str, action: str, detail: str = ""):
    """Append an audit entry to the brain-dir-backed store (survives restart)."""
    _state_store().add_audit(user, action, detail)
def _broadcast_event(event_type: str, data: dict[str, Any], *,
                     tenant_id: str | None = None, admin_only: bool = False):
    broadcast_live_event(
        event_type,
        data,
        tenant_id=tenant_id,
        admin_only=admin_only,
        fire_webhooks=_fire_webhooks,
    )
def _fire_webhooks(event_type: str, data: dict[str, Any]):
    webhooks = _state_store().list_webhooks()
    if not webhooks:
        return
    import asyncio

    import httpx

    payload = {"event": event_type, "data": data, "ts": datetime.now(timezone.utc).isoformat()}

    def _send() -> None:
        for wh in webhooks:
            try:
                httpx.post(wh["url"], json=payload, timeout=5.0, headers={"X-Hub-Event": event_type})
            except Exception:
                pass

    # A synchronous httpx.post here would block the asyncio event loop for up to
    # 5s per webhook on every item mutation. Offload to a worker thread when a
    # loop is running; fall back to inline send (e.g. in sync tests / scripts).
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _send()
    else:
        loop.run_in_executor(None, _send)
def _brain_dir() -> Path:
    return Path(os.environ.get("BRAIN_DIR", os.path.expanduser("~/.agent-memory-hub")))
_MAX_COMPONENT_CACHE_ENTRIES = 8


class _ComponentsCache(OrderedDict[str, tuple]):
    def _close_components(self, components: tuple) -> None:
        if len(components) < 2:
            return
        close = getattr(components[1], "close", None)
        if callable(close):
            close()

    def __delitem__(self, key: str) -> None:
        components = self[key]
        super().__delitem__(key)
        self._close_components(components)

    def clear(self) -> None:
        cached = list(self.values())
        super().clear()
        for components in cached:
            self._close_components(components)

    def pop(self, key: str, default: Any = None) -> tuple | Any:
        if key not in self:
            return default
        components = super().pop(key)
        self._close_components(components)
        return components

    def popitem(self, last: bool = True) -> tuple[str, tuple]:
        key, components = super().popitem(last=last)
        self._close_components(components)
        return key, components


_components_cache: _ComponentsCache = _ComponentsCache()
_components_cache_lock = threading.Lock()


def _components():
    brain = _brain_dir()
    key = str(brain)
    with _components_cache_lock:
        components = _components_cache.get(key)
        if components is None:
            from agent_brain.memory.recall.retrieval import Retriever
            from agent_brain.memory.store.items_store import ItemsStore
            from agent_brain.platform.embedding import get_default_embedder
            from agent_brain.platform.indexing.index import HubIndex

            store = ItemsStore(items_dir=brain / "items")
            embedder = get_default_embedder()
            idx = HubIndex(db_path=brain / "index.db", embedding_dim=embedder.dim)
            retriever = Retriever(index=idx, embedder=embedder)
            components = (store, idx, retriever, embedder)
            _components_cache[key] = components
            while len(_components_cache) > max(1, _MAX_COMPONENT_CACHE_ENTRIES):
                _components_cache.popitem(last=False)
        else:
            _components_cache.move_to_end(key)
        return components
def _write_service() -> WriteService:
    from agent_brain.memory.store.items_store import ItemsStore

    brain = _brain_dir()
    store = ItemsStore(items_dir=brain / "items")
    return WriteService(
        store,
        lambda: _components()[1],
        lambda: _components()[3],
        brain_dir=brain,
    )
def _state_store() -> WebStateStore:
    """Brain-dir-backed store for audit log, webhooks, snapshots and links.

    These were RAM-only module globals (P2-11): lost on restart and not shared
    across worker processes. Persisting them to <brain>/web_state.db (sqlite,
    WAL) fixes both. Cached per brain dir like _components().
    """
    return get_state_store(_brain_dir())
def _save_snapshot(item_id: str, item_data: dict, body: str) -> None:
    """Persist a version snapshot to the brain-dir store (survives restart)."""
    _state_store().add_snapshot(item_id, item_data, body)
def _evict_from_index(item_id: str) -> None:
    """Drop an item from the sqlite index so deletes don't leave ghost hits."""
    try:
        _, idx, _, _ = _components()
        idx.delete(item_id)
    except Exception:  # noqa: BLE001 — best-effort eviction
        pass
def mutate_item(item_id: str, user: CurrentUser, updates: dict, *,
                event: str = "item_updated", snapshot: bool = False):
    """Single mutation primitive: load → tenant check → update md → reindex → broadcast.

    Every mutating route used to inline this sequence and could forget a step
    (the root cause behind the cross-tenant and ghost-hit bug classes). Routing
    mutations through here makes the correct sequence the only path. Returns the
    updated MemoryItem.
    """
    store, idx, _, embedder = _components()
    old_item, old_body = _require_visible(store, item_id, user)
    if snapshot:
        _save_snapshot(item_id, old_item.model_dump(mode="json"), old_body)
    try:
        updated = store.update_frontmatter(item_id, **updates)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="item not found")
    try:
        _, body = store.get(item_id)
    except FileNotFoundError:
        body = ""
    idx.upsert(
        updated,
        body,
        embedding=embedder.embed(updated.context_views.locator),
    )
    _broadcast_event(event, {"id": item_id, "fields": list(updates.keys())},
                     tenant_id=updated.tenant_id)
    return updated


__all__ = ['_lifespan', '_STATIC_DIR', '_TEMPLATES_DIR', '_audit', '_sse_subscribers', '_ws_subscribers', '_event_visible_to', '_ws_send_and_reap', '_broadcast_event', '_fire_webhooks', '_brain_dir', '_components_cache', '_components', '_write_service', '_state_store', '_save_snapshot', '_visible', '_safe_item_id', '_require_visible', '_evict_from_index', 'mutate_item']
