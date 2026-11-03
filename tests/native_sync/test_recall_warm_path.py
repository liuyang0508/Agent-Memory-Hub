"""Warm recall path: resident service, keep-alive transport and the configurable budget."""

import json
import shutil
import socket
import tempfile
import threading
import time

import pytest
import uvicorn
from pathlib import Path

from agent_brain.native_sync import hooks, recall_service
from agent_brain.native_sync.api import create_app
from agent_brain.native_sync.client import NativeClient
from agent_brain.native_sync.common import (
    RECALL_BUDGET_DEFAULT,
    RECALL_BUDGET_KEY,
    hook_timeout,
    recall_budget,
    request_timeout,
)
from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.install import OWNER, install_hooks
from agent_brain.native_sync.recall_service import RecallService, ServiceUnavailable, request
from agent_brain.native_sync.transport import HubHTTPError, WarmTransport
from agent_brain.native_sync.worker import work
from test_hub import asset
from test_runtime import live as live  # noqa: F401

FAVOURS_A = "订单 验签 签名 密钥"


@pytest.fixture
def short_keepalive(tmp_path):
    """A Hub whose server drops idle connections after one second."""
    hub = Hub(tmp_path / "hub.sqlite")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(create_app(hub), log_level="error", timeout_keep_alive=1)
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.01)
    assert server.started
    yield hub, f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(5)
    sock.close()


@pytest.fixture
def short_state():
    """A state directory short enough for a Unix socket path (macOS caps it at 104 bytes)."""
    root = Path(tempfile.mkdtemp(prefix="amh-warm-"))
    try:
        yield root / "state"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def seeded(hub):
    author = hub.provision("作者", "mac-author")
    hub.put(author["token"], asset("a", body="订单回调验签失败检查签名密钥版本", title="订单验签"))
    return author


def test_budget_comes_from_env_then_config_and_is_clamped(monkeypatch):
    monkeypatch.delenv("AMH_RECALL_TIMEOUT", raising=False)
    assert recall_budget({}) == RECALL_BUDGET_DEFAULT
    assert recall_budget(None) == RECALL_BUDGET_DEFAULT
    assert recall_budget({RECALL_BUDGET_KEY: 3}) == 3.0
    assert recall_budget({RECALL_BUDGET_KEY: "oops"}) == RECALL_BUDGET_DEFAULT
    assert recall_budget({RECALL_BUDGET_KEY: 99}) == 10.0
    assert recall_budget({RECALL_BUDGET_KEY: 0.01}) == 0.3
    assert recall_budget({RECALL_BUDGET_KEY: float("inf")}) == RECALL_BUDGET_DEFAULT
    monkeypatch.setenv("AMH_RECALL_TIMEOUT", "2.5")
    assert recall_budget({RECALL_BUDGET_KEY: 3}) == 2.5
    monkeypatch.setenv("AMH_RECALL_TIMEOUT", "")
    assert recall_budget({RECALL_BUDGET_KEY: 3}) == 3.0
    assert request_timeout(1.2) == 1.0 and request_timeout(0.3) == 0.3
    assert hook_timeout(1.2) == 2 and hook_timeout(3.0) == 4 and hook_timeout(0.3) == 2


def test_warm_transport_reuses_one_connection_and_survives_the_server_dropping_it(
    short_keepalive,
):
    hub, url = short_keepalive
    author = seeded(hub)
    reader = hub.provision("读者", "mac-reader")
    transport = WarmTransport(url, reader["token"], timeout=5)
    first = transport.recall(FAVOURS_A, limit=1)
    second = transport.recall(FAVOURS_A, limit=1, asking={"product": "claude-code"})
    assert first["items"] and second["items"] and transport.connects == 1
    assert "recall-parity-v1" in transport.capabilities() and transport.connects == 1
    time.sleep(1.6)  # the server's idle timeout has passed; the socket is dead on its side
    assert transport.connected()
    third = transport.recall(FAVOURS_A, limit=1)
    assert third["items"] and transport.connects == 2
    # Error classes match the cold transport.
    with pytest.raises(HubHTTPError) as rejected:
        transport.call("no-such-route")
    assert rejected.value.http_status == 404
    with pytest.raises(PermissionError):
        WarmTransport(url, "not-a-token", timeout=5).me()
    assert author["token"] != reader["token"]


def test_service_answers_over_the_socket_and_keeps_the_connection_warm(live, short_state):
    hub, url = live
    seeded(hub)
    c = NativeClient.adopt(short_state, url, hub.provision("李四", "Mac"), "Mac")
    service = RecallService(c.root, ping_interval=0.3)
    service.configure(c.config)
    service.start()
    try:
        assert (c.root / recall_service.SOCKET_NAME).exists()
        message = {"query": FAVOURS_A, "asking": {"product": "claude-code"}, "limit": 1}
        first = request(c.root, message, timeout=5)
        second = request(c.root, message, timeout=5)
        assert first["channel"] == "recall" and len(first["items"]) == 1
        assert len(first["trace_id"]) == 24 and second["trace_id"] != first["trace_id"]
        assert service.transport is not None and service.transport.connects == 1
        assert service.served == 2
        stored = hub.recalls(c.config["identity"]["token"], trace_id=first["trace_id"])
        assert stored["traces"][0]["asking"]["product"] == "claude-code"
        time.sleep(1.2)
        assert service.pings >= 1 and service.transport.connects == 1
        with pytest.raises(ValueError):
            request(c.root, {"nonsense": True}, timeout=5)
        # A revoked or re-issued token is adopted from config, never cached.
        service.configure({**c.config, "identity": {**c.config["identity"], "token": "bad"}})
        with pytest.raises(PermissionError):
            request(c.root, message, timeout=5)
        service.configure(c.config)
        assert request(c.root, message, timeout=5)["items"]
    finally:
        service.close()
    assert not (c.root / recall_service.SOCKET_NAME).exists()
    with pytest.raises(ServiceUnavailable):
        request(c.root, message, timeout=1)


def test_hook_prefers_the_service_and_falls_back_to_the_subprocess(live, short_state):
    hub, url = live
    seeded(hub)
    c = NativeClient.adopt(short_state, url, hub.provision("李四", "Mac"), "Mac")
    payload = {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": FAVOURS_A}

    def last_metric():
        return json.loads((c.root / "hook-metrics.jsonl").read_text().splitlines()[-1])

    service = RecallService(c.root)
    service.configure(c.config)
    service.start()
    try:
        result = hooks.run(c.root, "claude-code", payload)
        assert "订单" in result["hookSpecificOutput"]["additionalContext"]
        metric = last_metric()
        assert metric["via"] == "service" and metric["mode"] == "remote"
        assert metric["channel"] == "recall" and metric["budget_ms"] == 1200
        assert service.served == 1
    finally:
        service.close()
    # A dead socket file left behind by a crashed worker is not a service either.
    (c.root / recall_service.SOCKET_NAME).touch()
    result = hooks.run(c.root, "claude-code", {**payload, "session_id": "s2"})
    assert "订单" in result["hookSpecificOutput"]["additionalContext"]
    metric = last_metric()
    assert metric["via"] == "worker" and metric["mode"] == "remote"
    assert metric["channel"] == "recall" and len(metric["trace_id"]) == 24


def test_worker_hosts_the_service_and_follows_config_changes(live, short_state):
    hub, url = live
    seeded(hub)
    c = NativeClient.adopt(short_state, url, hub.provision("李四", "Mac"), "Mac")
    stop = threading.Event()
    thread = threading.Thread(target=work, args=(c.root, 0.3), kwargs={"stop": stop}, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if (c.root / recall_service.SOCKET_NAME).exists():
                break
            time.sleep(0.05)
        message = {"query": FAVOURS_A, "limit": 1}
        assert request(c.root, message, timeout=5)["items"]
        broken = {**c.config, "identity": {**c.config["identity"], "token": "bad"}}
        (c.root / "config.json").write_text(json.dumps(broken))
        deadline = time.monotonic() + 5
        while True:
            try:
                request(c.root, message, timeout=5)
            except PermissionError:
                break
            assert time.monotonic() < deadline, "worker did not adopt the new config"
            time.sleep(0.1)
    finally:
        stop.set()
        thread.join(5)
    assert not thread.is_alive()
    assert not (c.root / recall_service.SOCKET_NAME).exists()


def test_recall_timeout_command_rewrites_the_host_hook_timeout(live, tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from agent_brain.native_sync.cli import app

    hub, url = live
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("AMH_RECALL_TIMEOUT", raising=False)
    state = tmp_path / "state"
    NativeClient.adopt(state, url, hub.provision("李四", "Mac"), "Mac")
    install_hooks(home, state, products=["claude-code"])

    def prompt_timeout():
        settings = json.loads((home / ".claude/settings.json").read_text())
        entries = settings["hooks"]["UserPromptSubmit"]
        return [h["timeout"] for e in entries for h in e["hooks"] if OWNER in h["command"]]

    assert prompt_timeout() == [2]
    runner = CliRunner()
    shown = runner.invoke(app, ["recall-timeout", "--state", str(state)])
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output)["timeout_seconds"] == 1.2
    changed = runner.invoke(app, ["recall-timeout", "3", "--state", str(state)])
    assert changed.exit_code == 0, changed.output
    report = json.loads(changed.output)
    assert report["hook_timeout_seconds"] == 4
    assert str(home / ".claude/settings.json") in report["hooks_rewritten"]
    assert json.loads((state / "config.json").read_text())[RECALL_BUDGET_KEY] == 3.0
    assert prompt_timeout() == [4]
    rejected = runner.invoke(app, ["recall-timeout", "30", "--state", str(state)])
    assert rejected.exit_code != 0
    assert json.loads(runner.invoke(app, ["status", "--state", str(state)]).output)["recall"] == {
        "timeout_seconds": 3.0,
        "configured": 3.0,
        "env_override": None,
        "hook_timeout_seconds": 4,
        "resident_service": False,
    }
