"""Decay, declared retirement, Hopfield association, traces and feedback on a real Hub."""

import json
import math
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from agent_brain.native_sync import facets
from agent_brain.native_sync.api import create_app
from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.storage import database
from agent_brain.retrieval.hopfield import hopfield_expand
from test_hub import asset
from test_hybrid_retrieval import SyntheticBackend

DISABLED = "未启用"


def make(tmp_path, **options):
    hub = Hub(tmp_path / "hub.sqlite", **options)
    who = hub.provision(
        "one", "device"
    )  # The Hub's default tenant, as the operator pages scope to it.
    return hub, who


def declared(**block):
    return {"declared": {k: str(v) for k, v in block.items()}}


def days_ago(days):
    """A calendar date relative to today: retrieve() uses the real clock, so the
    fixtures must age with it rather than drift past a half-life on some future date."""
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")


def test_fresher_declared_memory_outranks_stale_state_on_equal_lexical_score(tmp_path):
    hub, who = make(tmp_path)
    # Equal term statistics and lengths give equal BM25 scores; distinct bodies avoid dedup.
    body = "灰度发布审批需要两人复核 "
    hub.put(
        who["token"],
        asset(
            "stale",
            body=body + "alpha",
            source_meta=declared(type="project", modified=days_ago(300)),
        ),
    )
    hub.put(
        who["token"],
        asset(
            "fresh",
            body=body + "bravo",
            source_meta=declared(type="feedback", modified=days_ago(2)),
        ),
    )
    hub.put(who["token"], asset("undated", body=body + "delta"))
    result = hub.retrieve(who["token"], "审批复核")
    order = [i["asset_id"] for i in result["items"]]
    # RRF ranks separate neighbouring candidates by well under 5%, so the
    # bounded decay weight swaps a stale state below a fresh rule but cannot
    # lift a lower-ranked candidate two places. Re-ranking, never reordering.
    assert order.index("fresh") < order.index("stale")
    weights = {i["asset_id"]: i["recall"]["decay_weight"] for i in result["items"]}
    assert weights["fresh"] > weights["undated"] > weights["stale"]
    assert weights["undated"] == pytest.approx(1 + 0.05 * math.tanh(math.log(0.5)), abs=1e-5)
    assert all(0.95 <= w <= 1.05 for w in weights.values())
    assert result["retrieval"]["stages"]["decay"] == "on"
    trace = result["trace"]
    by_id = {c["asset_id"]: c for c in trace["candidates"]}
    assert by_id["undated"]["decay"]["reason"] == "time_unknown_neutral"
    assert by_id["undated"]["times"]["content_modified"] is None
    assert by_id["stale"]["decay"]["reference"]["basis"] == "content_modified"
    assert by_id["stale"]["nature"] == {"value": "project_state", "provenance": "declared"}
    assert [s["stage"] for s in by_id["fresh"]["stages"]] == [
        "rrf_fusion",
        "supporting_weight",
        "declared_filter",
        "decay",
    ]


def test_decay_can_be_disabled_and_is_reported_as_not_enabled(tmp_path):
    hub, who = make(tmp_path, decay=False)
    hub.put(
        who["token"], asset("a", body="审批复核", source_meta=declared(modified=days_ago(2000)))
    )
    result = hub.retrieve(who["token"], "审批复核")
    assert result["retrieval"]["stages"]["decay"] == DISABLED
    assert result["items"][0]["recall"]["decay_weight"] is None
    assert "decay" not in result["trace"]["candidates"][0]


def test_declared_supersession_and_expiry_drop_candidates_except_exact_id(tmp_path):
    hub, who = make(tmp_path)
    hub.put(
        who["token"],
        asset("old-rule", body="审批复核旧规则", source_meta=declared(superseded_by="new-rule")),
    )
    hub.put(who["token"], asset("new-rule", body="审批复核新规则"))
    hub.put(
        who["token"],
        asset("expired", body="审批复核临时通知", source_meta=declared(expires=days_ago(30))),
    )
    result = hub.retrieve(who["token"], "审批复核")
    assert [i["asset_id"] for i in result["items"]] == ["new-rule"]
    states = {c["asset_id"]: c for c in result["trace"]["candidates"]}
    assert states["old-rule"]["state"] == "dropped:superseded"
    assert states["old-rule"]["state_detail"]["by"] == "new-rule"
    assert states["expired"]["state"] == "dropped:expired"
    assert states["new-rule"]["state"] == "selected" and states["new-rule"]["final_rank"] == 1
    exact = hub.retrieve(who["token"], "old-rule")
    assert [i["asset_id"] for i in exact["items"]] == ["old-rule"]


def test_hopfield_expand_pulls_in_unseen_neighbours_only():
    seeds = [("seed", 1.0, [1.0, 0.0, 0.0])]
    patterns = {"seed": [1, 0, 0], "near": [0.9, 0.1, 0], "far": [0, 0, 1], "zero": [0, 0, 0]}
    attractor, found = hopfield_expand(seeds, patterns, beta=8.0, min_similarity=0.5, limit=5)
    assert [key for key, _ in found] == ["near"]
    assert math.isclose(sum(v * v for v in attractor), 1.0, abs_tol=1e-9)
    assert hopfield_expand([], patterns) == ([], [])
    assert hopfield_expand(seeds, {}) == ([], [])


class DirectionalBackend(SyntheticBackend):
    """Query and hit share a direction; the neighbour is close to the hit, not the query."""

    def embed(self, text):
        self.seen.append(text)
        if "sign-off" in text:
            return [1.0, 0.0, 0.0] if text.strip() == "sign-off" else [0.8, 0.6, 0.0]
        if "复核" in text:
            return [0.2, 0.98, 0.0]
        return [0.0, 0.0, 1.0]


def test_hopfield_stage_adds_semantic_neighbours_the_query_missed(tmp_path):
    backend = DirectionalBackend()
    hub = Hub(tmp_path / "hub.sqlite", semantic_backend=backend, hopfield=True)
    who = hub.provision("one", "device")
    hub.put(who["token"], asset("hit", title="", body="approval requires sign-off"))
    # cos(query, neighbour) = 0.2 < 0.45 floor, but cos(hit, neighbour) = 0.75.
    hub.put(who["token"], asset("neighbour", title="", body="两人复核流程"))
    hub.put(who["token"], asset("unrelated", title="", body="secret"))
    hub.rebuild_semantic(who["token"])
    result = hub.retrieve(who["token"], "sign-off")
    ids = [i["asset_id"] for i in result["items"]]
    assert ids == ["hit", "neighbour"]
    assert result["retrieval"]["stages"]["hopfield"] == "on"
    assert result["retrieval"]["candidates"] == {"lexical": 1, "semantic": 1, "hopfield": 1}
    neighbour = result["items"][1]
    assert neighbour["retrieval_ranks"] == {"lexical": None, "semantic": None, "hopfield": 1}
    assert neighbour["recall"]["routes"] == {
        "lexical": None,
        "semantic": None,
        "hopfield": 1,
        "rerank": DISABLED,
    }
    assert 0.45 <= neighbour["hopfield_match"]["similarity"] < 1
    assert neighbour["score"] < result["items"][0]["score"]
    stage = next(
        s for s in result["trace"]["candidates"][1]["stages"] if s["stage"] == "hopfield_expand"
    )
    assert (
        stage["before_rank"] is None
        and stage["after_rank"] == 2
        and stage["effect"].startswith("hopfield_added")
    )
    hub.hopfield = False
    off = hub.retrieve(who["token"], "sign-off")
    assert [i["asset_id"] for i in off["items"]] == ["hit"]
    assert off["retrieval"]["stages"]["hopfield"] == DISABLED
    assert off["items"][0]["recall"]["routes"]["hopfield"] == DISABLED
    lexical_only = Hub(tmp_path / "hub.sqlite", hopfield=True)
    plain = lexical_only.retrieve(who["token"], "sign-off")
    assert plain["retrieval"]["stages"]["semantic"] == DISABLED
    assert plain["retrieval"]["stages"]["hopfield"] == DISABLED
    assert plain["items"][0]["recall"]["routes"] == {
        "lexical": 1,
        "semantic": DISABLED,
        "hopfield": DISABLED,
        "rerank": DISABLED,
    }


def test_trace_is_recorded_without_bodies_and_feedback_moves_states_and_signals(tmp_path):
    hub, who = make(tmp_path)
    canary = "BODY_CANARY_不得入账"
    hub.put(who["token"], asset("a", body="审批复核 " + canary, title="审批规则"))
    hub.put(who["token"], asset("b", body="审批复核另一条"))
    result = hub.retrieve(who["token"], "审批复核", limit=1)
    trace_id = result["retrieval"]["trace_id"]
    with database(hub.path) as db:
        row = db.execute("SELECT * FROM recall_events WHERE trace_id=?", (trace_id,)).fetchone()
    assert canary not in row["payload"] and row["query_preview"] == "审批复核"
    stored = json.loads(row["payload"])
    assert {c["asset_id"]: c["state"] for c in stored["candidates"]} == {
        "a": "selected",
        "b": "dropped:limit",
    }
    assert stored["candidates"][0]["title"] == "审批规则"
    assert hub.recall_feedback(
        who["token"],
        trace_id,
        [
            {"asset_id": "a", "revision": 1, "event": "packed"},
            {"asset_id": "a", "revision": 1, "event": "emitted"},
            {"asset_id": "a", "revision": 1, "event": "confirmed"},
            {"asset_id": "a", "revision": 1, "event": "cite"},
        ],
    )["states"] == {"a": "cited"}
    listed = hub.recalls(who["token"])["traces"]
    assert listed[0]["trace_id"] == trace_id and listed[0]["counts"]["cited"] == 1
    full = hub.recalls(who["token"], trace_id=trace_id)["traces"][0]
    assert [e["event"] for e in full["candidates"][0]["events"]] == [
        "packed",
        "emitted",
        "confirmed",
        "cite",
    ]
    with database(hub.path) as db:
        signals = dict(db.execute("SELECT * FROM recall_signals WHERE asset_id='a'").fetchone())
    assert (signals["delivered"], signals["confirmed"], signals["cited"], signals["support"]) == (
        1,
        1,
        1,
        1,
    )
    assert signals["gain"] == pytest.approx(0.1) and signals["last_cited"] > 0
    # Feedback now participates in ranking and the trace shows it.
    again = hub.retrieve(who["token"], "审批复核")
    candidate = next(c for c in again["trace"]["candidates"] if c["asset_id"] == "a")
    assert candidate["signals"]["cited"] == 1
    assert candidate["decay"]["multipliers"]["access"] > 1
    assert candidate["times"]["last_cited"]["source"] == "server"
    for bad in (
        [{"asset_id": "zzz", "revision": 1, "event": "cite"}],
        [{"asset_id": "a", "revision": 9, "event": "cite"}],
        [{"asset_id": "a", "revision": 1, "event": "fly"}],
    ):
        with pytest.raises(ValueError):
            hub.recall_feedback(who["token"], trace_id, bad)
    with pytest.raises(ValueError):
        hub.recall_feedback(
            who["token"], "0" * 24, [{"asset_id": "a", "revision": 1, "event": "cite"}]
        )
    other = hub.provision("two", "other-device")
    with pytest.raises(ValueError, match="recalling device"):
        hub.recall_feedback(
            other["token"], trace_id, [{"asset_id": "a", "revision": 1, "event": "emitted"}]
        )
    assert (
        hub.recall_feedback(
            other["token"], trace_id, [{"asset_id": "a", "revision": 1, "event": "support"}]
        )["applied"]
        == 1
    )
    assert hub.recalls(other["token"])["traces"] == []  # Another device sees only its own traces.
    foreign = hub.provision("x", "d", tenant="elsewhere")
    with pytest.raises(ValueError, match="unknown recall trace"):
        hub.recall_feedback(
            foreign["token"], trace_id, [{"asset_id": "a", "revision": 1, "event": "support"}]
        )


def test_query_preview_setting_and_unrecorded_trace_do_not_break_retrieval(tmp_path):
    hub, who = make(tmp_path)
    hub.put(who["token"], asset("a", body="审批复核"))
    admin = hub.admin_create("ops")
    hub.admin_setting(admin, "recall_query_preview", "off")
    result = hub.retrieve(who["token"], "审批复核 private prompt")
    assert result["trace"]["query_preview"] == "" and len(result["trace"]["query_digest"]) == 64
    unrecorded = hub.retrieve(who["token"], "审批复核", record=False)
    assert unrecorded["items"] and len(hub.recalls(who["token"])["traces"]) == 1


def test_http_recall_feedback_listing_and_admin_page(tmp_path):
    hub, who = make(tmp_path)
    hub.put(who["token"], asset("a", body="审批复核 FULL_BODY", title="审批"))
    hub.put(
        who["token"],
        asset("chat", kind="transcript", body="审批复核 审批复核 审批复核 transcript noise"),
    )
    api = TestClient(create_app(hub))
    headers = {"Authorization": "Bearer " + who["token"]}
    response = api.post("/native/v1/recall", json={"query": "审批复核"}, headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert [i["asset_id"] for i in data["items"]] == [
        "a"
    ]  # transcript noise is out of the hook channel
    assert data["items"][0]["body"] == "审批复核 FULL_BODY"
    assert (
        data["retrieval"]["trace_id"] == data["trace_id"]
        and data["retrieval"]["stages"]["decay"] == "on"
    )
    trace_id = data["trace_id"]
    feedback = api.post(
        "/native/v1/recall/feedback",
        json={
            "trace_id": trace_id,
            "events": [{"asset_id": "a", "revision": 1, "event": "emitted"}],
        },
        headers=headers,
    )
    assert feedback.status_code == 200 and feedback.json()["states"] == {"a": "emitted"}
    assert (
        api.post(
            "/native/v1/recall/feedback", json={"trace_id": "nope", "events": []}, headers=headers
        ).status_code
        == 422
    )
    assert (
        api.post(
            "/native/v1/recall/feedback",
            json={
                "trace_id": "0" * 24,
                "events": [{"asset_id": "a", "revision": 1, "event": "cite"}],
            },
            headers=headers,
        ).status_code
        == 409
    )
    listed = api.get("/native/v1/recalls", headers=headers).json()["traces"]
    assert listed[0]["trace_id"] == trace_id and listed[0]["channel"] == "hook"
    assert "FULL_BODY" not in json.dumps(listed, ensure_ascii=False)
    full = api.get("/native/v1/recalls?trace_id=" + trace_id, headers=headers).json()["traces"][0]
    assert full["candidates"][0]["state"] == "emitted"
    retrieve = api.post("/native/v1/retrieve", json={"query": "审批复核"}, headers=headers).json()
    assert (
        retrieve["retrieval"]["trace_id"]
        and retrieve["retrieval"]["stages"]["hopfield"] == DISABLED
    )
    assert retrieve["items"][0]["recall"]["routes"]["lexical"] == 1
    assert retrieve["items"][0]["recall"]["state"] == "selected"
    operator = hub.admin_create("ops")
    page = api.get("/native/v1/admin/recalls")
    assert page.status_code == 200 and "召回明细" in page.text and operator not in page.text
    assert api.get("/native/v1/admin/recalls/data").status_code == 401
    assert api.get("/native/v1/admin/recalls/data", headers=headers).status_code == 401
    admin_view = api.get(
        "/native/v1/admin/recalls/data", headers={"Authorization": "Bearer " + operator}
    )
    assert admin_view.status_code == 200
    assert len(admin_view.json()["traces"]) == 2 and "FULL_BODY" not in admin_view.text


def test_mcp_bridge_passes_trace_through_and_leaves_row_diagnostics_out(tmp_path):
    from agent_brain.native_sync.org_tools import OrganizationTools
    from test_org_mcp import TOKEN

    hub, who = make(tmp_path)
    hub.put(who["token"], asset("a", body="审批复核"))
    import threading
    import uvicorn
    import socket
    import time

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    server = uvicorn.Server(uvicorn.Config(create_app(hub), log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.01)
    try:
        state = tmp_path / "profile"
        state.mkdir()
        (state / "config.json").write_text(
            json.dumps(
                {
                    "hub": f"http://127.0.0.1:{sock.getsockname()[1]}",
                    "identity": {
                        "tenant_id": "one",
                        "member_id": who["member_id"],
                        "token": who["token"],
                    },
                }
            )
        )
        bridge = OrganizationTools(state)
        result = bridge.search("审批复核")
        assert result["ok"], result
        assert len(result["retrieval"]["trace_id"]) == 24
        assert result["retrieval"]["stages"]["decay"] == "on"
        # Per-row diagnostics stay on the trace: they took room from candidates in the row.
        assert "recall" not in result["items"][0]
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()
