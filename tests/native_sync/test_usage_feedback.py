"""Usage feedback: judging use at the end of a turn, labels on the Hub, recalibration.

The judge is checked on hand-made turns that mirror what real sessions showed:
shared project vocabulary is not use, a copied identifier or several copied
passages are, and an answer with nothing of the memory is a clear "unused".
"""

import json
import time

import pytest
from fastapi.testclient import TestClient

from agent_brain.native_sync import autotune, hooks, recall_ledger, usage
from agent_brain.native_sync.api import create_app
from agent_brain.native_sync.client import NativeClient
from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.recall_view import admit
from agent_brain.native_sync.relevance import FEATURES, GateModel
from agent_brain.native_sync.storage import database
from agent_brain.native_sync.transport import HubHTTPError
from test_hub import asset
from test_learned_gate import TopicBackend, sim_only, topic_hub
from test_runtime import live as live  # noqa: F401  (fixture re-export)

SALT = "session-1"
MEMORY = (
    "发布前先跑 scripts/release-check-2026.sh，确认 CHANGELOG 写好。"
    "灰度用 canary-pool-07，观察半小时再全量。回滚命令是 deploy --rollback-to-last-green。"
)
FILLER = "这一轮先把需求过一遍，然后整理成清单，最后再统一给出建议和下一步。" * 8


def judge(output, *, seen="", allow_used=True, memory=MEMORY):
    return usage.judge(
        usage.fingerprint(memory, SALT), output=output, seen=seen, salt=SALT, allow_used=allow_used
    )


def test_a_copied_specific_identifier_means_used():
    label, evidence = judge(FILLER + " 先执行 scripts/release-check-2026.sh 再说。")
    assert label == "used" and evidence["strong"] == 1


def test_three_separate_copied_passages_mean_used():
    output = FILLER + "灰度观察半小时再全量；确认CHANGELOG写好；回滚命令记得留。"
    label, evidence = judge(output)
    assert label == "used" and evidence["pieces"] >= 3


def test_one_shared_phrase_is_not_enough_either_way():
    label, evidence = judge(FILLER + " 我们先观察半小时。")
    assert label is None and evidence["hits"] >= 1


def test_nothing_in_common_means_unused():
    label, evidence = judge(FILLER + " TCP 三次握手是 SYN、SYN-ACK、ACK。")
    assert label == "unused" and evidence["hits"] == 0


def test_what_the_model_already_saw_is_not_evidence():
    output = FILLER + " 先执行 scripts/release-check-2026.sh 再说。"
    assert judge(output, seen="上次说过 scripts/release-check-2026.sh 要先跑")[0] == "unused"


def test_short_answers_are_not_judged_and_used_can_be_withheld():
    assert judge("好的。")[0] is None
    output = FILLER + " 先执行 scripts/release-check-2026.sh 再说。"
    assert judge(output, allow_used=False)[0] is None


def test_fingerprints_keep_no_text():
    stored = json.dumps(usage.fingerprint(MEMORY, SALT), ensure_ascii=False)
    assert "release" not in stored and "灰度" not in stored and "canary" not in stored


def test_background_events_are_not_searched():
    assert admit("<task-notification> <task-id>b1</task-id> done") == (False, "background_event")


def _transcript(path, rows):
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    return path


def _prompt(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def _assistant(*parts):
    return {"type": "assistant", "message": {"role": "assistant", "content": list(parts)}}


def test_claude_turn_separates_context_output_results_and_org_reads(tmp_path):
    path = _transcript(
        tmp_path / "t.jsonl",
        [
            _prompt("上一轮的问题"),
            _assistant({"type": "text", "text": "上一轮的回答 alpha-context-token-1"}),
            _prompt("这一轮的问题"),
            {
                "type": "attachment",
                "attachment": {"type": "hook_additional_context", "content": "X"},
            },
            _assistant(
                {"type": "text", "text": "我来查一下"},
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "mcp__amh-org__read_org_memory",
                    "input": {"asset_id": "m9", "revision": 4},
                },
                {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "ls"}},
            ),
            {
                "type": "user",
                "message": {
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t1", "content": "记忆正文"},
                        {"type": "tool_result", "tool_use_id": "t2", "content": "file-list-output"},
                    ]
                },
            },
            _assistant({"type": "text", "text": "结论在这里"}),
        ],
    )
    turn = usage.claude_turn(path)
    assert "alpha-context-token-1" in turn["seen"] and "这一轮的问题" in turn["seen"]
    assert "结论在这里" in turn["output"] and '"command": "ls"' in turn["output"]
    assert turn["results"] == "file-list-output"
    assert turn["reads"] == [{"arguments": {"asset_id": "m9", "revision": 4}, "text": "记忆正文"}]
    assert usage.read_target(turn["reads"][0]["arguments"], "") == ("m9", 4)


def _seed_pending(c, *, assets):
    with database(c.path) as db:
        db.execute(
            "INSERT INTO usage_pending VALUES(?,?,?,?,?,?)", ("s1", "a" * 24, "", 0, "", 1.0)
        )
        for asset_id, text in assets:
            db.execute(
                "INSERT INTO usage_pending VALUES(?,?,?,?,?,?)",
                ("s1", "a" * 24, asset_id, 3, json.dumps(usage.fingerprint(text, "s1")), 1.0),
            )


def _reports(c):
    with database(c.path) as db:
        return sorted(
            (r["asset"], r["revision"], r["event"])
            for r in db.execute("SELECT * FROM recall_reports")
        )


def test_stop_labels_injected_memories_and_consulted_reads(live, tmp_path):
    hub, url = live
    c = NativeClient.adopt(tmp_path / "state", url, hub.provision("Reader", "r"), "r")
    _seed_pending(
        c,
        assets=[("rel", MEMORY), ("noise", "周报模板：标题、进展、风险、计划四段，每段三行以内。")],
    )
    consulted = (
        "排查 webhook 签名失败：先核对 HMAC-SHA256 密钥版本 key-rotation-2026q3，再看时钟偏差。"
    )
    path = _transcript(
        tmp_path / "t.jsonl",
        [
            _prompt("发布流程是什么"),
            _assistant(
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "mcp__amh-org__read_org_memory",
                    "input": {"asset_id": "deep", "revision": 7},
                }
            ),
            {
                "type": "user",
                "message": {
                    "content": [{"type": "tool_result", "tool_use_id": "t1", "content": consulted}]
                },
            },
            _assistant(
                {
                    "type": "text",
                    "text": FILLER
                    + " 先执行 scripts/release-check-2026.sh。另外签名问题先核对 key-rotation-2026q3。",
                }
            ),
        ],
    )
    out = hooks.run(
        c.root,
        "claude-code",
        {"session_id": "s1", "hook_event_name": "Stop", "transcript_path": str(path)},
    )
    assert out == {}
    assert _reports(c) == [("deep", 7, "consulted"), ("noise", 3, "unused"), ("rel", 3, "used")]
    with database(c.path) as db:
        assert db.execute("SELECT count(*) FROM usage_pending").fetchone()[0] == 0


def test_without_a_transcript_only_unused_is_reported(live, tmp_path):
    hub, url = live
    c = NativeClient.adopt(tmp_path / "state", url, hub.provision("Reader", "r"), "r")
    _seed_pending(c, assets=[("rel", MEMORY), ("noise", "周报模板：标题、进展、风险、计划四段。")])
    message = FILLER + " 先执行 scripts/release-check-2026.sh。"
    hooks.run(
        c.root,
        "codex-cli",
        {"session_id": "s1", "hook_event_name": "Stop", "last_assistant_message": message},
    )
    assert _reports(c) == [("noise", 3, "unused")]


def test_prompt_records_the_turn_and_stop_reports_through_the_hub(live, tmp_path):
    hub, url = live
    author = hub.provision("Author", "a")
    hub.put(author["token"], asset("fact", title="发布流程", body=MEMORY))
    c = NativeClient.adopt(tmp_path / "state", url, hub.provision("Reader", "r"), "r")
    first = hooks.run(
        c.root,
        "claude-code",
        {"session_id": "s1", "hook_event_name": "UserPromptSubmit", "prompt": "发布流程 灰度"},
    )
    assert "canary-pool-07" in first["hookSpecificOutput"]["additionalContext"]
    with database(c.path) as db:
        pending = db.execute("SELECT asset, fingerprint FROM usage_pending").fetchall()
    assert sorted(r["asset"] for r in pending) == ["", "fact"]
    assert all("canary" not in r["fingerprint"] for r in pending)
    path = _transcript(
        tmp_path / "t.jsonl",
        [
            _prompt("发布流程 灰度"),
            _assistant(
                {
                    "type": "text",
                    "text": FILLER
                    + " 灰度池用 canary-pool-07，回滚用 deploy --rollback-to-last-green。",
                }
            ),
        ],
    )
    hooks.run(
        c.root,
        "claude-code",
        {"session_id": "s1", "hook_event_name": "Stop", "transcript_path": str(path)},
    )
    assert c.sync()["errors"] == []
    trace = hub.recalls(c.config["identity"]["token"], limit=1)["traces"][0]
    assert trace["counts"].get("usage_used") == 1


def test_usage_reports_travel_apart_so_old_hubs_keep_delivery(live, tmp_path):
    hub, url = live
    c = NativeClient.adopt(tmp_path / "state", url, hub.provision("Reader", "r"), "r")
    sent = []

    class OldHub:
        def recall_feedback(self, trace_id, events):
            if any(e["event"] in ("used", "unused") for e in events):
                raise HubHTTPError(422, "request_rejected")
            sent.extend(e["event"] for e in events)
            return {}

    c.transport = OldHub()
    with database(c.path) as db:
        db.executemany(
            "INSERT INTO recall_reports VALUES(?,?,?,?,?)",
            [("b" * 24, "x", 1, "emitted", time.time()), ("b" * 24, "x", 1, "used", time.time())],
        )
    c._report_recalls()
    assert sent == ["emitted"]
    with database(c.path) as db:
        assert db.execute("SELECT count(*) FROM recall_reports").fetchone()[0] == 0


# --- Hub side ---------------------------------------------------------------


@pytest.fixture(autouse=True)
def one_cv_seed(monkeypatch):
    monkeypatch.setattr(autotune, "CV_SEEDS", 1)


def _hook_recall(hub, token, query="approval reviewers"):
    result = hub.recall(token, query)
    return result["trace_id"], result["items"]


def _deliver(hub, token, trace_id, items):
    events = [
        {"asset_id": i["asset_id"], "revision": i["revision"], "event": e}
        for i in items
        for e in ("packed", "emitted")
    ]
    hub.recall_feedback(token, trace_id, events)


def test_hub_keeps_one_label_per_candidate_with_the_gate_evidence(tmp_path):
    hub, token = topic_hub(tmp_path, sim_only(threshold=0.5))
    trace_id, items = _hook_recall(hub, token)
    assert items
    _deliver(hub, token, trace_id, items)
    first = items[0]
    event = {"asset_id": first["asset_id"], "revision": first["revision"], "event": "used"}
    assert hub.recall_feedback(token, trace_id, [event])["applied"] == 1
    again = hub.recall_feedback(token, trace_id, [{**event, "event": "unused"}])
    assert again["noop"] == 1
    # A memory outside the recall says nothing about the gate.
    stray = {"asset_id": "zz", "revision": 1, "event": "consulted"}
    assert hub.recall_feedback(token, trace_id, [stray])["ignored"] == 1
    with database(hub.path) as db:
        labels = recall_ledger.labels(db, hub.identity(token)["tenant"])
    assert [(row["asset_id"], row["label"]) for row in labels] == [(first["asset_id"], "used")]
    assert len(labels[0]["features"]) == len(FEATURES)
    assert labels[0]["gate_version"] == "learned-v1"


def test_usage_needs_a_delivery_and_must_come_from_the_recalling_device(tmp_path):
    hub, token = topic_hub(tmp_path, sim_only(threshold=0.5))
    trace_id, items = _hook_recall(hub, token)
    first = items[0]
    event = {"asset_id": first["asset_id"], "revision": first["revision"], "event": "used"}
    assert hub.recall_feedback(token, trace_id, [event])["ignored"] == 1  # never delivered
    other = hub.provision("one", "laptop")["token"]
    _deliver(hub, token, trace_id, items)
    with pytest.raises(ValueError):
        hub.recall_feedback(other, trace_id, [event])


def test_consulted_labels_a_candidate_the_gate_held_back(tmp_path):
    hub, token = topic_hub(tmp_path, sim_only(threshold=0.5))
    trace_id, items = _hook_recall(hub, token)
    trace = hub.recalls(token, trace_id=trace_id)["traces"][0]
    held = next(c for c in trace["candidates"] if str(c.get("state", "")).startswith("dropped:"))
    event = {"asset_id": held["asset_id"], "revision": held["revision"], "event": "consulted"}
    assert hub.recall_feedback(token, trace_id, [event])["applied"] == 1


def _labels(n_used, n_unused, *, good=True):
    """Labels whose evidence agrees (good) or disagrees with similarity."""
    rows = []
    for i in range(n_used):
        sim = 0.9 - (i % 5) * 0.02 if good else 0.3 + (i % 5) * 0.02
        rows.append({"label": "used", "features": [sim] + [0.0] * 7, "created": i})
    for i in range(n_unused):
        sim = 0.3 + (i % 5) * 0.02 if good else 0.9 - (i % 5) * 0.02
        rows.append({"label": "unused", "features": [sim] + [0.0] * 7, "created": 100 + i})
    return rows


def _gold(n=24):
    cases = []
    for i in range(n):
        if i % 3 == 0:
            cases.append({"id": f"g{i}", "query": f"weather today {i}", "expect": [], "tags": []})
        else:
            cases.append(
                {
                    "id": f"g{i}",
                    "query": f"approval reviewers {i}",
                    "expect": ["approval"],
                    "tags": [],
                }
            )
    from agent_brain.native_sync.recall_eval import parse_gold

    return parse_gold(cases)


def test_autotune_waits_for_enough_feedback(tmp_path):
    hub, token = topic_hub(tmp_path, None)
    tenant = hub.identity(token)["tenant"]
    hub._gate_source = None
    report = autotune.run(hub, tenant, cases=_gold(), labels=_labels(3, 3), token=token)
    assert report["status"] in ("insufficient_feedback", "no_learned_gate")


def test_autotune_promotes_only_through_both_checks_and_can_roll_back(tmp_path, monkeypatch):
    hub, token = topic_hub(tmp_path, None)
    tenant = hub.identity(token)["tenant"]
    monkeypatch.setattr(TopicBackend, "hook_gate", sim_only(threshold=0.5), raising=False)
    hub._gate, hub._gate_error = None, None
    base = hub.gate_model(tenant)
    assert base is not None and base.version == "learned-v1"
    dry = autotune.run(hub, tenant, cases=_gold(), labels=_labels(20, 20), token=token, apply=False)
    assert dry["status"] == "would_promote" and hub.gate_model(tenant).version == "learned-v1"
    report = autotune.run(
        hub, tenant, cases=_gold(), labels=_labels(20, 20), token=token, apply=True
    )
    assert report["status"] == "promoted", report
    assert all(check["ok"] for check in report["gold_check"].values())
    held = report["held_out_use"]
    assert held["labels"] == 10 and held["candidate"]["auc"] >= held["live"]["auc"]
    tuned = hub.gate_model(tenant)
    assert tuned.version.startswith("learned-v1-fb") and tuned.calibration["labels"]["used"] == 20
    assert hub.rollback_gate(tenant) == "manifest"
    assert hub.gate_model(tenant).version == "learned-v1"


def test_autotune_refuses_feedback_that_predicts_use_worse(tmp_path, monkeypatch):
    hub, token = topic_hub(tmp_path, None)
    tenant = hub.identity(token)["tenant"]
    monkeypatch.setattr(TopicBackend, "hook_gate", sim_only(threshold=0.5), raising=False)
    hub._gate, hub._gate_error = None, None
    # Old labels say one thing, the newest the opposite: what was learned from
    # the old ones ranks the new ones worse than the live gate does.
    labels = _labels(15, 15, good=False)[:30]
    for row in labels:
        row["created"] = row["created"] - 1000
    labels += _labels(5, 5, good=True)
    report = autotune.run(hub, tenant, cases=_gold(), labels=labels, token=token, apply=True)
    assert report["status"] == "rejected_worse_on_use", report
    assert hub.gate_model(tenant).version == "learned-v1"


def test_promoted_gate_resolution_rules(tmp_path, monkeypatch):
    hub, token = topic_hub(tmp_path, None)
    tenant = hub.identity(token)["tenant"]
    monkeypatch.setattr(TopicBackend, "hook_gate", sim_only(threshold=0.5), raising=False)
    hub._gate, hub._gate_error = None, None
    tuned = GateModel.from_dict({**sim_only(threshold=0.4), "version": "learned-v1-fb20261111"})
    hub.activate_gate(tenant, tuned, embedding=TopicBackend.model_id, report={})
    assert hub.gate_model(tenant).version == "learned-v1-fb20261111"
    assert hub.gate_model().version == "learned-v1"  # health and other tenants
    # Another embedding model: the tuned similarities mean nothing there.
    hub.activate_gate(tenant, tuned, embedding="other-model", report={})
    assert hub.gate_model(tenant).version == "learned-v1"
    # An operator pin always wins.
    hub.activate_gate(tenant, tuned, embedding=TopicBackend.model_id, report={})
    pinned = tmp_path / "pin.json"
    pinned.write_text(json.dumps({**sim_only(), "version": "learned-pinned"}))
    monkeypatch.setenv("AMH_NATIVE_HOOK_GATE", str(pinned))
    hub._gate, hub._gate_error = None, None
    assert hub.gate_model(tenant).version == "learned-pinned"


def test_gold_upload_status_and_capability_over_http(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    token = hub.provision("one", "device")["token"]
    api = TestClient(create_app(hub))
    headers = {"Authorization": "Bearer " + token}
    cases = [{"id": "q1", "query": "审批要几个人", "expect": ["审批"], "tags": ["short"]}]
    stored = api.post("/native/v1/recall/gold", json={"cases": cases}, headers=headers)
    assert stored.status_code == 200 and stored.json() == {"stored": 1, "total": 1}
    bad = api.post("/native/v1/recall/gold", json={"cases": [{"id": "x"}]}, headers=headers)
    assert bad.status_code == 422
    state = api.get("/native/v1/recall/autotune", headers=headers).json()
    assert state["gold_cases"] == 1 and state["labels_7d"] == {
        "consulted": 0,
        "unused": 0,
        "used": 0,
    }
    assert state["last_run"] is None
    started = api.post("/native/v1/recall/autotune", headers=headers).json()
    assert started == {"started": False, "reason": "semantic_not_ready"}
    assert "recall-usage-feedback-v1" in api.get("/native/v1/health").json()["capabilities"]


def test_run_and_remember_records_every_outcome(tmp_path):
    hub, token = topic_hub(tmp_path, sim_only(threshold=0.5))
    tenant = hub.identity(token)["tenant"]
    report = autotune.run_and_remember(hub, tenant)
    assert report["status"] == "insufficient_gold"
    with database(hub.path) as db:
        assert autotune.last_report(db, tenant)["status"] == "insufficient_gold"
        assert autotune.due(hub, interval=7 * 86400) == []  # no gold yet: nothing due


def test_a_new_prompt_drops_an_unjudged_turn(live, tmp_path):
    hub, url = live
    c = NativeClient.adopt(tmp_path / "state", url, hub.provision("Reader", "r"), "r")
    _seed_pending(c, assets=[("rel", MEMORY)])
    hooks.run(
        c.root,
        "claude-code",
        {"session_id": "s1", "hook_event_name": "UserPromptSubmit", "prompt": "好的"},
    )
    with database(c.path) as db:
        assert db.execute("SELECT count(*) FROM usage_pending").fetchone()[0] == 0
