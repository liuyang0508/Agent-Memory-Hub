#!/usr/bin/env python3
"""Generate or verify the committed stage-three adapter governance report."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import get_args

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_brain._version import __version__  # noqa: E402
from agent_brain.agent_integrations.lifecycle_records import (  # noqa: E402
    LifecycleReasonCode,
)
from agent_brain.agent_integrations.manifests import (  # noqa: E402
    MANIFEST_SCHEMA_VERSION,
    manifests_for_all,
)
from agent_brain.agent_integrations.release_controls import (  # noqa: E402
    RELEASE_CONTROL_SCHEMA_VERSION,
)
from agent_brain.product.adapter_onboarding import (  # noqa: E402
    LIFECYCLE_RESULT_SCHEMA_VERSION,
)


EVIDENCE_PATH = ROOT / "tests/fixtures/adapter_productization_evidence.json"
REPORT_PATH = ROOT / "docs/evaluation/stage3-adapter-productization-report.json"
READINESS_PATH = ROOT / "docs/evaluation/stage3-adapter-productization-readiness.zh.md"
REPORT_SCHEMA_VERSION = "amh-adapter-productization-report/v1"
EVALUATION_AT = "2026-07-19T05:30:00+00:00"
IMPLEMENTATION_PATHS = (
    "agent_brain/agent_integrations/manifests.py",
    "agent_brain/agent_integrations/lifecycle_records.py",
    "agent_brain/agent_integrations/release_controls.py",
    "agent_brain/agent_integrations/capabilities.py",
    "agent_brain/product/adapter_onboarding.py",
    "agent_brain/interfaces/cli/commands/adapters.py",
    "web/api/routes/adapters.py",
    "agent_runtime_kit/hooks/inject-context.sh",
    "scripts/generate-adapter-governance.py",
)
EXPECTED_BATCHES = (
    (1, ("codex", "qoder")),
    (2, ("claude_code", "qoder_work")),
)
REQUIRED_CHECKS = frozenset({
    "doctor",
    "install_idempotent",
    "owned_only_uninstall",
    "repair",
    "upgrade_rollback",
})
REQUIRED_REASON_CODES = frozenset({
    "OK",
    "ADAPTER_DISABLED",
    "CONTEXT_MISSING",
    "EVIDENCE_STALE",
    "INVALID_PROMOTION",
    "OWNERSHIP_CONFLICT",
    "ROLLBACK_FAILED",
})
PRIVATE_ABSOLUTE_PATH = re.compile(r"/(?:Users|home|root|private|var/folders)/")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = generate_report()
    json_text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    markdown = render_markdown(report)
    if args.check:
        if not REPORT_PATH.exists() or REPORT_PATH.read_text(encoding="utf-8") != json_text:
            print("adapter governance report is stale; run scripts/generate-adapter-governance.py")
            return 1
        if not READINESS_PATH.exists() or READINESS_PATH.read_text(encoding="utf-8") != markdown:
            print("adapter governance readiness markdown is stale")
            return 1
    else:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json_text, encoding="utf-8")
        READINESS_PATH.write_text(markdown, encoding="utf-8")
    if report["status"] != "pass":
        print("adapter-governance: FAIL " + ",".join(report["failed_gates"]))
        return 1
    print(
        "adapter-governance: PASS "
        f"manifests={report['manifest_count']} "
        f"batches={len(report['pilot_batches'])} privacy=PASS"
    )
    return 0


def generate_report() -> dict[str, object]:
    evidence = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
    manifests = [item.to_dict() for item in manifests_for_all(Path("/nonexistent/amh-report"))]
    reason_codes = sorted(str(value) for value in get_args(LifecycleReasonCode))
    failed_gates: list[str] = []
    if evidence.get("schema_version") != "amh-adapter-productization-evidence/v1":
        failed_gates.append("evidence_schema")
    if len(manifests) != 16 or len({item["adapter_id"] for item in manifests}) != 16:
        failed_gates.append("manifest_completeness")
    if {item["schema_version"] for item in manifests} != {MANIFEST_SCHEMA_VERSION}:
        failed_gates.append("manifest_schema")
    if not all(_manifest_complete(item) for item in manifests):
        failed_gates.append("manifest_fields")
    pilot_batches = evidence.get("pilot_batches")
    if not _batches_valid(pilot_batches):
        failed_gates.append("pilot_batches")
    core_isolation = evidence.get("core_isolation")
    if not _core_isolation_valid(core_isolation):
        failed_gates.append("core_isolation")
    if not REQUIRED_REASON_CODES <= set(reason_codes):
        failed_gates.append("reason_codes")
    baseline_commit = str(evidence.get("baseline_commit") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", baseline_commit):
        failed_gates.append("baseline_commit")
    report: dict[str, object] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "evaluated_at": EVALUATION_AT,
        "status": "fail" if failed_gates else "pass",
        "failed_gates": failed_gates,
        "package_version": __version__,
        "baseline_commit": baseline_commit,
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "manifest_count": len(manifests),
        "manifest_sha256": _json_sha256(manifests),
        "manifests": manifests,
        "lifecycle_result_schema_version": LIFECYCLE_RESULT_SCHEMA_VERSION,
        "release_control_schema_version": RELEASE_CONTROL_SCHEMA_VERSION,
        "reason_codes": reason_codes,
        "release_stages": ["shadow", "canary", "default", "disabled"],
        "pilot_batches": pilot_batches,
        "core_isolation": core_isolation,
        "test_commands": evidence.get("test_commands"),
        "fixture_sha256": _file_sha256(EVIDENCE_PATH),
        "implementation_sha256": _implementation_sha256(),
    }
    violations = _privacy_violations(report)
    report["privacy"] = {
        "status": "pass" if not violations else "fail",
        "prohibited_field_count": len(violations),
        "violations": violations,
    }
    if violations:
        failed_gates.append("privacy")
        report["status"] = "fail"
    return report


def _manifest_complete(item: dict[str, object]) -> bool:
    lifecycle = item.get("lifecycle")
    evidence = item.get("evidence")
    required_lifecycle = {"install", "verify", "doctor", "repair", "upgrade", "uninstall"}
    return bool(
        item.get("adapter_id")
        and item.get("adapter_version")
        and item.get("platforms")
        and item.get("client_version_range")
        and item.get("payload_schema")
        and item.get("output_protocol")
        and item.get("feature_flag")
        and item.get("degrade_mode")
        and item.get("rollback_mode")
        and isinstance(lifecycle, dict)
        and required_lifecycle <= set(lifecycle)
        and all(lifecycle[name] for name in required_lifecycle)
        and isinstance(evidence, dict)
        and all(int(evidence[name]) > 0 for name in (
            "runtime_ttl_seconds",
            "context_ttl_seconds",
            "verification_ttl_seconds",
        ))
    )


def _batches_valid(value: object) -> bool:
    if not isinstance(value, list) or len(value) != len(EXPECTED_BATCHES):
        return False
    for row, (number, adapters) in zip(value, EXPECTED_BATCHES, strict=True):
        if not isinstance(row, dict):
            return False
        if row.get("batch") != number or tuple(row.get("adapters") or ()) != adapters:
            return False
        checks = row.get("checks")
        if not isinstance(checks, dict) or set(checks) != REQUIRED_CHECKS:
            return False
        if not all(checks.values()):
            return False
    return True


def _core_isolation_valid(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    return bool(
        value.get("cli_stats") is True
        and value.get("disabled_hook_clean_protocol") is True
        and set(value.get("mcp_required_tools") or ())
        == {"read_memory", "search_memory", "write_memory"}
    )


def _privacy_violations(report: dict[str, object]) -> list[str]:
    violations: list[str] = []

    def visit(value: object, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                lowered = str(key).lower()
                if lowered in {
                    "prompt",
                    "transcript",
                    "token",
                    "api_key",
                    "jwt",
                    "secret",
                }:
                    violations.append(f"{path}.{key}:prohibited_key")
                visit(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")
        elif isinstance(value, str) and PRIVATE_ABSOLUTE_PATH.search(value):
            violations.append(f"{path}:private_absolute_path")

    visit(report, "report")
    return sorted(set(violations))


def _implementation_sha256() -> str:
    digest = hashlib.sha256()
    for relative in IMPLEMENTATION_PATHS:
        path = ROOT / relative
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _file_sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _json_sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def render_markdown(report: dict[str, object]) -> str:
    batches = report["pilot_batches"]
    batch_rows = "\n".join(
        f"| {row['batch']} | {', '.join(row['adapters'])} | "
        f"{'PASS' if all(row['checks'].values()) else 'FAIL'} |"
        for row in batches
    )
    manifests = report["manifests"]
    manifest_rows = "\n".join(
        f"| `{row['adapter_id']}` | {', '.join(row['platforms'])} | "
        f"{', '.join(row['channels'])} | {row['output_protocol']} |"
        for row in manifests
    )
    return f"""# 阶段三多 Agent 产品化治理就绪报告

状态：**{str(report['status']).upper()}**  
证据时间：`{report['evaluated_at']}`  
基线提交：`{report['baseline_commit']}`  
实现摘要：`{report['implementation_sha256']}`

## 结论

- manifest：{report['manifest_count']} 个，schema `{report['manifest_schema_version']}`；
- 生命周期结果：`{report['lifecycle_result_schema_version']}`；
- 发布控制：`{report['release_control_schema_version']}`，顺序为 shadow → canary → default，disabled 为单 adapter kill switch；
- 隐私扫描：{report['privacy']['status']}，违规字段 {report['privacy']['prohibited_field_count']}；
- core isolation：CLI、MCP、禁用 hook 空协议均通过。

## 两批合同证据

| 批次 | Adapter | 同合同结果 |
|---:|---|---|
{batch_rows}

## Manifest 矩阵

| Adapter | 平台 | Channel | Hook output protocol |
|---|---|---|---|
{manifest_rows}

## 真实性边界

本报告证明 manifest、生命周期事务、TTL、provenance、发布控制和隔离合同已经机器化。单机 `verified` 仍必须同时满足 configured、doctor、fresh runtime、fresh context injection 与 fresh verification；缺少真实客户端证据时保持 blocker，不由本报告静态晋升。
"""


if __name__ == "__main__":
    raise SystemExit(main())
