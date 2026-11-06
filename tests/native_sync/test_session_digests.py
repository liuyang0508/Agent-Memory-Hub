"""A finished session leaves a short, searchable record; nothing private goes without consent."""

import json
import os
import time

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from agent_brain.native_sync import cli, digests
from agent_brain.native_sync.api import create_app
from agent_brain.native_sync.asset_roles import asset_role
from agent_brain.native_sync.hub import Hub
from test_client import client

WORK = "/" + "srv/work"
SESSION = "-".join(["1" * 8, "2" * 4, "4" + "3" * 3, "8" + "4" * 3, "5" * 12])
OLDER = "-".join(["9" * 8, "2" * 4, "4" + "3" * 3, "8" + "4" * 3, "5" * 12])
START = "2026-11-06T01:00:00.000Z"
AFTER_START = 1793930000.0  # After START, in seconds.
ENABLED = time.mktime((2026, 11, 5, 0, 0, 0, 0, 0, -1))


def claude_lines(
    ask="把日报流程接上 Feishu",
    answer="日报流程已经接好并跑通了一次，现在还差一次线上验证。",
    **extra,
):
    base = {"cwd": WORK + "/dailies", "gitBranch": "main", "timestamp": START, "entrypoint": "cli"}
    return [
        {"type": "ai-title", "aiTitle": "日报流程接入"},
        {**base, "type": "user", "message": {"content": ask}, **extra},
        {
            **base,
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": "不应出现的思考内容"},
                    {
                        "type": "tool_use",
                        "name": "Edit",
                        "input": {"file_path": WORK + "/dailies/a.py"},
                    },
                ]
            },
        },
        {**base, "type": "user", "message": {"content": [{"type": "tool_result", "content": "x"}]}},
        {**base, "type": "user", "message": {"content": "<system-reminder>隐藏</system-reminder>"}},
        {**base, "type": "user", "isSidechain": True, "message": {"content": "子任务不算"}},
        {**base, "type": "user", "message": {"content": "继续把验证也做了"}},
        {**base, "type": "assistant", "message": {"content": [{"type": "text", "text": answer}]}},
    ]


def codex_lines(source="cli", answer="RSS 源已经接好并能正常抓取，现在还差订阅列表的确认。"):
    def item(role, text, **extra):
        kind = "input_text" if role == "user" else "output_text"
        return {
            "type": "response_item",
            "timestamp": START,
            "payload": {
                "type": "message",
                "role": role,
                "content": [{"type": kind, "text": text}],
                **extra,
            },
        }

    return [
        {
            "type": "session_meta",
            "timestamp": START,
            "payload": {"id": SESSION, "cwd": WORK + "/feeds", "source": source},
        },
        item("developer", "你是 Codex，开发者指令不应出现"),
        item("user", "# AGENTS.md instructions\n规则不应出现"),
        item("user", "<environment_context>x</environment_context>"),
        item("user", "搭一个公众号 RSS 源"),
        {
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "搭一个公众号 RSS 源"},
        },
        item("assistant", "这是过程中的说明，不是最终结论的一部分内容", phase="commentary"),
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "apply_patch",
                "input": "*** Begin Patch\n*** Update File: feeds/rss.py\n*** End Patch",
            },
        },
        item("user", "接着做"),
        item("assistant", answer, phase="final_answer"),
    ]


def write(path, lines, *, age=7200.0, now=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines))
    moment = (now or AFTER_START) - age
    os.utime(path, (moment, moment))


def run(tmp_path, *, since=ENABLED, now=AFTER_START):
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    bound = []
    counts = digests.run(
        state,
        {"session_digests": {"enabled": True, "since": since}},
        lambda root, product: bound.append((root, product)),
        home=tmp_path / "home",
        now=now,
    )
    return state, counts, bound


def claude_file(tmp_path, session=SESSION):
    return tmp_path / "home/.claude/projects/-home-me/" / (session + ".jsonl")


def codex_file(tmp_path):
    return tmp_path / f"home/.codex/sessions/2026/11/06/rollout-2026-11-06T01-00-00-{SESSION}.jsonl"


def body(state, product="claude-code", session=SESSION):
    return (state / "session-digests" / product / "session_digests" / (session + ".md")).read_text()


def test_a_finished_claude_session_is_written_with_its_title_ask_and_outcome(tmp_path):
    write(claude_file(tmp_path), claude_lines())
    state, counts, bound = run(tmp_path)
    assert counts["written"] == 1 and bound[0][1] == "claude-code"
    text = body(state)
    assert "# 日报流程接入" in text
    assert "把日报流程接上 Feishu" in text and "继续把验证也做了" in text
    assert "还差一次线上验证" in text and "- a.py" in text
    for hidden in ("thinking", "不应出现的思考内容", "隐藏", "子任务不算"):
        assert hidden not in text


def test_a_finished_codex_session_is_written_from_its_real_shape(tmp_path):
    write(codex_file(tmp_path), codex_lines())
    state, counts, bound = run(tmp_path)
    assert counts["written"] == 1 and bound[0][1] == "codex-cli"
    text = body(state, "codex-cli")
    assert "搭一个公众号 RSS 源" in text and "还差订阅列表的确认" in text
    assert "feeds/rss.py" in text
    for hidden in ("开发者指令", "规则不应出现", "environment_context", "过程中的说明"):
        assert hidden not in text


@pytest.mark.parametrize(
    "line",
    [
        "服务器密码 hunter2-plain",
        "password: hunter2-plain",
        "ubuntu@host hunter2-plain 是 password",
        "cookie 是 hunter2-plain",
        "Author" + "ization: Bear" + "er hunter2-plain",
    ],
)
def test_a_line_about_a_credential_goes_entirely(tmp_path, line):
    write(claude_file(tmp_path), claude_lines(ask="把日报接上\n" + line))
    state, _, _ = run(tmp_path)
    assert "hunter2-plain" not in body(state) and "把日报接上" in body(state)


@pytest.mark.parametrize("value", ["Xk9#mQ2$vL", "aB3dE5gH7j", "p@ssw0rd!x"])
def test_an_unlabelled_value_is_masked(value):
    assert value not in digests.clean("登录用这个 " + value + " 就行")


@pytest.mark.parametrize(
    "name",
    ["CLAUDE.md", "Qwen3.7-Max", "feature_v2", "src/app/Main.java", "**Claude**", "**OpenAI**"],
)
def test_ordinary_names_survive(name):
    assert name in digests.clean("改了 " + name)


def test_addresses_and_home_paths_are_masked():
    address = ".".join(["203", "0", "113", "5"])
    text = digests.clean(f"连 {address}，文件在 {WORK}/a.md", WORK)
    assert address not in text and WORK not in text and "~/a.md" in text


def test_a_session_from_before_consent_is_not_read(tmp_path):
    write(claude_file(tmp_path), claude_lines(), age=7200)
    since = AFTER_START  # Enabled after the file last changed.
    state, counts, _ = run(tmp_path, since=since)
    assert counts["written"] == 0 and not digests.listing(state)


def test_a_session_begun_before_consent_stays_out_even_if_it_continues(tmp_path):
    begun = digests._when(START)
    now = begun + 7200
    write(claude_file(tmp_path), claude_lines(), age=3600, now=now)
    state, counts, _ = run(tmp_path, since=begun + 100, now=now)
    assert counts["written"] == 0 and not digests.listing(state)


def test_a_named_older_session_may_be_added(tmp_path):
    write(claude_file(tmp_path), claude_lines(), age=7200)
    state = tmp_path / "state"
    digests.add(state, SESSION)
    state, counts, _ = run(tmp_path, since=AFTER_START)
    assert counts["written"] == 1


def test_scripts_subagents_and_a_session_still_running_are_left_out(tmp_path):
    write(claude_file(tmp_path), claude_lines(entrypoint="sdk-cli"))
    write(codex_file(tmp_path), codex_lines(source={"subagent": "review"}))
    other = claude_file(tmp_path, OLDER)
    write(other, claude_lines(), age=60)
    state, counts, _ = run(tmp_path)
    assert counts["written"] == 0 and not digests.listing(state)


def test_exec_is_a_script(tmp_path):
    write(codex_file(tmp_path), codex_lines(source="exec"))
    state, _, _ = run(tmp_path)
    assert not digests.listing(state)


def test_a_one_line_chat_is_not_worth_a_record(tmp_path):
    write(
        claude_file(tmp_path),
        [
            {
                "type": "user",
                "timestamp": START,
                "cwd": WORK,
                "entrypoint": "cli",
                "message": {"content": "你好"},
            },
            {
                "type": "assistant",
                "timestamp": START,
                "cwd": WORK,
                "message": {
                    "content": [{"type": "text", "text": "你好，有什么可以帮你的吗？" * 3}]
                },
            },
        ],
    )
    state, _, _ = run(tmp_path)
    assert not digests.listing(state)


def test_a_session_flagged_sensitive_is_withdrawn(tmp_path):
    write(claude_file(tmp_path), claude_lines())
    state, _, _ = run(tmp_path)
    assert digests.listing(state)
    write(claude_file(tmp_path), claude_lines(answer="候选人手机号是 13812345678，已经发给他了。"))
    state, counts, _ = run(tmp_path)
    assert counts["removed"] == 1 and not digests.listing(state)


def test_an_unchanged_session_is_not_parsed_again(tmp_path, monkeypatch):
    write(claude_file(tmp_path), claude_lines())
    run(tmp_path)
    monkeypatch.setattr(digests, "claude_session", lambda *a: pytest.fail("parsed again"))
    run(tmp_path)


def test_a_huge_session_is_read_only_at_both_ends(tmp_path, monkeypatch):
    monkeypatch.setattr(digests, "HEAD_BYTES", 2000)
    monkeypatch.setattr(digests, "TAIL_BYTES", 2000)
    filler = {"type": "api-request", "body": "x" * 500}
    lines = claude_lines()
    write(claude_file(tmp_path), lines[:2] + [filler] * 400 + lines[2:])
    state, counts, _ = run(tmp_path)
    assert counts["written"] == 1 and "还差一次线上验证" in body(state)


def test_an_oversized_record_is_skipped_without_losing_what_follows(tmp_path, monkeypatch):
    monkeypatch.setattr(digests, "LINE_BYTES", 400)
    lines = claude_lines()
    big = {"type": "api-request-blob", "body": "x" * 5000}
    write(claude_file(tmp_path), lines[:3] + [big] + lines[3:])
    state, _, _ = run(tmp_path)
    assert "还差一次线上验证" in body(state)


def test_forgetting_removes_it_and_keeps_it_out(tmp_path):
    write(claude_file(tmp_path), claude_lines())
    state, _, _ = run(tmp_path)
    assert digests.forget(state, SESSION) == 1
    write(claude_file(tmp_path), claude_lines(answer="后来又继续推进到了新的阶段，全部完成。"))
    state, counts, _ = run(tmp_path)
    assert counts["written"] == 0 and not digests.listing(state)


def test_only_the_newest_are_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(digests, "MAX_KEPT", 1)
    write(claude_file(tmp_path), claude_lines())
    write(claude_file(tmp_path, OLDER), claude_lines())
    state, counts, _ = run(tmp_path)
    assert counts["removed"] == 1 and len(digests.listing(state)) == 1


def test_the_hub_treats_them_as_session_summaries_for_any_product():
    assert asset_role("claude-code", "session_digests/x.md", "memory") == "conversation_summary"
    assert asset_role("codex-cli", "session_digests/x.md", "memory") == "conversation_summary"
    assert asset_role("claude-code", "notes/session_digests.md", "memory") == "topic_memory"


def test_off_by_default_and_the_hub_says_it_understands_them(tmp_path):
    assert not digests.enabled({})
    assert not digests.enabled({"session_digests": {"enabled": False}})
    health = TestClient(create_app(Hub(tmp_path / "hub.sqlite"))).get("/native/v1/health").json()
    assert digests.CAPABILITY in health["capabilities"]


def test_end_to_end_found_by_search_but_never_injected_or_listed(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    c = client(tmp_path, hub, "A")
    write(codex_file(tmp_path), codex_lines())
    counts = digests.run(
        c.root,
        {"session_digests": {"enabled": True, "since": ENABLED}},
        lambda root, product: c.register(root, product, "", "memory"),
        home=tmp_path / "home",
        now=AFTER_START,
    )
    assert counts["written"] == 1
    c.sync()
    assert c.pending_count() == 0
    token = c.identity["token"]
    found = hub.retrieve(token, "公众号 RSS 源", kinds=["memory"], include_evidence=True)["items"]
    assert found and found[0]["path"].startswith("session_digests/")
    assert not hub.retrieve(token, "公众号 RSS 源", kinds=["memory"])["items"]
    recalled = hub.recall(token, "公众号 RSS 源")
    assert not (recalled["items"] if isinstance(recalled, dict) else recalled)
    assert not hub.catalog(token)


def test_withdrawing_reaches_the_hub(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    c = client(tmp_path, hub, "A")
    write(codex_file(tmp_path), codex_lines())
    digests.run(
        c.root,
        {"session_digests": {"enabled": True, "since": ENABLED}},
        lambda root, product: c.register(root, product, "", "memory"),
        home=tmp_path / "home",
        now=AFTER_START,
    )
    c.sync()
    assert digests.forget(c.root, SESSION) == 1
    c.sync()
    c.sync()
    found = hub.retrieve(
        c.identity["token"], "公众号 RSS 源", kinds=["memory"], include_evidence=True
    )["items"]
    assert not found


def test_the_command_asks_first_and_records_the_moment(tmp_path, monkeypatch):
    hub = Hub(tmp_path / "hub.sqlite")
    c = client(tmp_path, hub, "A")
    monkeypatch.setattr(cli, "load", lambda state: c)
    state = ["--state", str(c.root)]
    declined = CliRunner().invoke(cli.app, ["digests", *state, "--enable"], input="n\n")
    assert declined.exit_code == 0 and not digests.enabled(c.config)
    before = time.time()
    accepted = CliRunner().invoke(cli.app, ["digests", *state, "--enable"], input="y\n")
    assert accepted.exit_code == 0 and digests.enabled(c.config)
    since = c.config["session_digests"]["since"]
    assert since >= before
    assert CliRunner().invoke(cli.app, ["digests", *state, "--enable", "--yes"]).exit_code == 0
    assert c.config["session_digests"]["since"] == since, "re-enabling must not widen consent"
    assert CliRunner().invoke(cli.app, ["digests", *state, "--disable"]).exit_code == 0
    assert not digests.enabled(c.config)


def test_the_portable_runtime_carries_the_module(tmp_path):
    from agent_brain.native_sync.install import export_cowork_plugin

    import zipfile

    output = export_cowork_plugin(tmp_path / "plugin")
    with zipfile.ZipFile(str(output) + ".zip") as bundle:
        assert "runtime/agent_brain/native_sync/digests.py" in bundle.namelist()


def test_summaries_can_be_found_by_meaning_but_stay_out_of_injection(tmp_path):
    from test_hybrid_retrieval import asset, make

    hub, token, _ = make(tmp_path)
    hub.put(token, asset("digest", body="approval 一", path="session_digests/a.md"))
    hub.put(token, asset("topic", body="approval 二"))
    summary = dict(path="rollout_summaries/a.md", product="codex-cli")
    hub.put(token, asset("rollout", body="approval 三", **summary))
    hub.rebuild_semantic(token)

    found = hub.retrieve(token, "两人复核", include_evidence=True)["items"]
    assert {"digest", "topic", "rollout"} <= {
        i["asset_id"] for i in found if i.get("semantic_match")
    }

    default = hub.retrieve(token, "两人复核")["items"]
    assert "digest" not in {item["asset_id"] for item in default}
    assert "digest" not in {item["asset_id"] for item in hub.recall(token, "两人复核")["items"]}


def test_earlier_replies_are_kept_because_the_last_one_is_often_a_side_question(tmp_path):
    lines = claude_lines(answer="最后这条只是回答了一个顺带的小问题，和主线工作没有什么关系。")
    base = {"cwd": WORK + "/dailies", "timestamp": START, "entrypoint": "cli"}
    main = "主线结论：日报流程走飞书审批，已经上线一周，还差权限回收这一步。"
    lines.insert(
        -1, {**base, "type": "assistant", "message": {"content": [{"type": "text", "text": main}]}}
    )
    write(claude_file(tmp_path), lines)
    state, _, _ = run(tmp_path)
    text = body(state)
    assert "主线结论" in text and text.index("结束时的进展") < text.index("更早的进展")
    assert text.index("小问题") < text.index("主线结论")
