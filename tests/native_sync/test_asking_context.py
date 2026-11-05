"""Asking-side context: where a recall came from, carried as hints and rolled up as such."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from agent_brain.native_sync import hooks, insights
from agent_brain.native_sync.api import create_app
from agent_brain.native_sync.client import NativeClient
from agent_brain.native_sync.common import digest
from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.install import org_mcp_server
from agent_brain.native_sync.org_tools import OrganizationTools
from agent_brain.native_sync.query_worker import fetch
from agent_brain.native_sync.storage import database
from agent_brain.native_sync.transport import HubHTTPError
from test_hub import asset
from test_client import DirectTransport
from test_org_mcp import hub as synthetic_hub  # noqa: F401
from test_runtime import hook, live as live  # noqa: F401

SESSION = "0123456789abcdef"
FAVOURS_A = "订单 验签 签名 密钥"


@pytest.fixture
def ledger(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    author = hub.provision("作者", "mac-author")
    reader = hub.provision("读者", "mac-reader")
    hub.put(author["token"], asset("a", body="订单回调验签失败检查签名密钥版本", title="订单验签"))
    hub.put(author["token"], asset("b", body="订单回调验签失败另一条说明", title="验签补充"))
    return hub, author, reader


def deliver(hub, who, trace_id, asset_id, *events):
    return hub.recall_feedback(
        who["token"],
        trace_id,
        [{"asset_id": asset_id, "revision": 1, "event": event} for event in events],
    )


def test_trace_keeps_bounded_hints_and_the_api_validates_them(ledger):
    hub, author, reader = ledger
    asking = {"product": "claude-code", "project": "orders", "session_digest": SESSION}

    def stored(who, trace_id):
        return hub.recalls(who["token"], trace_id=trace_id)["traces"][0]["asking"]

    blank = {"product": "", "project": "", "session_digest": ""}
    result = hub.recall(reader["token"], FAVOURS_A, limit=1, asking=asking)
    assert stored(reader, result["trace_id"]) == asking
    assert stored(reader, hub.recall(reader["token"], FAVOURS_A, limit=1)["trace_id"]) == blank
    # Direct callers may pass junk; it is bounded and never guessed.
    loose = hub.retrieve(
        reader["token"],
        FAVOURS_A,
        asking={"product": "x" * 100, "session_digest": "NOT-HEX", "extra": 1},
    )
    assert loose["trace"]["asking"] == {"product": "x" * 64, "project": "", "session_digest": ""}
    assert stored(reader, loose["retrieval"]["trace_id"]) == loose["trace"]["asking"]

    client = TestClient(create_app(hub))
    headers = {"Authorization": "Bearer " + reader["token"]}
    body = {"query": FAVOURS_A, "limit": 1, "asking": asking}
    ok = client.post("/native/v1/recall", json=body, headers=headers)
    assert ok.status_code == 200
    assert stored(reader, ok.json()["trace_id"]) == asking
    retrieve = client.post(
        "/native/v1/retrieve", json={"query": FAVOURS_A, "asking": asking}, headers=headers
    )
    assert retrieve.status_code == 200
    for bad in (
        {"session_digest": "NOT-HEX"},
        {"session_digest": "a" * 17},
        {"product": "x" * 65},
        {"unexpected": "field"},
    ):
        response = client.post(
            "/native/v1/recall", json={"query": "q", "asking": bad}, headers=headers
        )
        assert response.status_code == 422, bad
    assert "asking-context-v1" in client.get("/native/v1/health").json()["capabilities"]


def test_rollup_counts_products_projects_and_distinct_tasks(ledger):
    hub, author, reader = ledger
    first = hub.recall(
        reader["token"],
        FAVOURS_A,
        limit=1,
        asking={"product": "claude-code", "project": "orders", "session_digest": SESSION},
    )
    deliver(hub, reader, first["trace_id"], "a", "packed", "emitted")
    second = hub.recall(  # same task asks again: one task, two traces
        reader["token"],
        FAVOURS_A,
        limit=1,
        asking={"product": "claude-code", "project": "orders", "session_digest": SESSION},
    )
    deliver(hub, reader, second["trace_id"], "a", "packed", "emitted", "confirmed")
    third = hub.recall(  # an MCP research from another product, project unknown
        author["token"],
        FAVOURS_A,
        limit=1,
        asking={"product": "codex-cli", "session_digest": "f" * 16},
    )
    deliver(hub, author, third["trace_id"], "a", "packed", "emitted")
    hub.recall(reader["token"], "xyzzy plugh", limit=1)  # old client: no hints at all

    data = hub.admin_insights(hub.admin_create("ops"), days=7)

    assert data["totals"]["tasks_active"] == 2 and data["totals"]["traces"] == 4
    assert data["by_asking_product"] == [
        {
            "name": "claude-code",
            "traces": 2,
            "no_match": 0,
            "selected": 2,
            "emitted": 2,
            "confirmed": 1,
            "cited": 0,
        },
        {
            "name": "codex-cli",
            "traces": 1,
            "no_match": 0,
            "selected": 1,
            "emitted": 1,
            "confirmed": 0,
            "cited": 0,
        },
        {
            "name": "未知",
            "traces": 1,
            "no_match": 1,
            "selected": 0,
            "emitted": 0,
            "confirmed": 0,
            "cited": 0,
        },
    ]
    assert [(p["name"], p["traces"]) for p in data["by_asking_project"]] == [
        ("orders", 2),
        ("未知", 2),
    ]
    top = data["top_assets"][0]
    assert top["asset_id"] == "a" and top["tasks_emitted"] == 2 and top["members_emitted"] == 2
    assert data["by_producer"][0]["tasks"] == 2
    assert [(m["name"], m["tasks"]) for m in data["by_member"]] == [("读者", 1), ("作者", 1)]


def test_worker_retries_without_hints_on_a_hub_that_predates_them():
    calls = []

    class OldHub:
        def recall(self, query, *, limit=8, asking=None):
            calls.append(asking)
            if asking:
                raise HubHTTPError(422)
            return {"items": [{"asset_id": "a"}], "trace_id": "c" * 24}

        def search(self, query, *, limit=8):
            raise AssertionError("search is not the fallback for a validation error")

    asking = {"product": "claude-code", "project": "", "session_digest": SESSION}
    assert fetch(OldHub(), "q", asking=asking) == {
        "items": [{"asset_id": "a"}],
        "hints": [],
        "self_feedback_rate": 0.0,
        "trace_id": "c" * 24,
        "channel": "recall",
        "retrieval": {"mode": None, "reasons": []},
    }
    assert calls == [asking, None]

    class Missing:
        def recall(self, query, *, limit=8, asking=None):
            raise HubHTTPError(404)

        def search(self, query, *, limit=8):
            return [{"asset_id": "legacy"}]

    assert fetch(Missing(), "q", asking=asking)["channel"] == "search"

    class Strict:
        def recall(self, query, *, limit=8, asking=None):
            raise HubHTTPError(422)

    with pytest.raises(HubHTTPError):
        fetch(Strict(), "q")  # without hints a 422 is a real rejection


def test_hook_resolves_the_project_from_registered_sources_only(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    identity = hub.provision("李四", "Mac")
    client = NativeClient(
        tmp_path / "state", transport=DirectTransport(hub, identity), identity=identity
    )
    with database(client.path) as db:
        for sid, root, project in (
            ("s1", tmp_path / "work", "workspace"),
            ("s2", tmp_path / "work" / "orders", "orders"),
            ("s3", tmp_path / "bare", ""),
        ):
            db.execute(
                "INSERT INTO sources VALUES(?,?,?,?,?,?)",
                (sid, str(root), "claude-code", project, "memory", 1),
            )
    assert hooks._project_for(client, str(tmp_path / "work" / "orders" / "api")) == "orders"
    assert hooks._project_for(client, str(tmp_path / "work" / "billing")) == "workspace"
    assert hooks._project_for(client, str(tmp_path / "bare" / "x")) == ""
    assert hooks._project_for(client, str(tmp_path / "elsewhere")) == ""
    assert hooks._project_for(client, None) == "" and hooks._project_for(client, "") == ""


def test_hook_sends_product_project_and_a_session_digest(live, tmp_path):
    hub, url = live
    author = hub.provision("作者", "mac-author")
    hub.put(author["token"], asset("a", body="订单回调验签失败检查签名密钥版本", title="订单验签"))
    c = NativeClient.adopt(tmp_path / "state", url, hub.provision("李四", "Mac"), "Mac")
    with database(c.path) as db:
        db.execute(
            "INSERT INTO sources VALUES(?,?,?,?,?,?)",
            ("s1", str(tmp_path / "work"), "claude-code", "orders", "memory", 1),
        )
    hook(c.root, "UserPromptSubmit", prompt=FAVOURS_A, cwd=str(tmp_path / "work" / "deep"))
    trace = hub.recalls(c.config["identity"]["token"])["traces"][0]
    assert trace["asking"] == {
        "product": "claude-code",
        "project": "orders",
        "session_digest": digest("session1")[:16],
    }
    assert "session1" not in json.dumps(trace)


def test_mcp_search_reports_its_product_and_research_once_per_hub_generation(
    synthetic_hub,  # noqa: F811
):
    hub = synthetic_hub
    tools = OrganizationTools(hub.state, product="codex-cli")
    first = tools.search("question")
    assert first["ok"]
    sent = hub.calls[-1][1]["asking"]
    assert sent["product"] == "codex-cli" and len(sent["session_digest"]) == 16
    assert first["research_id"] not in json.dumps(sent)
    again = tools.search("question two", research_id=first["research_id"])
    assert again["ok"] and hub.calls[-1][1]["asking"] == sent

    original = hub.response
    hub.response = lambda path, payload: (
        (422, {"detail": [{"loc": ["body", "asking"], "msg": "extra", "type": "extra"}]})
        if "asking" in payload
        else original(path, payload)
    )
    older = OrganizationTools(hub.state, product="codex-cli")
    result = older.search("question")
    assert result["ok"] and "asking" not in hub.calls[-1][1]
    assert older._asking_rejected
    older.search("question three", research_id=result["research_id"])
    assert "asking" not in hub.calls[-1][1]


def test_installer_labels_each_mcp_entry_with_its_product(tmp_path):
    entry = org_mcp_server(tmp_path, product="claude-code")
    assert entry["args"][-2:] == ["--product", "claude-code"]
    assert "--product" not in org_mcp_server(tmp_path)["args"]
    plain = org_mcp_server(tmp_path, policy_path="p.json", product="cowork")
    assert plain["args"][-4:] == ["--policy", "p.json", "--product", "cowork"]


def test_old_hub_hint_retry_rechecks_profile_recall_switch(synthetic_hub):  # noqa: F811
    """A policy change while the first HTTP request runs also gates its fallback."""
    hub = synthetic_hub
    tools = OrganizationTools(hub.state, product="codex-cli")

    def disable_on_validation(path, payload):
        assert path == "retrieve" and "asking" in payload
        config = json.loads((hub.state / "config.json").read_text())
        config["recall_enabled"] = False
        (hub.state / "config.json").write_text(json.dumps(config))
        return 422, {"detail": [{"loc": ["body", "asking"], "type": "extra"}]}

    hub.response = disable_on_validation
    result = tools.search("question")
    assert result["code"] == "recall_disabled"
    assert len(hub.calls) == 1
