"""Enterprise login: states, tickets and loopback handoff; no real Feishu calls."""

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
def configured(monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret-value")
    monkeypatch.setenv("AMH_HUB_PUBLIC_URL", "https://hub.example.com")
    monkeypatch.setenv("FEISHU_TENANT", "linkcrux")
    monkeypatch.setenv("FEISHU_ALLOWED_TENANT_KEYS", "tk")
    return feishu.load()


def test_login_is_unavailable_without_deployment_credentials(hub, monkeypatch):
    for key in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "AMH_HUB_PUBLIC_URL"):
        monkeypatch.delenv(key, raising=False)
    client = TestClient(create_app(hub))
    assert client.get("/native/v1/auth/feishu").json() == {"available": False}
    assert client.post("/native/v1/auth/feishu/start", json={"port": 5000}).status_code == 503


def test_redirect_uri_is_derived_from_public_url_not_host_header(configured):
    assert configured.redirect_uri == "https://hub.example.com/native/v1/auth/feishu/callback"


def test_public_url_must_be_https(monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret-value")
    monkeypatch.setenv("AMH_HUB_PUBLIC_URL", "http://hub.example.com")
    with pytest.raises(ValueError):
        feishu.load()


def test_authorize_url_carries_no_scope_and_no_secret(configured):
    url = feishu.authorize_url(configured, "state-value")
    assert url.startswith(feishu.AUTHORIZE_ENDPOINT)
    assert "client_id=cli_test" in url
    assert "secret-value" not in url
    assert "scope=" not in url


def test_state_is_single_use_and_bound_to_its_port(hub):
    state = hub.feishu_begin(51234)
    identity = {"name": "张三", "open_id": "ou_1", "union_id": "on_1", "tenant_key": "tk"}
    port, ticket = hub.feishu_complete(state, identity, app_id="cli_test")
    assert port == 51234
    with pytest.raises(ValueError):
        hub.feishu_complete(state, identity, app_id="cli_test")
    # The ticket alone completes the loopback exchange; the code is never stored.
    issued = hub.feishu_redeem(ticket, "Mac")
    assert issued["member_name"] == "张三"
    assert issued["tenant_id"] == "linkcrux"
    with pytest.raises(ValueError):
        hub.feishu_redeem(ticket, "Mac")


def test_same_feishu_account_reuses_one_member_across_devices(hub):
    identity = {"name": "张三", "open_id": "ou_1", "union_id": "on_1", "tenant_key": "tk"}
    first_port, first_ticket = hub.feishu_complete(
        hub.feishu_begin(50001), identity, app_id="cli_test"
    )
    second_port, second_ticket = hub.feishu_complete(
        hub.feishu_begin(50002), identity, app_id="cli_test"
    )
    assert (first_port, second_port) == (50001, 50002)
    a = hub.feishu_redeem(first_ticket, "Mac A")
    b = hub.feishu_redeem(second_ticket, "Mac B")
    assert a["member_id"] == b["member_id"]
    assert a["device_id"] != b["device_id"]
    assert a["token"] not in Path(hub.path).read_bytes().decode("utf8", errors="ignore")


def test_tenant_allowlist_rejects_foreign_tenant(configured, monkeypatch):
    monkeypatch.setenv("FEISHU_ALLOWED_TENANT_KEYS", "tk-ours")
    config = feishu.load()
    assert config.allows_tenant("tk-ours")
    assert not config.allows_tenant("tk-other")
    # No allowlist is not a yes: it defers to the Hub, which pins the tenant.
    monkeypatch.delenv("FEISHU_ALLOWED_TENANT_KEYS")
    assert feishu.load().allows_tenant("tk-anything") is None


def test_callback_rejects_declined_authorization(hub, configured):
    client = TestClient(create_app(hub), follow_redirects=False)
    response = client.get(
        "/native/v1/auth/feishu/callback", params={"error": "access_denied", "state": "s"}
    )
    assert response.status_code == 400


def test_unknown_state_never_mints_a_ticket(hub, configured, monkeypatch):
    monkeypatch.setattr(feishu, "exchange_code", lambda config, code: "token")
    monkeypatch.setattr(
        feishu,
        "user_identity",
        lambda token: {"name": "x", "open_id": "ou_x", "union_id": "", "tenant_key": "tk"},
    )
    client = TestClient(create_app(hub), follow_redirects=False)
    response = client.get(
        "/native/v1/auth/feishu/callback", params={"code": "c", "state": "forged"}
    )
    assert response.status_code == 409


def test_callback_redirects_to_loopback_with_one_use_ticket(hub, configured, monkeypatch):
    monkeypatch.setattr(feishu, "exchange_code", lambda config, code: "token")
    monkeypatch.setattr(
        feishu,
        "user_identity",
        lambda token: {"name": "张三", "open_id": "ou_1", "union_id": "", "tenant_key": "tk"},
    )
    client = TestClient(create_app(hub), follow_redirects=False)
    state = hub.feishu_begin(51234)
    response = client.get("/native/v1/auth/feishu/callback", params={"code": "c", "state": state})
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("http://127.0.0.1:51234/?ticket=")
    assert response.headers["cache-control"] == "no-store"
    ticket = location.split("ticket=", 1)[1]
    identity = client.post(
        "/native/v1/auth/feishu/redeem", json={"ticket": ticket, "device_name": "Mac"}
    ).json()
    assert identity["member_name"] == "张三"
    assert identity["token"]


def test_foreign_tenant_is_refused_after_code_exchange(hub, monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret-value")
    monkeypatch.setenv("AMH_HUB_PUBLIC_URL", "https://hub.example.com")
    monkeypatch.setenv("FEISHU_ALLOWED_TENANT_KEYS", "tk-ours")
    monkeypatch.setattr(feishu, "exchange_code", lambda config, code: "token")
    monkeypatch.setattr(
        feishu,
        "user_identity",
        lambda token: {"name": "x", "open_id": "ou_x", "union_id": "", "tenant_key": "tk-other"},
    )
    client = TestClient(create_app(hub), follow_redirects=False)
    response = client.get(
        "/native/v1/auth/feishu/callback",
        params={"code": "c", "state": hub.feishu_begin(5000)},
    )
    assert response.status_code == 403
