"""1.5.4: recall ranks literally (like grep) by default; hybrid is one switch away.
In hybrid mode a wiki page is held to a higher bar than other memory."""

import pytest

from agent_brain.native_sync import semantic
from agent_brain.native_sync.hub import WIKI_MARGIN, Hub
from agent_brain.native_sync.retrieval import substring_terms
from test_hub import asset
from test_learned_gate import TopicBackend, sim_only, topic_hub


def literal_hub(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite", retrieval_mode="literal")
    token = hub.provision("one", "device")["token"]
    hub.put(
        token,
        asset("a1", title="回调", body="E500 回调验签失败，重试后仍是 E500；检查签名密钥版本"),
    )
    hub.put(token, asset("a2", title="网关", body="网关偶发 E500，与验签无关"))
    hub.put(token, asset("a3", title="审批", body="审批流程需要两名评审人"))
    return hub, token


def ids(result):
    return [item["asset_id"] for item in result["items"]]


def gate_rows(hub, token, trace_id):
    trace = hub.recalls(token, trace_id=trace_id)["traces"][0]
    return {c["asset_id"]: c for c in trace["candidates"]}


# --- the switch -------------------------------------------------------------------


def test_literal_is_the_default_and_hybrid_is_one_setting_away(tmp_path, monkeypatch):
    monkeypatch.delenv("AMH_NATIVE_RETRIEVAL", raising=False)
    assert Hub(tmp_path / "a.sqlite").retrieval_mode == "literal"
    monkeypatch.setenv("AMH_NATIVE_RETRIEVAL", "Hybrid")
    assert Hub(tmp_path / "b.sqlite").retrieval_mode == "hybrid"
    assert Hub(tmp_path / "c.sqlite", retrieval_mode="literal").retrieval_mode == "literal"
    monkeypatch.setenv("AMH_NATIVE_RETRIEVAL", "fuzzy")
    with pytest.raises(ValueError):
        Hub(tmp_path / "d.sqlite")


def test_substring_terms_are_space_separated_and_case_folded():
    assert substring_terms("E500  验签 e500 rollout_summaries/x.md") == [
        "e500",
        "验签",
        "rollout_summaries/x.md",
    ]
    assert substring_terms("   ") == []


# --- the search tool ----------------------------------------------------------------


def test_the_search_tool_ranks_like_grep(tmp_path):
    hub, token = literal_hub(tmp_path)
    result = hub.retrieve(token, "E500 验签", channel="tool")
    assert ids(result) == ["a1", "a2"]
    assert result["retrieval"]["mode"] == "literal"
    assert result["retrieval"]["reasons"] == ["literal_mode"]
    # No tokenizer and no synonyms: a term matches wherever its characters appear.
    assert ids(hub.retrieve(token, "评审人", channel="tool")) == ["a3"]
    assert ids(hub.retrieve(token, "签名密钥 e500", channel="tool")) == ["a1", "a2"]
    assert ids(hub.retrieve(token, "不存在的词", channel="tool")) == []


def test_more_distinct_terms_beat_more_repetitions(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite", retrieval_mode="literal")
    token = hub.provision("one", "device")["token"]
    hub.put(token, asset("many", body="验签 验签 验签 验签 验签 验签"))
    hub.put(token, asset("both", body="验签 和 密钥 各出现一次"))
    assert ids(hub.retrieve(token, "验签 密钥", channel="tool")) == ["both", "many"]


# --- the prompt-time hook -----------------------------------------------------------


def test_the_hook_injects_only_what_the_prompt_names(tmp_path, monkeypatch):
    monkeypatch.setenv("AMH_NATIVE_HOOK_LEXICAL_GUARD", "1")
    hub, token = literal_hub(tmp_path)
    named = hub.recall(token, "订单回调返回 E500 是什么原因")
    assert set(ids(named)) == {"a1", "a2"}
    assert named["retrieval"]["mode"] == "literal"
    # Shared ordinary words are not relevance: nothing is injected unasked.
    assert ids(hub.recall(token, "审批流程怎么走")) == []


# --- reversibility ------------------------------------------------------------------


def test_the_semantic_index_is_kept_but_not_consulted(tmp_path, monkeypatch):
    hub = Hub(
        tmp_path / "hub.sqlite",
        semantic_backend=TopicBackend(),
        hook_gate=sim_only(),
        retrieval_mode="literal",
    )
    token = hub.provision("one", "device")["token"]
    hub.put(token, asset("a1", title="approval", body="approval needs two reviewers"))
    hub.put(token, asset("r1", title="release", body="release train on thursdays"))
    hub.rebuild_semantic(token, kinds=["memory", "rule"])
    asked: list[str] = []
    original = semantic.query_vector

    def counting(backend, text):
        asked.append(text)
        return original(backend, text)

    monkeypatch.setattr(semantic, "query_vector", counting)
    literal = hub.retrieve(token, "approval reviewers", channel="tool")
    assert ids(literal) == ["a1"] and literal["retrieval"]["mode"] == "literal"
    assert asked == []
    hub.retrieval_mode = "hybrid"
    hybrid = hub.retrieve(token, "approval reviewers", channel="tool")
    assert hybrid["retrieval"]["mode"] == "hybrid" and ids(hybrid)[0] == "a1"
    assert asked == ["approval reviewers"]


def test_autotune_is_skipped_in_literal_mode(tmp_path):
    hub, token = literal_hub(tmp_path)
    assert hub.recall_autotune_start(token) == {"started": False, "reason": "literal_mode"}


# --- the wiki margin (hybrid mode) ----------------------------------------------------


def test_a_wiki_page_is_held_to_a_higher_bar(tmp_path):
    hub, token = topic_hub(tmp_path, sim_only(max_items=8))  # 8 is the model's cap
    assert hub.wiki_margin == WIKI_MARGIN == 0.08
    # Only a designated device may upload the organization wiki (1.5.1). The page
    # covers the topic in its own words, so near-duplicate folding leaves it alone.
    dreamer = hub.provision("one", "dreamer")
    hub.admin_setting(hub.admin_create("ops"), "dream_devices", dreamer["device_id"])
    page = "approval wiki: who signs off, weekend escalation, audit retention, contractors"
    hub.put(
        dreamer["token"],
        asset("w1", title="approval wiki", body=page, product="amh", path="org-wiki/approval.md"),
    )
    hub.rebuild_semantic(token, kinds=["memory", "rule"])

    hub.wiki_margin = 0.0
    plain = hub.recall(token, "approval needs two reviewers")
    assert {"w1", "a1"} <= set(ids(plain))
    assert gate_rows(hub, token, plain["trace_id"])["w1"]["gate"]["threshold"] == 0.5

    hub.wiki_margin = 0.6  # a bar above 1: the page cannot clear it, the topic memory still does
    held = hub.recall(token, "approval needs two reviewers")
    assert "a1" in ids(held) and "w1" not in ids(held)
    rows = gate_rows(hub, token, held["trace_id"])
    assert rows["w1"]["gate"]["threshold"] == 1.1 and rows["a1"]["gate"]["threshold"] == 0.5


def test_the_margin_comes_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("AMH_NATIVE_WIKI_MARGIN", "0.2")
    assert Hub(tmp_path / "a.sqlite").wiki_margin == 0.2
    assert Hub(tmp_path / "b.sqlite", wiki_margin=0.0).wiki_margin == 0.0
