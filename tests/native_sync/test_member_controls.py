"""1.4.25: the four things a member needs: install, uninstall, stop recall, stop writing."""

import json

from typer.testing import CliRunner

from agent_brain.native_sync import hooks
from agent_brain.native_sync.cli import app
from agent_brain.native_sync.client import NativeClient
from agent_brain.native_sync.install import install_hooks, uninstall_hooks
from test_hub import asset
from test_runtime import live as live  # noqa: F401  (fixture re-export)

runner = CliRunner()


def ours(path):
    data = json.loads(path.read_text())
    return [
        h["command"]
        for entries in data.get("hooks", {}).values()
        for entry in entries
        for h in entry["hooks"]
        if "native_sync" in h["command"] or "amh-native-hook" in h["command"]
    ]


def installed(tmp_path, existing=None):
    home, state = tmp_path / "home", tmp_path / "state"
    settings = home / ".claude/settings.json"
    settings.parent.mkdir(parents=True)
    state.mkdir()
    if existing is not None:
        settings.write_text(json.dumps(existing))
    install_hooks(home, state, products=["claude-code"])
    assert ours(settings)
    return home, state, settings


MINE = {"type": "command", "command": "my-own-hook"}


def test_uninstall_survives_an_ordinary_later_edit(tmp_path):
    before = {"model": "opus", "hooks": {"Stop": [{"hooks": [MINE]}]}}
    home, state, settings = installed(tmp_path, before)
    edited = json.loads(settings.read_text())
    edited["model"] = "sonnet"
    edited["permissions"] = {"allow": ["Read"]}
    settings.write_text(json.dumps(edited))
    assert uninstall_hooks(home, state) == []
    after = json.loads(settings.read_text())
    assert not ours(settings)
    assert after["model"] == "sonnet" and after["permissions"] == {"allow": ["Read"]}
    assert after["hooks"] == {"Stop": [{"hooks": [MINE]}]}
    assert "autoMemoryEnabled" not in after
    assert not (state / "install-receipt.json").exists()


def test_uninstall_keeps_a_hook_added_later_in_our_events(tmp_path):
    home, state, settings = installed(tmp_path)
    edited = json.loads(settings.read_text())
    edited["hooks"]["UserPromptSubmit"].append({"hooks": [MINE]})
    settings.write_text(json.dumps(edited))
    assert uninstall_hooks(home, state) == []
    assert json.loads(settings.read_text())["hooks"] == {"UserPromptSubmit": [{"hooks": [MINE]}]}


def test_uninstall_reports_a_hook_of_ours_that_was_edited(tmp_path):
    home, state, settings = installed(tmp_path)
    edited = json.loads(settings.read_text())
    handler = edited["hooks"]["Stop"][0]["hooks"][0]
    handler["command"] += " --changed"
    settings.write_text(json.dumps(edited))
    assert str(settings) in uninstall_hooks(home, state)
    assert ours(settings) == [handler["command"]]


def test_member_setting_chosen_after_install_stands(tmp_path):
    home, state, settings = installed(tmp_path, {"autoMemoryEnabled": False})
    edited = json.loads(settings.read_text())
    edited["autoMemoryEnabled"] = True
    edited["model"] = "x"
    settings.write_text(json.dumps(edited))
    uninstall_hooks(home, state)
    assert json.loads(settings.read_text())["autoMemoryEnabled"] is False


def test_untouched_file_still_restores_exactly(tmp_path):
    before = {"model": "opus"}
    home, state, settings = installed(tmp_path, before)
    assert uninstall_hooks(home, state) == []
    assert json.loads(settings.read_text()) == before


def member(hub, url, tmp_path):
    author = hub.provision("Author", "a")
    hub.put(author["token"], asset("note", title="OSS 坑", body="OSS 会话目录用 oss2 短超时并发列"))
    return NativeClient.adopt(tmp_path / "state", url, hub.provision("Reader", "r"), "r")


def test_uninstall_command_silences_whatever_is_left(live, tmp_path, monkeypatch):  # noqa: F811
    hub, url = live
    c = member(hub, url, tmp_path)
    result = runner.invoke(app, ["uninstall", "--state", str(c.root), "--home", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "purge-asset" in result.output and (c.root / "paused").exists()
    out = hooks.run(
        c.root,
        "claude-code",
        {"session_id": "s", "hook_event_name": "UserPromptSubmit", "prompt": "oss2 怎么列目录"},
    )
    assert out == {}


def test_stop_writing_keeps_recall_and_sends_nothing(live, tmp_path, monkeypatch):  # noqa: F811
    hub, url = live
    c = member(hub, url, tmp_path)
    source = tmp_path / "notes"
    source.mkdir()
    result = runner.invoke(app, ["upload", "--state", str(c.root), "--disable"])
    assert result.exit_code == 0 and json.loads(result.output)["uploads_enabled"] is False
    fresh = NativeClient(c.root)
    assert fresh.uploads_enabled() is False
    scanned = []
    monkeypatch.setattr(NativeClient, "scan", lambda self: scanned.append(1) or [])
    outcome = fresh.sync()
    assert outcome["uploads"] == "off" and outcome["pushed"] == 0 and not scanned
    # Recall is a different switch: reading from the Hub still works.
    assert fresh.recall_enabled() and fresh.search("oss2")
    assert (
        json.loads(runner.invoke(app, ["upload", "--state", str(c.root), "--enable"]).output)[
            "uploads_enabled"
        ]
        is True
    )
    assert NativeClient(c.root).sync().get("uploads") != "off" and scanned


def test_stop_recall_keeps_uploads_and_status_shows_both(live, tmp_path):  # noqa: F811
    hub, url = live
    c = member(hub, url, tmp_path)
    runner.invoke(app, ["recall", "--state", str(c.root), "--disable"])
    fresh = NativeClient(c.root)
    assert fresh.recall_enabled() is False and fresh.uploads_enabled() is True
    assert fresh.sync().get("uploads") != "off"
    shown = json.loads(runner.invoke(app, ["status", "--state", str(c.root)]).output)
    assert shown["recall_enabled"] is False and shown["uploads_enabled"] is True
