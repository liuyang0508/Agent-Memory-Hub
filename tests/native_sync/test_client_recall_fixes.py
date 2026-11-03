"""Client-side recall fixes: one confirmation per delivery, feedback outages that
never block a sync pass, per-asset source_meta retries, covered-vs-budget states,
gateway fallbacks for the hook channel, trace-link housekeeping and CLI input checks."""

import json
import re
import time

from typer.testing import CliRunner

from agent_brain.native_sync import cli, hooks
from agent_brain.native_sync.client import NativeClient
from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.query_worker import fetch
from agent_brain.native_sync.storage import database
from agent_brain.native_sync.transport import HubHTTPError, Transport
from test_client import DirectTransport, client
from test_hub import asset, enroll
from test_runtime import hook, live as live  # noqa: F401  (fixture re-export)

MEMORY = """---
name: approvals
metadata:
  type: feedback
  modified: 2026-10-30T00:00:00Z
---
审批需要两人复核。
"""


def _last_metric(root):
    return json.loads((root / "hook-metrics.jsonl").read_text().splitlines()[-1])


def test_confirmation_is_reported_once_per_delivery(live, tmp_path):
    """B1: the marker stays in the transcript all session long; confirm the trace once."""
    hub, url = live
    author = enroll(hub)
    hub.put(author["token"], asset("fact", title="审批规则", body="审批需要两人复核，附变更清单。"))
    c = NativeClient.adopt(tmp_path / "state", url, hub.provision("李四", "Mac"), "Mac")
    sent = []

    class Recording(Transport):
        def recall_feedback(self, trace_id, events):
            sent.extend((trace_id, e["event"]) for e in events)
            return super().recall_feedback(trace_id, events)

    c.transport = Recording(url, c.config["identity"]["token"])
    first = hook(c.root, "UserPromptSubmit", prompt="审批复核要几个人")
    assert "两人复核" in first["hookSpecificOutput"]["additionalContext"]
    trace_id = _last_metric(c.root)["trace_id"]
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(json.dumps(first, ensure_ascii=False))
    for _ in range(3):
        again = hook(
            c.root, "UserPromptSubmit", prompt="审批复核要几个人", transcript_path=str(transcript)
        )
        # Coverage survives: the delivery is not repeated while the receipt is visible.
        assert not again.get("hookSpecificOutput", {}).get("additionalContext")
        assert c.sync()["errors"] == []
    assert sent.count((trace_id, "confirmed")) == 1
    with database(c.path) as db:
        assert db.execute("SELECT count(*) FROM emitted_traces").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM emitted").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM recall_reports").fetchone()[0] == 0
    full = hub.recalls(c.config["identity"]["token"], trace_id=trace_id)["traces"][0]
    assert [e["event"] for e in full["candidates"][0]["events"]] == [
        "packed",
        "emitted",
        "confirmed",
    ]


def test_feedback_outage_keeps_reports_without_blocking_sync(tmp_path):
    """B3: a failing feedback endpoint must not stop changes replay or projection."""
    hub = Hub(tmp_path / "hub.sqlite")
    author = client(tmp_path, hub, "张三")
    hub.put(author.identity["token"], asset("shared"))
    c = client(tmp_path, hub, "李四")

    class Outage(DirectTransport):
        def __init__(self, hub, identity):
            super().__init__(hub, identity)
            self.feedback, self.pulls = [], 0

        def recall_feedback(self, trace_id, events):
            self.feedback.append(trace_id)
            raise OSError("Hub temporarily unavailable: HTTP 503")

        def changes(self, cursor, *, metadata_only=False):
            self.pulls += 1
            return super().changes(cursor, metadata_only=metadata_only)

    transport = Outage(hub, c.identity)
    c.transport = transport
    fresh, stale = "a" * 24, "b" * 24
    target = tmp_path / "claude-code"
    target.mkdir()
    with database(c.path) as db:
        db.execute("INSERT INTO recall_reports VALUES(?,'x',1,'packed',?)", (fresh, time.time()))
        db.execute(
            "INSERT INTO recall_reports VALUES(?,'y',1,'packed',?)",
            (stale, time.time() - 31 * 86400),
        )
        db.execute("INSERT INTO dynamic_targets VALUES(?,?)", (str(target), "claude-code"))
    for _ in range(3):
        result = c.sync()
        assert result["errors"] == [f"recall report {fresh}: unavailable"]
    assert transport.pulls >= 3 and transport.feedback == [fresh] * 3
    assert len(c.cached()) == 1 and "amh-org" in (target / "MEMORY.md").read_text()
    with database(c.path) as db:
        assert db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()["value"] != "0"
        remaining = [r["trace_id"] for r in db.execute("SELECT trace_id FROM recall_reports")]
    assert remaining == [fresh]  # kept for a later pass; the 31-day-old one was purged


def test_one_rejected_asset_does_not_strip_source_meta_from_the_rest(tmp_path):
    """B5: only a successful retry without the hints proves the Hub rejects the field."""
    hub = Hub(tmp_path / "hub.sqlite")

    class Picky(DirectTransport):
        def __init__(self, hub, identity):
            super().__init__(hub, identity)
            self.seen = []

        def capabilities(self):
            return ["org-research-v1", "recall-parity-v1"]

        def put(self, value):
            self.seen.append((value["path"], "source_meta" in value))
            if value["path"] == "0-bad.md":
                raise HubHTTPError(422)
            return super().put(value)

    c = client(tmp_path, hub, "张三")
    transport = Picky(hub, c.identity)
    c.transport = transport
    root = tmp_path / "source"
    root.mkdir()
    c.register(root, "claude-code", "orders", "memory")
    for name in ("0-bad.md", "1-good.md", "2-good.md"):
        (root / name).write_text(MEMORY + name + "\n")
    result = c.sync()
    assert len(result["errors"]) == 1 and "HubHTTPError" in result["errors"][0]
    assert sorted(transport.seen) == [
        ("0-bad.md", False),
        ("0-bad.md", True),
        ("1-good.md", True),
        ("2-good.md", True),
    ]
    saved = {r["path"]: r for r in hub.search(c.identity["token"], "复核", limit=10)}
    assert set(saved) == {"1-good.md", "2-good.md"}
    for record in saved.values():
        assert record["source_meta"]["declared"]["type"] == "feedback"
        assert record["source_meta"]["mtime"] > 0


def test_excerpt_delivery_is_reported_covered_on_the_next_hit(live, tmp_path):
    """B8: a body over the per-item budget covers under an excerpt key; the Hub sees `covered`."""
    hub, url = live
    author = enroll(hub)
    body = "审批需要两人复核。" + "补充说明：变更清单要附在审批单后面。" * 120
    assert len(body.encode()) > 1536
    hub.put(author["token"], asset("long", title="审批规则", body=body))
    c = NativeClient.adopt(tmp_path / "state", url, hub.provision("李四", "Mac"), "Mac")
    first = hook(c.root, "UserPromptSubmit", prompt="审批复核要几个人")
    assert "[片段截断]" in first["hookSpecificOutput"]["additionalContext"]
    with database(c.path) as db:
        keys = json.loads(db.execute("SELECT keys_json FROM emitted").fetchone()["keys_json"])
    assert keys and all(":excerpt:" in k for k in keys)
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(json.dumps(first, ensure_ascii=False))
    second = hook(
        c.root, "UserPromptSubmit", prompt="审批复核要几个人", transcript_path=str(transcript)
    )
    assert not second.get("hookSpecificOutput", {}).get("additionalContext")
    second_trace = _last_metric(c.root)["trace_id"]
    with database(c.path) as db:
        states = {
            r["event"]
            for r in db.execute(
                "SELECT event FROM recall_reports WHERE trace_id=?", (second_trace,)
            )
        }
    assert states == {"covered"}


def test_deleted_or_empty_hits_and_missing_trace_ids_queue_no_reports(live, tmp_path, monkeypatch):
    """B8 + server `trace_id: null`: nothing to report for what pack() never injects."""
    hub, url = live
    who = hub.provision("Author", "a")
    good = hub.put(who["token"], asset("good", body="GOOD_CANARY 订单回调验签"))
    gone = {
        **hub.put(who["token"], asset("gone", body="GONE_CANARY 订单回调验签")),
        "deleted": True,
    }
    blank = {**hub.put(who["token"], asset("blank", body="BLANK_CANARY 订单回调验签")), "body": " "}
    c = NativeClient.adopt(tmp_path / "reader", url, hub.provision("Reader", "r"), "r")
    responses = iter(
        [
            {"items": [gone, blank, good], "trace_id": "c" * 24, "channel": "recall"},
            {"items": [good], "trace_id": None, "channel": "recall"},
        ]
    )
    monkeypatch.setattr(
        "agent_brain.native_sync.query_worker.bounded_search", lambda *a, **k: next(responses)
    )
    first = hooks.run(
        c.root,
        "codex-cli",
        {"session_id": "s1", "hook_event_name": "UserPromptSubmit", "prompt": "GOOD_CANARY"},
    )
    context = first["hookSpecificOutput"]["additionalContext"]
    assert "GOOD_CANARY" in context and "GONE_CANARY" not in context
    with database(c.path) as db:
        reports = {(r["asset"], r["event"]) for r in db.execute("SELECT * FROM recall_reports")}
    assert reports == {("good", "packed"), ("good", "emitted")}
    second = hooks.run(
        c.root,
        "codex-cli",
        {"session_id": "s2", "hook_event_name": "UserPromptSubmit", "prompt": "GOOD_CANARY"},
    )
    assert "GOOD_CANARY" in second["hookSpecificOutput"]["additionalContext"]
    assert _last_metric(c.root)["trace_id"] is None
    with database(c.path) as db:
        assert db.execute("SELECT count(*) FROM recall_reports").fetchone()[0] == 2
        links = [r["session"] for r in db.execute("SELECT session FROM emitted_traces")]
    assert links == ["s1"]  # the untraced delivery still covers, but links to no trace


def test_fetch_falls_back_on_gateway_errors_and_probes_search_before_revoking():
    """B22: 405/501 behave like 404; a denial only counts once /search is denied too."""

    class Gateway:
        def __init__(self, error):
            self.error, self.searched = error, 0

        def recall(self, query, *, limit):
            raise self.error

        def search(self, query, **kwargs):
            self.searched += 1
            return [{"asset_id": "legacy"}]

    for error in (HubHTTPError(405), HubHTTPError(501), PermissionError("gateway")):
        gateway = Gateway(error)
        assert fetch(gateway, "q") == {
            "items": [{"asset_id": "legacy"}],
            "trace_id": None,
            "channel": "search",
        }
        assert gateway.searched == 1

    class Revoked(Gateway):
        def search(self, query, **kwargs):
            raise PermissionError("organization access rejected")

    try:
        fetch(Revoked(PermissionError("gateway")), "q")
    except PermissionError:
        pass
    else:
        raise AssertionError("a denial on both paths must still revoke")
    broken = Gateway(HubHTTPError(400))
    try:
        fetch(broken, "q")
    except HubHTTPError as exc:
        assert exc.http_status == 400 and broken.searched == 0
    else:
        raise AssertionError("other statuses must propagate")


def test_compaction_clears_trace_links_and_orphans_are_swept(live, tmp_path, monkeypatch):
    """B23: PostCompact drops the session's trace links; a delivery sweeps links without receipts."""
    hub, url = live
    who = hub.provision("Author", "a")
    record = hub.put(who["token"], asset("busy", body="SWEEP_CANARY 订单回调验签"))
    c = NativeClient.adopt(tmp_path / "reader", url, hub.provision("Reader", "r"), "r")
    old, now = time.time() - 2 * 86400, time.time()
    with database(c.path) as db:
        db.execute("INSERT INTO emitted VALUES('s','m-s','[]',?)", (now,))
        db.execute("INSERT INTO emitted_traces VALUES('s','m-s',?)", ("a" * 24,))
        db.execute("INSERT INTO emitted VALUES('other','m-live','[]',?)", (now,))
        db.execute("INSERT INTO emitted_traces VALUES('other','m-live',?)", ("d" * 24,))
    assert (
        hooks.run(c.root, "codex-cli", {"session_id": "s", "hook_event_name": "PostCompact"}) == {}
    )
    with database(c.path) as db:
        rows = {r["session"] for r in db.execute("SELECT session FROM emitted_traces")}
        assert db.execute("SELECT count(*) FROM emitted WHERE session='s'").fetchone()[0] == 0
    assert rows == {"other"}
    with database(c.path) as db:
        db.execute("INSERT INTO emitted VALUES('other','m-old','[]',?)", (old,))
        db.execute("INSERT INTO emitted_traces VALUES('other','m-old',?)", ("b" * 24,))
        db.execute("INSERT INTO emitted_traces VALUES('other','m-orphan',?)", ("c" * 24,))
    monkeypatch.setattr(
        "agent_brain.native_sync.query_worker.bounded_search",
        lambda *a, **k: {"items": [record], "trace_id": "e" * 24, "channel": "recall"},
    )
    result = hooks.run(
        c.root,
        "codex-cli",
        {"session_id": "other", "hook_event_name": "UserPromptSubmit", "prompt": "SWEEP_CANARY"},
    )
    assert "SWEEP_CANARY" in result["hookSpecificOutput"]["additionalContext"]
    with database(c.path) as db:
        links = {r["trace_id"] for r in db.execute("SELECT trace_id FROM emitted_traces")}
        receipts = db.execute("SELECT count(*) FROM emitted").fetchone()[0]
    assert links == {"d" * 24, "e" * 24} and receipts == 2  # expired and orphan links are gone


def test_recall_trace_rejects_malformed_trace_ids_and_explains_old_hubs(tmp_path, monkeypatch):
    """B24 / O7: bad ids and old Hubs get a hint (JSON under --json), never a traceback."""
    from rich.console import Console

    monkeypatch.setattr(cli, "console", Console(width=400, force_terminal=False))
    runner = CliRunner()
    state = ["--state", str(tmp_path)]
    for bad in ("ab cd", "中文", "ABCDEF0123456789ABCDEF01", "f" * 23):
        result = runner.invoke(cli.app, ["recall-trace", *state, "--trace", bad])
        assert result.exit_code == 1 and cli.TRACE_ID_HINT in result.output, bad
        assert "Traceback" not in result.output
        raw = runner.invoke(cli.app, ["recall-trace", *state, "--trace", bad, "--json"])
        assert raw.exit_code == 1 and json.loads(raw.output) == {"error": cli.TRACE_ID_HINT}
    feedback = runner.invoke(
        cli.app,
        [
            "recall-feedback",
            *state,
            "--trace",
            "ab cd",
            "--asset",
            "fact",
            "--revision",
            "1",
            "--event",
            "cite",
        ],
    )

    # Typer wraps usage errors in a box and, where the runner looks like a colour
    # terminal (GitHub Actions does), colours it; compare without either.
    def collapse(text):
        return re.sub(r"\x1b\[[0-9;]*m|[\s│╭╮╰╯─]", "", text)

    assert feedback.exit_code != 0 and collapse(cli.TRACE_ID_HINT) in collapse(feedback.output)
    assert "Traceback" not in feedback.output

    class OldHub:
        class transport:
            @staticmethod
            def recalls(*, trace_id=None, limit=20):
                raise HubHTTPError(404)

    monkeypatch.setattr(cli, "load", lambda state: OldHub())
    listing = runner.invoke(cli.app, ["recall-trace", *state])
    assert listing.exit_code == 1 and cli.OLD_HUB_HINT in listing.output
    raw = runner.invoke(cli.app, ["recall-trace", *state, "--json"])
    assert raw.exit_code == 1 and json.loads(raw.output) == {"error": cli.OLD_HUB_HINT}


def test_transport_escapes_trace_ids_in_the_query_string():
    """B24: the id is validated upstream, but the transport never builds an invalid URL."""
    transport = Transport("http://127.0.0.1:1")
    seen = []
    transport.call = lambda path, data=None: seen.append(path) or {"traces": []}
    assert transport.recalls(trace_id="ab cd/&x", limit=5) == []
    assert seen == ["recalls?limit=5&trace_id=ab%20cd%2F%26x"]
