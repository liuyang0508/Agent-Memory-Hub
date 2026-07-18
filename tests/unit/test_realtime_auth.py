from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


@pytest.fixture()
def realtime_client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path))
    monkeypatch.setenv("MEMORY_HUB_RATE_LIMIT", "0")

    from web.app import app

    with TestClient(app) as client:
        init = client.post(
            "/api/auth/init",
            json={"username": "admin", "password": "test-password"},
        )
        assert init.status_code == 200
        yield client


def _login(client: TestClient) -> str:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert response.status_code == 200
    return response.json()["token"]


def test_login_sets_http_only_same_site_session_cookie(realtime_client: TestClient) -> None:
    _login(realtime_client)

    cookie = realtime_client.cookies.get("amh_session")
    assert cookie
    response = realtime_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    set_cookie = response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie


def test_websocket_uses_session_cookie_and_rejects_long_token_query(
    realtime_client: TestClient,
) -> None:
    token = _login(realtime_client)

    with realtime_client.websocket_connect("/ws/events") as websocket:
        assert websocket.receive_json()["event"] == "connected"

    with pytest.raises(WebSocketDisconnect):
        with realtime_client.websocket_connect(
            f"/ws/events?token={quote(token, safe='')}"
        ):
            pass


def test_realtime_ticket_is_single_use_without_session_cookie(
    realtime_client: TestClient,
) -> None:
    token = _login(realtime_client)
    ticket_response = realtime_client.post(
        "/api/auth/realtime-ticket",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert ticket_response.status_code == 200
    ticket = ticket_response.json()["ticket"]
    realtime_client.cookies.clear()
    url = f"/ws/events?ticket={quote(ticket, safe='')}"

    with realtime_client.websocket_connect(url) as websocket:
        assert websocket.receive_json()["event"] == "connected"

    with pytest.raises(WebSocketDisconnect):
        with realtime_client.websocket_connect(url):
            pass


def test_realtime_ticket_cannot_authorize_rest_api(realtime_client: TestClient) -> None:
    token = _login(realtime_client)
    ticket = realtime_client.post(
        "/api/auth/realtime-ticket",
        headers={"Authorization": f"Bearer {token}"},
    ).json()["ticket"]

    response = realtime_client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {ticket}"},
    )

    assert response.status_code == 401


def test_websocket_rejects_cross_origin_session_cookie(
    realtime_client: TestClient,
) -> None:
    _login(realtime_client)

    with pytest.raises(WebSocketDisconnect):
        with realtime_client.websocket_connect(
            "/ws/events",
            headers={"origin": "https://evil.example"},
        ):
            pass


def test_dashboard_does_not_put_session_token_in_realtime_urls() -> None:
    dashboard = Path("web/templates/dashboard.html").read_text(encoding="utf-8")

    assert "/ws/events?token=" not in dashboard
    assert "/api/events?token=" not in dashboard
