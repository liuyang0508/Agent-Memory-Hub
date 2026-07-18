import json
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / "docs/evaluation/stage3-adapter-productization-report.json"


def _report() -> dict[str, object]:
    return json.loads(REPORT_PATH.read_text(encoding="utf-8"))


def test_stage3_report_is_fresh_and_manifest_derived() -> None:
    report = _report()

    assert report["schema_version"] == "amh-adapter-productization-report/v1"
    assert report["status"] == "pass"
    assert report["failed_gates"] == []
    assert report["manifest_count"] == 16
    batches = report["pilot_batches"]
    assert batches[0]["adapters"] == ["codex", "qoder"]
    assert batches[1]["adapters"] == ["claude_code", "qoder_work"]
    assert all(all(batch["checks"].values()) for batch in batches)
    assert report["privacy"] == {
        "prohibited_field_count": 0,
        "status": "pass",
        "violations": [],
    }


def test_stage3_report_exposes_versioned_contracts_and_provenance() -> None:
    report = _report()

    assert report["manifest_schema_version"] == "amh-adapter-manifest/v1"
    assert report["lifecycle_result_schema_version"] == (
        "amh-adapter-lifecycle-result/v1"
    )
    assert report["release_control_schema_version"] == (
        "amh-adapter-release-controls/v1"
    )
    assert report["release_stages"] == ["shadow", "canary", "default", "disabled"]
    assert {
        "OK",
        "ADAPTER_DISABLED",
        "CONTEXT_MISSING",
        "EVIDENCE_STALE",
        "INVALID_PROMOTION",
        "OWNERSHIP_CONFLICT",
        "ROLLBACK_FAILED",
    } <= set(report["reason_codes"])
    assert re.fullmatch(r"[0-9a-f]{40}", report["baseline_commit"])
    for key in ("fixture_sha256", "implementation_sha256", "manifest_sha256"):
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", report[key])


def test_stage3_report_contains_no_private_or_prompt_payload_fields() -> None:
    public_json = REPORT_PATH.read_text(encoding="utf-8")
    assert not re.search(r"/(?:Users|home|root|private|var/folders)/", public_json)
    report = _report()

    def keys(value: object) -> set[str]:
        if isinstance(value, dict):
            return {str(key).lower() for key in value} | set().union(
                *(keys(child) for child in value.values())
            )
        if isinstance(value, list):
            return set().union(*(keys(child) for child in value))
        return set()

    assert keys(report).isdisjoint(
        {"prompt", "transcript", "token", "api_key", "jwt", "secret"}
    )


def test_stage3_committed_outputs_are_current() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/generate-adapter-governance.py", "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "adapter-governance: PASS manifests=16 batches=2 privacy=PASS" in (
        completed.stdout
    )
