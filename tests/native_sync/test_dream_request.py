"""1.5.6: 「现在做梦」 — an administrator asks for one pass; the designated device runs it."""

import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from agent_brain.native_sync import dream, dream_request, worker
from agent_brain.native_sync.api import create_app
from agent_brain.native_sync.cli import app
from agent_brain.native_sync.hub import DREAM_REQUEST_STALE, Hub
from agent_brain.native_sync.knowledge import KnowledgeConflict
from agent_brain.native_sync.transport import HubHTTPError
from test_dream import HubLink, change, fake_runner
from test_worker_sync_limits import persisted_client


@pytest.fixture
def hub(tmp_path):
    return Hub(tmp_path / "hub.sqlite")


class RequestLink(HubLink):
    """A Hub that also answers the request route; ``claim`` may be made to refuse."""

    def __init__(self, items, *, claim=None, **kwargs):
        super().__init__(items, **kwargs)
        self.claim, self.requests = claim, []

    def call(self, path, data=None):
        if path == dream.REQUEST_PATH:
            self.requests.append(data)
            if data["action"] == "claim" and isinstance(self.claim, Exception):
                raise self.claim
            return {"designated": True, "pending": data["action"] != "finish", "requested": 1.0}
        return super().call(path, data)


class Poll:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def call(self, path, data=None):
        self.calls.append((path, data))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_an_administrator_asks_once_and_only_the_designated_device_runs_it(hub):
    a, b = hub.provision("张三", "Mac A"), hub.provision("李四", "Mac B")
    ops = hub.admin_create("ops")
    with pytest.raises(ValueError, match="no dream device"):
        hub.admin_dream_request(ops)
    hub.admin_setting(ops, "dream_devices", a["device_id"])
    status = hub.admin_dream_request(ops)
    assert status["devices"] == [{"id": a["device_id"], "name": "Mac A"}]
    assert status["running"] is None
    first = status["request"]
    assert first["pending"] and first["device"] is None and first["requested_by"] == "ops"
    # A device nobody designated sees nothing and may not claim.
    idle = {"designated": False, "pending": False, "requested": None}
    assert hub.dream_request(b["token"]) == idle
    with pytest.raises(KnowledgeConflict, match="dream not designated"):
        hub.dream_request(b["token"], action="claim")
    seen = hub.dream_request(a["token"])
    assert seen == {"designated": True, "pending": True, "requested": first["requested"]}
    # Asking again while the request is open changes nothing.
    assert hub.admin_dream_request(ops)["request"]["requested"] == first["requested"]
    assert hub.dream_request(a["token"], action="claim")["pending"]
    assert hub.admin_dream_status(ops)["request"]["device"] == "Mac A"
    # Claiming again (the pass restarted) is fine; a second designated device is refused.
    hub.admin_setting(ops, "dream_devices", f"{a['device_id']},{b['device_id']}")
    with pytest.raises(KnowledgeConflict, match="dream request taken"):
        hub.dream_request(b["token"], action="claim")
    assert hub.dream_request(b["token"])["pending"] is False
    assert hub.dream_request(a["token"], action="claim")["pending"]
    with pytest.raises(KnowledgeConflict, match="dream request taken"):
        hub.dream_request(b["token"], action="finish", outcome="published")
    done = hub.dream_request(a["token"], action="finish", outcome="published", message="3 页")
    assert done["pending"] is False and done["finished"] and done["requested"] == first["requested"]
    last = hub.admin_dream_status(ops)["request"]
    assert (last["pending"], last["outcome"], last["message"]) == (False, "published", "3 页")
    assert hub.dream_request(a["token"])["pending"] is False
    with pytest.raises(KnowledgeConflict, match="dream no request"):
        hub.dream_request(a["token"], action="claim")
    with pytest.raises(KnowledgeConflict, match="dream no request"):
        hub.dream_request(a["token"], action="finish", outcome="published")
    fresh = hub.admin_dream_request(ops)["request"]
    assert fresh["pending"] and fresh["requested"] >= last["requested"] and fresh["device"] is None
    with pytest.raises(ValueError, match="unknown request action"):
        hub.dream_request(a["token"], action="steal")


def test_a_claim_nobody_closed_is_abandoned_and_a_running_pass_shows_on_the_page(hub):
    a, b = hub.provision("张三", "Mac A"), hub.provision("李四", "Mac B")
    ops = hub.admin_create("ops")
    hub.admin_setting(ops, "dream_devices", f"{a['device_id']},{b['device_id']}")
    hub.admin_dream_request(ops)
    hub.dream_request(a["token"], action="claim")
    hub.dream_lease(a["token"], action="acquire", ttl_seconds=600)
    running = hub.admin_dream_status(ops)["running"]
    assert running["device"] == "Mac A" and running["expires"] > time.time()
    with sqlite3.connect(hub.path) as db:
        db.execute("UPDATE dream_requests SET claimed=?", (time.time() - DREAM_REQUEST_STALE - 1,))
    assert hub.dream_request(b["token"])["pending"]
    assert hub.dream_request(b["token"], action="claim")["pending"]
    with pytest.raises(KnowledgeConflict, match="dream request taken"):
        hub.dream_request(a["token"], action="finish", outcome="failed")
    # An administrator asking while a claim went stale gets a fresh request.
    with sqlite3.connect(hub.path) as db:
        db.execute("UPDATE dream_requests SET claimed=?", (time.time() - DREAM_REQUEST_STALE - 1,))
    assert hub.admin_dream_request(ops)["request"]["device"] is None


def test_the_routes_speak_the_public_vocabulary_and_keep_devices_off_the_admin_side(hub):
    client = TestClient(create_app(hub))
    a, b = hub.provision("张三", "Mac A"), hub.provision("李四", "Mac B")
    ops = hub.admin_create("ops")
    dev = {"Authorization": f"Bearer {a['token']}"}
    other = {"Authorization": f"Bearer {b['token']}"}
    adm = {"Authorization": f"Bearer {ops}"}
    assert "dream-request-v1" in client.get("/native/v1/health").json()["capabilities"]
    assert client.get("/native/v1/admin/dream", headers=dev).status_code == 401
    assert client.post("/native/v1/admin/dream/run", headers=dev).status_code == 401
    refused = client.post("/native/v1/admin/dream/run", headers=adm)
    assert (refused.status_code, refused.json()["detail"]) == (409, "no dream device")
    hub.admin_setting(ops, "dream_devices", a["device_id"])
    asked = client.post("/native/v1/admin/dream/run", headers=adm)
    assert asked.status_code == 200 and asked.json()["request"]["pending"]
    poll = client.post("/native/v1/dream/request", json={}, headers=dev)
    assert poll.status_code == 200 and poll.json()["pending"]
    denied = client.post("/native/v1/dream/request", json={"action": "claim"}, headers=other)
    assert (denied.status_code, denied.json()["code"]) == (409, "dream_not_designated")
    assert client.post("/native/v1/dream/request", json={"action": "steal"}, headers=dev).status_code == 422
    assert client.post("/native/v1/dream/request", json={"action": "claim"}, headers=dev).status_code == 200
    closing = {"action": "finish", "outcome": "published", "message": "3 页 · 花费 1.20"}
    assert client.post("/native/v1/dream/request", json=closing, headers=dev).status_code == 200
    shown = client.get("/native/v1/admin/dream", headers=adm).json()["request"]
    assert shown["outcome"] == "published" and shown["device"] == "Mac A" and not shown["pending"]
    again = client.post("/native/v1/dream/request", json=closing, headers=dev)
    assert (again.status_code, again.json()["code"]) == (409, "dream_no_request")
    page = client.get("/native/v1/admin").text
    assert "现在做梦" in page and "/dream/run" in page and "--schedule on" in page


def test_a_requested_pass_claims_first_publishes_and_reports_how_it_ended(tmp_path):
    link = RequestLink([change("a", "部署走 pre 线")])
    report = dream.run(
        tmp_path / "state",
        link,
        lambda *_: None,
        runner=fake_runner(["deploy"]),
        home=tmp_path,
        on_request=True,
    )
    assert report["outcome"] == "published" and report["published"]
    assert [r["action"] for r in link.requests] == ["claim", "finish"]
    assert link.requests[-1]["outcome"] == "published" and "页" in link.requests[-1]["message"]
    assert link.leases == ["acquire", "release"]


def test_a_refused_claim_or_lease_ends_the_requested_pass_before_anything_is_read(tmp_path):
    def never(*_, **__):
        pytest.fail("a refused pass reached a document or a model")

    refusals = {
        "no_request": HubHTTPError(409, "dream_no_request"),
        "request_taken": HubHTTPError(409, "dream_request_taken"),
        "not_designated": HubHTTPError(409, "dream_not_designated"),
        "hub_too_old": HubHTTPError(404),
    }
    for outcome, refusal in refusals.items():
        link = RequestLink([change("a", "正文")], claim=refusal)
        report = dream.run(
            tmp_path / outcome, link, never, runner=never, home=tmp_path, on_request=True
        )
        assert report["outcome"] == outcome and report["message"] == dream.MESSAGES[outcome]
        assert link.calls == [] and link.leases == []
        assert [r["action"] for r in link.requests] == ["claim"]
        assert dream.status(tmp_path / outcome)["runs"][-1]["outcome"] == outcome
    # The request was claimed, then another machine held the lease: the page learns that.
    link = RequestLink([change("a", "正文")], lease=HubHTTPError(409, "dream_lease_held"))
    report = dream.run(
        tmp_path / "held", link, never, runner=never, home=tmp_path, on_request=True
    )
    assert report["outcome"] == "lease_held" and link.calls == []
    assert link.requests[-1] == {
        "action": "finish",
        "outcome": "lease_held",
        "message": dream.LEASE_MESSAGES["lease_held"],
    }


def test_the_worker_starts_one_pass_per_request_and_other_devices_stay_quiet(tmp_path):
    state, started = tmp_path / "state", []
    spawn = started.append
    pending = Poll({"designated": True, "pending": True, "requested": 5.0})
    assert dream.pick_up(state, pending, spawn=spawn)["started"] and started == [state]
    assert not dream.pick_up(state, pending, spawn=spawn)["started"] and len(started) == 1
    pending.answer = {"designated": True, "pending": True, "requested": 6.0}
    assert dream.pick_up(state, pending, spawn=spawn)["started"] and len(started) == 2
    marker = state / dream.DIRECTORY / dream.REQUEST_FILE
    stale = json.loads(marker.read_text())
    stale["started"] -= dream_request.PASS_SECONDS + 1
    marker.write_text(json.dumps(stale))
    assert dream.pick_up(state, pending, spawn=spawn)["started"] and len(started) == 3
    assert all(call == (dream.REQUEST_PATH, {"action": "poll"}) for call in pending.calls)
    quiet = Poll({"designated": False, "pending": False, "requested": None})
    standing = dream.pick_up(state, quiet, spawn=spawn)
    assert standing == {**quiet.answer, "supported": True, "refusal": "", "started": False}
    old = dream.pick_up(state, Poll(HubHTTPError(404)), spawn=spawn)
    assert old["supported"] is False and not old["started"] and len(started) == 3


def test_the_spawned_pass_runs_the_cli_on_request_with_a_path_that_finds_claude(
    tmp_path, monkeypatch
):
    launched = {}

    def popen(argv, **kwargs):
        launched.update(argv=argv, **kwargs)

    monkeypatch.setattr(dream_request.subprocess, "Popen", popen)
    state = tmp_path / "state"
    dream.spawn_pass(state)
    argv = launched["argv"]
    assert argv[-4:] == ["dream", "--state", str(state.resolve()), "--on-request"]
    assert launched["start_new_session"] and ".local/bin" in launched["env"]["PATH"]
    assert (state / dream.DIRECTORY / "request.log").exists()


def test_the_background_worker_asks_the_hub_on_its_clock(tmp_path, monkeypatch):
    c, _ = persisted_client(tmp_path)
    monkeypatch.setattr(worker, "background_client", lambda *a: c)
    asked = []

    def pick(state, transport, **_):
        asked.append((state, transport))
        raise KeyboardInterrupt

    monkeypatch.setattr(dream_request, "pick_up", pick)
    now = [0.0]
    monkeypatch.setattr(worker.time, "monotonic", lambda: now[0])

    def tick(seconds):
        now[0] += 100

    monkeypatch.setattr(worker.time, "sleep", tick)
    with pytest.raises(KeyboardInterrupt):
        worker.work(c.root)
    assert asked == [(c.root, c.transport)]


def test_the_cli_flag_publishes_without_asking(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    identity = {
        "tenant_id": "t",
        "member_id": "m",
        "member_name": "n",
        "device_id": "d",
        "token": "x",
    }
    (state / "config.json").write_text(
        json.dumps({"hub": "http://127.0.0.1:9", "identity": identity})
    )
    captured = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return {"outcome": "published"}

    monkeypatch.setattr(dream, "run", fake_run)
    result = CliRunner().invoke(app, ["dream", "--on-request", "--state", str(state)])
    assert result.exit_code == 0, result.output
    assert captured["publish_pages"] and captured["on_request"]
    assert json.loads(result.output) == {"outcome": "published"}
