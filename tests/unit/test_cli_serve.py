from typer.testing import CliRunner

from agent_brain.interfaces.cli import app


runner = CliRunner()


def test_serve_defaults_to_loopback_host(monkeypatch):
    calls = []

    def fake_serve(*, host: str, port: int) -> None:
        calls.append({"host": host, "port": port})

    import web.app as web_app

    monkeypatch.setattr(web_app, "serve", fake_serve)

    result = runner.invoke(app, ["serve"])

    assert result.exit_code == 0, result.output
    assert calls == [{"host": "127.0.0.1", "port": 8765}]
    assert "Starting Admin UI on http://127.0.0.1:8765" in result.output


def test_serve_warns_when_binding_all_interfaces(monkeypatch):
    calls = []

    def fake_serve(*, host: str, port: int) -> None:
        calls.append({"host": host, "port": port})

    import web.app as web_app

    monkeypatch.setattr(web_app, "serve", fake_serve)

    result = runner.invoke(app, ["serve", "--host", "0.0.0.0"])

    assert result.exit_code == 0, result.output
    assert calls == [{"host": "0.0.0.0", "port": 8765}]
    assert "Starting Admin UI on http://127.0.0.1:8765" in result.output
    assert "listening on all network interfaces" in result.output
    assert "--host 0.0.0.0" in result.output


def test_web_app_serve_defaults_to_loopback_host(monkeypatch):
    calls = []

    def fake_uvicorn_run(app_obj, *, host: str, port: int) -> None:
        calls.append({"app": app_obj, "host": host, "port": port})

    import uvicorn
    import web.app as web_app

    monkeypatch.setattr(uvicorn, "run", fake_uvicorn_run)

    web_app.serve()

    assert calls == [{"app": web_app.app, "host": "127.0.0.1", "port": 8765}]
