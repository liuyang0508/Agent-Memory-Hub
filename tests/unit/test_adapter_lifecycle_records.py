import json
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _seed_evidence(brain_dir: Path, *, adapter: str, now: datetime) -> None:
    from agent_brain.agent_integrations.runtime_events import record_runtime_event
    from agent_brain.agent_integrations.verifications import record_adapter_verification
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort

    record_runtime_event(
        brain_dir,
        adapter=adapter,
        event_name="UserPromptSubmit",
        session_id="stage3-evidence",
        now=now,
    )
    record_injection_cohort(
        brain_dir,
        adapter=adapter,
        session_id="stage3-evidence",
        item_ids=["mem-stage3-evidence"],
        now=now,
    )
    record_adapter_verification(
        brain_dir,
        adapter=adapter,
        status="passed",
        verifier="pytest",
        evidence=[f"memory adapter doctor {adapter} --format json"],
        now=now,
    )


def test_lifecycle_evidence_summary_marks_fresh_and_stale_records(tmp_path: Path) -> None:
    from agent_brain.agent_integrations.lifecycle_records import lifecycle_evidence_summary

    now = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)
    _seed_evidence(tmp_path, adapter="codex", now=now - timedelta(days=8))

    stale = lifecycle_evidence_summary(
        tmp_path,
        "codex",
        now=now,
        runtime_ttl_seconds=7 * 24 * 60 * 60,
        context_ttl_seconds=7 * 24 * 60 * 60,
        verification_ttl_seconds=7 * 24 * 60 * 60,
    )

    assert stale.runtime.observed is True
    assert stale.runtime.fresh is False
    assert stale.context_injection.fresh is False
    assert stale.verification.fresh is False
    assert stale.stale_reasons == (
        "runtime evidence stale",
        "context injection evidence stale",
        "verification evidence stale",
    )

    fresh_root = tmp_path / "fresh"
    _seed_evidence(fresh_root, adapter="codex", now=now - timedelta(minutes=5))
    fresh = lifecycle_evidence_summary(
        fresh_root,
        "codex",
        now=now,
        runtime_ttl_seconds=3600,
        context_ttl_seconds=3600,
        verification_ttl_seconds=3600,
    )

    assert fresh.runtime.fresh is True
    assert fresh.context_injection.fresh is True
    assert fresh.verification.fresh is True
    assert fresh.stale_reasons == ()


def test_future_or_invalid_timestamp_never_counts_as_fresh(tmp_path: Path) -> None:
    from agent_brain.agent_integrations.lifecycle_records import evidence_freshness

    now = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)

    future = evidence_freshness(
        "runtime",
        "2026-07-19T09:00:00+00:00",
        now=now,
        ttl_seconds=3600,
    )
    invalid = evidence_freshness(
        "runtime",
        "not-a-timestamp",
        now=now,
        ttl_seconds=3600,
    )

    assert future.fresh is False
    assert future.invalid_reason == "future_timestamp"
    assert invalid.fresh is False
    assert invalid.invalid_reason == "invalid_timestamp"


def test_stale_evidence_cannot_keep_capability_verified(tmp_path: Path) -> None:
    from agent_brain.agent_integrations.capabilities import capability_for_adapter
    from agent_brain.agent_integrations.registry import get_adapter

    now = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)
    _seed_evidence(tmp_path, adapter="codex", now=now - timedelta(days=8))

    cap = capability_for_adapter("codex", get_adapter("codex", tmp_path), now=now)

    assert cap.states["installed"] is True
    assert cap.states["configured"] is True
    assert cap.states["doctor_passed"] is False
    assert cap.states["runtime_observed"] is False
    assert cap.states["context_injected"] is False
    assert cap.verified is False
    assert cap.verification_blockers == [
        "verification evidence stale",
        "runtime evidence stale",
        "context injection evidence stale",
    ]


def test_lifecycle_record_is_low_sensitive_and_private(tmp_path: Path) -> None:
    from agent_brain.agent_integrations.lifecycle_records import (
        lifecycle_records_path,
        record_lifecycle_event,
    )

    record = record_lifecycle_event(
        tmp_path,
        adapter="codex",
        action="install",
        status="passed",
        reason_code="OK",
        artifact_hashes={"inject-context.sh": "sha256:abc123"},
        backup_id="backup-001",
        cohort="shadow",
    )
    path = lifecycle_records_path(tmp_path)
    serialized = path.read_text(encoding="utf-8")
    lowered = serialized.lower()

    assert record.package_version
    assert record.commit
    assert record.manifest_version == "amh-adapter-manifest/v1"
    assert record.artifact_hashes == {"inject-context.sh": "sha256:abc123"}
    assert "prompt" not in lowered
    assert "transcript" not in lowered
    assert "memory body" not in lowered
    assert "api_key" not in lowered
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_lifecycle_parser_skips_unknown_reason_codes(tmp_path: Path) -> None:
    from agent_brain.agent_integrations.lifecycle_records import (
        iter_lifecycle_records,
        lifecycle_records_path,
        record_lifecycle_event,
    )

    record_lifecycle_event(
        tmp_path,
        adapter="codex",
        action="doctor",
        status="passed",
        reason_code="OK",
    )
    path = lifecycle_records_path(tmp_path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "adapter": "codex",
            "action": "install",
            "status": "passed",
            "reason_code": "MADE_UP",
            "timestamp": "2026-07-19T08:00:00+00:00",
            "package_version": "1.1.1",
            "commit": "unknown",
            "manifest_version": "amh-adapter-manifest/v1",
            "artifact_hashes": {},
        }) + "\n")

    records = list(iter_lifecycle_records(tmp_path, adapter="codex"))

    assert len(records) == 1
    assert records[0].reason_code == "OK"
