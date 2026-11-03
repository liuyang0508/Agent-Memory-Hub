"""Remote browser onboarding: actual loopback HTTP, synthetic identity only."""

from concurrent.futures import ThreadPoolExecutor
import socket
import subprocess
import time
import urllib.error
import urllib.request

import pytest
import typer

from agent_brain.native_sync import cli, picker
from agent_brain.native_sync.client import NativeClient
from agent_brain.native_sync.install import launch_agent
from tests.native_sync.test_picker import rows


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def wait_page(opener, url, *, redirect=False):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            return opener.open(url, timeout=1)
        except urllib.error.HTTPError as exc:
            if redirect and exc.code == 302:
                return exc
            if exc.code != 400:
                raise
        except (urllib.error.URLError, ConnectionError):
            pass
        time.sleep(0.01)
    pytest.fail("browser endpoint did not become ready")


def test_login_then_picker_reuses_one_port_without_remote_browser(tmp_path, monkeypatch):
    port = free_port()
    url = f"http://127.0.0.1:{port}/"
    calls = []
    identity = dict(
        tenant_id="org",
        member_id="m",
        member_name="Example",
        device_id="new",
        token="synthetic-device-credential",
    )

    class Transport:
        def __init__(self, hub):
            pass

        def call(self, route, data=None):
            calls.append((route, data))
            if route == "auth/feishu":
                return {"available": True}
            if route == "auth/feishu/start":
                assert data == {"port": port}
                return {"authorize_url": "https://example.com/synthetic-authorization"}
            assert route == "auth/feishu/redeem"
            assert data == {"ticket": "synthetic-once", "device_name": "remote-mac"}
            return identity

    monkeypatch.setattr("agent_brain.native_sync.transport.Transport", Transport)
    monkeypatch.setattr("webbrowser.open", lambda *a: pytest.fail("must not open remote browser"))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with ThreadPoolExecutor() as pool:
        login = pool.submit(
            cli.feishu_login,
            "https://example.com",
            tmp_path,
            "remote-mac",
            5,
            port=port,
            open_browser=False,
        )
        response = wait_page(opener, url, redirect=True)
        assert response.code == 302
        assert response.headers["Location"] == "https://example.com/synthetic-authorization"
        response.close()
        assert opener.open(url + "?ticket=synthetic-once", timeout=2).status == 200
        assert login.result(timeout=5) == identity
        assert NativeClient(tmp_path).identity["device_id"] == "new"

        selection = pool.submit(picker.serve, rows(), port=port, open_browser=False, timeout=5)
        page = wait_page(opener, url)
        assert "synthetic-device-secret" not in page.read().decode()
        for origin in ("https://evil.example", f"http://127.0.0.1:{port + 1}"):
            req = urllib.request.Request(
                url + "submit",
                data=b'{"picked":[0]}',
                headers={"Content-Type": "application/json", "Origin": origin},
            )
            with pytest.raises(urllib.error.HTTPError) as caught:
                opener.open(req, timeout=2)
            assert caught.value.code == 403
        req = urllib.request.Request(
            url + "submit",
            data=b'{"picked":[0]}',
            headers={"Content-Type": "application/json", "Origin": f"http://localhost:{port}"},
        )
        assert opener.open(req, timeout=2).status == 200
        assert selection.result(timeout=5) == rows()[:1]
    # Both servers closed, so retries can use the same forwarding command.
    with pytest.raises(TimeoutError):
        picker.serve([], port=port, open_browser=False, timeout=0)


def test_login_timeout_releases_port_without_creating_profile(tmp_path, monkeypatch):
    class Transport:
        def __init__(self, hub):
            pass

        def call(self, route, data=None):
            return {"available": True, "authorize_url": "https://example.com/auth"}

    monkeypatch.setattr("agent_brain.native_sync.transport.Transport", Transport)
    port = free_port()
    for _ in range(2):
        with pytest.raises(typer.BadParameter, match="等待授权超时"):
            cli.feishu_login(
                "https://example.com", tmp_path, "mac", 0, port=port, open_browser=False
            )
    assert not (tmp_path / "config.json").exists()


def test_ssh_mode_auto_detection_and_override(monkeypatch, capsys):
    monkeypatch.setenv("SSH_CONNECTION", "synthetic")
    monkeypatch.setattr(typer, "confirm", lambda *a, **k: True)
    assert cli.browser_mode(None, 0) == (True, 8765)
    assert cli.browser_mode(True, 8080) == (True, 8080)
    assert cli.browser_mode(False, 0) == (False, 0)
    monkeypatch.delenv("SSH_CONNECTION")
    monkeypatch.delenv("SSH_TTY", raising=False)
    assert cli.browser_mode(None, 0) == (False, 0)
    output = capsys.readouterr().out
    assert "remote-claude forward 8765" in output
    assert "127.0.0.1:8080:127.0.0.1:8080" in output


def test_port_conflict_is_actionable(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agent_brain.native_sync.transport.Transport.call", lambda *a, **k: {"available": True}
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        with pytest.raises(typer.BadParameter, match="--web-port"):
            cli.feishu_login(
                "https://example.com",
                tmp_path,
                "mac",
                port=sock.getsockname()[1],
                open_browser=False,
            )
    assert not (tmp_path / "config.json").exists()


def test_ssh_service_uses_user_domain_without_gui_and_reuses_existing(tmp_path, monkeypatch):
    import plistlib

    calls = []
    existing = False

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0 if argv[1] == "bootstrap" or existing else 1)

    monkeypatch.setattr(cli.subprocess, "run", run)
    cli.start_background_service(tmp_path / "state", tmp_path, remote=True)
    assert calls[-1][1:3] == ["bootstrap", f"user/{cli.os.getuid()}"]
    path = launch_agent(tmp_path / "state", tmp_path, install=False)
    assert plistlib.loads(path.read_bytes())["LimitLoadToSessionType"] == "Background"
    existing = True
    calls.clear()
    cli.start_background_service(tmp_path / "state", tmp_path, remote=False)
    assert len(calls) == 1 and calls[0][1] == "print"


def test_service_failure_does_not_report_completed_install(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cli.subprocess, "run", lambda argv, **k: subprocess.CompletedProcess(argv, 1)
    )
    with pytest.raises(typer.Exit):
        cli.start_background_service(tmp_path / "state", tmp_path, remote=True)


def test_setup_passes_remote_browser_settings_before_any_profile_write(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    monkeypatch.setattr(cli, "require_research_service", lambda hub: None)
    monkeypatch.setattr(cli, "discover_products", lambda home: [])
    seen = []

    def login(hub, state, name, **kwargs):
        seen.append(kwargs)
        assert not (state / "config.json").exists()
        raise typer.Exit()

    monkeypatch.setattr(cli, "feishu_login", login)
    result = CliRunner().invoke(
        cli.app,
        [
            "setup",
            "--hub",
            "https://example.com",
            "--state",
            str(tmp_path / "state"),
            "--home",
            str(tmp_path),
            "--ssh",
            "--web-port",
            "8080",
        ],
        input="y\n",
    )
    assert result.exit_code == 0, result.output
    assert seen == [{"port": 8080, "open_browser": False}]
