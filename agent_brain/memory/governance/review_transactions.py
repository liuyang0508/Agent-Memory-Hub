"""Preview-first, digest-bound review resolution transactions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.memory.governance.lifecycle_ledger import (
    lifecycle_transaction_lock,
)
from agent_brain.memory.governance.review_queue import (
    ACTIVE_REVIEW_TAGS,
    APPROVED_TAG,
    is_active_review_candidate,
    REJECTED_TAG,
    TERMINAL_REVIEW_TAGS,
)
from agent_brain.memory.store.durable_fs import SecureDirectory
from agent_brain.memory.store.items_store import ItemsStore


ReviewResolutionAction = Literal["approve", "reject"]


@dataclass(frozen=True)
class ReviewResolutionResult:
    action: ReviewResolutionAction
    item_id: str
    status: str
    reason: str
    dry_run: bool
    expected_sha256: str
    transaction_id: str | None = None
    confidence: float | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ReviewReceiptHealth:
    status: str = "healthy"
    record_count: int = 0
    incomplete_count: int = 0


def resolve_review_candidate(
    *,
    brain_dir: Path,
    store: ItemsStore,
    item_id: str,
    action: ReviewResolutionAction,
    confidence: float,
    apply: bool = False,
    expected_sha256: str | None = None,
) -> ReviewResolutionResult:
    """Preview or apply one review resolution bound to exact Markdown bytes."""

    raw, item = _read_exact_item(store, item_id)
    current_sha256 = hashlib.sha256(raw).hexdigest()
    if not is_active_review_candidate(item):
        return ReviewResolutionResult(
            action,
            item_id,
            "blocked",
            "NOT_ACTIVE_REVIEW_CANDIDATE",
            not apply,
            current_sha256,
        )
    bounded_confidence = min(1.0, max(0.0, float(confidence)))
    if not apply:
        return ReviewResolutionResult(
            action,
            item_id,
            "ready",
            "REVIEW_RESOLUTION_READY",
            True,
            current_sha256,
            confidence=bounded_confidence,
        )
    if expected_sha256 != current_sha256:
        return ReviewResolutionResult(
            action,
            item_id,
            "blocked",
            "REVIEW_RESOLUTION_CHANGED",
            False,
            current_sha256,
        )

    transaction_id = secrets.token_hex(16)
    intent_sha256 = hashlib.sha256(
        f"{action}\0{item_id}\0{bounded_confidence:.12g}".encode()
    ).hexdigest()
    with lifecycle_transaction_lock(brain_dir):
        with store.locked_catalog():
            with store.locked_items([item_id]) as locked:
                locked_raw = locked.read_bytes(item_id)
                locked_sha256 = hashlib.sha256(locked_raw).hexdigest()
                if locked_sha256 != expected_sha256:
                    return ReviewResolutionResult(
                        action,
                        item_id,
                        "blocked",
                        "REVIEW_RESOLUTION_CHANGED",
                        False,
                        locked_sha256,
                    )
                locked_item, _body = locked.get(item_id)
                if not is_active_review_candidate(locked_item):
                    return ReviewResolutionResult(
                        action,
                        item_id,
                        "blocked",
                        "NOT_ACTIVE_REVIEW_CANDIDATE",
                        False,
                        locked_sha256,
                    )
                _append_review_receipt(
                    brain_dir,
                    transaction_id=transaction_id,
                    state="prepared",
                    action=action,
                    item_id=item_id,
                    before_sha256=locked_sha256,
                    intent_sha256=intent_sha256,
                    after_sha256=None,
                )
                blocked_tags = ACTIVE_REVIEW_TAGS | TERMINAL_REVIEW_TAGS
                tags = {
                    tag for tag in locked_item.tags if tag.casefold() not in blocked_tags
                }
                if action == "approve":
                    updates: dict[str, object] = {
                        "tags": sorted({*tags, APPROVED_TAG}),
                        "confidence": bounded_confidence,
                    }
                else:
                    updates = {
                        "tags": sorted({*tags, REJECTED_TAG}),
                        "confidence": bounded_confidence,
                        "contradict_count": locked_item.contradict_count + 1,
                        "gain_score": min(locked_item.gain_score, -0.2),
                    }
                prepared = locked.prepare_update_frontmatter(item_id, **updates)
                locked.apply_prepared(prepared)
                updated = prepared.updated_item
                after_sha256 = hashlib.sha256(locked.read_bytes(item_id)).hexdigest()
                _append_review_receipt(
                    brain_dir,
                    transaction_id=transaction_id,
                    state="completed",
                    action=action,
                    item_id=item_id,
                    before_sha256=locked_sha256,
                    intent_sha256=intent_sha256,
                    after_sha256=after_sha256,
                )
    return ReviewResolutionResult(
        action,
        item_id,
        "applied",
        "REVIEW_RESOLUTION_APPLIED",
        False,
        current_sha256,
        transaction_id=transaction_id,
        confidence=updated.confidence,
    )


def _append_review_receipt(
    brain_dir: Path,
    *,
    transaction_id: str,
    state: str,
    action: str,
    item_id: str,
    before_sha256: str,
    intent_sha256: str,
    after_sha256: str | None,
) -> None:
    payload = (
        json.dumps(
            {
                "schema_version": 1,
                "transaction_id": transaction_id,
                "state": state,
                "action": action,
                "item_id": item_id,
                "before_sha256": before_sha256,
                "intent_sha256": intent_sha256,
                "after_sha256": after_sha256,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
    with SecureDirectory.open(Path(brain_dir)) as brain:
        with brain.child("runtime", create=True) as runtime:
            descriptor, created = runtime.open_or_create_file(
                "review-resolution-receipts.jsonl",
                os.O_WRONLY | os.O_APPEND,
            )
            try:
                os.fchmod(descriptor, 0o600)
                view = memoryview(payload)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise OSError("REVIEW_RECEIPT_WRITE_FAILED")
                    view = view[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            if created:
                runtime.fsync()


def read_review_receipt_health(brain_dir: Path) -> ReviewReceiptHealth:
    """Validate the bounded append-only receipt chain without mutating it."""

    try:
        with SecureDirectory.open(Path(brain_dir)) as brain:
            with brain.child("runtime") as runtime:
                descriptor, _ = runtime.open_file(
                    "review-resolution-receipts.jsonl",
                    os.O_RDONLY,
                )
                try:
                    opened = os.fstat(descriptor)
                    if opened.st_size > 16 * 1024 * 1024:
                        return ReviewReceiptHealth("corrupt")
                    with os.fdopen(descriptor, "rb") as handle:
                        descriptor = -1
                        raw_lines = handle.readlines()
                finally:
                    if descriptor >= 0:
                        os.close(descriptor)
    except FileNotFoundError:
        return ReviewReceiptHealth()
    except OSError:
        return ReviewReceiptHealth("unavailable")
    if len(raw_lines) > 100_000:
        return ReviewReceiptHealth("corrupt")
    prepared: dict[str, tuple[object, ...]] = {}
    completed: set[str] = set()
    for raw in raw_lines:
        if len(raw) > 1024 * 1024:
            return ReviewReceiptHealth("corrupt", len(raw_lines))
        try:
            record = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return ReviewReceiptHealth("corrupt", len(raw_lines))
        if not _valid_receipt_record(record):
            return ReviewReceiptHealth("corrupt", len(raw_lines))
        transaction_id = record["transaction_id"]
        binding = (
            record["action"],
            record["item_id"],
            record["before_sha256"],
            record["intent_sha256"],
        )
        if record["state"] == "prepared":
            if transaction_id in prepared or transaction_id in completed:
                return ReviewReceiptHealth("corrupt", len(raw_lines))
            prepared[transaction_id] = binding
        else:
            if (
                prepared.get(transaction_id) != binding
                or transaction_id in completed
            ):
                return ReviewReceiptHealth("corrupt", len(raw_lines))
            completed.add(transaction_id)
    incomplete = len(set(prepared) - completed)
    return ReviewReceiptHealth(
        "incomplete" if incomplete else "healthy",
        len(raw_lines),
        incomplete,
    )


def _valid_receipt_record(record: object) -> bool:
    if not isinstance(record, dict) or set(record) != {
        "schema_version",
        "transaction_id",
        "state",
        "action",
        "item_id",
        "before_sha256",
        "intent_sha256",
        "after_sha256",
        "timestamp",
    }:
        return False
    digest = r"[0-9a-f]{64}"
    return (
        record["schema_version"] == 1
        and isinstance(record["transaction_id"], str)
        and re.fullmatch(r"[0-9a-f]{32}", record["transaction_id"]) is not None
        and isinstance(record["state"], str)
        and record["state"] in {"prepared", "completed"}
        and isinstance(record["action"], str)
        and record["action"] in {"approve", "reject"}
        and isinstance(record["item_id"], str)
        and isinstance(record["before_sha256"], str)
        and isinstance(record["intent_sha256"], str)
        and re.fullmatch(digest, record["before_sha256"]) is not None
        and re.fullmatch(digest, record["intent_sha256"]) is not None
        and (
            record["after_sha256"] is None
            if record["state"] == "prepared"
            else (
                isinstance(record["after_sha256"], str)
                and re.fullmatch(digest, record["after_sha256"]) is not None
            )
        )
        and isinstance(record["timestamp"], str)
    )


def _read_exact_item(
    store: ItemsStore,
    item_id: str,
) -> tuple[bytes, MemoryItem]:
    with store.locked_catalog():
        with store.locked_items([item_id]) as locked:
            raw = locked.read_bytes(item_id)
            item, _body = locked.get(item_id)
            return raw, item


__all__ = [
    "ReviewResolutionAction",
    "ReviewReceiptHealth",
    "ReviewResolutionResult",
    "read_review_receipt_health",
    "resolve_review_candidate",
]
