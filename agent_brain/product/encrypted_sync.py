"""End-to-end encrypted, append-only memory object synchronization."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.memory.governance.audit.scanner import audit_memory_text
from agent_brain.memory.store.item_markdown import render_item_markdown
from agent_brain.memory.store.items_store import ItemsStore


SYNC_FORMAT = 1
MAX_SYNC_OBJECT_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class SyncConfig:
    server: str
    api_key: str
    device_id: str
    device_name: str

    def public_dict(self) -> dict[str, str]:
        return {
            "server": self.server,
            "device_id": self.device_id,
            "device_name": self.device_name,
        }


@dataclass(frozen=True)
class EncryptedSyncObject:
    object_key: str
    payload: str
    device_id: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class SyncApplyReport:
    received: int
    decrypted: int
    imported: int
    updated: int
    conflicts: int
    skipped: int
    blocked: int

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def generate_recovery_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")


def decode_recovery_key(value: str) -> bytes:
    try:
        key = base64.urlsafe_b64decode(value.strip().encode("ascii"))
    except Exception as exc:
        raise ValueError("invalid recovery key") from exc
    if len(key) != 32:
        raise ValueError("invalid recovery key")
    return key


def sync_dir(brain_dir: Path) -> Path:
    return Path(brain_dir) / "sync"


def save_sync_setup(brain_dir: Path, config: SyncConfig, recovery_key: str) -> None:
    root = sync_dir(brain_dir)
    root.mkdir(parents=True, exist_ok=True)
    _atomic_private_json(root / "config.json", asdict(config))
    _atomic_private_text(root / "key", recovery_key.strip() + "\n")


def load_sync_config(brain_dir: Path) -> SyncConfig:
    path = sync_dir(brain_dir) / "config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return SyncConfig(
            server=str(data["server"]).rstrip("/"),
            api_key=str(data["api_key"]),
            device_id=str(data["device_id"]),
            device_name=str(data["device_name"]),
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("sync is not initialized; run `memory sync init`") from exc


def load_sync_key(brain_dir: Path) -> bytes:
    override = os.environ.get("AGENT_MEMORY_HUB_SYNC_KEY")
    if override:
        return decode_recovery_key(override)
    try:
        return decode_recovery_key((sync_dir(brain_dir) / "key").read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError("sync key missing; run `memory sync init`") from exc


def encrypt_items(
    store: ItemsStore,
    *,
    key: bytes,
    device_id: str,
) -> list[EncryptedSyncObject]:
    rows: list[EncryptedSyncObject] = []
    for item, body in store.iter_all():
        record = _canonical_record(item, body)
        digest = hashlib.sha256(record).hexdigest()
        object_key = hmac.new(
            key,
            f"{item.id}:{digest}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        nonce = secrets.token_bytes(12)
        aad = f"amh-sync-v{SYNC_FORMAT}:{object_key}".encode("ascii")
        ciphertext = AESGCM(key).encrypt(nonce, record, aad)
        envelope = {
            "v": SYNC_FORMAT,
            "alg": "A256GCM",
            "nonce": _b64(nonce),
            "ciphertext": _b64(ciphertext),
        }
        payload = json.dumps(envelope, separators=(",", ":"), sort_keys=True)
        if len(payload.encode("utf-8")) > MAX_SYNC_OBJECT_BYTES:
            continue
        rows.append(EncryptedSyncObject(object_key, payload, device_id))
    return rows


def decrypt_object(row: EncryptedSyncObject, *, key: bytes) -> tuple[MemoryItem, str, str]:
    if not re_full_hex(row.object_key):
        raise ValueError("invalid sync object key")
    try:
        envelope = json.loads(row.payload)
        if envelope.get("v") != SYNC_FORMAT or envelope.get("alg") != "A256GCM":
            raise ValueError("unsupported sync envelope")
        nonce = _unb64(envelope["nonce"])
        ciphertext = _unb64(envelope["ciphertext"])
        if len(nonce) != 12 or len(ciphertext) > MAX_SYNC_OBJECT_BYTES:
            raise ValueError("invalid sync envelope")
        aad = f"amh-sync-v{SYNC_FORMAT}:{row.object_key}".encode("ascii")
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, aad)
        record = json.loads(plaintext)
        item = MemoryItem.model_validate(record["frontmatter"])
        body = str(record["body"])
    except (InvalidTag, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("sync object authentication failed") from exc
    digest = hashlib.sha256(_canonical_record(item, body)).hexdigest()
    expected = hmac.new(
        key,
        f"{item.id}:{digest}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, row.object_key):
        raise ValueError("sync object key mismatch")
    return item, body, digest


def apply_encrypted_objects(
    store: ItemsStore,
    rows: list[EncryptedSyncObject],
    *,
    key: bytes,
    index: Any = None,
    embedder: Any = None,
) -> SyncApplyReport:
    candidates: dict[str, tuple[MemoryItem, str, str]] = {}
    decrypted = blocked = 0
    for row in rows:
        try:
            item, body, digest = decrypt_object(row, key=key)
        except ValueError:
            blocked += 1
            continue
        decrypted += 1
        current = candidates.get(item.id)
        if current is None or _winner_key(item, digest) > _winner_key(current[0], current[2]):
            candidates[item.id] = (item, body, digest)

    imported = updated = conflicts = skipped = 0
    for item_id in sorted(candidates):
        remote_item, remote_body, remote_digest = candidates[item_id]
        if not audit_memory_text(
            f"{remote_item.title}\n{remote_item.summary}\n{remote_body}"
        ).passed:
            blocked += 1
            continue
        try:
            local_item, local_body = store.get(item_id)
        except FileNotFoundError:
            store.write(remote_item, remote_body)
            _upsert(index, embedder, remote_item, remote_body)
            imported += 1
            continue
        local_digest = hashlib.sha256(_canonical_record(local_item, local_body)).hexdigest()
        if local_digest == remote_digest:
            skipped += 1
            continue

        if _winner_key(remote_item, remote_digest) > _winner_key(local_item, local_digest):
            _preserve_conflict(store, local_item, local_body, local_digest, index, embedder)
            store.restore_raw(
                item_id,
                render_item_markdown(remote_item, remote_body).encode("utf-8"),
            )
            _upsert(index, embedder, remote_item, remote_body)
            updated += 1
        else:
            _preserve_conflict(
                store,
                remote_item,
                remote_body,
                remote_digest,
                index,
                embedder,
            )
        conflicts += 1

    return SyncApplyReport(
        received=len(rows),
        decrypted=decrypted,
        imported=imported,
        updated=updated,
        conflicts=conflicts,
        skipped=skipped,
        blocked=blocked,
    )


def push_objects(config: SyncConfig, rows: list[EncryptedSyncObject]) -> dict[str, int]:
    result = _request(config, "POST", "/api/sync/objects", {"objects": [r.to_dict() for r in rows]})
    return {
        "accepted": int(result.get("accepted", 0)),
        "existing": int(result.get("existing", 0)),
    }


def pull_objects(config: SyncConfig) -> list[EncryptedSyncObject]:
    result = _request(config, "GET", "/api/sync/objects")
    rows = result.get("objects")
    if not isinstance(rows, list):
        raise ValueError("invalid sync server response")
    return [
        EncryptedSyncObject(
            object_key=str(row["object_key"]),
            payload=str(row["payload"]),
            device_id=str(row.get("device_id") or "unknown"),
        )
        for row in rows
        if isinstance(row, dict)
    ]


def send_heartbeat(config: SyncConfig, *, object_count: int) -> bool:
    """Report device liveness without sending memory plaintext or recovery keys."""

    from agent_brain import __version__

    _request(
        config,
        "POST",
        "/api/sync/heartbeat",
        {
            "device_id": config.device_id,
            "device_name": config.device_name,
            "client_version": __version__,
            "object_count": max(0, object_count),
        },
    )
    return True


def _request(
    config: SyncConfig,
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
) -> dict[str, Any]:
    url = config.server + path
    body = None
    headers = {
        "Accept": "application/json",
        "X-API-Key": os.environ.get("AGENT_MEMORY_HUB_SYNC_API_KEY", config.api_key),
    }
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(MAX_SYNC_OBJECT_BYTES * 16)
    except urllib.error.HTTPError as exc:
        raise ValueError(f"sync server rejected request: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"sync server unavailable: {exc.reason}") from exc
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid sync server response") from exc
    if not isinstance(result, dict):
        raise ValueError("invalid sync server response")
    return result


def _canonical_record(item: MemoryItem, body: str) -> bytes:
    return json.dumps(
        {"frontmatter": item.model_dump(mode="json"), "body": body},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _winner_key(item: MemoryItem, digest: str) -> tuple[int, str, str]:
    return item.version, item.created_at.astimezone(timezone.utc).isoformat(), digest


def _preserve_conflict(
    store: ItemsStore,
    item: MemoryItem,
    body: str,
    digest: str,
    index: Any,
    embedder: Any,
) -> None:
    created = item.created_at.astimezone(timezone.utc)
    conflict_id = (
        f"mem-{created:%Y%m%d-%H%M%S}-sync-conflict-{digest[:12]}"
    )
    refs = item.refs.model_copy(deep=True)
    if item.id not in refs.mems:
        refs.mems.append(item.id)
    conflict = item.model_copy(
        update={
            "id": conflict_id,
            "title": f"[sync conflict] {item.title}"[:200],
            "tags": sorted({*item.tags, "sync-conflict", "needs-review"}),
            "refs": refs,
            "version": 1,
        },
        deep=True,
    )
    try:
        store.write(conflict, body)
    except FileExistsError:
        return
    _upsert(index, embedder, conflict, body)


def _upsert(index: Any, embedder: Any, item: MemoryItem, body: str) -> None:
    if index is None:
        return
    embedding = None
    if embedder is not None:
        embedding = embedder.embed(item.context_views.locator or item.summary)
    index.upsert(item, body, embedding=embedding)


def _atomic_private_json(path: Path, data: dict[str, object]) -> None:
    _atomic_private_text(
        path,
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _atomic_private_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def _unb64(value: object) -> bytes:
    if not isinstance(value, str):
        raise ValueError("invalid base64")
    return base64.urlsafe_b64decode(value.encode("ascii"))


def re_full_hex(value: str) -> bool:
    return len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


__all__ = [
    "EncryptedSyncObject",
    "SyncApplyReport",
    "SyncConfig",
    "apply_encrypted_objects",
    "decode_recovery_key",
    "encrypt_items",
    "generate_recovery_key",
    "load_sync_config",
    "load_sync_key",
    "pull_objects",
    "push_objects",
    "save_sync_setup",
    "send_heartbeat",
]
