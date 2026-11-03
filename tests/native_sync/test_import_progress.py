"""Progress must distinguish local selection from acknowledged cloud uploads."""

import json

import pytest
from typer.testing import CliRunner

from agent_brain.native_sync import cli, history
from agent_brain.native_sync.hub import Hub
from test_client import client
from test_history_import import make_home


def test_history_check_reports_progress_without_uploading(tmp_path):
    home = make_home(tmp_path)
    hub = Hub(tmp_path / "hub.sqlite")
    c = client(tmp_path, hub, "A")
    rows = [r for r in history.items(home, ["claude-code"]) if r["kind"] == "memory"]
    events = []
    result = history.import_paths(c, home, rows, progress=lambda *event: events.append(event))
    assert result["selected"] == 2
    assert events[0] == ("prepare", 0, None)
    assert events[-1] == ("check", 2, 2)
    assert not hub.search(c.identity["token"], "订单回调")
    assert all(len(event) == 3 for event in events)  # No file paths or bodies in callbacks.


def test_upload_progress_advances_only_after_acknowledgement(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    c = client(tmp_path, hub, "A")
    root = tmp_path / "memory"
    root.mkdir()
    c.register(root, "claude-code", "", "memory")
    (root / "one.md").write_text("FIRST_UPLOAD_CANARY")
    (root / "two.md").write_text("SECOND_UPLOAD_CANARY")
    original = c.transport.put
    acknowledgements = []
    events = []

    def put(record):
        if acknowledgements:
            raise OSError("offline")
        saved = original(record)
        acknowledgements.append(saved["asset_id"])
        return saved

    def progress(stage, completed, total):
        events.append((stage, completed, total))
        if stage == "upload":
            assert completed == len(acknowledgements)

    c.transport.put = put
    with pytest.raises(OSError, match="offline"):
        c.sync(progress=progress)
    assert ("upload", 0, 2) in events
    assert ("upload", 1, 2) in events
    assert ("upload", 2, 2) not in events
    assert c.pending_count() == 1
    c.transport.put = original
    retry = []
    c.sync(progress=lambda *event: retry.append(event))
    assert ("upload", 1, 1) in retry
    assert c.pending_count() == 0


def test_sync_cli_keeps_json_on_stdout_and_progress_on_stderr(tmp_path, monkeypatch):
    hub = Hub(tmp_path / "hub.sqlite")
    c = client(tmp_path, hub, "A")
    root = tmp_path / "memory"
    root.mkdir()
    c.register(root, "claude-code", "", "memory")
    (root / "one.md").write_text("PRIVATE_BODY_NOT_FOR_PROGRESS")
    monkeypatch.setattr(cli, "load", lambda state: c)
    result = CliRunner().invoke(cli.app, ["sync", "--state", str(c.root)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["pushed"] == 1
    assert "上传到组织" in result.stderr
    assert "1/1" in result.stderr
    assert "PRIVATE_BODY_NOT_FOR_PROGRESS" not in result.output


def test_first_sync_failure_reports_pending_without_claiming_success(tmp_path, monkeypatch):
    hub = Hub(tmp_path / "hub.sqlite")
    c = client(tmp_path, hub, "A")
    root = tmp_path / "memory"
    root.mkdir()
    c.register(root, "claude-code", "", "memory")
    (root / "one.md").write_text("QUEUED_CANARY")
    monkeypatch.setattr(c.transport, "put", lambda item: (_ for _ in ()).throw(OSError("offline")))
    assert cli.initial_sync(c) is False
    assert c.pending_count() == 1


@pytest.mark.parametrize("answer,expected", [("n\n", []), ("y\n", ["install", "sync"])])
def test_setup_only_starts_visible_sync_after_enable_confirmation(
    tmp_path, monkeypatch, answer, expected
):
    from agent_brain.native_sync.common import atomic_json

    hub = Hub(tmp_path / "hub.sqlite")
    c = client(tmp_path, hub, "A")
    root = tmp_path / "memory"
    root.mkdir()
    c.register(root, "claude-code", "", "memory")
    c.config.update(hub="http://127.0.0.1:9", identity=c.identity)
    atomic_json(c.root / "config.json", c.config)
    events = []
    monkeypatch.setattr(cli, "load", lambda state: c)
    monkeypatch.setattr(cli, "discover_products", lambda home: [])
    monkeypatch.setattr(cli, "require_research_service", lambda hub: None)
    monkeypatch.setattr(history, "items", lambda *args, **kwargs: [])
    monkeypatch.setattr(cli, "install_hooks", lambda *args, **kwargs: events.append("install"))
    monkeypatch.setattr(cli, "initial_sync", lambda client: events.append("sync"))
    result = CliRunner().invoke(
        cli.app,
        ["setup", "--hub", c.config["hub"], "--state", str(c.root), "--no-service"],
        input=answer,
    )
    assert result.exit_code == 0, result.output
    assert events == expected
