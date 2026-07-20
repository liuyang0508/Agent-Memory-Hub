from __future__ import annotations

import json
import os
import stat

import pytest

from agent_brain.memory.governance import pending_receipts as receipts_module


PREPARED_AT = "2026-07-20T12:00:00+00:00"
COMPLETED_AT = "2026-07-20T12:00:01+00:00"


def _prepared():
    return receipts_module.prepare_pending_receipt(
        selection_mode="explicit",
        requested_count=1,
        selected=[
            receipts_module.PendingReceiptSelection(
                record_id="PRIVATE_RECORD_ID_CANARY",
                payload_sha256="a" * 64,
            )
        ],
        depth_before=3,
        batch_id="b" * 32,
        prepared_at=PREPARED_AT,
    )


def _completed():
    return receipts_module.complete_pending_receipt(
        _prepared(),
        outcomes=[
            receipts_module.PendingReceiptOutcome(
                record_id="PRIVATE_RECORD_ID_CANARY",
                status="written",
                classification="ready",
                reason="WRITTEN",
                index_repair_required=False,
                warnings=(),
            )
        ],
        depth_after=2,
        completed_at=COMPLETED_AT,
    )


def test_pending_receipt_is_fixed_schema_deterministic_and_low_sensitivity():
    prepared = _prepared()
    repeated = _prepared()
    payload = prepared.to_dict()
    encoded = json.dumps(payload, sort_keys=True)

    assert set(payload) == receipts_module.PENDING_RECEIPT_FIELDS
    assert payload["schema_version"] == 1
    assert payload["state"] == "prepared"
    assert payload["batch_digest"] == repeated.batch_digest
    assert len(str(payload["batch_digest"])) == 64
    assert "PRIVATE_RECORD_ID_CANARY" not in encoded
    assert "record_id" not in encoded
    assert "item_id" not in encoded
    assert "title" not in encoded
    assert "summary" not in encoded
    assert "path" not in encoded


def test_pending_receipt_completion_contains_only_aggregate_outcomes():
    completed = _completed()
    payload = completed.to_dict()
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["state"] == "completed"
    assert payload["depth_before"] == 3
    assert payload["depth_after"] == 2
    assert payload["status_counts"] == {"written": 1}
    assert payload["classification_counts"] == {"ready": 1}
    assert payload["reason_counts"] == {"WRITTEN": 1}
    assert len(str(payload["result_digest"])) == 64
    assert "PRIVATE_RECORD_ID_CANARY" not in encoded


def test_pending_receipt_completion_includes_bounded_batch_warnings():
    completed = receipts_module.complete_pending_receipt(
        _prepared(),
        outcomes=[],
        depth_after=3,
        completed_at=COMPLETED_AT,
        batch_warnings=("PENDING_LOCK_GC_TRUNCATED",),
    )

    assert completed.warning_counts == {"PENDING_LOCK_GC_TRUNCATED": 1}


def test_pending_receipt_ledger_is_durable_private_and_reports_incomplete(tmp_brain):
    prepared = _prepared()
    receipts_module.append_pending_receipt(tmp_brain, prepared)

    ledger = tmp_brain / "runtime" / "pending-apply-receipts.jsonl"
    first_health = receipts_module.read_pending_receipt_ledger_health(tmp_brain)

    assert stat.S_IMODE(os.lstat(ledger).st_mode) == 0o600
    assert first_health.status == "healthy"
    assert first_health.record_count == 1
    assert first_health.incomplete_count == 1

    receipts_module.append_pending_receipt(tmp_brain, _completed())
    second_health = receipts_module.read_pending_receipt_ledger_health(tmp_brain)

    assert second_health.status == "healthy"
    assert second_health.record_count == 2
    assert second_health.incomplete_count == 0


def test_pending_receipt_append_rolls_back_partial_line(tmp_brain, monkeypatch):
    receipts_module.append_pending_receipt(tmp_brain, _prepared())
    ledger = tmp_brain / "runtime" / "pending-apply-receipts.jsonl"
    before = ledger.read_bytes()

    def partial_then_fail(descriptor: int, payload: bytes) -> None:
        os.write(descriptor, payload[:7])
        raise OSError("simulated partial append")

    monkeypatch.setattr(receipts_module, "_write_all", partial_then_fail)

    with pytest.raises(OSError, match="simulated partial append"):
        receipts_module.append_pending_receipt(tmp_brain, _completed())

    assert ledger.read_bytes() == before
    assert receipts_module.read_pending_receipt_ledger_health(tmp_brain).status == "healthy"


def test_pending_receipt_health_fails_closed_on_malformed_ledger(tmp_brain):
    runtime = tmp_brain / "runtime"
    runtime.mkdir(parents=True)
    ledger = runtime / "pending-apply-receipts.jsonl"
    ledger.write_text("{bad json\n", encoding="utf-8")
    os.chmod(ledger, 0o600)

    before = ledger.read_bytes()
    health = receipts_module.read_pending_receipt_ledger_health(tmp_brain)

    assert health.status == "corrupt"
    assert health.record_count == 0
    assert ledger.read_bytes() == before


def test_pending_receipt_append_refuses_corrupt_existing_ledger(tmp_brain):
    runtime = tmp_brain / "runtime"
    runtime.mkdir(parents=True)
    ledger = runtime / "pending-apply-receipts.jsonl"
    ledger.write_text("{bad json\n", encoding="utf-8")
    os.chmod(ledger, 0o600)
    before = ledger.read_bytes()

    with pytest.raises(OSError, match="PENDING_RECEIPT_LEDGER_CORRUPT"):
        receipts_module.append_pending_receipt(tmp_brain, _prepared())

    assert ledger.read_bytes() == before
