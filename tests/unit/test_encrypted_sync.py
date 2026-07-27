from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.product.encrypted_sync import (
    apply_encrypted_objects,
    decode_recovery_key,
    encrypt_items,
    generate_recovery_key,
)


def _item(item_id: str, body: str, *, version: int = 1) -> tuple[MemoryItem, str]:
    return (
        MemoryItem(
            id=item_id,
            type=MemoryType.fact,
            created_at=datetime(2026, 7, 28, 4, 0, tzinfo=timezone.utc),
            title="Encrypted device memory",
            summary="A private value shared between devices",
            version=version,
        ),
        body,
    )


def test_encrypted_objects_hide_plaintext_and_reject_wrong_key(tmp_path: Path) -> None:
    store = ItemsStore(tmp_path / "items")
    item, body = _item(
        "mem-20260728-040000-encrypted",
        "never visible to the sync server",
    )
    store.write(item, body)
    key = decode_recovery_key(generate_recovery_key())

    rows = encrypt_items(store, key=key, device_id="dev-a")

    assert len(rows) == 1
    assert item.title not in rows[0].payload
    assert body not in rows[0].payload
    target = ItemsStore(tmp_path / "other" / "items")
    report = apply_encrypted_objects(target, rows, key=key)
    assert report.imported == 1
    imported_item, imported_body = target.get(item.id)
    assert imported_item == item
    assert imported_body.rstrip() == body
    wrong = decode_recovery_key(generate_recovery_key())
    assert apply_encrypted_objects(
        ItemsStore(tmp_path / "wrong" / "items"),
        rows,
        key=wrong,
    ).blocked == 1


def test_two_devices_converge_and_preserve_equal_version_conflict(tmp_path: Path) -> None:
    key = decode_recovery_key(generate_recovery_key())
    first = ItemsStore(tmp_path / "first" / "items")
    second = ItemsStore(tmp_path / "second" / "items")
    item_id = "mem-20260728-040001-conflict"
    first.write(*_item(item_id, "device A body"))
    second.write(*_item(item_id, "device B body"))

    cloud = [
        *encrypt_items(first, key=key, device_id="dev-a"),
        *encrypt_items(second, key=key, device_id="dev-b"),
    ]
    first_report = apply_encrypted_objects(first, cloud, key=key)
    second_report = apply_encrypted_objects(second, cloud, key=key)
    assert first_report.conflicts + second_report.conflicts >= 1

    cloud.extend(encrypt_items(first, key=key, device_id="dev-a"))
    cloud.extend(encrypt_items(second, key=key, device_id="dev-b"))
    apply_encrypted_objects(first, cloud, key=key)
    apply_encrypted_objects(second, cloud, key=key)

    first_rows = {(item.id, body) for item, body in first.iter_all()}
    second_rows = {(item.id, body) for item, body in second.iter_all()}
    assert first_rows == second_rows
    assert len(first_rows) == 2
    assert any("sync-conflict" in item_id for item_id, _body in first_rows)


def test_cloud_sync_storage_is_tenant_scoped_and_opaque(tmp_path: Path) -> None:
    os.environ["BRAIN_DIR"] = str(tmp_path)
    os.environ["MEMORY_HUB_RATE_LIMIT"] = "0"
    try:
        from web.app import app

        with TestClient(app) as client:
            admin = client.post(
                "/api/auth/init",
                json={"username": "admin", "password": "pw"},
            )
            admin_token = admin.json()["token"]
            client.post(
                "/api/auth/register",
                json={
                    "username": "alice",
                    "password": "pw",
                    "tenant_id": "org-a",
                },
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            client.post(
                "/api/auth/register",
                json={
                    "username": "bob",
                    "password": "pw",
                    "tenant_id": "org-b",
                },
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            alice = client.post(
                "/api/auth/login",
                json={"username": "alice", "password": "pw"},
            ).json()["token"]
            bob = client.post(
                "/api/auth/login",
                json={"username": "bob", "password": "pw"},
            ).json()["token"]
            row = {
                "object_key": "a" * 64,
                "payload": '{"v":1,"alg":"A256GCM","ciphertext":"opaque"}',
                "device_id": "dev-a",
            }
            pushed = client.post(
                "/api/sync/objects",
                json={"objects": [row]},
                headers={"Authorization": f"Bearer {alice}"},
            )
            assert pushed.status_code == 200
            own = client.get(
                "/api/sync/objects",
                headers={"Authorization": f"Bearer {alice}"},
            ).json()
            other = client.get(
                "/api/sync/objects",
                headers={"Authorization": f"Bearer {bob}"},
            ).json()
            assert own["objects"][0]["payload"] == row["payload"]
            assert other == {"objects": [], "count": 0}
    finally:
        os.environ.pop("BRAIN_DIR", None)
        os.environ.pop("MEMORY_HUB_RATE_LIMIT", None)
