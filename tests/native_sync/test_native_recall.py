"""Organization recall works the way a native reader does: an index at the start, then
search and read. Only a memory the prompt plainly calls for is put in front of the model."""

from __future__ import annotations

from typing import Any

from agent_brain.native_sync import relevance
from agent_brain.native_sync.retrieval import _name_section
from test_hybrid_retrieval import asset, make
from test_learned_gate import TopicBackend, sim_only
from test_recall_warm_path import live as live  # noqa: F401  (fixture re-export)


def paths(items: list[dict[str, Any]]) -> list[str]:
    return [item["asset_id"] for item in items]


def test_a_document_naming_what_was_asked_comes_first(tmp_path):
    hub, token, _ = make(tmp_path)
    for i in range(12):
        hub.put(token, asset(f"noise{i}", body="send message 会话 问题 " * 8, path=f"n{i}.md"))
    summary = dict(path="rollout_summaries/a.md", product="codex-cli")
    hub.put(token, asset("rollout", body="结论：SessionProcess.send_message 没有失效。", **summary))
    found = hub.retrieve(token, "SessionProcess.send_message 有什么问题", include_evidence=True)
    assert paths(found["items"])[0] == "rollout"
    match = found["items"][0]["semantic_match"]
    assert match["aggregation"] == "literal" and match["char_end"] > match["char_start"]


def test_a_summary_is_not_ranked_below_a_topic_for_being_a_summary(tmp_path):
    hub, token, _ = make(tmp_path)
    summary = dict(path="rollout_summaries/a.md", product="codex-cli")
    hub.put(token, asset("rollout", body="订单回调验签 一", **summary))
    hub.put(token, asset("topic", body="订单回调验签 二", path="topic.md"))
    found = hub.retrieve(token, "订单回调验签", include_evidence=True)
    stages = {s["stage"] for row in found["trace"]["candidates"] for s in row["stages"]}
    assert {"rollout", "topic"} <= set(paths(found["items"])) and "supporting_weight" not in stages


def _hub(tmp_path):
    from agent_brain.native_sync.hub import Hub

    hub = Hub(
        tmp_path / "hub.sqlite",
        semantic_backend=TopicBackend(),
        hook_gate={**sim_only(threshold=0.99), "lift": 0.5},
    )
    return hub, hub.provision("甲", "d1"), hub.provision("乙", "d2")


def test_a_rare_name_is_injected_summaries_included(tmp_path):
    hub, writer, reader = _hub(tmp_path)
    summary = dict(path="rollout_summaries/a.md", product="codex-cli")
    hub.put(writer["token"], asset("a", body="飞书返回 99991672 时改走用户身份。", **summary))
    hub.refresh_semantic_local()
    assert paths(hub.recall(reader["token"], "传附件返回 99991672")["items"]) == ["a"]


def test_a_raw_extraction_is_never_injected_by_name(tmp_path):
    hub, writer, reader = _hub(tmp_path)
    raw = dict(path="raw_memories.md", product="codex-cli")
    hub.put(writer["token"], asset("raw", body="原始记录 99991672", **raw))
    hub.refresh_semantic_local()
    assert hub.recall(reader["token"], "传附件返回 99991672")["items"] == []


def test_a_common_or_partial_name_is_not(tmp_path):
    hub, writer, reader = _hub(tmp_path)
    for i in range(3):
        hub.put(writer["token"], asset(f"c{i}", body=f"AgentOs 第 {i} 篇", path=f"c{i}.md"))
    hub.put(writer["token"], asset("p", body="跑通的 agentos-terminal-bench-local", path="p.md"))
    hub.refresh_semantic_local()
    assert hub.recall(reader["token"], "AgentOs 好像没有 System Prompt")["items"] == []
    assert hub.recall(reader["token"], "优先跑 Terminal-Bench")["items"] == []


def test_this_machines_own_memory_is_not_handed_back(tmp_path):
    hub, writer, reader = _hub(tmp_path)
    hub.put(
        writer["token"],
        asset("a", body="飞书返回 99991672 时改走用户身份。", product="claude-code", path="a.md"),
    )
    hub.refresh_semantic_local()
    asking = {"product": "claude-code", "project": "", "session_digest": "0" * 16}
    question = "传附件返回 99991672"
    assert hub.recall(writer["token"], question, asking=asking)["items"] == []
    # Another product on this machine does not read Claude Code's files.
    codex = {**asking, "product": "codex-cli"}
    assert paths(hub.recall(writer["token"], question, asking=codex)["items"]) == ["a"]
    assert paths(hub.recall(reader["token"], question, asking=asking)["items"]) == ["a"]


def test_whole_name_allows_sentence_ends_but_not_longer_names():
    assert relevance.whole_name("跑 mine_v3.py。", "mine_v3.py")
    assert relevance.whole_name("see e1234.", "e1234")
    assert relevance.whole_name("(e1234)", "e1234")
    assert not relevance.whole_name("agentos-terminal-bench-local", "terminal-bench")
    assert not relevance.whole_name("e12345", "e1234")
    assert not relevance.whole_name("a/e1234", "e1234")


def test_an_index_file_is_never_injected_on_its_score():
    model = relevance.GateModel.from_dict({**sim_only(threshold=0.1), "nav_threshold": 0.9})
    values = [0.99, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]
    assert relevance.admitted(model, 0.99, values)
    navigation = list(values)
    navigation[relevance.FEATURES.index("nav")] = 1.0
    assert not relevance.admitted(model, 0.99, navigation)


def test_no_title_only_tier_reaches_a_client(tmp_path):
    from fastapi.testclient import TestClient

    from agent_brain.native_sync.api import create_app

    hub, writer, reader = _hub(tmp_path)
    hub.put(
        writer["token"], asset("a", body="approval 记录", path="MEMORY.md", product="codex-cli")
    )
    hub.refresh_semantic_local()
    reply = TestClient(create_app(hub)).post(
        "/native/v1/recall",
        json={"query": "两人复核"},
        headers={"Authorization": "Bearer " + reader["token"]},
    )
    assert reply.status_code == 200 and reply.json()["hints"] == []


def test_a_part_of_a_handbook_is_named_by_its_own_section():
    body = (
        "# Task Group: 灵核发布\n\n说明\n\n## Task 1: 细节\n正文 X123\n\n# Task Group: 飞书\n别的"
    )
    item = {
        "role": "navigation",
        "title": "MEMORY",
        "body": body,
        "semantic_match": {"source_field": "body", "char_end": body.index("X123") + 4},
    }
    _name_section(item)
    assert item["section_title"] == "灵核发布"
    # The title stays: it is part of the source identity a later read must match.
    assert item["title"] == "MEMORY"
    topic = {"role": "topic_memory", "title": "原名", "body": "# 别的"}
    _name_section(topic)
    assert "section_title" not in topic
    unnamed = {"role": "navigation", "title": "MEMORY", "body": "- [a](a.md) 没有标题"}
    _name_section(unnamed)
    assert "section_title" not in unnamed


def test_a_named_section_can_still_be_read(live, tmp_path):  # noqa: F811
    """The section name goes beside the title: a read checks the source against the search."""
    from agent_brain.native_sync.client import NativeClient
    from agent_brain.native_sync.org_tools import OrganizationTools

    hub, url = live
    writer = hub.provision("甲", "d1")
    body = "# Task Group: 灵核发布\n\nscope: x\n\n## Task 1\n发布前跑 verify_release_x9.py。\n"
    hub.put(writer["token"], asset("handbook", body=body, path="MEMORY.md", product="codex-cli"))
    c = NativeClient.adopt(tmp_path / "state", url, hub.provision("乙", "d2"), "d2")
    tools = OrganizationTools(c.root)
    found = tools.search("verify_release_x9.py")
    assert found["ok"], found
    row = found["items"][0]
    assert row["asset_id"] == "handbook" and row["section"] == "灵核发布"
    page = tools.read("handbook", row["revision"], found["research_id"])
    assert page["ok"], page
    assert "verify_release_x9.py" in page["text"]


def test_prompt_recall_can_be_turned_off_leaving_the_catalog_and_tools(live, tmp_path):  # noqa: F811
    from typer.testing import CliRunner

    from agent_brain.native_sync import hooks
    from agent_brain.native_sync.cli import app
    from agent_brain.native_sync.client import NativeClient

    hub, url = live
    writer = hub.provision("甲", "d1")
    hub.put(writer["token"], asset("a", body="飞书返回 99991672 时改走用户身份。", path="a.md"))
    c = NativeClient.adopt(tmp_path / "state", url, hub.provision("乙", "d2"), "d2")
    payload = {"hook_event_name": "UserPromptSubmit", "session_id": "s", "prompt": "返回 99991672"}
    assert "hookSpecificOutput" in hooks.run(c.root, "claude-code", payload)
    changed = CliRunner().invoke(app, ["recall", "--state", str(c.root), "--no-prompt"])
    assert changed.exit_code == 0 and '"prompt_recall": false' in changed.output
    assert hooks.run(c.root, "claude-code", {**payload, "session_id": "t"}) == {}
    assert NativeClient(c.root).recall_enabled()
