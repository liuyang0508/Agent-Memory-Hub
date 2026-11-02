"""The prompt-time hook uses the hybrid recall channel, reports delivery states, and degrades to search."""

import json

from agent_brain.native_sync.client import NativeClient
from agent_brain.native_sync.query_worker import fetch
from agent_brain.native_sync.storage import database
from agent_brain.native_sync.transport import HubHTTPError
from test_hub import asset, enroll
from test_runtime import hook, live as live  # noqa: F401  (fixture re-export)


def test_hook_recall_skips_transcript_noise_and_reports_delivery_states(live, tmp_path):
    hub, url = live
    author = enroll(hub)
    hub.put(author["token"], asset("fact", title="审批规则", body="审批需要两人复核，附变更清单。"))
    hub.put(
        author["token"],
        asset("noise", kind="transcript", body="审批 复核 " * 400 + "TOOL_SCHEMA_NOISE"),
    )
    c = NativeClient.adopt(tmp_path / "state", url, hub.provision("李四", "Mac"), "Mac")
    first = hook(c.root, "UserPromptSubmit", prompt="审批复核要几个人")
    context = first["hookSpecificOutput"]["additionalContext"]
    assert "两人复核" in context and "TOOL_SCHEMA_NOISE" not in context
    metric = json.loads((c.root / "hook-metrics.jsonl").read_text().splitlines()[-1])
    assert metric["channel"] == "recall" and metric["hits"] == 1 and metric["packed"] == 1
    assert len(metric["trace_id"]) == 24
    trace_id = metric["trace_id"]
    with database(c.path) as db:
        reports = {
            (r["asset"], r["event"]) for r in db.execute("SELECT * FROM recall_reports")
        }
        assert db.execute("SELECT trace_id FROM emitted_traces").fetchone()["trace_id"] == trace_id
    assert reports == {("fact", "packed"), ("fact", "emitted")}
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(json.dumps(first, ensure_ascii=False))
    second = hook(c.root, "UserPromptSubmit", prompt="审批复核要几个人", transcript_path=str(transcript))
    assert not second.get("hookSpecificOutput", {}).get("additionalContext")
    with database(c.path) as db:
        reports = {
            (r["trace_id"], r["asset"], r["event"]) for r in db.execute("SELECT * FROM recall_reports")
        }
    assert (trace_id, "fact", "confirmed") in reports
    second_trace = json.loads((c.root / "hook-metrics.jsonl").read_text().splitlines()[-1])["trace_id"]
    assert (second_trace, "fact", "covered") in reports
    assert c.sync()["errors"] == []
    with database(c.path) as db:
        assert db.execute("SELECT count(*) FROM recall_reports").fetchone()[0] == 0
    traces = {t["trace_id"]: t for t in hub.recalls(c.config["identity"]["token"])["traces"]}
    assert traces[trace_id]["channel"] == "hook" and traces[trace_id]["counts"]["confirmed"] == 1
    assert traces[second_trace]["counts"].get("dropped:covered") == 1
    full = hub.recalls(c.config["identity"]["token"], trace_id=trace_id)["traces"][0]
    assert [e["event"] for e in full["candidates"][0]["events"]] == ["packed", "emitted", "confirmed"]
    assert "两人复核" not in json.dumps(full, ensure_ascii=False)


def test_fetch_falls_back_to_search_on_an_old_hub_and_keeps_other_errors():
    class OldHub:
        def recall(self, query, *, limit):
            raise HubHTTPError(404)

        def search(self, query, **kwargs):
            return [{"asset_id": "legacy", "query": query, **kwargs}]

    assert fetch(OldHub(), "q") == {
        "items": [{"asset_id": "legacy", "query": "q", "limit": 8}],
        "trace_id": None,
        "channel": "search",
    }

    class Broken(OldHub):
        def recall(self, query, *, limit):
            raise HubHTTPError(409)

    try:
        fetch(Broken(), "q")
    except HubHTTPError as exc:
        assert exc.http_status == 409
    else:
        raise AssertionError("non-404 errors must propagate")

    class New:
        def recall(self, query, *, limit):
            return {"items": [{"asset_id": "a"}], "trace_id": "f" * 24, "private": 1}

    assert fetch(New(), "q") == {"items": [{"asset_id": "a"}], "trace_id": "f" * 24, "channel": "recall"}


def test_reports_are_dropped_for_hubs_that_cannot_accept_them_and_kept_on_outage(tmp_path):
    from agent_brain.native_sync.hub import Hub
    from test_client import DirectTransport, client

    hub = Hub(tmp_path / "hub.sqlite")
    c = client(tmp_path, hub, "张三")
    with database(c.path) as db:
        db.execute("INSERT INTO recall_reports VALUES('a','x',1,'packed',0)")
        db.execute("INSERT INTO recall_reports VALUES('b','y',1,'packed',0)")

    class Flaky(DirectTransport):
        def __init__(self, hub, identity):
            super().__init__(hub, identity)
            self.calls = []

        def recall_feedback(self, trace_id, events):
            self.calls.append(trace_id)
            if trace_id == "a":
                raise HubHTTPError(409)
            raise OSError("down")

    transport = Flaky(hub, c.identity)
    c.transport = transport
    try:
        c.sync()
    except OSError:
        pass
    with database(c.path) as db:
        remaining = [r["trace_id"] for r in db.execute("SELECT trace_id FROM recall_reports")]
    assert remaining == ["b"] and transport.calls == ["a", "b"]
