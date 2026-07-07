"""Regression tests for P3-8 web robustness fixes.

Covers three independent defects:
  1. users.yaml read-modify-write must be atomic (temp-file + os.replace).
  2. rate-limit middleware must tolerate a junk MEMORY_HUB_RATE_LIMIT env value
     (no 500) and must bound its per-IP store.
  3. WebSocket broadcast must reap dead subscribers.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ─── 1. atomic users.yaml write ───

def test_save_users_atomic_preserves_file_on_commit_failure(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path))
    from web import auth

    auth._save_users([{"username": "alice", "api_key": "k1"}])
    users_file = tmp_path / "users.yaml"
    original = users_file.read_text(encoding="utf-8")
    assert "alice" in original

    def boom(src, dst):
        raise OSError("simulated crash during commit")

    # Patch the atomic-commit step; the original file must survive untouched.
    monkeypatch.setattr(auth.os, "replace", boom)
    with pytest.raises(OSError):
        auth._save_users([{"username": "bob", "api_key": "k2"}])

    # File is neither truncated nor overwritten with the failed write.
    assert users_file.read_text(encoding="utf-8") == original
    assert "alice" in users_file.read_text(encoding="utf-8")
    assert "bob" not in users_file.read_text(encoding="utf-8")
    # No orphaned temp files left behind.
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "users.yaml"]
    assert leftovers == []


# ─── 2a. rate-limit: junk env value must not 500 ───

def test_rate_limit_bad_env_value_does_not_500(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path))
    monkeypatch.setenv("MEMORY_HUB_RATE_LIMIT", "not-a-number")
    from web.app import app

    client = TestClient(app, raise_server_exceptions=False)
    # Middleware runs before routing; an auth-protected route returns 401, but
    # the bad env value must not blow up the middleware with a 500.
    resp = client.get("/api/items/whatever/history")
    assert resp.status_code != 500
    assert resp.status_code == 401


# ─── 2b. rate-limit: per-IP store is bounded ───

def test_rate_limit_store_prunes_stale_ips(monkeypatch):
    from web import app as webapp

    monkeypatch.setenv("MEMORY_HUB_RATE_LIMIT", "120")
    monkeypatch.setattr(webapp, "_RATE_LIMIT_MAX_IPS", 2)
    webapp._rate_limit_store.clear()

    stale = time.time() - webapp._RATE_LIMIT_WINDOW - 10
    webapp._rate_limit_store["1.1.1.1"] = [stale]
    webapp._rate_limit_store["2.2.2.2"] = [stale]
    webapp._rate_limit_store["3.3.3.3"] = [stale]

    class _Client:
        host = "9.9.9.9"

    class _Req:
        client = _Client()

    async def _call_next(_req):
        return webapp.Response(content="ok", status_code=200)

    resp = asyncio.run(webapp.rate_limit_middleware(_Req(), _call_next))
    assert resp.status_code == 200
    # The flood of aged-out IPs is swept; only the live requester remains.
    assert "1.1.1.1" not in webapp._rate_limit_store
    assert "2.2.2.2" not in webapp._rate_limit_store
    assert "3.3.3.3" not in webapp._rate_limit_store
    assert "9.9.9.9" in webapp._rate_limit_store
    webapp._rate_limit_store.clear()


# ─── 3. WebSocket dead-subscriber cleanup ───

def test_broadcast_reaps_dead_websocket(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path))
    (tmp_path / "items").mkdir(exist_ok=True)
    from web import app as webapp

    class _DeadWS:
        async def send_text(self, _text):
            raise RuntimeError("connection closed")

    bad = _DeadWS()
    # Subscribers are (ws, tenant_id, is_admin) tuples after the SSE/WS
    # tenant-scoping change (P2-12); admin so the event is delivered.
    entry = (bad, None, True)
    webapp._ws_subscribers.clear()
    webapp._ws_subscribers.append(entry)

    async def _scenario():
        webapp._broadcast_event("test.event", {"x": 1})
        # Let the scheduled send task run and reap the dead socket.
        await asyncio.sleep(0.05)

    asyncio.run(_scenario())
    assert entry not in webapp._ws_subscribers
    webapp._ws_subscribers.clear()
