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
            _record(
                asset_id="c",
                title="MEMORY",
                path="MEMORY.md",
                part_index=2,
                part_count=15,
                retrieval_ranks=None,
                recall={"routes": {"lexical": 4, "semantic": 2, "hopfield": "disabled"}},
            ),
        ],
        states={"a:3:h": "packed", "b:3:h": "budget"},
        display="table",
    )
    assert "混合（词法+语义）" in view and "命中 3 条 · 注入 1 条" in view
    assert "POST https://hub.example.com/native/v1/recall" in view
    assert "订单验签失败怎么办" in view and "项目 orders" in view
    row = next(line for line in view.splitlines() if line.startswith("| 1 |"))
    assert "订单验签" in row and "Codex" in row and "敖丙" in row and "r3" in row
    assert "词法2·语义1" in row and "0.73" in row and "已注入" in row
    assert "| 张三 |" in view and "上传" not in view and "超出注入预算" in view
    third = next(line for line in view.splitlines() if line.startswith("| 3 |"))
    assert "MEMORY.md §3/15" in third and "词法4·语义2" in third


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
    message = result["systemMessage"]
    assert "**" not in message and "| 1 |" not in message
    assert any(line.startswith("│ 1 │ 订单验签") for line in message.splitlines())
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


def test_box_table_lines_up_mixed_width_text():
    from agent_brain.native_sync.recall_view import _width

    view = render(
        hub="https://h",
        query="订单验签失败怎么办" * 10,
        asking={"product": "claude-code"},
        budget=3.0,
        elapsed_ms=5,
        via="service",
        mode="remote",
        retrieval={"mode": "hybrid"},
        trace_id=None,
        hits=[
            _record(body="# 验签\n回调验签失败先核对密钥版本。"),
            _record(
                asset_id="b",
                title="很长的中文标题" * 6,
                producer_name=None,
                uploader_name="张三",
                body="---\ndescription: " + "很长的说明" * 30 + "\n---\n",
            ),
            _record(
                asset_id="c",
                title="MEMORY",
                path="MEMORY.md",
                part_index=0,
                part_count=4,
                body="# Task Group: 订单回调\n",
            ),
        ],
        states={"a:3:h": "packed", "b:3:h": "covered"},
        display="table",
        style="box",
    )
    lines = view.splitlines()
    table = [line for line in lines if line[:1] in "┌│├└"]
    # Top, header; per memory a rule, its row, a rule and its content line; bottom.
    assert len(table) == 2 + 4 * 3 + 1 and len({_width(line) for line in table}) == 1
    assert sum("内容：" in line for line in table) == 3
    assert "**" not in view and "`" not in view
    assert not any(mark in line for line in table for mark in "…·—§")
    assert "张三" in view and "上传" not in view and "已注入过" in view
    assert "MEMORY.md 第1/4段" in view and "相关性" in table[1] and "使用率" in table[1]
    assert "项目" in table[1]  # a hit carries a project, so the column is shown


def test_a_fresh_mod_mark_moves_the_view_into_a_panel_file(live, tmp_path):
    import os

    hub, url = live
    seeded(hub)
    c = NativeClient.adopt(tmp_path / "state", url, hub.provision("李四", "Mac"), "Mac")
    payload = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "sess-1/../x",
        "prompt": "订单 验签 密钥",
    }
    mark = c.root / "rich-display" / "sess-1x"
    mark.parent.mkdir()
    mark.write_text("1")
    drawn = hooks.run(c.root, "claude-code", payload)
    assert "systemMessage" not in drawn and "hookSpecificOutput" in drawn
    panel = json.loads((c.root / "recall-view" / "sess-1x.json").read_text())
    assert panel["summary"].startswith("组织记忆召回")
    assert any(line.startswith("│ 1 │ 订单验签") for line in panel["lines"])
    skipped = hooks.run(c.root, "claude-code", {**payload, "prompt": "好的"})
    assert skipped == {}
    assert "跳过" in json.loads((c.root / "recall-view" / "sess-1x.json").read_text())["summary"]
    # A stale mark (mod unloaded) or another host falls back to the message.
    os.utime(mark, (0, 0))
    assert "systemMessage" in hooks.run(c.root, "claude-code", payload)
    mark.write_text("1")
    assert "systemMessage" in hooks.run(c.root, "cowork", payload)


def test_claude_mod_export_binds_the_state_directory(tmp_path):
    from agent_brain.native_sync.claude_mod import export_claude_mod

    folder = export_claude_mod(tmp_path / "mod", tmp_path / 'state "x"')
    register = (folder / "hooks" / "register.tsx").read_text()
    assert "__AMH_STATE__" not in register
    assert json.dumps(str((tmp_path / 'state "x"').resolve())) in register
    assert json.loads((folder / ".claude-plugin" / "plugin.json").read_text())["name"] == (
        "amh-recall-view"
    )
    assert (folder / "types" / "index.d.ts").exists()


def test_usage_rate_shows_share_and_judged_turns():
    from agent_brain.native_sync.recall_view import _usage

    assert _usage(_record(recall={"usage": {"used": 3, "judged": 4}})) == "75%·4次"
    assert _usage(_record(recall={"usage": None})) == "—"
    assert _usage(_record()) == "—"


def test_content_line_says_what_each_memory_is_about():
    from agent_brain.native_sync.recall_view import _summary, _title

    described = "---\nname: hub\ndescription: linkcrux 组织 Hub 的服务器地址\n---\n正文"
    assert _summary(_record(body=described)) == "linkcrux 组织 Hub 的服务器地址"
    chunk = "前文\n# Task Group: 周报同步\n…\n# Task Group: 蝶阀选型复核\n"
    assert _summary(_record(body=chunk)) == "任务组：周报同步；蝶阀选型复核"
    index = "- [工作区约定](a.md) — x\n- [用户画像](b.md) — y\n"
    assert _summary(_record(body=index)) == "索引 2 条：工作区约定、用户画像"
    assert _summary(_record(body="# 飞书写作\n先用**模板**创建。")) == "飞书写作：先用模板创建。"
    assert _title(_record(title="SKILL", path="skills/feishu-doc/SKILL.md")) == "feishu-doc"
    assert _summary(_record(body=described, deleted=True)) == ""


def _wide_record():
    return {
        "asset_id": "a",
        "revision": 2,
        "body_hash": "h",
        "title": "MEMORY",
        "path": "MEMORY.md",
        "part_count": 8,
        "part_index": 6,
        "product": "codex-cli",
        "project": "linkcrux-platform",
        "kind": "memory",
        "role": "navigation",
        "producer_name": "敖丙",
        "producer_member_id": "m",
        "body": "# Task Group: 春光销售接单员整合\n" * 40,
        "recall": {
            "routes": {"lexical": 17, "semantic": 1},
            "relevance": 0.5,
            "usage": {"used": 3, "judged": 4},
        },
    }


@pytest.mark.parametrize("columns", [70, 90, 100, 110, 118, 126, 160, None])
def test_box_table_never_wraps_at_any_terminal_width(columns):
    from agent_brain.native_sync.recall_view import _HOST_MARGIN, _UNKNOWN_COLUMNS, _box_table, _width

    lines = _box_table([_wide_record()], {"a:2:h": "packed"}, columns)
    assert {_width(line) for line in lines} == {_width(lines[0])}  # every line closes the box
    assert _width(lines[0]) <= (columns or _UNKNOWN_COLUMNS) - _HOST_MARGIN


def test_box_table_keeps_what_matters_when_narrow_and_names_parts():
    from agent_brain.native_sync.recall_view import _box_table

    wide = "\n".join(_box_table([_wide_record()], {"a:2:h": "packed"}, 160))
    assert "MEMORY.md 第7/8段" in wide and "命中路由" in wide and "项目" in wide
    narrow = "\n".join(_box_table([_wide_record()], {"a:2:h": "packed"}, 70))
    for kept in ("记忆", "相关性", "本轮", "内容："):
        assert kept in narrow
    assert "项目" not in narrow and "版本" not in narrow


def test_terminal_width_falls_back_to_the_environment(monkeypatch):
    from agent_brain.native_sync import recall_view

    def no_ps(*args, **kwargs):
        raise OSError("no ps")

    monkeypatch.setattr(recall_view.subprocess, "run", no_ps)
    monkeypatch.setenv("COLUMNS", "99")
    assert recall_view.terminal_columns() == 99
    monkeypatch.setenv("COLUMNS", "0")
    assert recall_view.terminal_columns() is None


@pytest.mark.parametrize(
    "text,admitted",
    [
        ("?62;22;52c>1;10;0c", False),
        ("\x1b[?1;2c", False),
        ("1;10;0c", False),
        ("5G 网络", True),
        ("12c", True),
        ("v1.4.18 发了吗", True),
    ],
)
def test_terminal_replies_are_not_prompts(text, admitted):
    from agent_brain.native_sync.recall_view import admit

    assert admit(text)[0] is admitted
