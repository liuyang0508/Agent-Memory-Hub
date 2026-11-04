"""1.4.18 injection tiers: an absolute lift floor for full injection, and a
title-only tier the model reads on demand. Synthetic backends fix the
similarities; the shipped values are measured with recall-eval."""

import pytest
from fastapi.testclient import TestClient

from agent_brain.native_sync import relevance
from agent_brain.native_sync.api import create_app, recall_hints
from agent_brain.native_sync.recall import HINT_HEAD, pack
from agent_brain.native_sync.recall_view import render
from agent_brain.native_sync.relevance import FEATURES, Candidate, GateModel
from test_learned_gate import sim_only, topic_hub


def tiered(lift, hint_threshold=0.05, hint_lift=-1.0):
    gate = sim_only(threshold=0.5)
    gate.update(lift=lift, hint_threshold=hint_threshold, hint_lift=hint_lift, hint_items=2)
    return gate


def test_rule_round_trips_validates_and_travels_with_autotune():
    model = GateModel.from_dict(tiered(0.08, 0.15, 0.04))
    assert (model.lift, model.hint_threshold, model.hint_lift, model.hint_items) == (
        0.08,
        0.15,
        0.04,
        2,
    )
    assert GateModel.from_dict(model.to_dict()).to_dict() == model.to_dict()
    with pytest.raises(ValueError):
        GateModel.from_dict(tiered(0.08, hint_threshold=1.5))
    fitted = GateModel(mean=[0.0] * 8, scale=[1.0] * 8, weights=[0.0] * 8, bias=0.0)
    relevance.adopt_rule(fitted, model)
    assert (fitted.lift, fitted.hint_threshold, fitted.relative) == (0.08, 0.15, model.relative)


def candidate(sim, doc_bg, anchor=0.0, relevant=True):
    values = [0.0] * len(FEATURES)
    values[FEATURES.index("sim")] = sim
    values[FEATURES.index("doc_bg")] = doc_bg
    values[FEATURES.index("anchor")] = anchor
    values[FEATURES.index("lex_inv")] = 1.0
    return Candidate(values=values, relevant=relevant, shingles={str(sim)}, duplicate_key=(str(sim), "m"))


def test_select_needs_lift_unless_the_prompt_names_an_identifier():
    model = GateModel.from_dict(tiered(0.08))
    # High similarity everywhere, but no closer to this prompt than to chit-chat.
    flat = candidate(0.9, 0.86)
    assert relevance.select(model, [flat], 0.5) == []
    assert relevance.select(model, [candidate(0.9, 0.5)], 0.5)
    assert relevance.select(model, [candidate(0.9, 0.86, anchor=1.0)], 0.5)


def test_hook_recall_names_held_memories_without_their_bodies(tmp_path):
    # Every memory is held by an impossible floor; the probable ones are named.
    hub, token = topic_hub(tmp_path, tiered(lift=0.99))
    result = hub.recall(token, "approval 审批 要几个人")
    assert result["items"] == []
    assert 1 <= len(result["hints"]) <= 2
    for hint in result["hints"]:
        assert set(hint) == {"asset_id", "revision", "title", "path", "product", "kind", "probability"}
    trace = hub.recalls(token, trace_id=result["trace_id"])["traces"][0]
    states = {c["asset_id"]: c["state"] for c in trace["candidates"]}
    assert {states[h["asset_id"]] for h in result["hints"]} == {"hinted"}
    # Without the floor the same prompt injects in full and names nothing twice.
    open_hub, open_token = topic_hub(tmp_path / "open", tiered(lift=-1.0))
    opened = open_hub.recall(open_token, "approval 审批 要几个人")
    assert opened["items"]
    assert not {h["asset_id"] for h in opened["hints"]} & {i["asset_id"] for i in opened["items"]}
    # Tools are never tiered.
    assert hub.retrieve(token, "approval", kinds=["memory"], record=False)["hints"] == []


def test_api_returns_projected_hints_and_announces_them(tmp_path):
    hub, token = topic_hub(tmp_path, tiered(lift=0.99))
    api = TestClient(create_app(hub))
    assert "recall-hints-v1" in api.get("/native/v1/health").json()["capabilities"]
    body = api.post(
        "/native/v1/recall",
        json={"query": "approval 审批 要几个人"},
        headers={"Authorization": "Bearer " + token},
    ).json()
    assert body["items"] == [] and body["hints"]
    assert all("body" not in hint for hint in body["hints"])
    assert recall_hints([{"asset_id": "", "revision": 1}, {"asset_id": "x", "revision": "1"}]) == []


def test_pack_and_view_carry_titles_only():
    hints = [
        {"asset_id": "h1", "revision": 2, "title": "OSS 读取坑", "product": "claude-code", "probability": 0.21},
        {"asset_id": "h2", "revision": 1, "title": "已注入过", "product": "codex-cli"},
    ]
    body, keys = pack([], covered={"h2:1:abc"}, hints=hints)
    assert HINT_HEAD in body and "asset_id=h1 revision=2" in body and "h2" not in body
    assert keys == []  # a title is not a delivery of the memory
    assert pack([], covered=set(), hints=[]) == ("", [])
    view = render(
        hub="https://hub",
        query="q",
        asking={},
        budget=4.0,
        elapsed_ms=10,
        via="service",
        mode="remote",
        retrieval={"mode": "hybrid"},
        trace_id=None,
        hits=[],
        states={},
        display="table",
        hints=hints[:1],
    )
    assert "只给标题 1 条" in view and "《OSS 读取坑》（21%）" in view
