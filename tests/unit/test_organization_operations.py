from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from web.state_store import WebStateStore


@pytest.fixture()
def organization_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path))
    monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")
    monkeypatch.setenv("MEMORY_HUB_RATE_LIMIT", "0")
    from web.app import app

    with TestClient(app) as client:
        yield client


def _bootstrap(client: TestClient) -> str:
    response = client.post(
        "/api/auth/init",
        json={"username": "admin", "password": "pw"},
    )
    assert response.status_code == 200
    return response.json()["token"]


def _register(
    client: TestClient,
    admin_token: str,
    username: str,
    *,
    tenant_id: str = "default",
) -> str:
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": "pw", "tenant_id": tenant_id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    return client.post(
        "/api/auth/login",
        json={"username": username, "password": "pw"},
    ).json()["token"]


def test_live_org_roles_enforce_owner_admin_member_viewer(
    organization_client: TestClient,
) -> None:
    client = organization_client
    admin_token = _bootstrap(client)
    alice_token = _register(client, admin_token, "alice")
    bob_token = _register(client, admin_token, "bob")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    alice_headers = {"Authorization": f"Bearer {alice_token}"}
    bob_headers = {"Authorization": f"Bearer {bob_token}"}

    promoted = client.patch(
        "/api/organization/members/alice",
        json={"role": "admin"},
        headers=admin_headers,
    )
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "admin"
    viewed = client.post(
        "/api/organization/members",
        json={"username": "bob", "role": "viewer"},
        headers=alice_headers,
    )
    assert viewed.status_code == 200

    assert client.get("/api/organization", headers=bob_headers).status_code == 200
    assert client.get("/api/sync/objects", headers=bob_headers).status_code == 200
    denied = client.post(
        "/api/sync/objects",
        json={"objects": []},
        headers=bob_headers,
    )
    assert denied.status_code == 403
    denied_memory_write = client.post(
        "/api/items",
        json={
            "type": "fact",
            "title": "Viewer must not write",
            "summary": "Organization viewers are read-only",
        },
        headers=bob_headers,
    )
    assert denied_memory_write.status_code == 403
    denied_role_change = client.patch(
        "/api/organization/members/bob",
        json={"role": "member"},
        headers=alice_headers,
    )
    assert denied_role_change.status_code == 403
    removed = client.delete(
        "/api/organization/members/bob",
        headers=admin_headers,
    )
    assert removed.status_code == 200
    assert client.get("/api/organization", headers=bob_headers).status_code == 403


def test_operations_summary_is_tenant_scoped_and_never_returns_ciphertext(
    organization_client: TestClient,
) -> None:
    client = organization_client
    admin_token = _bootstrap(client)
    outsider_token = _register(client, admin_token, "outsider", tenant_id="org-b")
    headers = {"Authorization": f"Bearer {admin_token}"}
    outsider_headers = {"Authorization": f"Bearer {outsider_token}"}
    heartbeat = client.post(
        "/api/sync/heartbeat",
        json={
            "device_id": "dev-test",
            "device_name": "workstation",
            "client_version": "2.0",
            "object_count": 1,
        },
        headers=headers,
    )
    assert heartbeat.status_code == 200
    pushed = client.post(
        "/api/sync/objects",
        json={
            "objects": [
                {
                    "object_key": "a" * 64,
                    "payload": '{"ciphertext":"opaque-secret-value"}',
                    "device_id": "dev-test",
                }
            ]
        },
        headers=headers,
    )
    assert pushed.status_code == 200

    summary = client.get("/api/operations/summary", headers=headers)
    assert summary.status_code == 200
    data = summary.json()
    assert data["organization"]["id"] == "default"
    assert data["organization"]["current_role"] == "owner"
    assert data["sync"]["objects"] == 1
    assert data["sync"]["devices"] == 1
    assert data["sync"]["active_24h"] == 1
    assert data["security"]["server_plaintext"] is False
    assert "opaque-secret-value" not in summary.text

    outsider = client.get("/api/operations/summary", headers=outsider_headers).json()
    assert outsider["organization"]["id"] == "org-b"
    assert outsider["sync"]["objects"] == 0
    assert outsider["sync"]["devices"] == 0


def test_organization_memberships_survive_store_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "web_state.db"
    first = WebStateStore(db_path)
    first.ensure_organization("team-a", created_by="alice", name="Team A")
    first.set_org_member("team-a", "alice", "owner")
    first.close()

    second = WebStateStore(db_path)
    assert second.ensure_organization("team-a", created_by="ignored")["name"] == "Team A"
    assert second.get_org_member("team-a", "alice")["role"] == "owner"
    second.close()
