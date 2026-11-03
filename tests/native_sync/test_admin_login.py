"""Browser-bound OAuth and administrator authorization; no real Feishu accounts."""

from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from agent_brain.native_sync import feishu
from agent_brain.native_sync.admin_auth import AdminAuth, FLOW_COOKIE, SESSION_COOKIE
from agent_brain.native_sync.api import create_app
from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.storage import database

BASE = "https://hub.example.com"
ADMIN = "/native/v1/admin"
CALLBACK = "/native/v1/auth/feishu/callback"


@pytest.fixture
def scene(tmp_path, monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "synthetic-secret")
    monkeypatch.setenv("AMH_HUB_PUBLIC_URL", BASE)
    monkeypatch.setenv("FEISHU_TENANT", "org")
    monkeypatch.setenv("FEISHU_ALLOWED_TENANT_KEYS", "tk")
    hub = Hub(tmp_path / "hub.sqlite")
    identity = dict(open_id="ou_admin", tenant_key="tk", name="Example")
    _, ticket = hub.feishu_complete(hub.feishu_begin(40000), identity, app_id="cli_test")
    device = hub.feishu_redeem(ticket, "existing-device")
    auth = AdminAuth(hub)
    auth.set_role(device["member_id"], enabled=True)
    monkeypatch.setattr(feishu, "exchange_code", lambda config, code: "synthetic-oauth-token")
    monkeypatch.setattr(feishu, "user_identity", lambda token: identity.copy())
    client = TestClient(create_app(hub), base_url=BASE, follow_redirects=False)
    return hub, auth, identity, device, client


def start(client, target="admin"):
    response = client.get(ADMIN + "/login", params={"target": target})
    assert response.status_code == 303
    state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
    assert state.startswith("admin.")
    return state, response


def sign_in(client, target="admin"):
    state, _ = start(client, target)
    return client.get(CALLBACK, params={"state": state, "code": "synthetic-code"})


def test_browser_login_uses_existing_callback_and_secure_cookie(scene):
    hub, auth, identity, device, client = scene
    state, started = start(client)
    query = parse_qs(urlsplit(started.headers["location"]).query)
    assert query["redirect_uri"] == [BASE + CALLBACK]
    assert "synthetic-secret" not in started.headers["location"]
    assert "httponly" in started.headers["set-cookie"].lower()
    assert "samesite=lax" in started.headers["set-cookie"].lower()
    callback = client.get(CALLBACK, params={"state": state, "code": "synthetic-code"})
    assert callback.status_code == 303 and callback.headers["location"] == ADMIN
    assert "secure" in callback.headers["set-cookie"].lower()
    session = client.get(ADMIN + "/session")
    assert session.status_code == 200
    assert session.json()["member_id"] == device["member_id"]
    assert session.json()["role"] == "admin"
    assert client.get(ADMIN + "/overview").status_code == 200
    assert client.get(ADMIN + "/dashboard/data").status_code == 200
    assert client.get("/native/v1/me").status_code == 401
    cookie = client.cookies.get(SESSION_COOKIE)
    assert (
        client.get(ADMIN + "/overview", headers={"Authorization": "Bearer " + cookie}).status_code
        == 401
    )
    with database(hub.path) as db:
        assert db.execute("SELECT count(*) FROM devices").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM admins").fetchone()[0] == 0
        assert cookie not in str([tuple(r) for r in db.execute("SELECT * FROM admin_web_sessions")])
    assert (
        client.get(CALLBACK, params={"state": state, "code": "again"})
        .headers["location"]
        .endswith("login=expired")
    )


def test_state_requires_same_browser_before_calling_feishu(scene, monkeypatch):
    hub, auth, identity, device, client = scene
    state, _ = start(client)
    stranger = TestClient(create_app(hub), base_url=BASE, follow_redirects=False)
    monkeypatch.setattr(
        feishu, "exchange_code", lambda *a: pytest.fail("must reject before provider call")
    )
    result = stranger.get(CALLBACK, params={"state": state, "code": "x"})
    assert result.headers["location"].endswith("login=expired")
    assert not stranger.cookies.get(SESSION_COOKIE)


@pytest.mark.parametrize("change", ["ordinary", "foreign_tenant", "same_name", "other_app"])
def test_member_or_name_or_foreign_identity_never_grants_admin(scene, monkeypatch, change):
    hub, auth, identity, device, client = scene
    state, _ = start(client)
    if change == "ordinary":
        auth.set_role(device["member_id"], enabled=False)
    elif change == "foreign_tenant":
        identity["tenant_key"] = "other"
    elif change == "same_name":
        identity["open_id"] = "same-display-name-other-id"
    else:
        monkeypatch.setenv("FEISHU_APP_ID", "cli_changed")
    result = client.get(CALLBACK, params={"state": state, "code": "x"})
    assert result.headers["location"].endswith(("login=denied", "login=expired"))
    assert client.get(ADMIN + "/overview").status_code == 401
    assert not client.cookies.get(SESSION_COOKIE)


def test_mutations_require_csrf_and_configured_origin(scene):
    hub, auth, identity, device, client = scene
    assert sign_in(client).status_code == 303
    csrf = client.get(ADMIN + "/session").json()["csrf"]
    path = ADMIN + "/revoke"
    payload = {"device_id": device["device_id"]}
    for headers in (
        {},
        {"Origin": BASE},
        {"Origin": "https://evil.example", "X-AMH-CSRF": csrf},
        {"Origin": BASE, "X-AMH-CSRF": "forged"},
    ):
        assert client.post(path, json=payload, headers=headers).status_code == 403
    assert not hub.identity(device["token"]).get("revoked")
    assert (
        client.post(path, json=payload, headers={"Origin": BASE, "X-AMH-CSRF": csrf}).status_code
        == 200
    )
    with pytest.raises(PermissionError):
        hub.identity(device["token"])


def test_role_revocation_expires_all_sessions_and_does_not_revoke_device(scene):
    hub, auth, identity, device, client = scene
    sign_in(client)
    auth.set_role(device["member_id"], enabled=False)
    assert client.get(ADMIN + "/overview").status_code == 401
    assert hub.identity(device["token"])["member_id"] == device["member_id"]
    auth.set_role(device["member_id"], enabled=True)
    assert client.get(ADMIN + "/overview").status_code == 401


def test_session_expiry_logout_and_bootstrap_token_still_work(scene):
    hub, auth, identity, device, client = scene
    result = sign_in(client, "dashboard")
    assert result.headers["location"] == ADMIN + "/dashboard"
    csrf = client.get(ADMIN + "/session").json()["csrf"]
    assert (
        client.post(ADMIN + "/logout", headers={"Origin": BASE, "X-AMH-CSRF": csrf}).status_code
        == 200
    )
    assert client.get(ADMIN + "/overview").status_code == 401
    sign_in(client)
    with database(hub.path) as db:
        db.execute("UPDATE admin_web_sessions SET expires=0")
    assert client.get(ADMIN + "/overview").status_code == 401
    token = hub.admin_create("recovery")
    assert (
        client.get(ADMIN + "/overview", headers={"Authorization": "Bearer " + token}).status_code
        == 200
    )


def test_no_automatic_grant_and_only_fixed_return_targets(scene):
    hub, auth, identity, device, client = scene
    assert client.get(ADMIN + "/login?target=https://evil.example").status_code == 422
    with pytest.raises(ValueError):
        auth.set_role("unknown-member", enabled=True)
    with database(hub.path) as db:
        db.execute("DELETE FROM admin_web_roles")
    assert sign_in(client).headers["location"].endswith("login=denied")


def test_expired_state_and_provider_failure_do_not_create_sessions(scene, monkeypatch):
    hub, auth, identity, device, client = scene
    state, _ = start(client)
    with database(hub.path) as db:
        db.execute("UPDATE admin_web_states SET expires=0")
    result = client.get(CALLBACK, params={"state": state, "code": "x"})
    assert result.headers["location"].endswith("login=expired")
    state, _ = start(client)

    def offline(*a):
        raise OSError("offline")

    monkeypatch.setattr(feishu, "exchange_code", offline)
    assert (
        client.get(CALLBACK, params={"state": state, "code": "x"})
        .headers["location"]
        .endswith("login=unavailable")
    )
    assert not client.cookies.get(SESSION_COOKIE)


def test_switch_to_unprivileged_account_cannot_keep_previous_admin_session(scene):
    hub, auth, identity, device, client = scene
    sign_in(client)
    previous = client.cookies.get(SESSION_COOKIE)
    identity["open_id"] = "ordinary-member"
    assert sign_in(client).headers["location"].endswith("login=denied")
    assert not client.cookies.get(SESSION_COOKIE)
    with pytest.raises(PermissionError):
        auth.session(previous)


def test_changed_configuration_invalidates_existing_session(scene, monkeypatch):
    hub, auth, identity, device, client = scene
    sign_in(client)
    monkeypatch.setenv("FEISHU_APP_ID", "cli_other")
    assert client.get(ADMIN + "/overview").status_code == 401


def test_role_cli_requires_explicit_member_and_action(scene):
    from typer.testing import CliRunner
    from agent_brain.native_sync.cli import app

    hub, auth, identity, device, client = scene
    runner = CliRunner()
    assert (
        runner.invoke(
            app, ["admin-role", "--db", str(hub.path), "--member", device["member_id"]]
        ).exit_code
        != 0
    )
    result = runner.invoke(
        app, ["admin-role", "--db", str(hub.path), "--member", device["member_id"], "--revoke"]
    )
    assert result.exit_code == 0, result.output
    assert '"enabled": false' in result.output
    assert sign_in(client).headers["location"].endswith("login=denied")


def test_pages_use_feishu_session_and_keep_manual_recovery_explicit(scene):
    hub, auth, identity, device, client = scene
    for path in (ADMIN, ADMIN + "/dashboard"):
        page = client.get(path).text
        assert "使用飞书登录" in page and "主机管理员运维入口" in page
        assert "ensureAdminSession" in page and "signOutAdmin" in page
        assert "synthetic-secret" not in page
        assert "/* AMH_ADMIN_SESSION */" not in page


def test_cookie_authorization_rechecks_role_inside_hub_transaction(scene):
    from agent_brain.native_sync.admin_auth import WebAdminSession

    hub, auth, identity, device, client = scene
    sign_in(client)
    credential = WebAdminSession(client.cookies.get(SESSION_COOKIE))
    assert hub.admin_overview(credential)["members"]
    auth.set_role(device["member_id"], enabled=False)
    with pytest.raises(PermissionError):
        hub.admin_revoke(credential, device["device_id"])
    assert hub.identity(device["token"])["member_id"] == device["member_id"]


def test_additive_schema_preserves_existing_identity_and_assets(tmp_path):
    import sqlite3
    from pathlib import Path
    schema = (Path(__file__).parents[1] / "fixtures/native-hub-1.4.1.sql").read_text()
    path = tmp_path / "existing.sqlite"
    db = sqlite3.connect(path)
    db.executescript(schema)
    db.execute("INSERT INTO members VALUES('member','org','Existing')")
    db.execute("INSERT INTO devices VALUES('device','member','digest','Existing device',0)")
    db.execute("INSERT INTO settings VALUES('custom_setting','preserved')")
    db.execute("INSERT INTO assets VALUES('org','asset',1,'device',?)", ('{"kind":"memory","document_id":"doc","document_hash":"hash"}',))
    db.commit()
    db.close()
    hub = Hub(path)
    with database(hub.path) as db:
        assert tuple(db.execute("SELECT * FROM members").fetchone()) == (
            "member",
            "org",
            "Existing",
        )
        assert db.execute("SELECT count(*) FROM devices").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM admin_web_roles").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM assets").fetchone()[0] == 1
        assert (
            db.execute("SELECT value FROM settings WHERE key='custom_setting'").fetchone()[0]
            == "preserved"
        )
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_admin_responses_are_not_cached(scene):
    hub, auth, identity, device, client = scene
    assert client.get(ADMIN).headers["cache-control"] == "no-store"
    assert client.get(ADMIN + "/session").headers["cache-control"] == "no-store"
    sign_in(client)
    assert client.get(ADMIN + "/overview").headers["cache-control"] == "no-store"
