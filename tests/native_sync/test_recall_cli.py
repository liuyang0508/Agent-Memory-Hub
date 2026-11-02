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
        ["serve", "--db", str(tmp_path / "hub.sqlite"), "--hopfield", "--no-decay", "--hopfield-beta", "4"],
    )
    assert result.exit_code == 0, result.output
    assert captured["hopfield"] is True and captured["decay"] is False and captured["hopfield_beta"] == 4.0


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
    for expected in ("审批规则", "已注入上下文", "lexical·1", "semantic·未启用", "hopfield·未启用", "未知", "time_unknown_neutral"):
        assert expected in detail.output, expected
    assert "两人复核" not in detail.output  # titles and identifiers only, never bodies
    feedback = runner.invoke(
        cli.app,
        ["recall-feedback", "--state", str(c.root), "--trace", trace_id, "--asset", "fact", "--revision", "1", "--event", "cite"],
    )
    assert feedback.exit_code == 0, feedback.output
    assert json.loads(feedback.output)["states"] == {"fact": "cited"}
    after = runner.invoke(cli.app, ["recall-trace", "--state", str(c.root), "--trace", trace_id])
    assert "回答已采用" in after.output
    bad = runner.invoke(
        cli.app,
        ["recall-feedback", "--state", str(c.root), "--trace", trace_id, "--asset", "fact", "--revision", "1", "--event", "nope"],
    )
    assert bad.exit_code != 0
