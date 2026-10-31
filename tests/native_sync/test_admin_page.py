"""Operator page: separate credential, no secrets echoed, settings enable login."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_brain.native_sync import feishu
from agent_brain.native_sync.api import create_app
from agent_brain.native_sync.hub import Hub


@pytest.fixture
def hub(tmp_path):
    return Hub(tmp_path / "hub.sqlite")


@pytest.fixture
def operator(hub):
    token = hub.admin_create("运维")
    client = TestClient(create_app(hub))
    return client, {"Authorization": "Bearer " + token}, token


def test_page_contains_no_credential_or_memory_content(operator):
    client, _, token = operator
    page = client.get("/native/v1/admin")
    assert page.status_code == 200
    assert "组织记忆 Hub" in page.text
    assert token not in page.text
    assert "sessionStorage" in page.text


def test_overview_requires_an_operator_credential(operator):
    client, headers, _ = operator
    assert client.get("/native/v1/admin/overview").status_code == 401
    assert (
        client.get(
            "/native/v1/admin/overview", headers={"Authorization": "Bearer forged"}
        ).status_code
        == 401
    )
    assert client.get("/native/v1/admin/overview", headers=headers).status_code == 200


def test_device_credential_does_not_open_the_operator_page(hub, operator):
    client, headers, _ = operator
    device = hub.enroll(hub.invite("linkcrux", "张三"), "Mac")
    device_headers = {"Authorization": "Bearer " + device["token"]}
    assert client.get("/native/v1/admin/overview", headers=device_headers).status_code == 401
    # ...and the operator credential is not a device credential.
    with pytest.raises(PermissionError):
        hub.identity(hub.admin_create("另一人"))


def test_invite_then_enroll_shows_up_in_overview(operator):
    client, headers, _ = operator
    created = client.post("/native/v1/admin/invite", json={"name": "张三"}, headers=headers).json()
    assert len(created["invite"]) > 20
    device = client.post(
        "/native/v1/enroll", json={"invite": created["invite"], "device_name": "Mac A"}
    ).json()
    overview = client.get("/native/v1/admin/overview", headers=headers).json()
    assert [m["name"] for m in overview["members"]] == ["张三"]
    assert overview["members"][0]["devices"] == 1
    assert overview["devices"][0]["member_name"] == "张三"
    assert device["device_id"] in {d["id"] for d in overview["devices"]}


def test_revoke_through_page_disables_the_device(operator):
    client, headers, _ = operator
    invite = client.post("/native/v1/admin/invite", json={"name": "张三"}, headers=headers).json()
    device = client.post(
        "/native/v1/enroll", json={"invite": invite["invite"], "device_name": "Mac A"}
    ).json()
    client.post("/native/v1/admin/revoke", json={"device_id": device["device_id"]}, headers=headers)
    assert (
        client.get(
            "/native/v1/me", headers={"Authorization": "Bearer " + device["token"]}
        ).status_code
        == 401
    )


def test_stored_settings_enable_login_and_hide_the_secret(operator, hub):
    client, headers, _ = operator
    client.post(
        "/native/v1/admin/settings",
        json={"key": "amh_hub_public_url", "value": "https://amh.example.com"},
        headers=headers,
    )
    client.post(
        "/native/v1/admin/settings",
        json={"key": "feishu_app_id", "value": "cli_demo"},
        headers=headers,
    )
    client.post("/native/v1/admin/secret", json={"app_secret": "SECRET-XYZ"}, headers=headers)
    overview = client.get("/native/v1/admin/overview", headers=headers)
    assert "SECRET-XYZ" not in overview.text
    assert client.get("/native/v1/auth/feishu").json() == {"available": True}
    config = feishu.load(hub.setting)
    assert config.redirect_uri == "https://amh.example.com/native/v1/auth/feishu/callback"


def test_environment_wins_over_stored_settings(operator, hub, monkeypatch):
    client, headers, _ = operator
    client.post(
        "/native/v1/admin/settings",
        json={"key": "amh_hub_public_url", "value": "https://stored.example.com"},
        headers=headers,
    )
    monkeypatch.setenv("AMH_HUB_PUBLIC_URL", "https://env.example.com")
    monkeypatch.setenv("FEISHU_APP_ID", "cli_env")
    monkeypatch.setenv("FEISHU_APP_SECRET", "env-secret")
    assert feishu.load(hub.setting).public_url == "https://env.example.com"


def test_unknown_setting_is_refused(operator):
    client, headers, _ = operator
    response = client.post(
        "/native/v1/admin/settings", json={"key": "shell", "value": "x"}, headers=headers
    )
    assert response.status_code == 409


def test_revoked_operator_credential_stops_working(hub, operator):
    from agent_brain.native_sync.storage import database

    client, headers, _ = operator
    with database(hub.path) as db:
        db.execute("UPDATE admins SET revoked=1")
    assert client.get("/native/v1/admin/overview", headers=headers).status_code == 401
