#!/usr/bin/env python3
"""Generate or verify committed synthetic lifecycle-governance evidence."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
from typing import Any, get_args
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_brain.contracts.memory_item import MemoryItem  # noqa: E402
from agent_brain.evaluation.public_hygiene import scan_text  # noqa: E402
from agent_brain.interfaces.cli.commands.subapps import (  # noqa: E402
    apply_lifecycle_reviews,
)
from agent_brain.memory.governance.lifecycle_candidates import (  # noqa: E402
    rank_supersession_candidates,
)
from agent_brain.memory.governance.lifecycle_review import (  # noqa: E402
    LifecycleActionName,
)
from agent_brain.memory.governance.supersession import (  # noqa: E402
    SupersessionService,
)
from agent_brain.memory.store.items_store import ItemsStore  # noqa: E402
from agent_brain.memory.store.pending import PendingQueue  # noqa: E402
from agent_brain.product.governance_readiness import (  # noqa: E402
    build_memory_lifecycle_readiness,
)
from web.api.routes.governance import LifecycleActionRequest  # noqa: E402


FIXTURE_PATH = ROOT / "tests/fixtures/lifecycle_governance_evidence_v1.json"
REPORT_PATH = ROOT / "docs/evaluation/lifecycle-governance-readiness.json"
MARKDOWN_PATH = ROOT / "docs/evaluation/lifecycle-governance-readiness.zh.md"
REPORT_SCHEMA_VERSION = "amh-lifecycle-governance-readiness/v1"
FIXTURE_SCHEMA_VERSION = "amh-lifecycle-governance-evidence/v1"
GENERATOR_VERSION = "amh-lifecycle-governance-generator/v1"
EXIT_PASS = 0
EXIT_FAILED_GATES = 1
EXIT_STALE_EVIDENCE = 2
EXIT_INVALID_INPUT = 3
IMPLEMENTATION_PATHS = (
    "agent_brain/interfaces/cli/commands/maintenance.py",
    "agent_brain/interfaces/cli/commands/review.py",
    "agent_brain/interfaces/cli/commands/subapps.py",
    "agent_brain/interfaces/cli/doctor_offline.py",
    "agent_brain/interfaces/mcp/tools/graph.py",
    "agent_brain/memory/governance/git_fd_exec.py",
    "agent_brain/memory/governance/lifecycle_action_parsing.py",
    "agent_brain/memory/governance/lifecycle_archive.py",
    "agent_brain/memory/governance/lifecycle_candidates.py",
    "agent_brain/memory/governance/lifecycle_ledger.py",
    "agent_brain/memory/governance/lifecycle_review.py",
    "agent_brain/memory/governance/lifecycle_snapshot.py",
    "agent_brain/memory/governance/maintenance_plan.py",
    "agent_brain/memory/governance/supersession.py",
    "agent_brain/memory/store/durable_fs.py",
    "agent_brain/memory/store/items_store.py",
    "agent_brain/memory/store/pending.py",
    "agent_brain/platform/indexing/graph_index.py",
    "agent_brain/platform/indexing/index.py",
    "agent_brain/platform/indexing/index_schema.py",
    "agent_brain/platform/indexing/index_writer.py",
    "agent_brain/product/governance_readiness.py",
    "web/api/routes/governance.py",
    "scripts/generate-lifecycle-governance-report.py",
)


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _content_manifest() -> list[dict[str, str]]:
    manifest = []
    for relative in IMPLEMENTATION_PATHS:
        path = ROOT / relative
        manifest.append({"path": relative, "sha256": _sha256_bytes(path.read_bytes())})
    return manifest


def _implementation_hash(manifest: list[dict[str, str]]) -> str:
    payload = {
        "generator_version": GENERATOR_VERSION,
        "files": manifest,
    }
    return _sha256_bytes(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def _memory_item(raw: dict[str, object]) -> MemoryItem:
    return MemoryItem.model_validate(raw)


def _write_item(store: ItemsStore, raw: dict[str, object]) -> MemoryItem:
    item = _memory_item(raw)
    store.write(item, "")
    return item


def _run_supersession_contract(fixture: dict[str, object]) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    passed = True
    cases = fixture.get("supersession_cases")
    if not isinstance(cases, list):
        return {"status": "fail", "cases": [], "reason": "INVALID_CASES"}
    for case in cases:
        if not isinstance(case, dict):
            passed = False
            continue
        with tempfile.TemporaryDirectory(prefix="amh-lifecycle-synthetic-") as directory:
            brain = Path(directory).resolve() / "brain"
            store = ItemsStore(brain / "items")
            obsolete_raw = case.get("obsolete")
            replacement_raw = case.get("replacement")
            if not isinstance(obsolete_raw, dict) or not isinstance(replacement_raw, dict):
                passed = False
                continue
            obsolete = _write_item(store, obsolete_raw)
            replacement = _write_item(store, replacement_raw)
            result = SupersessionService(brain, store).preview(
                replacement.id, obsolete.id
            )
            candidate_ids = [
                candidate.replacement_id
                for candidate in rank_supersession_candidates(
                    obsolete=obsolete,
                    items=[replacement],
                    supersedes_edges=set(),
                )
            ]
            case_pass = (
                result.status == case.get("expected_status")
                and result.reason == case.get("expected_reason")
            )
            expected_candidate = case.get("expected_candidate")
            if expected_candidate is not None:
                case_pass = case_pass and candidate_ids[:1] == [expected_candidate]
            passed = passed and case_pass
            rows.append(
                {
                    "id": case.get("id"),
                    "status": result.status,
                    "reason": result.reason,
                    "candidate_ids": candidate_ids,
                    "pass": case_pass,
                }
            )
    return {"status": "pass" if passed else "fail", "cases": rows}


def _pending_payload_hash(item: dict[str, object]) -> str:
    payload = json.dumps(
        item,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stable_pending_item_id(record: dict[str, object]) -> str:
    item = record["item"]
    assert isinstance(item, dict)
    created = str(record["original_created_at"])
    from datetime import datetime, timezone

    created_at = datetime.fromisoformat(created).astimezone(timezone.utc)
    title = str(item["title"])
    normalized = (
        unicodedata.normalize("NFKD", title.casefold())
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")[:30].rstrip("-")
    stable = hashlib.sha256(str(record["record_id"]).encode("utf-8")).hexdigest()[:24]
    return f"mem-{created_at:%Y%m%d-%H%M%S}-{slug or 'pending'}-{stable}"


def _materialize_pending_case(
    brain: Path,
    store: ItemsStore,
    case: dict[str, object],
) -> None:
    pending = brain / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    path = pending / f"{case['id']}.jsonl"
    if "raw_line" in case:
        data = str(case["raw_line"]) + "\n"
    else:
        record = json.loads(json.dumps(case["record"], ensure_ascii=False))
        item = record["item"]
        assert isinstance(item, dict)
        payload_hash = _pending_payload_hash(item)
        if record.get("v") == 2:
            record["payload_sha256"] = payload_hash
        data = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        if case.get("seed_existing") is True:
            existing = dict(item)
            existing.update(
                {
                    "id": _stable_pending_item_id(record),
                    "created_at": record["original_created_at"],
                    "source": {
                        "kind": "pending-replay",
                        "span_hash": payload_hash,
                    },
                }
            )
            _write_item(store, existing)
    path.write_text(data, encoding="utf-8")
    os.chmod(path, 0o600)


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _run_pending_contract(fixture: dict[str, object]) -> dict[str, object]:
    cases = fixture.get("pending_cases")
    if not isinstance(cases, list):
        return {"status": "fail", "cases": [], "reason": "INVALID_CASES"}
    with tempfile.TemporaryDirectory(prefix="amh-pending-synthetic-") as directory:
        brain = Path(directory).resolve() / "brain"
        store = ItemsStore(brain / "items")
        expected: dict[str, tuple[str, str]] = {}
        for raw_case in cases:
            if not isinstance(raw_case, dict):
                continue
            _materialize_pending_case(brain, store, raw_case)
            expected[str(raw_case["id"])] = (
                str(raw_case["expected_classification"]),
                str(raw_case["expected_reason"]),
            )
        before = _tree_bytes(brain)
        preview = PendingQueue(brain=brain).preview(limit=len(cases) + 1)
        after = _tree_bytes(brain)
        rows = []
        passed = not preview.scan_unavailable and before == after
        for record in preview.records:
            case_id = Path(record.path).stem
            expected_result = expected.get(case_id)
            case_pass = expected_result == (record.classification, record.reason)
            passed = passed and case_pass
            rows.append(
                {
                    "id": case_id,
                    "classification": record.classification,
                    "reason": record.reason,
                    "pass": case_pass,
                }
            )
        passed = passed and len(rows) == len(expected)
        return {
            "status": "pass" if passed else "fail",
            "preview_only": before == after,
            "cases": sorted(rows, key=lambda row: str(row["id"])),
        }


def _run_graph_drift_contract(fixture: dict[str, object]) -> dict[str, object]:
    graph = fixture.get("graph_drift")
    if not isinstance(graph, dict):
        return {"status": "fail", "reason": "INVALID_GRAPH_FIXTURE"}
    with tempfile.TemporaryDirectory(prefix="amh-graph-synthetic-") as directory:
        brain = Path(directory).resolve() / "brain"
        store = ItemsStore(brain / "items")
        items = graph.get("items")
        edges = graph.get("index_edges")
        if not isinstance(items, list) or not isinstance(edges, list):
            return {"status": "fail", "reason": "INVALID_GRAPH_FIXTURE"}
        for raw in items:
            if isinstance(raw, dict):
                _write_item(store, raw)
        with sqlite3.connect(brain / "index.db") as connection:
            connection.execute(
                "CREATE TABLE refs_graph ("
                "source_id TEXT NOT NULL, target_id TEXT NOT NULL, "
                "relation TEXT NOT NULL, "
                "PRIMARY KEY (source_id, target_id, relation))"
            )
            for edge in edges:
                if isinstance(edge, list) and len(edge) == 2:
                    connection.execute(
                        "INSERT INTO refs_graph VALUES (?, ?, 'supersedes')",
                        tuple(edge),
                    )
        lane = build_memory_lifecycle_readiness(brain)
        actual = lane.metrics["supersession_drift_count"]
        expected = graph.get("expected_drift_count")
        return {
            "status": "pass" if actual == expected else "fail",
            "expected_drift_count": expected,
            "observed_drift_count": actual,
        }


def _run_surface_parity(fixture: dict[str, object]) -> dict[str, object]:
    expected = sorted(str(value) for value in fixture.get("surface_actions", []))
    core = sorted(str(value) for value in get_args(LifecycleActionName))
    schema = LifecycleActionRequest.model_json_schema()
    web = sorted(str(value) for value in schema["properties"]["action"]["enum"])
    parameters = inspect.signature(apply_lifecycle_reviews).parameters
    cli = sorted(
        action
        for action in expected
        if action.replace("-", "_") in parameters
    )
    apply_default = parameters["apply"].default
    cli_preview_default = getattr(apply_default, "default", None) is False
    status = "pass" if core == web == cli == expected and cli_preview_default else "fail"
    return {
        "status": status,
        "actions": expected,
        "core_actions": core,
        "web_actions": web,
        "cli_actions": cli,
        "cli_preview_default": cli_preview_default,
    }


def _privacy_contract(fixture: object, contracts: object) -> dict[str, object]:
    text = canonical_json({"fixture": fixture, "contracts": contracts})
    findings = scan_text(text, path="lifecycle-governance-evidence.json")
    return {
        "status": "pass" if not findings else "fail",
        "finding_count": len(findings),
        "rules": sorted({finding.rule for finding in findings}),
    }


def generate_report(
    *, fixture_path: Path = FIXTURE_PATH
) -> dict[str, object]:
    raw_fixture = Path(fixture_path).read_bytes()
    fixture = json.loads(raw_fixture)
    manifest = _content_manifest()
    failed_gates: list[str] = []
    if fixture.get("schema_version") != FIXTURE_SCHEMA_VERSION:
        failed_gates.append("fixture_schema")
    if raw_fixture.decode("utf-8") != canonical_json(fixture):
        failed_gates.append("fixture_canonical")
    supersession = _run_supersession_contract(fixture)
    pending = _run_pending_contract(fixture)
    graph_drift = _run_graph_drift_contract(fixture)
    surface_parity = _run_surface_parity(fixture)
    contracts = {
        "supersession_contract": supersession,
        "pending_contract": pending,
        "graph_drift_contract": graph_drift,
        "surface_parity": surface_parity,
    }
    for gate, result in contracts.items():
        if result["status"] != "pass":
            failed_gates.append(gate)
    privacy = _privacy_contract(fixture, contracts)
    if privacy["status"] != "pass":
        failed_gates.append("privacy")
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "implementation_hash": _implementation_hash(manifest),
        "implementation_manifest": manifest,
        "fixture_hash": _sha256_bytes(raw_fixture),
        "fixture_schema_version": fixture.get("schema_version"),
        "evidence_scope": {
            "code_and_synthetic_fixture": "pass" if not failed_gates else "fail",
            "real_brain_dry_run": "pending",
        },
        **contracts,
        "privacy": privacy,
        "failed_gates": failed_gates,
        "overall_status": "fail" if failed_gates else "pass",
    }


def render_markdown(report: dict[str, object]) -> str:
    overall = str(report["overall_status"]).upper()
    failed = report.get("failed_gates")
    failed_text = "、".join(str(value) for value in failed) if failed else "无"
    return (
        "# 可信记忆生命周期治理就绪报告\n\n"
        f"- 代码与 synthetic fixture：`{overall}`\n"
        "- 真实 brain dry-run：`PENDING`\n"
        f"- 失败门禁：{failed_text}\n\n"
        "本报告由提交内的纯 synthetic fixture 离线重放生成，只证明代码与 fixture 合同。"
        "它不读取真实 brain，也不代表真实 pending 或 stale backlog 已完成治理。\n\n"
        "## 合同结果\n\n"
        f"- Supersession：`{str(report['supersession_contract']['status']).upper()}`\n"
        f"- Pending：`{str(report['pending_contract']['status']).upper()}`\n"
        f"- Graph drift：`{str(report['graph_drift_contract']['status']).upper()}`\n"
        f"- CLI / Web surface parity：`{str(report['surface_parity']['status']).upper()}`\n"
        f"- Privacy：`{str(report['privacy']['status']).upper()}`\n\n"
        "## 可重放标识\n\n"
        f"- Implementation hash：`{report['implementation_hash']}`\n"
        f"- Fixture hash：`{report['fixture_hash']}`\n"
        f"- Generator：`{report['generator_version']}`\n\n"
        "下一阶段必须在稳定代码上对真实 brain 先执行只读 dry-run，经人工审核后才能分批 apply。\n"
    )


def committed_report_mismatches(
    *,
    report_path: Path = REPORT_PATH,
    fixture_path: Path = FIXTURE_PATH,
) -> list[str]:
    mismatches: list[str] = []
    if not Path(report_path).is_file():
        return ["report_missing"]
    committed = json.loads(Path(report_path).read_text(encoding="utf-8"))
    fixture_hash = _sha256_bytes(Path(fixture_path).read_bytes())
    if committed.get("fixture_hash") != fixture_hash:
        mismatches.append("fixture_hash_mismatch")
    expected = canonical_json(generate_report(fixture_path=fixture_path))
    if Path(report_path).read_text(encoding="utf-8") != expected:
        mismatches.append("report_bytes_mismatch")
    return mismatches


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate committed synthetic lifecycle governance evidence.",
        epilog=(
            "exit codes: 0=pass, 1=failed gates, 2=stale committed evidence, "
            "3=invalid input or infrastructure error"
        ),
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        report = generate_report()
        json_text = canonical_json(report)
        markdown = render_markdown(report)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        print(f"lifecycle-governance: ERROR {type(error).__name__}")
        return EXIT_INVALID_INPUT
    if args.check:
        stale = committed_report_mismatches()
        if not MARKDOWN_PATH.exists() or MARKDOWN_PATH.read_text(encoding="utf-8") != markdown:
            stale.append("markdown_bytes_mismatch")
        if stale:
            print("lifecycle-governance: STALE " + ",".join(stale))
            return EXIT_STALE_EVIDENCE
    else:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json_text, encoding="utf-8")
        MARKDOWN_PATH.write_text(markdown, encoding="utf-8")
    if report["overall_status"] != "pass":
        print("lifecycle-governance: FAIL " + ",".join(report["failed_gates"]))
        return EXIT_FAILED_GATES
    print("lifecycle-governance: PASS synthetic=PASS real-brain-dry-run=PENDING")
    return EXIT_PASS


if __name__ == "__main__":
    raise SystemExit(main())
