from __future__ import annotations

import json
import os
import stat
from dataclasses import replace

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


def _resolution_prepared(
    *,
    second_action="accept_duplicate",
    target_digest="c" * 64,
):
    return receipts_module.prepare_pending_receipt(
        selection_mode="resolution",
        requested_count=2,
        selected=[
            receipts_module.PendingReceiptSelection(
                record_id="pending-audit-one",
                payload_sha256="a" * 64,
                action="approve_audit",
            ),
            receipts_module.PendingReceiptSelection(
                record_id="pending-duplicate-two",
                payload_sha256="b" * 64,
                action=second_action,
                target_digest=target_digest,
            ),
        ],
        depth_before=2,
        batch_id="c" * 32,
        prepared_at=PREPARED_AT,
    )


def _resolution_outcomes():
    return [
        receipts_module.PendingReceiptOutcome(
            record_id="pending-audit-one",
            status="applied",
            classification="ready",
            reason="PENDING_RESOLUTION_READY",
            index_repair_required=False,
        ),
        receipts_module.PendingReceiptOutcome(
            record_id="pending-duplicate-two",
            status="blocked",
            classification="audit_blocked",
            reason="PENDING_AUDIT_APPROVAL_REQUIRED",
            index_repair_required=True,
        ),
    ]


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


def test_pending_resolution_receipt_binds_actions_without_exposing_inputs():
    prepared = _resolution_prepared()
    changed_action = _resolution_prepared(second_action="convert_type")
    changed_target = _resolution_prepared(target_digest="d" * 64)
    encoded = json.dumps(prepared.to_dict(), sort_keys=True)

    assert prepared.selection_mode == "resolution"
    assert prepared.batch_digest != changed_action.batch_digest
    assert prepared.batch_digest != changed_target.batch_digest
    assert prepared.action_counts == {
        "accept_duplicate": 1,
        "approve_audit": 1,
    }
    assert "pending-audit-one" not in encoded
    assert "pending-duplicate-two" not in encoded
    assert "c" * 64 not in encoded
    assert "record_id" not in encoded
    assert "target_digest" not in encoded


def test_pending_resolution_batch_digest_binds_selection_order():
    forward = _resolution_prepared()
    reverse = receipts_module.prepare_pending_receipt(
        selection_mode="resolution",
        requested_count=2,
        selected=[
            receipts_module.PendingReceiptSelection(
                record_id="pending-duplicate-two",
                payload_sha256="b" * 64,
                action="accept_duplicate",
                target_digest="c" * 64,
            ),
            receipts_module.PendingReceiptSelection(
                record_id="pending-audit-one",
                payload_sha256="a" * 64,
                action="approve_audit",
            ),
        ],
        depth_before=2,
        batch_id="c" * 32,
        prepared_at=PREPARED_AT,
    )

    assert forward.batch_digest == _resolution_prepared().batch_digest
    assert forward.batch_digest != reverse.batch_digest
    assert forward.action_counts == reverse.action_counts


def test_pending_explicit_and_safe_only_batch_digest_remains_stable():
    expected = "e210e57ed7cb866e0a8aadfcaab3960c7a37ac864be3e32d169710d9fcb4c100"
    selected = [
        receipts_module.PendingReceiptSelection(
            record_id="PRIVATE_RECORD_ID_CANARY",
            payload_sha256="a" * 64,
        )
    ]

    explicit = receipts_module.prepare_pending_receipt(
        selection_mode="explicit",
        requested_count=1,
        selected=selected,
        depth_before=3,
        batch_id="b" * 32,
        prepared_at=PREPARED_AT,
    )
    safe_only = receipts_module.prepare_pending_receipt(
        selection_mode="safe_only",
        requested_count=1,
        selected=selected,
        depth_before=3,
        batch_id="b" * 32,
        prepared_at=PREPARED_AT,
    )

    assert explicit.batch_digest == expected
    assert safe_only.batch_digest == expected
    assert explicit.action_counts == {}
    assert safe_only.action_counts == {}


def test_pending_explicit_and_safe_only_result_digest_remains_stable():
    expected = "e00b145c8475a5023ca6935c3821898cbb11300192a0cebac159dad7e3690ca6"
    outcome = receipts_module.PendingReceiptOutcome(
        record_id="PRIVATE_RECORD_ID_CANARY",
        status="written",
        classification="ready",
        reason="WRITTEN",
        index_repair_required=False,
        warnings=(),
    )
    safe_only = receipts_module.prepare_pending_receipt(
        selection_mode="safe_only",
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

    assert _completed().result_digest == expected
    assert (
        receipts_module.complete_pending_receipt(
            safe_only,
            outcomes=[outcome],
            depth_after=2,
            completed_at=COMPLETED_AT,
        ).result_digest
        == expected
    )


@pytest.mark.parametrize(
    ("selection_mode", "action", "target_digest"),
    [
        ("explicit", "approve_audit", None),
        ("safe_only", "apply", "c" * 64),
        ("resolution", "apply", None),
        ("resolution", "approve_audit", "c" * 64),
        ("resolution", "accept_duplicate", None),
        ("resolution", "convert_type", "C" * 64),
        ("resolution", "not_an_action", None),
    ],
)
def test_pending_receipt_rejects_invalid_action_target_combinations(
    selection_mode,
    action,
    target_digest,
):
    with pytest.raises(TypeError, match="INVALID_PENDING_RECEIPT_SELECTION"):
        receipts_module.prepare_pending_receipt(
            selection_mode=selection_mode,
            requested_count=1,
            selected=[
                receipts_module.PendingReceiptSelection(
                    record_id="pending-one",
                    payload_sha256="a" * 64,
                    action=action,
                    target_digest=target_digest,
                )
            ],
            depth_before=1,
        )


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


def test_pending_resolution_result_digest_binds_outcome_order():
    prepared = _resolution_prepared()
    outcomes = _resolution_outcomes()
    forward = receipts_module.complete_pending_receipt(
        prepared,
        outcomes=outcomes,
        depth_after=2,
        completed_at=COMPLETED_AT,
    )
    repeated = receipts_module.complete_pending_receipt(
        prepared,
        outcomes=outcomes,
        depth_after=2,
        completed_at=COMPLETED_AT,
    )
    reverse = receipts_module.complete_pending_receipt(
        prepared,
        outcomes=reversed(outcomes),
        depth_after=2,
        completed_at=COMPLETED_AT,
    )

    assert forward.result_digest == repeated.result_digest
    assert forward.result_digest != reverse.result_digest
    assert forward.status_counts == reverse.status_counts
    assert forward.reason_counts == reverse.reason_counts


def test_pending_receipt_completion_includes_bounded_batch_warnings():
    completed = receipts_module.complete_pending_receipt(
        _prepared(),
        outcomes=[],
        depth_after=3,
        completed_at=COMPLETED_AT,
        batch_warnings=("PENDING_LOCK_GC_TRUNCATED",),
    )

    assert completed.warning_counts == {"PENDING_LOCK_GC_TRUNCATED": 1}


def test_pending_resolution_completion_rejects_missing_outcomes():
    with pytest.raises(TypeError, match="INVALID_PENDING_BATCH_RECEIPT"):
        receipts_module.complete_pending_receipt(
            _resolution_prepared(),
            outcomes=[],
            depth_after=2,
            completed_at=COMPLETED_AT,
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("status_counts", {"applied": 1}),
        ("classification_counts", {"ready": 1}),
        ("reason_counts", {"PENDING_RESOLUTION_READY": 1}),
        ("index_repair_required_count", 3),
    ],
)
def test_pending_resolution_completion_rejects_mismatched_aggregates(
    tmp_brain,
    field_name,
    value,
):
    prepared = _resolution_prepared()
    completed = receipts_module.complete_pending_receipt(
        prepared,
        outcomes=_resolution_outcomes(),
        depth_after=2,
        completed_at=COMPLETED_AT,
    )

    receipts_module.append_pending_receipt(tmp_brain, prepared)
    with pytest.raises(TypeError, match="INVALID_PENDING_BATCH_RECEIPT"):
        receipts_module.append_pending_receipt(
            tmp_brain,
            replace(completed, **{field_name: value}),
        )


def test_pending_resolution_rejects_zero_action_count(tmp_brain):
    prepared = replace(
        _resolution_prepared(),
        action_counts={"accept_duplicate": 0, "approve_audit": 2},
    )

    with pytest.raises(TypeError, match="INVALID_PENDING_BATCH_RECEIPT"):
        receipts_module.append_pending_receipt(tmp_brain, prepared)


def test_pending_resolution_rejects_empty_current_batch():
    with pytest.raises(TypeError, match="INVALID_PENDING_BATCH_RECEIPT"):
        receipts_module.prepare_pending_receipt(
            selection_mode="resolution",
            requested_count=0,
            selected=[],
            depth_before=0,
        )


@pytest.mark.parametrize(
    ("requested_count", "selected_count"),
    [(0, 1), (1, 2)],
)
def test_pending_resolution_prepare_rejects_more_selected_than_requested(
    requested_count,
    selected_count,
):
    selected = [
        receipts_module.PendingReceiptSelection(
            record_id=f"pending-{index}",
            payload_sha256=f"{index + 1:064x}",
            action="approve_audit",
        )
        for index in range(selected_count)
    ]

    with pytest.raises(TypeError, match="INVALID_PENDING_BATCH_RECEIPT"):
        receipts_module.prepare_pending_receipt(
            selection_mode="resolution",
            requested_count=requested_count,
            selected=selected,
            depth_before=selected_count,
        )


@pytest.mark.parametrize("state", ["prepared", "completed", "incomplete"])
def test_pending_resolution_rejects_impossible_counts_in_every_state(state):
    prepared = _resolution_prepared()
    receipt = {
        "prepared": prepared,
        "completed": receipts_module.complete_pending_receipt(
            prepared,
            outcomes=_resolution_outcomes(),
            depth_after=0,
            completed_at=COMPLETED_AT,
        ),
        "incomplete": receipts_module.incomplete_pending_receipt(prepared),
    }[state]

    assert receipts_module._valid_receipt(  # noqa: SLF001 - validator contract.
        replace(receipt, requested_count=1)
    ) is False


def test_pending_resolution_parser_and_ledger_reject_impossible_counts(tmp_brain):
    payload = _resolution_prepared().to_dict()
    payload["requested_count"] = 1
    encoded = (
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()

    assert receipts_module._parse_receipt(encoded) is None  # noqa: SLF001
    runtime = tmp_brain / "runtime"
    runtime.mkdir(parents=True)
    ledger = runtime / "pending-apply-receipts.jsonl"
    ledger.write_bytes(encoded)
    os.chmod(ledger, 0o600)
    assert receipts_module.read_pending_receipt_ledger_health(tmp_brain).status == (
        "corrupt"
    )


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


def test_pending_receipt_ledger_reads_legacy_v1_and_appends_current_receipt(tmp_brain):
    runtime = tmp_brain / "runtime"
    runtime.mkdir(parents=True)
    ledger = runtime / "pending-apply-receipts.jsonl"
    legacy_lines = []
    for receipt in (_prepared(), _completed()):
        payload = receipt.to_dict()
        payload.pop("action_counts", None)
        legacy_lines.append(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    ledger.write_text("\n".join(legacy_lines) + "\n", encoding="utf-8")
    os.chmod(ledger, 0o600)

    initial = receipts_module.read_pending_receipt_ledger_health(tmp_brain)
    receipts_module.append_pending_receipt(
        tmp_brain,
        receipts_module.prepare_pending_receipt(
            selection_mode="explicit",
            requested_count=0,
            selected=[],
            depth_before=2,
            batch_id="d" * 32,
            prepared_at=PREPARED_AT,
        ),
    )
    after_append = receipts_module.read_pending_receipt_ledger_health(tmp_brain)

    assert initial.status == "healthy"
    assert initial.record_count == 2
    assert initial.incomplete_count == 0
    assert after_append.status == "healthy"
    assert after_append.record_count == 3
    assert after_append.incomplete_count == 1


def test_pending_receipt_ledger_rejects_legacy_shaped_resolution(tmp_brain):
    runtime = tmp_brain / "runtime"
    runtime.mkdir(parents=True)
    ledger = runtime / "pending-apply-receipts.jsonl"
    payload = _prepared().to_dict()
    payload.update(
        selection_mode="resolution",
        requested_count=0,
        selected_count=0,
        depth_before=0,
    )
    payload.pop("action_counts")
    ledger.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.chmod(ledger, 0o600)

    assert receipts_module.read_pending_receipt_ledger_health(tmp_brain).status == (
        "corrupt"
    )


def test_pending_resolution_sequence_rejects_tampered_action_counts(tmp_brain):
    prepared = _resolution_prepared()
    completed = receipts_module.complete_pending_receipt(
        prepared,
        outcomes=_resolution_outcomes(),
        depth_after=2,
        completed_at=COMPLETED_AT,
    )
    tampered = replace(completed, action_counts={"approve_audit": 2})

    assert completed.action_counts == prepared.action_counts
    assert (
        receipts_module.incomplete_pending_receipt(prepared).action_counts
        == prepared.action_counts
    )
    receipts_module.append_pending_receipt(tmp_brain, prepared)
    with pytest.raises(OSError, match="PENDING_RECEIPT_LEDGER_CORRUPT"):
        receipts_module.append_pending_receipt(tmp_brain, tampered)

    health = receipts_module.read_pending_receipt_ledger_health(tmp_brain)
    assert health.status == "healthy"
    assert health.record_count == 1
    assert health.incomplete_count == 1


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


def test_pending_receipt_ledger_symlink_is_unavailable_and_never_followed(tmp_brain):
    runtime = tmp_brain / "runtime"
    runtime.mkdir(parents=True)
    outside = tmp_brain.parent / "outside-receipt-ledger"
    outside.write_text("outside\n", encoding="utf-8")
    ledger = runtime / "pending-apply-receipts.jsonl"
    try:
        ledger.symlink_to(outside)
    except (NotImplementedError, OSError):
        pytest.skip("symlink unavailable")

    health = receipts_module.read_pending_receipt_ledger_health(tmp_brain)

    assert health.status == "unavailable"
    with pytest.raises(OSError):
        receipts_module.append_pending_receipt(tmp_brain, _prepared())
    assert outside.read_text(encoding="utf-8") == "outside\n"


def test_pending_receipt_health_rejects_oversized_ledger(tmp_brain):
    runtime = tmp_brain / "runtime"
    runtime.mkdir(parents=True)
    ledger = runtime / "pending-apply-receipts.jsonl"
    with ledger.open("wb") as handle:
        handle.truncate(receipts_module.MAX_PENDING_RECEIPT_LEDGER_BYTES + 1)
    os.chmod(ledger, 0o600)

    health = receipts_module.read_pending_receipt_ledger_health(tmp_brain)

    assert health.status == "corrupt"
