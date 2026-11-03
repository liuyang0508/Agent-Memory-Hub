"""What the prompt hook searches for, when it skips, and what the member sees."""

import json

import pytest

from agent_brain.native_sync import hooks
from agent_brain.native_sync.client import NativeClient
from agent_brain.native_sync.common import atomic_json
from agent_brain.native_sync.recall_view import admit, normalize, render
from test_hub import asset
from test_recall_warm_path import live as live  # noqa: F401  (fixture re-export)


@pytest.mark.parametrize(
    "prompt", ["1", "好的。", "继续吧！", "搞定了吗", "OK", "？？", "/compact", "", "  "]
)
def test_acknowledgements_and_controls_are_not_searched(prompt):
    assert admit(normalize(prompt))[0] is False


@pytest.mark.parametrize("prompt", ["灵鹤计划", "召回预算怎么配置", "5101 报错", "1234"])
def test_short_topics_are_still_searched(prompt):
    assert admit(normalize(prompt)) == (True, "topic")


def test_normalize_drops_injected_blocks_and_puts_own_words_first():
    prompt = (
        "<system-reminder>ignore me</system-reminder>[AMH_ORG_CONTEXT_V1]\nold\n"
        '[/AMH_ORG_CONTEXT_V1]\n<pasted_content id="1">ERROR trace line</pasted_content id="1">\n'
        "[Image #2] 帮我看这个报错 file:///tmp/a/b/report.md"
    )
    query = normalize(prompt)
    assert query.startswith("帮我看这个报错 report.md")
    assert "ERROR trace line" in query
    assert "ignore me" not in query and "old" not in query and "Image" not in query
    assert len(normalize("长" * 5000)) == 1500


def _record(**extra):
    base = {
        "asset_id": "a",
        "revision": 3,
        "body_hash": "h",
        "kind": "memory",
        "role": "topic_memory",
        "title": "订单验签",
        "product": "codex-shared",
        "project": "orders",
        "producer_name": "敖丙",
        "observed_at": 1793698377.0,
        "retrieval_ranks": {"lexical": 2, "semantic": 1, "hopfield": None},
        "semantic_match": {"similarity": 0.7312},
    }
    base.update(extra)
    return base


def test_table_lists_call_parameters_and_each_memory():
    view = render(
        hub="https://hub.example.com",
        query="订单验签失败怎么办",
        asking={"product": "claude-code", "project": "orders", "session_digest": "0123abcd"},
        budget=3.0,
        elapsed_ms=1530,
        via="service",
        mode="remote",
        retrieval={"mode": "hybrid", "reasons": []},
        trace_id="f" * 24,
        hits=[
            _record(),
            _record(asset_id="b", title="其他", producer_name=None, uploader_name="张三"),
        ],
        states={"a:3:h": "packed", "b:3:h": "budget"},
        display="table",
    )
    assert "混合（词法+语义）" in view and "命中 2 条 · 注入 1 条" in view
    assert "POST https://hub.example.com/native/v1/recall" in view
    assert "订单验签失败怎么办" in view and "项目 orders" in view
    row = next(line for line in view.splitlines() if line.startswith("| 1 |"))
    assert "订单验签" in row and "Codex" in row and "敖丙" in row and "r3" in row
    assert "词法2·语义1" in row and "0.73" in row and "已注入" in row
    assert "未知（张三上传）" in view and "超出注入预算" in view


def test_summary_mode_is_one_line():
    view = render(
        hub="https://h",
        query="q",
        asking={},
        budget=1.2,
        elapsed_ms=10,
        via="worker",
        mode="unavailable",
        retrieval=None,
        trace_id=None,
        hits=[],
        states={},
        display="summary",
    )
    assert "\n" not in view and "未取到" in view


def seeded(hub):
    author = hub.provision("作者", "mac-author")
    hub.put(author["token"], asset("a", body="订单回调验签失败检查签名密钥版本", title="订单验签"))


def test_claude_code_sees_the_table_as_a_message(live, tmp_path):
    hub, url = live
    seeded(hub)
    c = NativeClient.adopt(tmp_path / "state", url, hub.provision("李四", "Mac"), "Mac")
    payload = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "s1",
        "prompt": "订单 验签 密钥",
    }
    result = hooks.run(c.root, "claude-code", payload)
    assert "| 1 | 订单验签 |" in result["systemMessage"]
    context = result["hookSpecificOutput"]["additionalContext"]
    assert "订单回调验签" in context and "| # | 记忆 |" not in context


def test_codex_sees_the_table_inside_the_organization_block(live, tmp_path):
    hub, url = live
    seeded(hub)
    c = NativeClient.adopt(tmp_path / "state", url, hub.provision("李四", "Mac"), "Mac")
    payload = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "s1",
        "prompt": "订单 验签 密钥",
    }
    result = hooks.run(c.root, "codex-cli", payload)
    assert "systemMessage" not in result
    context = result["hookSpecificOutput"]["additionalContext"]
    head, _, tail = context.partition("[/AMH_ORG_CONTEXT_V1]")
    assert "| 1 | 订单验签 |" in head and "订单回调验签" in head


def test_acknowledgement_is_skipped_visibly_and_display_can_be_turned_off(live, tmp_path):
    hub, url = live
    seeded(hub)
    c = NativeClient.adopt(tmp_path / "state", url, hub.provision("李四", "Mac"), "Mac")
    payload = {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "好的"}
    result = hooks.run(c.root, "claude-code", payload)
    assert "跳过" in result["systemMessage"] and "hookSpecificOutput" not in result
    metric = json.loads((c.root / "hook-metrics.jsonl").read_text().splitlines()[-1])
    assert metric["mode"] == "skipped" and metric["reason"] == "acknowledgement"
    atomic_json(c.root / "config.json", {**c.config, "recall_display": "off"})
    quiet = hooks.run(c.root, "claude-code", {**payload, "prompt": "订单 验签 密钥"})
    assert "systemMessage" not in quiet and "hookSpecificOutput" in quiet


def test_cli_sets_display_and_summarizes_hook_metrics(live, tmp_path):
    from typer.testing import CliRunner

    from agent_brain.native_sync.cli import app

    hub, url = live
    seeded(hub)
    c = NativeClient.adopt(tmp_path / "state", url, hub.provision("李四", "Mac"), "Mac")
    runner = CliRunner()
    assert (
        json.loads(runner.invoke(app, ["recall-display", "--state", str(c.root)]).output)["display"]
        == "table"
    )
    assert runner.invoke(app, ["recall-display", "loud", "--state", str(c.root)]).exit_code != 0
    changed = runner.invoke(app, ["recall-display", "summary", "--state", str(c.root)])
    assert changed.exit_code == 0 and json.loads(changed.output)["display"] == "summary"
    for prompt in ("好的", "订单 验签 密钥", "订单 签名"):
        hooks.run(
            c.root,
            "claude-code",
            {"hook_event_name": "UserPromptSubmit", "session_id": "s", "prompt": prompt},
        )
    stats = json.loads(runner.invoke(app, ["hook-stats", "--state", str(c.root)]).output)
    assert stats["prompts"] == 3 and stats["skipped"] == 1
    assert stats["skip_reasons"] == {"acknowledgement": 1} and stats["with_hits"] >= 1
    assert stats["elapsed_ms"]["p50"] is not None
