"""Learned prompt-time relevance, recall evaluation and calibration.

Synthetic backends fix similarities; they exercise the mechanics, not the
shipped model's quality (that is measured with recall-eval on a real corpus).
"""

import json
import math

import pytest
from typer.testing import CliRunner

from agent_brain.native_sync import relevance, semantic
from agent_brain.native_sync.cli import app
from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.recall_eval import (
    evaluate,
    judge,
    load_gold,
    regressions,
    relevant,
    split_of,
    summarize,
)
from agent_brain.native_sync.relevance import FEATURES, Candidate, GateModel
from test_hub import asset


class TopicBackend:
    """Two topics on orthogonal axes; generic prompts sit in between."""

    semantic = True  # Test injection only.
    model_id = "topic-test-only"
    model_version = "1"
    dim = 3

    def embed(self, text):
        if "审批" in text or "approval" in text:
            return [1.0, 0.0, 0.1]
        if "发布" in text or "release" in text:
            return [0.0, 1.0, 0.1]
        return [0.5, 0.5, 0.5]


def sim_only(threshold=0.5, max_items=3, dedup=0.5):
    """A gate that trusts similarity alone: p = sigmoid(20 * (sim - 0.7))."""
    weights = [20.0] + [0.0] * (len(FEATURES) - 1)
    return GateModel(
        mean=[0.7] + [0.0] * (len(FEATURES) - 1),
        scale=[1.0] * len(FEATURES),
        weights=weights,
        bias=0.0,
        threshold=threshold,
        max_items=max_items,
        dedup=dedup,
    ).to_dict()


def topic_hub(tmp_path, gate):
    hub = Hub(tmp_path / "hub.sqlite", semantic_backend=TopicBackend(), hook_gate=gate)
    token = hub.provision("one", "device")["token"]
    note = "approval needs two reviewers and a change list, " * 4
    hub.put(token, asset("a1", title="approval", body=note))
    hub.put(token, asset("a2", title="approval copy", body=note + " (synced from codex)"))
    hub.put(
        token, asset("a3", title="approval extra", body="approval escalation path for weekends")
    )
    hub.put(token, asset("a4", title="approval more", body="approval audit retention policy"))
    hub.put(token, asset("a5", title="approval last", body="approval for contractors"))
    hub.put(token, asset("r1", title="release", body="release train on thursdays"))
    hub.rebuild_semantic(token, kinds=["memory", "rule"])
    return hub, token


def test_terms_coverage_and_containment():
    assert {"审批", "批流", "流程", "e1234"} <= relevance.terms("审批流程 E1234")
    assert relevance.coverage({"审批", "流程"}, "审批规则") == 0.5
    a = relevance.shingles("x" * 3 + "approval needs two reviewers" * 3)
    b = relevance.shingles("approval needs two reviewers" * 3 + " extra")
    assert relevance.containment(a, b) > 0.8
    assert relevance.containment(a, relevance.shingles("totally different text here")) == 0
    assert relevance.top_mean([0.1, 0.9, 0.5], k=2) == pytest.approx(0.7)


def test_gate_model_validates_and_round_trips():
    raw = sim_only()
    model = GateModel.from_dict(raw)
    assert GateModel.from_dict(model.to_dict()).to_dict() == model.to_dict()
    assert model.probability([0.7] + [0.0] * 7) == pytest.approx(0.5)
    assert model.probability([1.0] + [0.0] * 7) > 0.99
    for bad in (
        {**raw, "features": ["sim"]},
        {**raw, "scale": [0.0] * 8},
        {**raw, "threshold": 1.5},
        {**raw, "weights": [math.nan] * 8},
        {**raw, "max_items": 0},
    ):
        with pytest.raises(ValueError):
            GateModel.from_dict(bad)


def test_fit_learns_a_separable_signal_and_select_applies_the_rule():
    rows = [[0.9 - i * 0.01, 0, 0, 0, 0, 0, 0, 0] for i in range(10)] + [
        [0.2 + i * 0.01, 0, 0, 0, 0, 0, 0, 0] for i in range(10)
    ]
    model = relevance.fit(rows, [1.0] * 10 + [0.0] * 10)
    assert model.probability(rows[0]) > 0.8 and model.probability(rows[-1]) < 0.2
    same = relevance.shingles("the same note " * 10)
    candidates = [
        Candidate(rows[0], True, same, ("h1", "topic")),
        Candidate(rows[1], True, same, ("h2", "topic")),  # near-duplicate of the first
        Candidate(rows[2], True, relevance.shingles("another note " * 10), ("h3", "topic")),
        Candidate(rows[-1], False, relevance.shingles("noise " * 10), ("h4", "topic")),
        Candidate(
            [0.2, 0, 0, 0, 1.0, 0, 0, 1.0], True, relevance.shingles("E1234 " * 9), ("h5", "x")
        ),
    ]
    model.max_items = 3
    chosen = relevance.select(model, candidates, 0.5)
    # Folded duplicate, dropped noise, and the anchored identifier kept despite p.
    assert [c.duplicate_key[0] for c in chosen] == ["h1", "h3", "h5"]
    with pytest.raises(ValueError):
        relevance.fit(rows[:5], [1.0] * 5)


def test_cross_validation_reports_held_out_folds_and_prefers_recall_on_ties():
    def case(i, answerable):
        return {"id": f"c{i}", "split": "dev", "tags": [], "expect": ["x"] if answerable else []}

    cases = []
    for i in range(20):
        good = Candidate([0.9, 0, 0, 0, 0, 0, 0, 0], True, {f"g{i}"}, (f"g{i}", "t"))
        bad = Candidate([0.3, 0, 0, 0, 0, 0, 0, 0], False, {f"b{i}"}, (f"b{i}", "t"))
        cases.append((case(i, True), [good, bad]))
    for i in range(20, 26):
        cases.append(
            (
                case(i, False),
                [Candidate([0.3, 0, 0, 0, 0, 0, 0, 0], False, {f"n{i}"}, (f"n{i}", "t"))],
            )
        )
    report = relevance.cross_validate(cases, seeds=2)
    assert report["best"]["hit@1"] == 1.0 and report["best"]["abstention"] == 1.0
    # Every threshold is perfect here, so the tie rule keeps the lowest one.
    assert report["best"]["threshold"] == relevance.THRESHOLDS[0]


def test_hook_uses_the_learned_gate_caps_and_folds_near_duplicates(tmp_path):
    hub, token = topic_hub(tmp_path, sim_only(max_items=3))
    result = hub.recall(token, "approval 审批 要几个人")
    got = [item["asset_id"] for item in result["items"]]
    assert len(got) == 3 and "r1" not in got
    assert not {"a1", "a2"} <= set(got)  # one copy of the synced note
    assert all(0.5 <= item["recall"]["relevance"] <= 1 for item in result["items"])
    trace = hub.recalls(token, trace_id=result["trace_id"])["traces"][0]
    assert trace["stages"]["relevance_gate"] == "learned-v1"
    states = {c["asset_id"]: c["state"] for c in trace["candidates"]}
    assert "dropped:near_duplicate" in states.values()
    assert hub.gate_state() == "learned-v1"
    # Unrelated prompt: nothing clears the gate, nothing is injected.
    assert hub.recall(token, "帮我写个快速排序")["items"] == []
    # Tools are never gated or capped.
    assert len(hub.retrieve(token, "approval", kinds=["memory", "rule"])["items"]) >= 5


def test_gate_waits_for_its_background_and_falls_back(tmp_path, monkeypatch):
    hub, token = topic_hub(tmp_path, sim_only())
    hub._bank.clear()
    result = hub.retrieve(token, "approval", kinds=["memory", "rule"], channel="hook", record=False)
    assert "gate_background_pending" in result["retrieval"]["reasons"]
    assert result["retrieval"]["stages"]["relevance_gate"] != "learned-v1"
    broken = Hub(tmp_path / "other.sqlite", semantic_backend=TopicBackend(), hook_gate={"x": 1})
    assert broken.gate_model() is None and broken.gate_state().startswith("hook_gate_invalid")
    path = tmp_path / "gate.json"
    path.write_text(json.dumps(sim_only(threshold=0.4)))
    monkeypatch.setenv("AMH_NATIVE_HOOK_GATE", str(path))
    from_env = Hub(tmp_path / "env.sqlite", semantic_backend=TopicBackend())
    assert from_env.gate_model().threshold == 0.4


def test_gold_loading_splits_judging_and_regressions(tmp_path):
    gold = tmp_path / "gold.jsonl"
    gold.write_text(
        "\n".join(
            json.dumps(case, ensure_ascii=False)
            for case in (
                {"id": "a", "query": "审批", "expect": ["approval"], "tags": ["short"]},
                {"id": "b", "q": "无关", "targets": [], "split": "test"},
            )
        )
    )
    cases = load_gold(gold)
    assert cases[0]["split"] == split_of("a") and cases[1]["split"] == "test"
    assert relevant({"title": "approval rules"}, ["approval"])
    assert not relevant({"title": "release"}, ["approval"])
    outcome = judge(cases[0], [{"title": "release"}, {"path": "x/approval.md"}])
    assert outcome["first_relevant"] == 2 and outcome["relevant_returned"] == 1
    report = summarize([outcome, judge(cases[1], [])], [0.01, 0.02])
    assert report["mrr"] == 0.5 and report["abstention"] == 1.0 and report["precision"] == 0.5
    assert regressions({"overall": {**report, "hit@3": 0.5}}, {"overall": {**report, "hit@3": 0.9}})
    assert not regressions({"overall": report}, {"overall": report})
    (tmp_path / "dup.json").write_text(json.dumps([{"id": "a", "q": "x"}, {"id": "a", "q": "y"}]))
    with pytest.raises(ValueError):
        load_gold(tmp_path / "dup.json")


def test_evaluate_runs_without_traces(tmp_path):
    hub, token = topic_hub(tmp_path, sim_only())
    cases = [
        {
            "id": "a",
            "query": "approval 审批",
            "expect": ["approval"],
            "tags": ["t"],
            "split": "dev",
        },
        {"id": "n", "query": "写个快速排序", "expect": [], "tags": [], "split": "test"},
    ]
    report = evaluate(hub, token, cases)
    assert report["overall"]["hit@1"] == 1.0 and report["overall"]["abstention"] == 1.0
    assert report["dev"]["cases"] == 1 and report["by_tag"]["t"]["cases"] == 1
    assert hub.recalls(token)["traces"] == []


def test_cli_calibrates_and_evaluates_on_a_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(semantic, "load_backend", lambda spec: TopicBackend())
    hub, token = topic_hub(tmp_path, None)
    gold = tmp_path / "gold.json"
    cases = [
        {"id": f"a{i}", "query": f"approval 审批 {i}", "expect": ["approval"]} for i in range(12)
    ] + [{"id": f"r{i}", "query": f"release 发布 {i}", "expect": ["release"]} for i in range(6)]
    cases += [{"id": f"n{i}", "query": f"写个排序 {i}", "expect": []} for i in range(6)]
    gold.write_text(json.dumps(cases, ensure_ascii=False))
    runner = CliRunner()
    out = tmp_path / "gate.json"
    calibrated = runner.invoke(
        app,
        [
            "recall-calibrate",
            "--db",
            str(hub.path),
            "--gold",
            str(gold),
            "--model",
            "fake",
            "--out",
            str(out),
        ],
    )
    assert calibrated.exit_code == 0, calibrated.output
    gate = GateModel.from_dict(json.loads(out.read_text()))
    assert gate.calibration["cases"] == len(cases) and 0 < gate.threshold < 1
    monkeypatch.setenv("AMH_NATIVE_HOOK_GATE", str(out))
    report_path = tmp_path / "report.json"
    evaluated = runner.invoke(
        app,
        [
            "recall-eval",
            "--db",
            str(hub.path),
            "--gold",
            str(gold),
            "--model",
            "fake",
            "--out",
            str(report_path),
        ],
    )
    assert evaluated.exit_code == 0, evaluated.output
    report = json.loads(report_path.read_text())
    assert report["gate"] == "learned-v1" and report["overall"]["abstention"] == 1.0
    worse = tmp_path / "baseline.json"
    worse.write_text(json.dumps({"overall": {**report["overall"], "hit@1": 1.5}}))
    regressed = runner.invoke(
        app,
        [
            "recall-eval",
            "--db",
            str(hub.path),
            "--gold",
            str(gold),
            "--model",
            "fake",
            "--baseline",
            str(worse),
        ],
    )
    assert regressed.exit_code == 1
    # The live database was only read.
    assert hub.recalls(token)["traces"] == []


def test_health_names_the_gate(tmp_path):
    from fastapi.testclient import TestClient

    from agent_brain.native_sync.api import create_app

    hub, _ = topic_hub(tmp_path, sim_only())
    hub.semantic_state = {"state": "ready", "model": "topic-test-only"}
    health = TestClient(create_app(hub)).get("/native/v1/health").json()
    assert health["semantic"]["gate"] == "learned-v1"
    assert "relevance-gate-v1" in health["capabilities"]


def test_the_recall_api_returns_the_relevance_probability(tmp_path):
    from fastapi.testclient import TestClient

    from agent_brain.native_sync.api import create_app

    hub, token = topic_hub(tmp_path, sim_only())
    response = TestClient(create_app(hub)).post(
        "/native/v1/recall",
        json={"query": "approval 审批"},
        headers={"Authorization": "Bearer " + token},
    )
    items = response.json()["items"]
    assert items and all(0.5 <= item["recall"]["relevance"] <= 1 for item in items)


def test_followers_must_reach_a_share_of_the_leader():
    leader = Candidate(
        [0.95] + [0.0] * 7, True, relevance.shingles("leader note " * 9), ("h1", "t")
    )
    weak = Candidate([0.72] + [0.0] * 7, False, relevance.shingles("weak note " * 9), ("h2", "t"))
    anchored = Candidate(
        [0.65, 0, 0, 0, 1.0, 0, 0, 1.0], True, relevance.shingles("E1234 x " * 9), ("h3", "t")
    )
    model = GateModel.from_dict({**sim_only(threshold=0.5), "relative": 0.8})
    assert model.to_dict()["relative"] == 0.8
    # p(leader) ~ 0.99, p(weak) ~ 0.6 < 0.8 * 0.99; the identifier still passes.
    chosen = relevance.select(model, [leader, weak, anchored], 0.5)
    assert chosen == [leader, anchored]
    model.relative = 0.0
    assert weak in relevance.select(model, [leader, weak], 0.5)
    with pytest.raises(ValueError):
        GateModel.from_dict({**sim_only(), "relative": 1.0})


def test_hook_recall_drops_weak_followers_and_says_why(tmp_path):
    gate = {**sim_only(threshold=0.05), "relative": 0.9}
    # Keyword rank alone: rank 1 p~0.98, rank 2 p=0.5, unranked p~0.02 (below 0.05).
    gate["weights"], gate["bias"] = [0.0, 0, 0, 0, 8.0, 0, 0, 0], -4.0
    hub, token = topic_hub(tmp_path, gate)
    result = hub.recall(token, "approval reviewers")
    assert len(result["items"]) == 1
    trace = hub.recalls(token, trace_id=result["trace_id"])["traces"][0]
    reasons = [
        c.get("state_detail") or c.get("dropped") or c.get("state") for c in trace["candidates"]
    ]
    assert any("follow" in json.dumps(c) for c in trace["candidates"]), reasons
