"""Isolated production replay through the real UserPromptSubmit hook."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from typing import Mapping
import uuid

from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.evaluation.hook_recall_evidence import (
    HookCaseEvidence,
    HookRecallExpectedProvenance,
    derive_hook_recall_gate_failures,
)
from agent_brain.evaluation.recall_quality_corpus import (
    RecallQualityCorpus,
    load_recall_quality_corpus,
)
from agent_brain.memory.context.injection_cohorts import iter_injection_cohorts
from agent_brain.memory.governance.recall_events import iter_gap_records
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex
from agent_brain.platform.telemetry_safety import telemetry_digest


_ITEM_ID_RE = re.compile(r"\(id:([^\s)]+)")
_MAX_PROCESS_OUTPUT_BYTES = 1_048_576
_IMPLEMENTATION_PATHS = (
    "agent_brain/evaluation/hook_recall_evidence.py",
    "agent_brain/evaluation/hook_recall_runner.py",
    "agent_brain/evaluation/recall_quality_corpus.py",
    "agent_brain/interfaces/cli/routed_query.py",
    "agent_brain/memory/context/injection_gateway.py",
    "agent_brain/memory/context/query_signal.py",
    "agent_brain/memory/recall/retrieval.py",
)


@dataclass(frozen=True)
class ParsedHookOutput:
    status: str
    item_ids: tuple[str, ...]
    protocol_valid: bool
    reason: str


@dataclass(frozen=True)
class ProcessResult:
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool
    duration_ms: float


def parse_hook_output(raw: bytes) -> ParsedHookOutput:
    """Parse the exact adapter envelope without accepting stdout contamination."""

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ParsedHookOutput("error", (), False, "malformed_hook_json")
    if payload == {}:
        return ParsedHookOutput("empty", (), True, "empty_protocol")
    if not isinstance(payload, dict) or set(payload) != {"hookSpecificOutput"}:
        return ParsedHookOutput("error", (), False, "invalid_hook_envelope")
    envelope = payload.get("hookSpecificOutput")
    if (
        not isinstance(envelope, dict)
        or set(envelope) != {"hookEventName", "additionalContext"}
        or envelope.get("hookEventName") != "UserPromptSubmit"
        or not isinstance(envelope.get("additionalContext"), str)
        or not envelope["additionalContext"]
    ):
        return ParsedHookOutput("error", (), False, "invalid_hook_envelope")
    ids = tuple(dict.fromkeys(_ITEM_ID_RE.findall(envelope["additionalContext"])))
    if not ids:
        return ParsedHookOutput("error", (), False, "missing_context_item_ids")
    return ParsedHookOutput("injected", ids, True, "included")


def materialize_hook_fixture_brain(
    corpus: RecallQualityCorpus,
    brain_dir: Path,
) -> None:
    if brain_dir.exists() or brain_dir.is_symlink():
        raise ValueError("hook evidence brain must not already exist")
    brain_dir.mkdir(parents=True, mode=0o700)
    store = ItemsStore(brain_dir / "items")
    embedder = HashingEmbedder()
    index = HubIndex(brain_dir / "index.db", embedding_dim=embedder.dim)
    seen: dict[str, tuple[MemoryItem, str]] = {}
    try:
        for case in corpus.cases:
            for raw in case.memory_items:
                item, body = _memory_item_from_fixture(raw)
                previous = seen.get(item.id)
                if previous is not None and previous != (item, body):
                    raise ValueError(f"conflicting fixture item: {item.id}")
                if previous is None:
                    seen[item.id] = (item, body)
                    store.write(item, body)
                    index.upsert(item, body, embedding=embedder.embed(body))
    finally:
        index.close()


def run_hook_process(
    command: Path,
    payload: bytes,
    *,
    env: Mapping[str, str],
    timeout: float,
) -> ProcessResult:
    started = time.perf_counter()
    process = subprocess.Popen(
        [str(command)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(env),
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(payload, timeout=timeout)
        return ProcessResult(
            process.returncode,
            stdout,
            stderr,
            False,
            _elapsed_ms(started),
        )
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
        return ProcessResult(None, stdout, stderr, True, _elapsed_ms(started))


def run_hook_recall_evidence(
    *,
    root: Path,
    corpus_path: Path,
    hook_path: Path,
    adapter: str,
    timeout_seconds: float,
    workspace: Path,
) -> dict[str, object]:
    """Execute every Hook-applicable case and return a terminal manifest."""

    root = Path(root).resolve()
    corpus_path = Path(corpus_path).resolve()
    hook_path = Path(hook_path).resolve()
    workspace = Path(workspace).resolve()
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    started_at = _timestamp()
    run_id = str(uuid.uuid4())
    corpus = load_recall_quality_corpus(corpus_path)
    brain_dir = workspace / f"hook-recall-{run_id}" / "brain"
    materialize_hook_fixture_brain(corpus, brain_dir)
    expected, provenance = collect_expected_provenance(
        root=root,
        corpus=corpus,
        hook_path=hook_path,
        adapter=adapter,
        timeout_seconds=timeout_seconds,
    )
    environment = _hook_environment(
        root=root,
        brain_dir=brain_dir,
        adapter=adapter,
        timeout_seconds=timeout_seconds,
    )
    results: list[HookCaseEvidence] = []
    for case in corpus.cases:
        expectation = case.hook_expectation
        if not expectation.applicable:
            results.append(HookCaseEvidence(
                case_id=case.id,
                applicable=False,
                expected_status=None,
                actual_status="not_applicable",
                expected_item_ids=(),
                observed_item_ids=(),
                prohibited_item_ids=(),
                cohort_item_ids=(),
                protocol_valid=True,
                cohort_consistent=True,
                gap_consistent=True,
                exit_code=None,
                duration_ms=0.0,
                reason=expectation.reason or "invalid_not_applicable_reason",
            ))
            continue
        session_id = _session_id(run_id, case.id)
        payload = json.dumps({
            "prompt": case.query,
            "session_id": session_id,
            "cwd": expectation.cwd,
            "hook_event_name": "UserPromptSubmit",
        }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        process = run_hook_process(
            hook_path,
            payload,
            env=environment,
            timeout=timeout_seconds,
        )
        parsed = _parse_process_result(process)
        cohorts = tuple(iter_injection_cohorts(
            brain_dir,
            adapter=adapter,
            session_id=session_id,
        ))
        session_digest = telemetry_digest(session_id)
        gaps = tuple(
            gap
            for gap in iter_gap_records(brain_dir)
            if gap.adapter == adapter and gap.session_digest == session_digest
        )
        cohort_ids = cohorts[0].item_ids if len(cohorts) == 1 else ()
        if parsed.status == "injected":
            cohort_consistent = (
                len(cohorts) == 1 and tuple(cohort_ids) == parsed.item_ids
            )
            gap_consistent = not gaps or (
                len(gaps) == 1
                and gaps[0].reason == "partial_candidates_rejected"
            )
            reason = "included"
        elif parsed.status == "empty":
            cohort_consistent = not cohorts
            gap_consistent = len(gaps) == 1
            reason = gaps[0].reason if len(gaps) == 1 else "missing_gap_evidence"
        else:
            cohort_consistent = not cohorts
            gap_consistent = not gaps
            reason = parsed.reason
        results.append(HookCaseEvidence(
            case_id=case.id,
            applicable=True,
            expected_status=expectation.expected_status,
            actual_status=parsed.status,
            expected_item_ids=expectation.expected_item_ids,
            observed_item_ids=parsed.item_ids,
            prohibited_item_ids=expectation.prohibited_item_ids,
            cohort_item_ids=tuple(cohort_ids),
            protocol_valid=parsed.protocol_valid,
            cohort_consistent=cohort_consistent,
            gap_consistent=gap_consistent,
            exit_code=process.exit_code,
            duration_ms=round(process.duration_ms, 3),
            reason=reason,
        ))

    applicable_count = sum(result.applicable for result in results)
    manifest: dict[str, object] = {
        "schema_version": 1,
        "run_id": run_id,
        "started_at": started_at,
        "completed_at": _timestamp(),
        "status": "pass",
        "provenance": provenance,
        "counts": {
            "planned": len(corpus.cases),
            "applicable": applicable_count,
            "not_applicable": len(corpus.cases) - applicable_count,
            "executed": applicable_count,
        },
        "planned_case_ids": [case.id for case in corpus.cases],
        "results": [result.to_dict() for result in results],
        "failed_gates": [],
    }
    failures = derive_hook_recall_gate_failures(manifest, expected=expected)
    manifest["failed_gates"] = failures
    manifest["status"] = "pass" if not failures else "fail"
    return manifest


def collect_expected_provenance(
    *,
    root: Path,
    corpus: RecallQualityCorpus,
    hook_path: Path,
    adapter: str,
    timeout_seconds: float,
    require_clean: bool = False,
) -> tuple[HookRecallExpectedProvenance, dict[str, object]]:
    git_commit = _git_output(root, "rev-parse", "HEAD")
    dirty = bool(_git_output(
        root,
        "status",
        "--porcelain",
        "--untracked-files=no",
    ))
    config_sha256 = _json_sha256({
        "adapter": adapter,
        "hook_output_format": "json",
        "memory_hub_test_embedding": "1",
        "timeout_seconds": timeout_seconds,
    })
    expected = HookRecallExpectedProvenance(
        git_commit=git_commit,
        hook_sha256=_file_sha256(hook_path),
        implementation_sha256=_implementation_sha256(root),
        corpus_sha256=corpus.sha256,
        corpus_version=corpus.corpus_version,
        config_sha256=config_sha256,
        require_clean=require_clean,
    )
    return expected, {
        "git_commit": expected.git_commit,
        "dirty": dirty,
        "hook_sha256": expected.hook_sha256,
        "implementation_sha256": expected.implementation_sha256,
        "corpus_sha256": expected.corpus_sha256,
        "corpus_version": expected.corpus_version,
        "config_sha256": expected.config_sha256,
        "adapter": adapter,
        "timeout_seconds": timeout_seconds,
    }


def _parse_process_result(process: ProcessResult) -> ParsedHookOutput:
    if process.timed_out:
        return ParsedHookOutput("timeout", (), False, "hook_timeout")
    if process.exit_code != 0:
        return ParsedHookOutput("error", (), False, "hook_nonzero_exit")
    if len(process.stdout) > _MAX_PROCESS_OUTPUT_BYTES:
        return ParsedHookOutput("error", (), False, "hook_stdout_too_large")
    if process.stderr or len(process.stderr) > _MAX_PROCESS_OUTPUT_BYTES:
        return ParsedHookOutput("error", (), False, "hook_stderr_not_empty")
    return parse_hook_output(process.stdout)


def _memory_item_from_fixture(raw: Mapping[str, object]) -> tuple[MemoryItem, str]:
    payload = dict(raw)
    body = str(payload.pop("body", ""))
    return MemoryItem.model_validate(payload), body


def _hook_environment(
    *,
    root: Path,
    brain_dir: Path,
    adapter: str,
    timeout_seconds: float,
) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update({
        "AGENT_MEMORY_HUB_ADAPTER": adapter,
        "AGENT_MEMORY_HUB_DEBUG_QUERY_SIGNAL": "0",
        "AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT": "json",
        "AGENT_MEMORY_HUB_HOOK_TRACE_EMPTY": "0",
        "AGENT_MEMORY_HUB_PYTHON": sys.executable,
        "AGENT_MEMORY_HUB_SEARCH_TIMEOUT_SECONDS": str(timeout_seconds),
        "BRAIN_DIR": str(brain_dir),
        "MEMORY_HUB_TEST_EMBEDDING": "1",
        "PYTHONPATH": str(root) + os.pathsep + environment.get("PYTHONPATH", ""),
    })
    return environment


def _implementation_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for relative in _IMPLEMENTATION_PATHS:
        path = root / relative
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _file_sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _json_sha256(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _git_output(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _session_id(run_id: str, case_id: str) -> str:
    case_digest = hashlib.sha256(case_id.encode()).hexdigest()[:10]
    return f"hre-{run_id[:8]}-{case_digest}"


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "ParsedHookOutput",
    "ProcessResult",
    "collect_expected_provenance",
    "materialize_hook_fixture_brain",
    "parse_hook_output",
    "run_hook_process",
    "run_hook_recall_evidence",
]
