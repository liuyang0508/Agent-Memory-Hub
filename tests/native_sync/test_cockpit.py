"""The management cockpit: scorecard, exceptions, assets and people from real ledger data."""

import time

import pytest
from fastapi.testclient import TestClient

from agent_brain.native_sync import cockpit, dashboard
from agent_brain.native_sync.api import create_app
from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.storage import database
from test_hub import asset

URL = "/native/v1/admin/cockpit/data"
SECRET = "COCKPIT_BODY_CANARY"


@pytest.fixture
def org(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    author = hub.provision("作者", "a")
    reader = hub.provision("读者", "r")
    hub.provision("没用过的人", "s")
    hub.put(author["token"], asset("m1", title="审批规则", body=SECRET + " 审批需要两人复核"))
    hub.put(author["token"], asset("m2", title="发布规则", body="发布走灰度"))
    result = hub.recall(reader["token"], "审批需要几个人复核")
    trace = result["trace_id"]
    events = [
        {"asset_id": item["asset_id"], "revision": item["revision"], "event": event}
        for item in result["items"]
        for event in ("packed", "emitted", "used")
    ]
    hub.recall_feedback(reader["token"], trace, events)
    # Labels carry the gate's evidence; a Hub without a model records none, so seed one.
    with database(hub.path) as db:
        db.execute(
            "INSERT INTO recall_labels VALUES(?,?,?,?,?,?,?,?,?)",
            (
                hub.tenant,
                trace,
                "m1",
                1,
                time.time(),
                "used",
                reader["member_id"],
                "learned-v1",
                "[]",
            ),
        )
    return hub, hub.admin_create("ops"), author, reader


def test_needs_an_operator_and_never_carries_memory_text(org):
    hub, token, _, reader = org
    client = TestClient(create_app(hub))
    assert client.get(URL).status_code == 401
    assert (
        client.get(URL, headers={"Authorization": "Bearer " + reader["token"]}).status_code == 401
    )
    assert (
        client.get(URL + "?days=0", headers={"Authorization": "Bearer " + token}).status_code == 422
    )
    response = client.get(URL, headers={"Authorization": "Bearer " + token})
    assert response.status_code == 200
    assert SECRET not in response.text


def test_scorecard_has_four_perspectives_and_reads_the_ledger(org):
    hub, token, _, _ = org
    data = hub.admin_cockpit(token, days=30)
    assert [card["key"] for card in data["scorecard"]] == [
        "value",
        "adoption",
        "operations",
        "learning",
    ]
    indicators = {i["key"]: i for card in data["scorecard"] for i in card["indicators"]}
    assert indicators["members_active"]["value"] == 1
    assert indicators["penetration"]["value"] == pytest.approx(1 / 3)
    assert indicators["penetration"]["status"] == "critical"
    assert indicators["assets"]["value"] == 2
    assert data["north_star"]["basis"] == "used" and data["north_star"]["value"] == 1
    assert data["usage"]["used"] == 1
    assert indicators["usage_rate"]["value"] == 1.0
    # No earlier period: nothing to compare with, and it says so instead of showing zero.
    assert data["period"]["has_previous"] is False
    assert indicators["usage_rate"]["delta"] is None
    people = {row["name"]: row for row in data["people"]}
    assert people["作者"]["assets"] == 2 and people["作者"]["state"] == "contributor"
    assert people["读者"]["state"] == "active" and people["没用过的人"]["state"] == "silent"


def test_exceptions_lead_with_what_needs_a_decision(org):
    hub, token, _, _ = org
    data = hub.admin_cockpit(token, days=30)
    titles = [alert["title"] for alert in data["alerts"]]
    assert "语义检索没有运行" in titles  # this Hub has no model
    assert data["alerts"][0]["severity"] == "critical"
    assert "还有成员没用起来" in titles
    assert all(alert["action"] and alert["evidence"] for alert in data["alerts"])
    assert any("需要尽快处理" in line for line in data["summary"])


def test_an_empty_hub_says_so_rather_than_inventing_numbers(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    data = hub.admin_cockpit(hub.admin_create("ops"), days=7)
    assert "还没有召回记录" in data["summary"][0]
    assert data["north_star"]["basis"] == "emitted" and data["north_star"]["value"] == 0
    indicators = {i["key"]: i for card in data["scorecard"] for i in card["indicators"]}
    assert indicators["usage_rate"]["value"] is None
    assert indicators["usage_rate"]["status"] == "none"
    assert data["assets"]["pareto"]["assets"] == 0


def test_quadrants_follow_demand_and_use(tmp_path):
    now = time.time()
    live = [
        {
            "asset_id": key,
            "title": key,
            "path": key + ".md",
            "kind": "memory",
            "observed": now,
            "producer": "m",
        }
        for key in ("star", "rework", "potential", "dormant", "unjudged")
    ]

    def row(selected):
        return {"funnel": {"selected": selected, "emitted": selected}}

    window = {"star": row(9), "rework": row(9), "potential": row(1), "unjudged": row(3)}
    labels = []
    for key, used, unused in (("star", 3, 1), ("rework", 1, 4), ("potential", 2, 0)):
        for label, count in (("used", used), ("unused", unused)):
            labels += [{"created": now, "asset_id": key, "label": label, "member": "m"}] * count
    view = cockpit._assets_view(live, window, labels, now - 86400, now)
    groups = {cell["key"]: [t["title"] for t in cell["top"]] for cell in view["matrix"]}
    assert groups == {
        "star": ["star"],
        "potential": ["potential"],
        "rework": ["rework"],
        "dormant": ["dormant"],
    }
    assert view["unjudged"] == 1
    assert view["pareto"]["head"] == 1 and view["pareto"]["head_share"] == pytest.approx(9 / 22)


def test_retention_compares_consecutive_weeks(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    now = time.time()
    from agent_brain.native_sync import insights

    payload = '{"traces": 1}'
    with database(hub.path) as db:
        for member, offsets in (("a", (1, 8)), ("b", (8,)), ("c", (1,))):
            for offset in offsets:
                db.execute(
                    "INSERT INTO recall_daily VALUES(?,?,?,?,?,0)",
                    ("t", insights.day_key(now - offset * 86400), member, "hook", payload),
                )
        weekly = cockpit._weekly(db, "t", now, {"a": "甲", "b": "乙", "c": "丙"})
    assert weekly["active"][-2:] == [2, 2]
    assert weekly["retention"] == 0.5
    assert weekly["lost"] == ["乙"]


def test_generic_index_parts_read_as_names():
    assert cockpit.asset_title("x__p000006", "MEMORY", "a/MEMORY.md") == "MEMORY.md 第7段"
    assert cockpit.asset_title("y", "发布规则", "b.md") == "发布规则"


def test_page_is_self_contained_and_writes_untrusted_text_safely():
    page = dashboard.html("insights")
    assert "target=insights" in page and "sessionStorage" in page and "不可观测" in page
    assert "innerHTML" not in page  # titles and names are untrusted
    for external in ('src="http', 'href="http', "@import", "url(http"):
        assert external not in page
