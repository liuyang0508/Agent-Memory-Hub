import json

from typer.testing import CliRunner

from agent_brain.native_sync import cli
from agent_brain.native_sync.hub import Hub
from test_hub import asset
from test_hybrid_retrieval import SyntheticBackend


def test_operator_index_requires_existing_database_and_private_credential(tmp_path, monkeypatch):
    db = tmp_path / "missing.sqlite"
    result = CliRunner().invoke(cli.app, ["semantic-index", "--db", str(db), "--model", "cached"])
    assert result.exit_code != 0
    assert not db.exists()
    Hub(db)
    monkeypatch.delenv("AMH_NATIVE_INDEX_TOKEN", raising=False)
    result = CliRunner().invoke(cli.app, ["semantic-index", "--db", str(db), "--model", "cached"])
    assert result.exit_code != 0
    assert "AMH_NATIVE_INDEX_TOKEN" in result.output


def test_operator_refresh_watch_reuses_vectors_and_stops_on_revocation(tmp_path, monkeypatch):
    backend = SyntheticBackend()
    db = tmp_path / "hub.sqlite"
    hub = Hub(db, semantic_backend=backend)
    who = hub.provision("Operator", "private-device")
    hub.put(who["token"], asset("fact", body="approval requires two people"))
    monkeypatch.setenv("AMH_NATIVE_INDEX_TOKEN", who["token"])
    monkeypatch.setattr("agent_brain.native_sync.hub.Hub", lambda *a, **kw: hub)
    rounds = []

    def tick(seconds):
        rounds.append(seconds)
        if len(rounds) == 2:
            hub.revoke(who["device_id"])

    monkeypatch.setattr(cli.time, "sleep", tick)
    result = CliRunner().invoke(
        cli.app,
        ["semantic-index", "--db", str(db), "--model", "cached", "--watch", "--interval", "5"],
    )
    assert result.exit_code != 0
    assert rounds == [5, 5]
    assert '"reused": 1' in result.output
    assert len(backend.seen) == 1
    assert who["token"] not in result.output
    assert "已撤销" in result.output


def test_operator_profile_credential_never_appears_in_results(tmp_path, monkeypatch):
    db = tmp_path / "hub.sqlite"
    hub = Hub(db, semantic_backend=SyntheticBackend())
    who = hub.provision("Operator", "private-device")
    state = tmp_path / "profile"
    state.mkdir()
    (state / "config.json").write_text(json.dumps({"identity": who}))
    monkeypatch.setattr("agent_brain.native_sync.hub.Hub", lambda *a, **kw: hub)
    result = CliRunner().invoke(
        cli.app, ["semantic-index", "--db", str(db), "--model", "cached", "--state", str(state)]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["semantic_index"]["indexed"] == 0
    assert who["token"] not in result.output


def test_configured_model_is_loaded_before_server_accepts_requests(tmp_path, monkeypatch):
    backend = SyntheticBackend()
    hub = Hub(tmp_path / "hub.sqlite", semantic_model="cached", semantic_backend=backend)
    stages = []
    monkeypatch.setattr("agent_brain.native_sync.hub.Hub", lambda *a, **kw: hub)
    monkeypatch.setattr(hub, "_semantic_backend", lambda: (stages.append("load") or backend, None))
    monkeypatch.setattr("uvicorn.run", lambda *a, **kw: stages.append("serve"))
    result = CliRunner().invoke(
        cli.app, ["serve", "--db", str(hub.path), "--semantic-model", "cached"]
    )
    assert result.exit_code == 0, result.output
    assert stages == ["load", "serve"]
    assert not backend.seen  # Warmup must not encode source data.
