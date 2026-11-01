"""Failure/recovery contracts exercised against real transactions and durable queues."""

import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient

from agent_brain.native_sync.api import create_app
from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.transport import Transport
from test_client import client
from test_hub import asset, enroll


def test_readiness_detects_writer_lock_and_recovers_without_restart(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    api = TestClient(create_app(hub))
    with sqlite3.connect(hub.path) as lock:
        lock.execute("BEGIN IMMEDIATE")
        assert api.get("/native/v1/health").status_code == 200
        response = api.get("/native/v1/ready")
        assert response.status_code == 503
        assert str(hub.path) not in response.text
        lock.rollback()
    assert api.get("/native/v1/ready").status_code == 200


def test_readiness_does_not_create_replacement_for_missing_database(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    hub.path.unlink()
    api = TestClient(create_app(hub))
    assert api.get("/native/v1/ready").status_code == 503
    assert not hub.path.exists()


def test_database_error_is_retryable_and_does_not_leak_internal_details(tmp_path, monkeypatch):
    hub = Hub(tmp_path / "hub.sqlite")

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("private database path and SQL")

    monkeypatch.setattr(hub, "identity", fail)
    response = TestClient(create_app(hub)).get(
        "/native/v1/me", headers={"Authorization": "Bearer synthetic"}
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "storage unavailable"}


def test_process_crash_cannot_commit_half_a_hub_revision(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    who = enroll(hub)
    saved = hub.put(who["token"], asset())
    # Kill the process without unwinding/closing its transaction. Assets, changes
    # and FTS must all retain the last committed version when reopened.
    script = """
import os, sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute("BEGIN IMMEDIATE")
db.execute("UPDATE assets SET payload=json_set(payload, '$.body', 'uncommitted')")
db.execute("INSERT INTO changes(tenant,payload) VALUES('linkcrux','uncommitted')")
db.execute("DELETE FROM search_index")
os._exit(42)
"""
    result = subprocess.run([sys.executable, "-c", script, str(hub.path)], timeout=10)
    assert result.returncode == 42
    restarted = Hub(hub.path)
    assert restarted.changes(who["token"], 0)["items"] == [{**saved, "verified": True}]
    assert restarted.search(who["token"], "订单")[0]["revision"] == 1


@pytest.mark.parametrize("failure", [OSError("offline"), PermissionError("revoked")])
def test_failed_connection_stops_batch_and_preserves_all_pending(tmp_path, failure):
    hub = Hub(tmp_path / "hub.sqlite")
    c = client(tmp_path, hub, "producer")
    source = tmp_path / "memory"
    source.mkdir()
    c.register(source, "claude-code", "p", "memory")
    for n in range(3):
        (source / f"{n}.md").write_text(f"recovery canary {n}")
    original = c.transport.put
    attempts = []

    def fail(value):
        attempts.append(value)
        raise failure

    c.transport.put = fail
    with pytest.raises(type(failure)):
        c.sync()
    assert len(attempts) == 1
    assert c.pending_count() == 3
    c.transport.put = original
    assert c.sync()["pushed"] == 3
    assert c.pending_count() == 0
    assert len(c.cached()) == 3


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_transient_http_failure_is_retryable_not_a_record_conflict(status, monkeypatch):
    transport = Transport("https://memory.example.com")

    def unavailable(*args, **kwargs):
        raise HTTPError(transport.url, status, "private server detail", {}, None)

    monkeypatch.setattr(transport.opener, "open", unavailable)
    with pytest.raises(OSError, match=str(status)):
        transport.me()


def test_parallel_replays_commit_one_revision_and_one_change(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    who = enroll(hub)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: hub.put(who["token"], asset()), range(12)))
    assert {r["revision"] for r in results} == {1}
    assert len(hub.changes(who["token"], 0)["items"]) == 1


def test_parallel_enrollment_consumes_pairing_ticket_once(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    host = hub.provision("member", "host")
    ticket = hub.runtime_invite(host["token"])["invite"]

    def attempt(_):
        try:
            return hub.enroll(ticket, "test-device")
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(attempt, range(12)))
    assert sum(r is not None for r in results) == 1


def test_hook_wake_storm_cannot_bypass_worker_failure_backoff(tmp_path, monkeypatch):
    from agent_brain.native_sync import worker

    state = tmp_path / "state"
    state.mkdir()
    (state / "config.json").write_text("{}")
    now = [0.0]
    attempts = []

    class OfflineClient:
        def __init__(self, root):
            pass

        def sync(self):
            attempts.append(now[0])
            raise OSError("offline")

    def advance(seconds):
        now[0] += seconds
        (state / "wake").touch()  # Continuous real Hook notifications.
        if now[0] > 60:
            raise KeyboardInterrupt

    monkeypatch.setattr(worker, "NativeClient", OfflineClient)
    monkeypatch.setattr(worker.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(worker.time, "sleep", advance)
    import random

    monkeypatch.setattr(random, "uniform", lambda lower, upper: upper)
    with pytest.raises(KeyboardInterrupt):
        worker.work(state)
    assert attempts == [0, 20, 60]
