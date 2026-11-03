"""CLI surfaces for the recall ledger: serve flags, trace table and feedback."""

import json

from typer.testing import CliRunner

from agent_brain.native_sync import cli
from agent_brain.native_sync.client import NativeClient
from agent_brain.native_sync.hub import Hub
from test_hub import asset, enroll
from test_runtime import hook, live as live  # noqa: F401


def test_serve_flags_reach_the_hub(tmp_path, monkeypatch):
    captured = {}

    class FakeHub:
        semantic_model = None

        def __init__(self, db, **options):
            captured.update(options)

    monkeypatch.setattr("agent_brain.native_sync.hub.Hub", FakeHub)
    monkeypatch.setattr("uvicorn.run", lambda *a, **kw: None)
    result = CliRunner().invoke(
        cli.app,
        [
            "serve",
            "--db",
            str(tmp_path / "hub.sqlite"),
            "--hopfield",
            "--no-decay",
            "--hopfield-beta",
            "4",
            "--hopfield-min-score",
            "0.3",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (
        captured["hopfield"] is True
        and captured["decay"] is False
        and captured["hopfield_beta"] == 4.0
    )
    assert captured["hopfield_min_score"] == 0.3


def test_serve_without_flags_leaves_decay_and_hopfield_to_the_environment(tmp_path, monkeypatch):
    """B2: an omitted flag is None so Hub.__init__ reads AMH_NATIVE_DECAY / AMH_NATIVE_HOPFIELD."""
    captured = {}

    class FakeHub:
        semantic_model = None

        def __init__(self, db, **options):
            captured.update(options)

    monkeypatch.setattr("agent_brain.native_sync.hub.Hub", FakeHub)
    monkeypatch.setattr("uvicorn.run", lambda *a, **kw: None)
    monkeypatch.setenv("AMH_NATIVE_DECAY", "0")
    monkeypatch.setenv("AMH_NATIVE_HOPFIELD", "1")
    result = CliRunner().invoke(cli.app, ["serve", "--db", str(tmp_path / "hub.sqlite")])
    assert result.exit_code == 0, result.output
    assert captured["decay"] is None and captured["hopfield"] is None
    real = Hub(tmp_path / "real.sqlite", decay=captured["decay"], hopfield=captured["hopfield"])
    assert real.decay_enabled is False and real.hopfield is True
    explicit = CliRunner().invoke(
        cli.app, ["serve", "--db", str(tmp_path / "hub.sqlite"), "--decay", "--no-hopfield"]
    )
    assert explicit.exit_code == 0, explicit.output
    assert captured["decay"] is True and captured["hopfield"] is False


def test_recall_trace_table_and_feedback(live, tmp_path, monkeypatch):
    from rich.console import Console

    monkeypatch.setattr(cli, "console", Console(width=400, force_terminal=False))
    hub, url = live
    author = enroll(hub)
    hub.put(author["token"], asset("fact", title="审批规则", body="审批需要两人复核。"))
    c = NativeClient.adopt(tmp_path / "state", url, hub.provision("李四", "Mac"), "Mac")
    hook(c.root, "UserPromptSubmit", prompt="审批复核要几个人")
    c.sync()
    runner = CliRunner()
    listing = runner.invoke(cli.app, ["recall-trace", "--state", str(c.root)])
    assert listing.exit_code == 0, listing.output
    assert "审批复核要几个人" in listing.output and "hook" in listing.output
    raw = runner.invoke(cli.app, ["recall-trace", "--state", str(c.root), "--json"])
    trace_id = json.loads(raw.output)[0]["trace_id"]
    detail = runner.invoke(cli.app, ["recall-trace", "--state", str(c.root), "--trace", trace_id])
    assert detail.exit_code == 0, detail.output
    for expected in (
        "审批规则",
        "已注入上下文",
        "lexical·1",
        "semantic·未启用",
        "hopfield·未启用",
        "未知",
        "time_unknown_neutral",
    ):
        assert expected in detail.output, expected
    assert "两人复核" not in detail.output  # titles and identifiers only, never bodies
    feedback = runner.invoke(
        cli.app,
        [
            "recall-feedback",
            "--state",
            str(c.root),
            "--trace",
            trace_id,
            "--asset",
            "fact",
            "--revision",
            "1",
            "--event",
            "cite",
        ],
    )
    assert feedback.exit_code == 0, feedback.output
    assert json.loads(feedback.output)["states"] == {"fact": "cited"}
    after = runner.invoke(cli.app, ["recall-trace", "--state", str(c.root), "--trace", trace_id])
    assert "回答已采用" in after.output
    bad = runner.invoke(
        cli.app,
        [
            "recall-feedback",
            "--state",
            str(c.root),
            "--trace",
            trace_id,
            "--asset",
            "fact",
            "--revision",
            "1",
            "--event",
            "nope",
        ],
    )
    assert bad.exit_code != 0
