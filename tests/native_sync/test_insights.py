"""Insight rollups: funnel counts, reuse across members, frozen history and the admin surface."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from agent_brain.native_sync import insights, recall_ledger
from agent_brain.native_sync.api import create_app
from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.storage import database
from test_admin_login import ADMIN, SESSION_COOKIE, scene, sign_in  # noqa: F401
from test_hub import asset

DATA = "/native/v1/admin/insights/data"
LEDGER = "/native/v1/admin/recalls/data"
NOTHING = "xyzzy plugh"
# Both memories mention 订单/验签; only "a" also carries 签名/密钥, so it ranks first.
FAVOURS_A = "订单 验签 签名 密钥"


@pytest.fixture
def ledger(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    author = hub.provision("作者", "mac-author")
    reader = hub.provision("读者", "mac-reader")
    hub.put(
        author["token"],
        asset("a", body="订单回调验签失败检查签名密钥版本 BODY_CANARY", title="订单验签"),
    )
    hub.put(author["token"], asset("b", body="订单回调验签失败另一条说明", title="验签补充"))
    return hub, author, reader


def deliver(hub, who, trace_id, asset_id, *events):
    return hub.recall_feedback(
        who["token"],
        trace_id,
        [{"asset_id": asset_id, "revision": 1, "event": event} for event in events],
    )


def test_rollup_counts_the_funnel_and_reuse_across_members(ledger):
    hub, author, reader = ledger
    hit = hub.recall(reader["token"], FAVOURS_A, limit=1)  # a selected, b dropped:limit
    assert [item["asset_id"] for item in hit["items"]] == ["a"]
    assert (
        deliver(hub, reader, hit["trace_id"], "a", "packed", "emitted", "confirmed")["applied"] == 3
    )
    own = hub.recall(author["token"], FAVOURS_A, limit=2)  # the author reading their own memory
    assert deliver(hub, author, own["trace_id"], "a", "packed", "emitted")["applied"] == 2
    assert hub.recall(reader["token"], NOTHING, limit=2)["items"] == []
    token = hub.admin_create("ops")

    data = hub.admin_insights(token, days=7)

    assert "BODY_CANARY" not in json.dumps(data, ensure_ascii=False)
    totals = data["totals"]
    assert (totals["traces"], totals["no_match"], totals["members_active"]) == (3, 1, 2)
    assert (totals["emitted"], totals["confirmed"], totals["cited"]) == (2, 1, 0)
    assert (totals["assets_hit"], totals["assets_used"], totals["cross_member_emitted"]) == (
        2,
        1,
        1,
    )
    assert [(f["stage"], f["count"]) for f in data["funnel"]] == [
        ("candidate", 4),
        ("selected", 3),
        ("packed", 2),
        ("emitted", 2),
        ("confirmed", 1),
        ("cited", 0),
    ]
    top = data["top_assets"][0]
    assert top["asset_id"] == "a" and top["title"] == "订单验签" and top["producer"] == "作者"
    assert (top["members_emitted"], top["members_selected"], top["cross_member"]) == (2, 2, 1)
    assert data["top_assets"][1]["asset_id"] == "b" and data["top_assets"][1]["emitted"] == 0
    [producer] = data["by_producer"]
    assert producer["name"] == "作者"
    assert (producer["assets_hit"], producer["assets_used"], producer["consumers"]) == (2, 1, 1)
    assert [(m["name"], m["traces"], m["no_match"]) for m in data["by_member"]] == [
        ("读者", 2, 1),
        ("作者", 1, 0),
    ]
    assert [(c["channel"], c["traces"]) for c in data["by_channel"]] == [("hook", 3)]
    assert data["by_day"][-1]["traces"] == 3 and len(data["by_day"]) == 7
    assert data["collection_since"] == insights.day_key(time.time())
    assert data["dropped"] == [{"name": "limit", "count": 1}]
    gaps = data["gaps"]
    assert gaps["no_match_traces"] == 1 and gaps["repeated"] == []
    assert gaps["recent"][0]["preview"] == NOTHING and gaps["recent"][0]["member"] == "读者"
    assert data["rollup"] == {"frozen_days": 0, "live_days": 1}
    assert data["preview_enabled"] and data["window_days"] == 7


def test_gaps_follow_the_query_preview_setting_and_group_repeats(ledger):
    hub, author, reader = ledger
    for _ in range(2):
        hub.recall(reader["token"], NOTHING, limit=2)
    token = hub.admin_create("ops")
    data = hub.admin_insights(token, days=1)
    assert data["gaps"]["repeated"] == [
        {
            "query_digest": data["gaps"]["recent"][0]["query_digest"],
            "count": 2,
            "last": data["gaps"]["recent"][0]["created"],
            "preview": NOTHING,
        }
    ]
    hub.admin_setting(token, "recall_query_preview", "off")
    data = hub.admin_insights(token, days=1)
    assert not data["preview_enabled"]
    assert (
        data["gaps"]["recent"][0]["preview"] == "" and data["gaps"]["repeated"][0]["preview"] == ""
    )
    assert len(data["gaps"]["recent"][0]["query_digest"]) == 16
    assert NOTHING not in json.dumps(data, ensure_ascii=False)


def test_expired_days_are_frozen_before_their_traces_are_purged(ledger):
    hub, author, reader = ledger
    live = hub.recall(reader["token"], FAVOURS_A, limit=1)
    assert [item["asset_id"] for item in live["items"]] == ["a"]
    with database(hub.path) as db:
        payload = json.loads(
            db.execute(
                "SELECT payload FROM recall_events WHERE trace_id=?", (live["trace_id"],)
            ).fetchone()["payload"]
        )
    now = time.time()
    old = dict(payload, trace_id="a" * 24, created=now - 40 * 86400)
    # Expired by a minute, but its calendar day is not over yet: kept until the whole day is.
    partial = dict(payload, trace_id="b" * 24, created=now - recall_ledger.TRACE_TTL - 60)
    with database(hub.path) as db:
        recall_ledger.record_trace(db, hub.tenant, old)
        recall_ledger.record_trace(db, hub.tenant, partial)
    assert deliver(hub, reader, "b" * 24, "a", "packed", "emitted")["applied"] == 2

    hub.recall(reader["token"], FAVOURS_A, limit=1)  # recording a trace runs expiry

    old_day = insights.day_key(old["created"])
    with database(hub.path) as db:
        ids = {row["trace_id"] for row in db.execute("SELECT trace_id FROM recall_events")}
        frozen = db.execute(
            "SELECT member, channel, payload FROM recall_daily WHERE day=? AND frozen=1", (old_day,)
        ).fetchall()
        asset_rows = db.execute(
            "SELECT asset_id, frozen FROM asset_daily WHERE day=?", (old_day,)
        ).fetchall()
    assert "a" * 24 not in ids and "b" * 24 in ids
    assert [(row["member"], row["channel"]) for row in frozen] == [(reader["member_id"], "hook")]
    assert json.loads(frozen[0]["payload"])["funnel"]["selected"] == 1
    assert {row["asset_id"]: row["frozen"] for row in asset_rows} == {"a": 1, "b": 1}

    token = hub.admin_create("ops")
    data = hub.admin_insights(token, days=60)
    assert data["totals"]["traces"] == 4 and data["totals"]["emitted"] == 1
    assert data["rollup"]["frozen_days"] == 1 and data["rollup"]["live_days"] >= 1
    assert data["collection_since"] == old_day
    # A frozen day is never recomputed, even when a refresh is forced.
    hub.refresh_insights()
    with database(hub.path) as db:
        again = db.execute(
            "SELECT payload, frozen FROM recall_daily WHERE day=?", (old_day,)
        ).fetchall()
    assert [(row["payload"], row["frozen"]) for row in again] == [(frozen[0]["payload"], 1)]


def test_rollup_refreshes_on_demand_only_when_stale(ledger):
    hub, author, reader = ledger
    token = hub.admin_create("ops")
    hit = hub.recall(reader["token"], FAVOURS_A, limit=1)
    assert hub.admin_insights(token, days=1)["totals"]["emitted"] == 0
    assert deliver(hub, reader, hit["trace_id"], "a", "packed", "emitted")["applied"] == 2
    assert hub.admin_insights(token, days=1)["totals"]["emitted"] == 0  # cached rollup
    hub._insights_refreshed.clear()
    assert hub.admin_insights(token, days=1)["totals"]["emitted"] == 1
    assert hub.refresh_insights() == [hub.tenant]


def test_summarize_keeps_post_selection_drops_in_the_funnel_and_caps_members():
    trace = {
        "member": "m1",
        "channel": "hook",
        "created": 1.0,
        "query_chars": 5,
        "retrieval": {"mode": "lexical", "reasons": ["semantic_unavailable"]},
        "candidates": [
            {"asset_id": "a", "state": "dropped:budget", "producer": {"member_id": "p"}},
            {"asset_id": "b", "state": "dropped:dedup"},
            {"asset_id": "c", "state": "weird"},
            {"state": "selected"},
        ],
    }
    recall, assets = insights.summarize([trace])
    row = recall[("m1", "hook")]
    assert row["funnel"] == {
        "candidate": 4,
        "selected": 2,
        "packed": 0,
        "emitted": 0,
        "confirmed": 0,
        "cited": 0,
    }
    assert row["dropped"] == {"budget": 1, "dedup": 1}
    assert (row["degraded"], row["no_match"], row["candidates"]) == (1, 0, 4)
    assert assets["a"]["members"] == {"m1": 1} and assets["a"]["cross_member"] == 0
    assert set(assets) == {"a", "b", "c"}

    crowd = [
        {
            "member": f"m{n}",
            "channel": "tool",
            "created": float(n),
            "candidates": [{"asset_id": "a", "state": "emitted", "producer": {"member_id": "p"}}],
        }
        for n in range(insights.MEMBERS_PER_ASSET_DAY + 5)
    ]
    _, assets = insights.summarize(crowd)
    assert len(assets["a"]["members"]) == insights.MEMBERS_PER_ASSET_DAY
    assert assets["a"]["cross_member"] == insights.MEMBERS_PER_ASSET_DAY + 5


def test_insights_surface_requires_an_operator_and_leaks_nothing(ledger):
    hub, author, reader = ledger
    client = TestClient(create_app(hub))
    token = hub.admin_create("ops")
    headers = {"Authorization": "Bearer " + token}
    page = client.get("/native/v1/admin/insights")
    assert page.status_code == 200 and token not in page.text
    assert (
        "不可观测" in page.text and "sessionStorage" in page.text and "target=insights" in page.text
    )
    assert client.get(DATA).status_code == 401
    assert client.get(DATA, headers={"Authorization": "Bearer forged"}).status_code == 401
    assert client.get(DATA + "?days=0", headers=headers).status_code == 422
    device = {"Authorization": "Bearer " + reader["token"]}
    assert client.get(DATA, headers=device).status_code == 401
    response = client.get(DATA + "?days=7", headers=headers)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["observability"]["rows"][0]["stages"]["receipt"] == "observed"
    assert body["observability"]["rows"][2]["stages"]["cite"] == "unobservable"
    assert body["window_days"] == 7 and body["trace_ttl_days"] == 30
    assert "insights-v1" in client.get("/native/v1/health").json()["capabilities"]


def test_insights_accept_the_feishu_session(scene):  # noqa: F811
    hub, auth, identity, device, client = scene
    assert client.get(DATA).status_code == 401
    response = sign_in(client, target="insights")
    assert response.headers["location"] == ADMIN + "/insights"
    assert client.cookies.get(SESSION_COOKIE)
    assert client.get(DATA).status_code == 200
    assert sign_in(client, target="recalls").headers["location"] == ADMIN + "/recalls"
    auth.set_role(device["member_id"], enabled=False)
    assert client.get(DATA).status_code in (401, 403)


def test_recall_ledger_page_filters_by_member_channel_and_day(ledger):
    hub, author, reader = ledger
    hub.recall(reader["token"], FAVOURS_A, limit=1)
    hub.retrieve(author["token"], FAVOURS_A, channel="cli")
    client = TestClient(create_app(hub))
    headers = {"Authorization": "Bearer " + hub.admin_create("ops")}

    def traces(query=""):
        response = client.get(LEDGER + query, headers=headers)
        assert response.status_code == 200, query
        return response.json()

    everything = traces()
    assert len(everything["traces"]) == 2
    assert {m["name"] for m in everything["members"]} == {"作者", "读者"}
    assert [t["member"] for t in traces("?member=" + reader["member_id"])["traces"]] == [
        reader["member_id"]
    ]
    assert [t["channel"] for t in traces("?channel=cli")["traces"]] == ["cli"]
    assert len(traces("?day=" + insights.day_key(time.time()))["traces"]) == 2
    assert traces("?day=2020-01-01")["traces"] == []
    for bad in ("?day=2026-02-30", "?day=yesterday", "?channel=smoke", "?member=a%20b"):
        assert client.get(LEDGER + bad, headers=headers).status_code == 422, bad
    page = client.get("/native/v1/admin/recalls").text
    assert "target=recalls" in page and 'id="member"' in page and 'id="day"' in page
