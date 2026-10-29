"""Organization provenance, versioning and recall contracts; real SQLite + HTTP routes."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.api import create_app


@pytest.fixture
def hub(tmp_path):
    return Hub(tmp_path / "hub.sqlite")


def enroll(hub, tenant="linkcrux", name="张三", member=None):
    invite = hub.invite(tenant, name, member_id=member)
    return hub.enroll(invite, "test-mac")


def asset(key="a", body="订单回调验签失败检查签名密钥版本", **extra):
    value = dict(
        asset_id=key,
        base_revision=0,
        source_store="store-1",
        product="codex-cli",
        project="orders",
        kind="memory",
        path="MEMORY.md",
        title="订单验签",
        body=body,
    )
    value.update(extra)
    return value


def test_invitation_is_single_use_and_new_device_keeps_member(hub):
    invite = hub.invite("linkcrux", "张三")
    a = hub.enroll(invite, "Mac A")
    with pytest.raises(ValueError):
        hub.enroll(invite, "imposter")
    b = enroll(hub, member=a["member_id"])
    assert a["member_id"] == b["member_id"]
    assert a["device_id"] != b["device_id"]
    assert a["token"] != b["token"]
    assert a["token"] not in Path(hub.path).read_bytes().decode("utf8", errors="ignore")


def test_recall_is_org_wide_and_keeps_author_not_reader(hub):
    a, b = enroll(hub), enroll(hub, name="李四")
    foreign = enroll(hub, tenant="other")
    saved = hub.put(a["token"], asset())
    hits = hub.search(b["token"], "订单回调验签失败", project_hint="unrelated")
    assert hits[0]["producer_member_id"] == a["member_id"]
    assert hits[0]["producer_name"] == "张三"
    assert hits[0]["revision"] == saved["revision"]
    assert hub.search(foreign["token"], "订单回调") == []
    assert hub.search(b["token"], "订单回调", project_filter="unrelated") == []


def test_repeated_upload_idempotent_and_stale_write_rejected(hub):
    a = enroll(hub)
    first = hub.put(a["token"], asset())
    assert hub.put(a["token"], asset())["revision"] == first["revision"] == 1
    newer = asset(body="订单回调验签失败请先核对版本号")
    with pytest.raises(ValueError):
        hub.put(a["token"], newer)
    newer["base_revision"] = 1
    assert hub.put(a["token"], newer)["revision"] == 2
    assert len(hub.changes(a["token"], 0)["items"]) == 2


def test_cannot_spoof_producer_or_overwrite_another_members_asset(hub):
    a, b = enroll(hub), enroll(hub, name="李四")
    hub.put(a["token"], asset())
    with pytest.raises(ValueError):
        hub.put(b["token"], asset(body="伪造内容"))
    with pytest.raises(ValueError):
        hub.put(b["token"], asset("b", producer_member_id=a["member_id"]))


def test_same_text_different_producers_remain_two_origins(hub):
    a, b = enroll(hub), enroll(hub, name="张三")
    hub.put(a["token"], asset("a"))
    hub.put(b["token"], asset("b"))
    hits = hub.search(a["token"], "订单回调")
    assert {h["producer_member_id"] for h in hits} == {a["member_id"], b["member_id"]}


def test_delete_is_versioned_not_recalled_and_revoke_denies_access(hub):
    a = enroll(hub)
    hub.put(a["token"], asset())
    deleted = asset(deleted=True)
    deleted["base_revision"] = 1
    hub.put(a["token"], deleted)
    assert hub.search(a["token"], "订单回调") == []
    assert hub.changes(a["token"], 1)["items"][0]["deleted"] is True
    hub.revoke(a["device_id"])
    with pytest.raises(PermissionError):
        hub.search(a["token"], "订单")


def test_pagination_is_tenant_scoped_and_stable(hub):
    a, b = enroll(hub), enroll(hub, tenant="other")
    hub.put(a["token"], asset("a"))
    hub.put(b["token"], asset("foreign"))
    hub.put(a["token"], asset("b"))
    page = hub.changes(a["token"], 0, limit=1)
    assert len(page["items"]) == 1
    more = hub.changes(a["token"], page["cursor"], limit=1)
    assert more["items"][0]["asset_id"] == "b"


def test_http_auth_validation_and_no_cross_tenant_request_override(hub):
    c = TestClient(create_app(hub))
    assert c.post("/native/v1/search", json={"query": "订单"}).status_code == 401
    e = c.post(
        "/native/v1/enroll", json={"invite": hub.invite("linkcrux", "张三"), "device_name": "Mac"}
    )
    assert e.status_code == 200
    headers = {"Authorization": "Bearer " + e.json()["token"]}
    assert c.post("/native/v1/assets", headers=headers, json=asset()).status_code == 200
    bad = c.post("/native/v1/assets", headers=headers, json=asset("b", tenant_id="other"))
    assert bad.status_code == 422
    assert c.post("/native/v1/search", headers=headers, json={"query": "订单"}).json()["items"]
    assert (
        c.post("/native/v1/search", headers=headers, json={"query": '" OR 1=1 --'}).status_code
        == 200
    )


def test_explicit_project_filter_is_applied_before_candidate_limit(hub):
    a = enroll(hub)
    for n in range(105):
        hub.put(a["token"], asset("noise" + str(n), project="other", body="unique topic repeated"))
    hub.put(a["token"], asset("wanted", project="wanted", body="unique topic repeated"))
    hits = hub.search(a["token"], "unique topic", project_filter="wanted")
    assert [r["asset_id"] for r in hits] == ["wanted"]


def test_partial_document_is_not_recalled_as_complete_fact(hub):
    a = enroll(hub)
    hub.put(
        a["token"],
        asset(
            "part0",
            document_id="doc",
            document_hash="0" * 64,
            part_index=0,
            part_count=2,
            body="PARTIAL_CANARY",
        ),
    )
    assert hub.search(a["token"], "PARTIAL_CANARY") == []
