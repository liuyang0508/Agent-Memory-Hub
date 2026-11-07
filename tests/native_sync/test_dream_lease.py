"""1.5.1: the organization dreams in one place — a designated device, one lease at a time."""

import gzip
import io
import json
import sqlite3
import time
from email.message import Message
from urllib import error

import pytest
from fastapi.testclient import TestClient

from agent_brain.native_sync.api import create_app
from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.knowledge import KnowledgeConflict
from agent_brain.native_sync.transport import HubHTTPError, Transport
from test_hub import asset

WIKI = "---\nname: deploy\ndescription: 部署说明\n---\n正文"


def wiki(key="w", body=WIKI, **extra):
    return asset(key, body, product="amh", path="org-wiki/deploy.md", title="deploy", **extra)


@pytest.fixture
def hub(tmp_path):
    return Hub(tmp_path / "hub.sqlite")


def test_only_a_designated_device_publishes_wiki_pages_and_the_owner_may_still_withdraw(hub):
    a, b = hub.provision("张三", "Mac A"), hub.provision("李四", "Mac B")
    ops = hub.admin_create("ops")
    with pytest.raises(KnowledgeConflict, match="dream not designated"):
        hub.put(a["token"], wiki())
    # Ordinary memory is untouched by the gate.
    hub.put(a["token"], asset("m", "# 自己的主题", product="claude-code", path="own.md"))
    with pytest.raises(ValueError, match="unknown device"):
        hub.admin_setting(ops, "dream_devices", "dev_nobody")
    hub.admin_setting(ops, "dream_devices", f"{a['device_id']}, {a['device_id']}")
    assert hub.admin_overview(ops)["settings"]["dream_devices"] == a["device_id"]
    first = hub.put(a["token"], wiki())
    with pytest.raises(KnowledgeConflict, match="dream not designated"):
        hub.put(b["token"], wiki("w2"))
    hub.admin_setting(ops, "dream_devices", "")
    assert "dream_devices" not in hub.admin_overview(ops)["settings"]
    with pytest.raises(KnowledgeConflict):
        hub.put(a["token"], wiki(base_revision=first["revision"], body=WIKI + "\n改"))
    gone = hub.put(a["token"], wiki(base_revision=first["revision"], deleted=True))
    assert gone["revision"] == first["revision"] + 1


def test_the_lease_goes_to_one_designated_device_at_a_time_per_organization(hub):
    a, b = hub.provision("张三", "Mac A"), hub.provision("李四", "Mac B")
    c, d = hub.provision("王五", "Mac C"), hub.provision("赵六", "Mac D", tenant="other")
    ops = hub.admin_create("ops")
    hub.admin_setting(ops, "dream_devices", f"{a['device_id']},{b['device_id']},{d['device_id']}")
    idle = {"designated": False, "mine": False, "holder": None, "expires": None}
    assert hub.dream_lease(c["token"]) == idle
    with pytest.raises(KnowledgeConflict, match="dream not designated"):
        hub.dream_lease(c["token"], action="acquire")
    held = hub.dream_lease(a["token"], action="acquire", ttl_seconds=600)
    assert held["mine"] and held["holder"] == "Mac A" and held["expires"] > time.time() + 500
    with pytest.raises(KnowledgeConflict, match="dream lease held"):
        hub.dream_lease(b["token"], action="acquire")
    assert hub.dream_lease(b["token"]) == {**held, "mine": False}
    # Another organization on the same Hub dreams on its own.
    assert hub.dream_lease(d["token"], action="acquire")["mine"]
    renewed = hub.dream_lease(a["token"], action="acquire", ttl_seconds=3600)
    assert renewed["expires"] > held["expires"]
    # Only the holder gives the lease back; it also runs out on its own.
    assert hub.dream_lease(b["token"], action="release")["holder"] == "Mac A"
    assert hub.dream_lease(a["token"], action="release") == {**idle, "designated": True}
    assert hub.dream_lease(b["token"], action="acquire")["holder"] == "Mac B"
    with sqlite3.connect(hub.path) as db:
        db.execute("UPDATE dream_leases SET expires=0 WHERE device=?", (b["device_id"],))
    assert hub.dream_lease(a["token"], action="acquire")["holder"] == "Mac A"
    with pytest.raises(ValueError, match="unknown lease action"):
        hub.dream_lease(a["token"], action="steal")


def test_the_lease_route_speaks_the_public_error_vocabulary(hub):
    client = TestClient(create_app(hub))
    a = hub.provision("张三", "Mac A")
    headers = {"Authorization": f"Bearer {a['token']}"}
    assert "dream-lease-v1" in client.get("/native/v1/health").json()["capabilities"]
    standing = client.post("/native/v1/dream/lease", json={}, headers=headers)
    assert standing.status_code == 200 and standing.json()["designated"] is False
    refused = client.post("/native/v1/dream/lease", json={"action": "acquire"}, headers=headers)
    assert refused.status_code == 409 and refused.json()["code"] == "dream_not_designated"
    bad = client.post("/native/v1/dream/lease", json={"action": "steal"}, headers=headers)
    assert bad.status_code == 422
    assert client.post("/native/v1/assets", json=wiki(), headers=headers).status_code == 409
    hub.admin_setting(hub.admin_create("ops"), "dream_devices", a["device_id"])
    took = client.post(
        "/native/v1/dream/lease", json={"action": "acquire", "ttl_seconds": 60}, headers=headers
    )
    assert took.status_code == 200 and took.json()["mine"]
    assert client.post("/native/v1/assets", json=wiki(), headers=headers).status_code == 200


def test_the_client_reads_the_error_code_through_a_gzipping_proxy(monkeypatch):
    """aihub's nginx gzips a 409 envelope too; the code must survive the trip."""
    link = Transport("http://127.0.0.1:9", "device-token", timeout=5)
    headers = Message()
    headers["Content-Type"] = "application/json"
    headers["Content-Encoding"] = "gzip"
    envelope = {"code": "dream_not_designated", "detail": "dream not designated"}
    body = gzip.compress(json.dumps(envelope).encode())

    def refuse(req, timeout=None):
        raise error.HTTPError(req.full_url, 409, "Conflict", headers, io.BytesIO(body))

    monkeypatch.setattr(link.opener, "open", refuse)
    with pytest.raises(HubHTTPError) as caught:
        link.call("dream/lease", {"action": "acquire"})
    assert (caught.value.http_status, caught.value.code) == (409, "dream_not_designated")
