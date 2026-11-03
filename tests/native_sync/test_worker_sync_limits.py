"""Real Hub queues plus a deterministic clock for slow background network passes."""

import json

import pytest
from typer.testing import CliRunner

from agent_brain.native_sync import worker
from agent_brain.native_sync.cli import app
from agent_brain.native_sync.common import atomic_json
from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.org_tools import OrganizationTools
from agent_brain.native_sync.storage import database
from agent_brain.native_sync.transport import Transport
from test_client import client


def persisted_client(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    c = client(tmp_path, hub, "worker-device")
    c.config.update(hub="https://worker.example.com", identity=c.identity)
    atomic_json(c.root / "config.json", c.config)
    return c, hub


def sources(c, tmp_path, transcripts=5):
    history, memories = tmp_path / "sessions", tmp_path / "native-memory"
    history.mkdir()
    memories.mkdir()
    c.register(history, "claude-code", "", "transcript")
    c.register(memories, "claude-code", "", "memory")
    for number in range(transcripts):
        (history / f"{number}.md").write_text(f"historical conversation {number}")
    return history, memories


def test_worker_bounds_pass_then_rescans_and_prioritizes_new_memory(tmp_path, monkeypatch):
    c, hub = persisted_client(tmp_path)
    _, memories = sources(c, tmp_path)
    c.config["sync_worker"] = {"max_uploads": 2}
    atomic_json(c.root / "config.json", c.config)
    sent, passes = [], []
    put = c.transport.put

    def capture(item):
        sent.append((item["kind"], item["path"]))
        return put(item)

    c.transport.put = capture
    original_sync = c.sync

    def bounded_sync(**limits):
        passes.append(original_sync(**limits))
        if len(passes) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(c, "sync", bounded_sync)
    monkeypatch.setattr(worker, "background_client", lambda *a: c)
    now = [0.0]
    monkeypatch.setattr(worker.time, "monotonic", lambda: now[0])

    def next_pass(seconds):
        now[0] += seconds
        (memories / "new.md").write_text("current priority nova memory")
        (c.root / "wake").touch()

    monkeypatch.setattr(worker.time, "sleep", next_pass)
    with pytest.raises(KeyboardInterrupt):
        worker.work(c.root)
    assert [p["pushed"] for p in passes] == [2, 2]
    assert all(p["upload_deferred"] for p in passes)
    assert sent[2] == ("memory", "new.md")
    assert c.pending_count() == 2
    assert hub.search(c.identity["token"], "nova")
    # No limits remains the existing manual drain behavior.
    assert original_sync()["pushed"] == 2
    assert c.pending_count() == 0


def test_time_budget_retains_queue_and_feedback_but_replays_one_change_page(tmp_path, monkeypatch):
    c, _ = persisted_client(tmp_path)
    sources(c, tmp_path, transcripts=6)
    now = [0.0]
    monkeypatch.setattr(worker.time, "monotonic", lambda: now[0])
    put, changes = c.transport.put, c.transport.changes
    reads = []

    def slow_put(item):
        now[0] += 11.5  # The observed response latency exceeds the old 10s deadline.
        return put(item)

    def slow_changes(*a, **kw):
        reads.append(a)
        now[0] += 13
        return changes(*a, **kw)

    c.transport.put, c.transport.changes = slow_put, slow_changes
    c.transport.recall_feedback = lambda *a: pytest.fail("expired budget must retain feedback")
    with database(c.path) as db:
        db.execute(
            "INSERT INTO recall_reports VALUES(?,?,?,?,?)",
            ("trace", "asset", 1, "emitted", 9999999999),
        )
    result = c.sync(max_uploads=32, pass_budget_seconds=20)
    assert result["pushed"] == 2 and result["upload_deferred"] and result["budget_exhausted"]
    assert c.pending_count() == 4 and len(reads) == 1
    with database(c.path) as db:
        assert db.execute("SELECT COUNT(*) FROM recall_reports").fetchone()[0] == 1
        assert int(db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()[0]) > 0
    status = json.loads((c.root / "status.json").read_text())
    assert status["pending"] == 4 and status["upload_attempts"] == 2
    assert status["sync_limits"]["pass_budget_seconds"] == 20


@pytest.mark.parametrize("failure", [TimeoutError("deadline"), PermissionError("revoked")])
def test_conflicts_count_toward_batch_and_transport_failure_still_propagates(tmp_path, failure):
    c, _ = persisted_client(tmp_path)
    sources(c, tmp_path, transcripts=4)
    put = c.transport.put
    attempts = []

    def reject(item):
        attempts.append(item)
        raise ValueError("record conflict")

    c.transport.put = reject
    result = c.sync(max_uploads=2, pass_budget_seconds=45)
    assert len(attempts) == len(result["errors"]) == 2
    assert result["upload_deferred"] and c.pending_count() == 4
    attempts.clear()

    def unavailable(item):
        attempts.append(item)
        if len(attempts) == 1:
            return put(item)
        raise failure

    c.transport.put = unavailable
    with pytest.raises(type(failure)):
        c.sync(max_uploads=3, pass_budget_seconds=45)
    assert len(attempts) == 2 and c.pending_count() == 3
    c.transport.put = put
    assert c.sync()["pushed"] == 3 and c.pending_count() == 0


def test_background_timeout_accepts_slow_response_without_changing_mcp_defaults(
    tmp_path, monkeypatch
):
    c, _ = persisted_client(tmp_path)
    settings = worker.sync_settings(c.config)
    background = worker.background_client(c.root, c.config, settings)
    assert background.transport.timeout == 30
    assert OrganizationTools(c.root)._connect()[0].timeout == 10
    assert Transport(c.config["hub"]).timeout == 10
    now = [0.0]
    monkeypatch.setattr(worker.time, "monotonic", lambda: now[0])

    class SlowResponse:
        headers = {}
        consumed = False

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read1(self, size):
            if self.consumed:
                return b""
            self.consumed = True
            now[0] += 13.17
            return b'{"ok":true}'

    observed_timeouts = []

    def slow_open(req, *, timeout):
        observed_timeouts.append(timeout)
        return SlowResponse()

    monkeypatch.setattr(background.transport.opener, "open", slow_open)
    assert background.transport.me() == {"ok": True}
    regular = Transport(c.config["hub"])
    monkeypatch.setattr(regular.opener, "open", slow_open)
    with pytest.raises(TimeoutError, match="deadline"):
        regular.me()
    assert observed_timeouts == [30, 10]
    report = CliRunner().invoke(app, ["status", "--state", str(c.root)])
    assert report.exit_code == 0, report.output
    assert json.loads(report.output)["sync_worker"] == settings


@pytest.mark.parametrize(
    "values",
    [
        {"request_timeout_seconds": float("inf")},
        {"request_timeout_seconds": 121},
        {"pass_budget_seconds": 0},
        {"pass_budget_seconds": float("nan")},
        {"max_uploads": True},
        {"max_uploads": 1001},
        {"unknown": 1},
    ],
)
def test_worker_rejects_invalid_limits(values):
    with pytest.raises(ValueError, match="sync_worker"):
        worker.sync_settings({"sync_worker": values})


def test_worker_explicit_limits_are_profile_scoped(tmp_path):
    c, _ = persisted_client(tmp_path)
    c.config["sync_worker"] = {
        "request_timeout_seconds": 45,
        "max_uploads": 8,
        "pass_budget_seconds": 60,
    }
    settings = worker.sync_settings(c.config)
    assert settings == c.config["sync_worker"]
    assert worker.background_client(c.root, c.config, settings).transport.timeout == 45
    assert worker.sync_settings({})["request_timeout_seconds"] == 30
