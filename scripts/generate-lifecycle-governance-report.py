#!/usr/bin/env python3
"""Generate or verify committed synthetic lifecycle-governance evidence."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import tempfile
from typing import Any, Iterator
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_brain.contracts.memory_item import MemoryItem  # noqa: E402
from agent_brain.evaluation.public_hygiene import scan_text  # noqa: E402
from agent_brain.memory.governance.lifecycle_candidates import (  # noqa: E402
    rank_supersession_candidates,
)
from agent_brain.memory.governance.supersession import (  # noqa: E402
    SupersessionService,
)
from agent_brain.memory.store.items_store import ItemsStore  # noqa: E402
from agent_brain.memory.store.pending import PendingQueue  # noqa: E402
from agent_brain.product.governance_readiness import (  # noqa: E402
    build_memory_lifecycle_readiness,
)
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
    "agent_brain/agent_integrations/hermes/import_export_tools.py",
    "agent_brain/agent_integrations/hermes/item_tools.py",
    "agent_brain/contracts/resource.py",
    "agent_brain/interfaces/cli/commands/crud.py",
    "agent_brain/interfaces/cli/commands/gc.py",
    "agent_brain/interfaces/cli/commands/index_maintenance.py",
    "agent_brain/interfaces/cli/commands/maintenance.py",
    "agent_brain/interfaces/cli/commands/review.py",
    "agent_brain/interfaces/cli/commands/subapps.py",
    "agent_brain/interfaces/cli/doctor_offline.py",
    "agent_brain/interfaces/mcp/onboarding.py",
    "agent_brain/interfaces/mcp/tools/graph.py",
    "agent_brain/interfaces/mcp/tools/io.py",
    "agent_brain/interfaces/mcp/tools/mutation_tools.py",
    "agent_brain/memory/evidence/import_service.py",
    "agent_brain/memory/evidence/integrations/obsidian.py",
    "agent_brain/memory/evidence/resource_store.py",
    "agent_brain/memory/governance/auto_governance.py",
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
    "agent_brain/memory/store/item_ids.py",
    "agent_brain/memory/store/items_store.py",
    "agent_brain/memory/store/pending.py",
    "agent_brain/memory/store/write_service.py",
    "agent_brain/platform/indexing/graph_index.py",
    "agent_brain/platform/indexing/index.py",
    "agent_brain/platform/indexing/index_schema.py",
    "agent_brain/platform/indexing/index_writer.py",
    "agent_brain/product/governance_readiness.py",
    "web/api/routes/governance.py",
    "scripts/generate-lifecycle-governance-report.py",
)
REQUIRED_SUPERSESSION_CASES = {
    "valid-supersession": "valid_supersession",
    "cycle": "cycle",
    "cross-tenant": "cross_tenant",
}
REQUIRED_PENDING_CASES = {
    "stale-pending": "stale_pending",
    "already-written": "already_written",
    "unsupported-feedback": "unsupported_feedback",
    "malformed-record": "malformed",
}
OPTIONAL_PENDING_CASES = {"ready-fact": "ready_pending"}
GRAPH_DRIFT_CASE = ("graph-drift", "graph_drift")
EVALUATION_NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
SUPERSESSION_ORACLES = {
    "valid-supersession": ("ready", "OK"),
    "cycle": ("blocked", "SUPERSESSION_CYCLE"),
    "cross-tenant": ("blocked", "TENANT_MISMATCH"),
}
PENDING_ORACLES = {
    "ready-fact": ("ready", "READY"),
    "stale-pending": ("stale_requires_review", "STALE_EPHEMERAL_MEMORY"),
    "already-written": ("already_written", "STABLE_ITEM_ALREADY_WRITTEN"),
    "unsupported-feedback": ("unsupported_type", "UNSUPPORTED_MEMORY_TYPE"),
    "malformed-record": ("malformed", "MALFORMED_JSON"),
}


@dataclass(frozen=True)
class FileSnapshot:
    path: Path
    data: bytes
    identity: tuple[int, int, int, int, int]


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns)


def _read_stable_file(path: Path) -> FileSnapshot:
    resolved = Path(path)
    before_path = resolved.lstat()
    if not stat.S_ISREG(before_path.st_mode):
        raise ValueError(f"not a regular file: {resolved}")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(resolved, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or _identity(before) != _identity(before_path):
            raise ValueError(f"file identity changed before read: {resolved}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    after_path = resolved.lstat()
    if _identity(before) != _identity(after) or _identity(after) != _identity(after_path):
        raise ValueError(f"file changed during read: {resolved}")
    data = b"".join(chunks)
    if len(data) != after.st_size:
        raise ValueError(f"short read: {resolved}")
    return FileSnapshot(resolved, data, _identity(after))


def _snapshot_unchanged(snapshot: FileSnapshot) -> bool:
    try:
        return _identity(snapshot.path.lstat()) == snapshot.identity
    except OSError:
        return False


def _atomic_write_text(path: Path, text: str) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = stat.S_IMODE(destination.stat().st_mode)
    except FileNotFoundError:
        mode = 0o644
    descriptor, temporary = tempfile.mkstemp(
        prefix=".amh-lifecycle-", dir=destination.parent
    )
    temporary_path = Path(temporary)
    try:
        os.fchmod(descriptor, mode)
        data = text.encode("utf-8")
        written = 0
        while written < len(data):
            written += os.write(descriptor, data[written:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary_path, destination)
        directory_fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON constant: {value}")


def _loads_json(data: bytes | str) -> dict[str, object]:
    value = json.loads(data, parse_constant=_reject_json_constant)
    if not isinstance(value, dict):
        raise ValueError("JSON top-level object required")
    return value


def canonical_json(value: object) -> str:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    )


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _content_manifest(
    snapshots: dict[str, FileSnapshot] | None = None,
) -> list[dict[str, str]]:
    manifest = []
    for relative in IMPLEMENTATION_PATHS:
        snapshot = (snapshots or {}).get(relative)
        data = snapshot.data if snapshot is not None else _read_stable_file(ROOT / relative).data
        manifest.append({"path": relative, "sha256": _sha256_bytes(data)})
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


def _validate_named_cases(
    raw_cases: object,
    *,
    required: dict[str, str],
    optional: dict[str, str] | None = None,
    required_fields: tuple[str, ...],
) -> list[str]:
    if not isinstance(raw_cases, list):
        return ["CASES_NOT_LIST"]
    errors: list[str] = []
    if not raw_cases:
        errors.append("CASES_EMPTY")
    allowed = {**required, **(optional or {})}
    seen_ids: set[str] = set()
    seen_kinds: set[str] = set()
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict):
            errors.append("CASE_NOT_OBJECT")
            continue
        case_id = raw_case.get("id")
        kind = raw_case.get("kind")
        if not isinstance(case_id, str) or not case_id:
            errors.append("CASE_ID_INVALID")
        elif case_id in seen_ids:
            errors.append("CASE_ID_DUPLICATE")
        else:
            seen_ids.add(case_id)
        if not isinstance(kind, str) or not kind:
            errors.append("CASE_KIND_INVALID")
        elif kind in seen_kinds:
            errors.append("CASE_KIND_DUPLICATE")
        else:
            seen_kinds.add(kind)
        if isinstance(case_id, str) and isinstance(kind, str):
            expected_kind = allowed.get(case_id)
            if expected_kind is None:
                errors.append("CASE_UNKNOWN")
            elif kind != expected_kind:
                errors.append("CASE_KIND_MISMATCH")
        if any(field not in raw_case for field in required_fields):
            errors.append("CASE_FIELDS_MISSING")
    if not set(required).issubset(seen_ids):
        errors.append("REQUIRED_CASE_MISSING")
    return sorted(set(errors))


def _validate_supersession_cases(raw_cases: object) -> list[str]:
    errors = _validate_named_cases(
        raw_cases,
        required=REQUIRED_SUPERSESSION_CASES,
        required_fields=("obsolete", "replacement"),
    )
    if isinstance(raw_cases, list):
        for case in raw_cases:
            if not isinstance(case, dict):
                continue
            if not isinstance(case.get("obsolete"), dict) or not isinstance(
                case.get("replacement"), dict
            ):
                errors.append("CASE_ITEM_SCHEMA_INVALID")
    return sorted(set(errors))


def _validate_pending_cases(raw_cases: object) -> list[str]:
    errors = _validate_named_cases(
        raw_cases,
        required=REQUIRED_PENDING_CASES,
        optional=OPTIONAL_PENDING_CASES,
        required_fields=(),
    )
    if isinstance(raw_cases, list):
        for case in raw_cases:
            if not isinstance(case, dict):
                continue
            record = case.get("record")
            raw_line = case.get("raw_line")
            if (isinstance(record, dict)) == (isinstance(raw_line, str)):
                errors.append("CASE_PAYLOAD_INVALID")
            if "seed_existing" in case and not isinstance(case["seed_existing"], bool):
                errors.append("CASE_SEED_INVALID")
    return sorted(set(errors))


def _validate_graph_drift_case(graph: object) -> list[str]:
    if not isinstance(graph, dict):
        return ["GRAPH_CASE_NOT_OBJECT"]
    errors: list[str] = []
    if (graph.get("id"), graph.get("kind")) != GRAPH_DRIFT_CASE:
        errors.append("GRAPH_CASE_ID_KIND_MISMATCH")
    items = graph.get("items")
    edges = graph.get("index_edges")
    if not isinstance(items, list) or not items or not all(
        isinstance(item, dict) for item in items
    ):
        errors.append("GRAPH_ITEMS_INVALID")
    if not isinstance(edges, list):
        errors.append("GRAPH_EDGES_INVALID")
    return sorted(set(errors))


def _supersession_input_matches_oracle(
    case_id: str,
    obsolete: MemoryItem,
    replacement: MemoryItem,
) -> bool:
    if case_id == "valid-supersession":
        return (
            obsolete.project == replacement.project
            and obsolete.tenant_id == replacement.tenant_id
            and replacement.created_at > obsolete.created_at
            and obsolete.superseded_by is None
            and obsolete.id in replacement.refs.mems
        )
    if case_id == "cycle":
        return replacement.superseded_by == obsolete.id
    if case_id == "cross-tenant":
        return bool(
            obsolete.tenant_id
            and replacement.tenant_id
            and obsolete.tenant_id != replacement.tenant_id
        )
    return False


def _pending_input_matches_oracle(case: dict[str, object]) -> bool:
    case_id = str(case.get("id"))
    if case_id == "malformed-record":
        raw_line = case.get("raw_line")
        if not isinstance(raw_line, str):
            return False
        try:
            json.loads(raw_line, parse_constant=_reject_json_constant)
        except (ValueError, json.JSONDecodeError):
            return True
        return False
    record = case.get("record")
    if not isinstance(record, dict) or not isinstance(record.get("item"), dict):
        return False
    item = record["item"]
    type_value = item.get("type")
    if case_id == "ready-fact":
        return bool(type_value == "fact")
    if case_id == "stale-pending":
        created = record.get("original_created_at")
        if not isinstance(created, str) or type_value not in {"signal", "handoff"}:
            return False
        created_at = datetime.fromisoformat(created).astimezone(timezone.utc)
        return (EVALUATION_NOW - created_at).days >= 30
    if case_id == "already-written":
        return type_value == "fact" and case.get("seed_existing") is True
    if case_id == "unsupported-feedback":
        return bool(type_value == "feedback")
    return False


def _run_supersession_contract(fixture: dict[str, object]) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    passed = True
    cases = fixture.get("supersession_cases")
    schema_errors = _validate_supersession_cases(cases)
    if schema_errors:
        return {"status": "fail", "cases": [], "schema_errors": schema_errors}
    assert isinstance(cases, list)
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
            case_id = str(case.get("id"))
            expected = SUPERSESSION_ORACLES.get(case_id)
            input_matches = _supersession_input_matches_oracle(
                case_id, obsolete, replacement
            )
            case_pass = input_matches and expected == (result.status, result.reason)
            if case_id == "valid-supersession":
                case_pass = case_pass and candidate_ids[:1] == [replacement.id]
            passed = passed and case_pass
            rows.append(
                {
                    "id": case.get("id"),
                    "status": result.status,
                    "reason": result.reason,
                    "candidate_ids": candidate_ids,
                    "input_matches_oracle": input_matches,
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
    schema_errors = _validate_pending_cases(cases)
    if schema_errors:
        return {"status": "fail", "cases": [], "schema_errors": schema_errors}
    assert isinstance(cases, list)
    with tempfile.TemporaryDirectory(prefix="amh-pending-synthetic-") as directory:
        brain = Path(directory).resolve() / "brain"
        store = ItemsStore(brain / "items")
        input_matches: dict[str, bool] = {}
        for raw_case in cases:
            if not isinstance(raw_case, dict):
                continue
            _materialize_pending_case(brain, store, raw_case)
            input_matches[str(raw_case["id"])] = _pending_input_matches_oracle(raw_case)
        before = _tree_bytes(brain)
        from agent_brain.memory.store import pending as pending_module

        original_utc_now = pending_module._utc_now
        pending_module._utc_now = lambda: EVALUATION_NOW
        try:
            preview = PendingQueue(brain=brain).preview(limit=len(cases) + 1)
        finally:
            pending_module._utc_now = original_utc_now
        after = _tree_bytes(brain)
        rows = []
        passed = not preview.scan_unavailable and before == after
        for record in preview.records:
            case_id = Path(record.path).stem
            expected_result = PENDING_ORACLES.get(case_id)
            case_pass = input_matches.get(case_id, False) and expected_result == (
                record.classification,
                record.reason,
            )
            passed = passed and case_pass
            rows.append(
                {
                    "id": case_id,
                    "classification": record.classification,
                    "reason": record.reason,
                    "input_matches_oracle": input_matches.get(case_id, False),
                    "pass": case_pass,
                }
            )
        passed = passed and len(rows) == len(input_matches)
        return {
            "status": "pass" if passed else "fail",
            "preview_only": before == after,
            "cases": sorted(rows, key=lambda row: str(row["id"])),
        }


def _run_graph_drift_contract(fixture: dict[str, object]) -> dict[str, object]:
    graph = fixture.get("graph_drift")
    schema_errors = _validate_graph_drift_case(graph)
    if schema_errors:
        return {"status": "fail", "schema_errors": schema_errors}
    assert isinstance(graph, dict)
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
        item_by_id = {
            str(item.get("id")): item for item in items if isinstance(item, dict)
        }
        declared_edges = {
            (source, target)
            for source, target in (
                (str(item_id), str(item.get("superseded_by")))
                for item_id, item in item_by_id.items()
                if item.get("superseded_by")
            )
        }
        index_edges = {
            (str(edge[0]), str(edge[1]))
            for edge in edges
            if isinstance(edge, list) and len(edge) == 2
        }
        input_matches = len(declared_edges) == 1 and declared_edges.isdisjoint(index_edges)
        return {
            "status": "pass" if input_matches and actual == 1 else "fail",
            "expected_drift_count": 1,
            "observed_drift_count": actual,
            "input_matches_oracle": input_matches,
        }


@contextmanager
def _isolated_brain(brain: Path) -> Iterator[None]:
    old_brain = os.environ.get("BRAIN_DIR")
    old_embedding = os.environ.get("MEMORY_HUB_TEST_EMBEDDING")
    os.environ["BRAIN_DIR"] = str(brain)
    os.environ["MEMORY_HUB_TEST_EMBEDDING"] = "1"
    try:
        yield
    finally:
        if old_brain is None:
            os.environ.pop("BRAIN_DIR", None)
        else:
            os.environ["BRAIN_DIR"] = old_brain
        if old_embedding is None:
            os.environ.pop("MEMORY_HUB_TEST_EMBEDDING", None)
        else:
            os.environ["MEMORY_HUB_TEST_EMBEDDING"] = old_embedding


def _surface_pair(brain: Path, suffix: str) -> tuple[MemoryItem, MemoryItem]:
    store = ItemsStore(brain / "items")
    obsolete = _write_item(
        store,
        {
            "id": f"mem-20260720-100000-{suffix}-obsolete",
            "type": "fact",
            "created_at": "2026-07-20T10:00:00+00:00",
            "title": f"Synthetic {suffix} obsolete",
            "summary": f"Synthetic {suffix} obsolete",
            "project": "surface-parity",
            "tenant_id": "tenant-fixture-a",
        },
    )
    replacement = _write_item(
        store,
        {
            "id": f"mem-20260720-110000-{suffix}-replacement",
            "type": "fact",
            "created_at": "2026-07-20T11:00:00+00:00",
            "title": f"Synthetic {suffix} replacement",
            "summary": f"Synthetic {suffix} replacement",
            "project": "surface-parity",
            "tenant_id": "tenant-fixture-a",
        },
    )
    return obsolete, replacement


def _surface_result(
    *,
    preview: dict[str, object],
    applied: dict[str, object],
    before: dict[str, bytes],
    after_preview: dict[str, bytes],
    brain: Path,
    obsolete_id: str,
) -> dict[str, object]:
    obsolete = ItemsStore(brain / "items").get(obsolete_id)
    mutated = obsolete is not None and obsolete[0].superseded_by is not None
    return {
        "preview_status": preview.get("status"),
        "preview_dry_run": preview.get("dry_run"),
        "preview_zero_mutation": before == after_preview,
        "apply_status": applied.get("status"),
        "apply_dry_run": applied.get("dry_run"),
        "apply_mutated": mutated,
    }


def _first_result(payload: dict[str, object]) -> dict[str, object]:
    results = payload.get("results")
    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        raise ValueError("surface payload requires a result object")
    return results[0]


def _run_cli_surface(brain: Path) -> dict[str, object]:
    from typer.testing import CliRunner
    import agent_brain.interfaces.cli  # noqa: F401
    from agent_brain.interfaces.cli._app import app

    obsolete, replacement = _surface_pair(brain, "cli")
    pair = f"{obsolete.id}:{replacement.id}"
    runner = CliRunner()
    before = _tree_bytes(brain)
    preview_result = runner.invoke(
        app,
        ["govern", "apply-lifecycle", "--supersede", pair, "--format", "json"],
    )
    if preview_result.exit_code != 0:
        raise ValueError("CLI lifecycle preview failed")
    preview_payload = _loads_json(preview_result.stdout)
    after_preview = _tree_bytes(brain)
    apply_result = runner.invoke(
        app,
        [
            "govern",
            "apply-lifecycle",
            "--supersede",
            pair,
            "--format",
            "json",
            "--apply",
        ],
    )
    if apply_result.exit_code != 0:
        raise ValueError("CLI lifecycle apply failed")
    apply_payload = _loads_json(apply_result.stdout)
    return _surface_result(
        preview=_first_result(preview_payload),
        applied=_first_result(apply_payload),
        before=before,
        after_preview=after_preview,
        brain=brain,
        obsolete_id=obsolete.id,
    )


def _run_web_surface(brain: Path) -> dict[str, object]:
    from web._base import _components_cache as web_components_cache
    from web.api.routes.governance import (
        LifecycleActionRequest,
        LifecycleApplyRequest,
        lifecycle_apply,
    )
    from web.auth import CurrentUser

    obsolete, replacement = _surface_pair(brain, "web")
    action = LifecycleActionRequest(
        action="supersede",
        item_id=obsolete.id,
        replacement_id=replacement.id,
    )
    user = CurrentUser("fixture-admin", "tenant-fixture-a", "admin")
    before = _tree_bytes(brain)
    preview_payload = asyncio.run(
        lifecycle_apply(LifecycleApplyRequest(actions=[action]), user)
    )
    after_preview = _tree_bytes(brain)
    apply_payload = asyncio.run(
        lifecycle_apply(LifecycleApplyRequest(actions=[action], apply=True), user)
    )
    web_components_cache.clear()
    return _surface_result(
        preview=preview_payload["results"][0],
        applied=apply_payload["results"][0],
        before=before,
        after_preview=after_preview,
        brain=brain,
        obsolete_id=obsolete.id,
    )


def _run_mcp_surface(brain: Path) -> dict[str, object]:
    from agent_brain.interfaces.mcp.tools._shared import (
        _components,
        _components_cache,
    )
    from agent_brain.interfaces.mcp.tools.graph import link_memories

    obsolete, replacement = _surface_pair(brain, "mcp")
    _components()
    before = _tree_bytes(brain)
    preview_payload = link_memories(
        replacement.id, obsolete.id, relation="supersedes"
    )
    after_preview = _tree_bytes(brain)
    apply_payload = link_memories(
        replacement.id, obsolete.id, relation="supersedes", apply=True
    )
    _components_cache.clear()
    return _surface_result(
        preview=preview_payload,
        applied=apply_payload,
        before=before,
        after_preview=after_preview,
        brain=brain,
        obsolete_id=obsolete.id,
    )


def _run_surface_parity(fixture: dict[str, object]) -> dict[str, object]:
    actions = [
        "archive",
        "defer",
        "keep-active",
        "revert-supersession",
        "supersede",
    ]
    raw_actions = fixture.get("surface_actions")
    fixture_actions = (
        sorted(str(value) for value in raw_actions)
        if isinstance(raw_actions, list)
        else []
    )
    surfaces: dict[str, dict[str, object]] = {}
    with tempfile.TemporaryDirectory(prefix="amh-surface-parity-") as directory:
        root = Path(directory).resolve()
        for name, runner in (
            ("cli", _run_cli_surface),
            ("web", _run_web_surface),
            ("mcp", _run_mcp_surface),
        ):
            brain = root / name
            with _isolated_brain(brain):
                surfaces[name] = runner(brain)
    preview_ok = all(
        row["preview_dry_run"] is True and row["preview_zero_mutation"] is True
        for row in surfaces.values()
    )
    apply_ok = all(
        row["apply_dry_run"] is False and row["apply_mutated"] is True
        for row in surfaces.values()
    )
    status = "pass" if fixture_actions == actions and preview_ok and apply_ok else "fail"
    return {
        "status": status,
        "actions": actions,
        "surfaces": surfaces,
        "default_preview_zero_mutation": preview_ok,
        "explicit_apply_mutates": apply_ok,
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
    *,
    fixture_path: Path = FIXTURE_PATH,
    fixture_snapshot: FileSnapshot | None = None,
    implementation_snapshots: dict[str, FileSnapshot] | None = None,
) -> dict[str, object]:
    snapshot = fixture_snapshot or _read_stable_file(Path(fixture_path))
    raw_fixture = snapshot.data
    fixture = _loads_json(raw_fixture)
    manifest = _content_manifest(implementation_snapshots)
    failed_gates: list[str] = []
    if fixture.get("schema_version") != FIXTURE_SCHEMA_VERSION:
        failed_gates.append("fixture_schema")
    if raw_fixture.decode("utf-8") != canonical_json(fixture):
        failed_gates.append("fixture_canonical")
    supersession = _run_supersession_contract(fixture)
    pending = _run_pending_contract(fixture)
    graph_drift = _run_graph_drift_contract(fixture)
    upstream_contracts = (supersession, pending, graph_drift)
    if all(result["status"] == "pass" for result in upstream_contracts):
        surface_parity = _run_surface_parity(fixture)
    else:
        surface_parity = {
            "status": "not_run",
            "reason": "UPSTREAM_CONTRACT_FAILED",
            "default_preview_zero_mutation": False,
            "explicit_apply_mutates": False,
        }
    contracts = {
        "supersession_contract": supersession,
        "pending_contract": pending,
        "graph_drift_contract": graph_drift,
        "surface_parity": surface_parity,
    }
    for gate, result in contracts.items():
        if result["status"] == "fail":
            failed_gates.append(gate)
        if result.get("schema_errors") and "fixture_schema" not in failed_gates:
            failed_gates.append("fixture_schema")
    privacy = _privacy_contract(fixture, contracts)
    if privacy["status"] != "pass":
        failed_gates.append("privacy")
    synthetic_status = "fail" if failed_gates else "pass"
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "implementation_hash": _implementation_hash(manifest),
        "implementation_manifest": manifest,
        "fixture_hash": _sha256_bytes(raw_fixture),
        "fixture_schema_version": fixture.get("schema_version"),
        "evidence_scope": {
            "code_and_synthetic_fixture": synthetic_status,
            "real_brain_dry_run": "pending",
        },
        "evaluation_now": EVALUATION_NOW.isoformat(),
        **contracts,
        "privacy": privacy,
        "failed_gates": failed_gates,
        "synthetic_status": synthetic_status,
        "release_status": "pending",
        "release_truth": {
            "branch_protection_required_context": "pending_external_configuration",
            "workflow_job": "configured_non_advisory",
        },
        "overall_status": "fail" if failed_gates else "pending",
    }


def render_markdown(report: dict[str, object]) -> str:
    synthetic = str(report["synthetic_status"]).upper()
    overall = str(report["overall_status"]).upper()
    failed_raw = report.get("failed_gates")
    failed = failed_raw if isinstance(failed_raw, list) else []
    failed_text = "、".join(str(value) for value in failed) if failed else "无"
    contract_statuses: dict[str, str] = {}
    for name in (
        "supersession_contract",
        "pending_contract",
        "graph_drift_contract",
        "surface_parity",
        "privacy",
    ):
        contract = report.get(name)
        if not isinstance(contract, dict):
            raise ValueError(f"missing report contract: {name}")
        contract_statuses[name] = str(contract.get("status", "missing")).upper()
    return (
        "# 可信记忆生命周期治理就绪报告\n\n"
        f"- 代码与 synthetic fixture：`{synthetic}`\n"
        f"- 整体发布状态：`{overall}`\n"
        "- branch protection required context：`PENDING`（需仓库管理员外部配置）\n"
        "- 真实 brain dry-run：`PENDING`\n"
        f"- 失败门禁：{failed_text}\n\n"
        "本报告由提交内的纯 synthetic fixture 离线重放生成，只证明代码与 fixture 合同。"
        "它不读取真实 brain，也不代表真实 pending 或 stale backlog 已完成治理；"
        "workflow job 已配置但当前不是 required context。\n\n"
        "## 合同结果\n\n"
        f"- Supersession：`{contract_statuses['supersession_contract']}`\n"
        f"- Pending：`{contract_statuses['pending_contract']}`\n"
        f"- Graph drift：`{contract_statuses['graph_drift_contract']}`\n"
        f"- CLI / Web surface parity：`{contract_statuses['surface_parity']}`\n"
        f"- Privacy：`{contract_statuses['privacy']}`\n\n"
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
    report_snapshot = _read_stable_file(Path(report_path))
    fixture_snapshot = _read_stable_file(Path(fixture_path))
    committed = _loads_json(report_snapshot.data)
    fixture_hash = _sha256_bytes(fixture_snapshot.data)
    if committed.get("fixture_hash") != fixture_hash:
        mismatches.append("fixture_hash_mismatch")
    expected = canonical_json(
        generate_report(
            fixture_path=fixture_path,
            fixture_snapshot=fixture_snapshot,
        )
    )
    if report_snapshot.data.decode("utf-8") != expected:
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
        fixture_snapshot = _read_stable_file(FIXTURE_PATH)
        implementation_snapshots = {
            relative: _read_stable_file(ROOT / relative)
            for relative in IMPLEMENTATION_PATHS
        }
        report = generate_report(
            fixture_snapshot=fixture_snapshot,
            implementation_snapshots=implementation_snapshots,
        )
        json_text = canonical_json(report)
        markdown = render_markdown(report)
        input_snapshots = [fixture_snapshot, *implementation_snapshots.values()]
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        print(f"lifecycle-governance: ERROR {type(error).__name__}")
        return EXIT_INVALID_INPUT
    if args.check:
        stale: list[str] = []
        try:
            report_snapshot = _read_stable_file(REPORT_PATH)
            markdown_snapshot = _read_stable_file(MARKDOWN_PATH)
        except (OSError, ValueError):
            print("lifecycle-governance: STALE report_or_markdown_missing")
            return EXIT_STALE_EVIDENCE
        if report_snapshot.data.decode("utf-8") != json_text:
            stale.append("report_bytes_mismatch")
        if markdown_snapshot.data.decode("utf-8") != markdown:
            stale.append("markdown_bytes_mismatch")
        if not all(
            _snapshot_unchanged(snapshot)
            for snapshot in [
                *input_snapshots,
                report_snapshot,
                markdown_snapshot,
            ]
        ):
            stale.append("file_identity_changed_during_check")
        if stale:
            print("lifecycle-governance: STALE " + ",".join(stale))
            return EXIT_STALE_EVIDENCE
    else:
        try:
            _atomic_write_text(REPORT_PATH, json_text)
            _atomic_write_text(MARKDOWN_PATH, markdown)
        except OSError as error:
            print(f"lifecycle-governance: ERROR {type(error).__name__}")
            return EXIT_INVALID_INPUT
    if report["synthetic_status"] != "pass":
        failed = report.get("failed_gates")
        failed_names = failed if isinstance(failed, list) else ["invalid_failed_gates"]
        print("lifecycle-governance: FAIL " + ",".join(map(str, failed_names)))
        return EXIT_FAILED_GATES
    print(
        "lifecycle-governance: PASS synthetic=PASS release=PENDING "
        "real-brain-dry-run=PENDING"
    )
    return EXIT_PASS


if __name__ == "__main__":
    raise SystemExit(main())
