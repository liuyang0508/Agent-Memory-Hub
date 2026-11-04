"""Degraded prompt-time recall: without semantic ranking there is no relevance
gate, so the hook injects only memories that literally contain an identifier
from the prompt. A shared keyword alone ("SLS", "链路") is not relevance."""

import pytest

from agent_brain.native_sync.hub import Hub
from test_hub import asset


@pytest.fixture
def lexical_hub(tmp_path, monkeypatch):
    monkeypatch.setenv("AMH_NATIVE_HOOK_LEXICAL_GUARD", "1")
    hub = Hub(tmp_path / "hub.sqlite")
    token = hub.provision("one", "device")["token"]
    hub.put(token, asset("m1", title="任务组索引", body="SLS 日志 OSS 会话 链路 排查 汇总目录"))
    hub.put(token, asset("m2", title="额度说明", body="SLS OSS 链路 额度 申请 资格"))
    hub.put(
        token,
        asset("m3", title="网关报错", body="gateway 返回 E40213 时检查 sign_key_v2 是否轮换"),
    )
    return hub, token


def hook(hub, token, query):
    return hub.retrieve(token, query, kinds=["memory", "rule"], channel="hook", record=False)


def test_keyword_overlap_alone_injects_nothing_when_degraded(lexical_hub):
    hub, token = lexical_hub
    result = hook(hub, token, "结合 SLS 和 OSS 讲讲核心链路怎么排查")
    assert result["items"] == []
    assert result["retrieval"]["mode"] == "lexical"
    assert "hook_lexical_guard" in result["retrieval"]["reasons"]


def test_a_literal_identifier_from_the_prompt_still_comes_through(lexical_hub):
    hub, token = lexical_hub
    result = hook(hub, token, "SLS 里看到 E40213 是什么原因")
    assert [item["asset_id"] for item in result["items"]] == ["m3"]


def test_tools_and_an_explicit_opt_out_keep_plain_keyword_recall(lexical_hub, monkeypatch):
    hub, token = lexical_hub
    query = "结合 SLS 和 OSS 讲讲核心链路怎么排查"
    assert len(hub.retrieve(token, query, kinds=["memory", "rule"], record=False)["items"]) >= 2
    hub.hook_lexical_guard = False
    assert hook(hub, token, query)["items"]


def test_the_table_names_degraded_reasons_in_words():
    from agent_brain.native_sync.recall_view import _reason

    assert _reason("semantic_model_unavailable:ModuleNotFoundError") == "Hub 语义模型不可用"
    assert _reason("hook_lexical_guard") == "只注入含提问中标识的记忆"
    assert _reason("something_new") == "something_new"


def test_a_model_that_cannot_load_is_reported_unavailable_not_loading(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite", semantic_model=str(tmp_path / "no-such-model"))
    assert hub.semantic_state["state"] == "loading"
    hub.refresh_semantic_local()
    assert hub.semantic_state["state"] == "unavailable"
    assert hub.semantic_state["reason"].startswith("semantic_model_unavailable:")
