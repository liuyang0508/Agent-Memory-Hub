"""Versioned, fail-closed corpus contract for recall quality evaluation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


RecallQualitySplit = Literal["calibration", "heldout", "production_replay"]
HookExpectedStatus = Literal["injected", "empty"]
_SPLITS = frozenset({"calibration", "heldout", "production_replay"})
_ANSWERABILITY = frozenset({"supported", "partial", "insufficient", "not_applicable"})
_TEMPORAL = frozenset({"stable", "current", "stale", "conflict", "not_applicable"})
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_HOOK_NOT_APPLICABLE_REASONS = frozenset({
    "explicit_project_scope_unavailable",
})
_FORBIDDEN_SOURCE_FIELDS = frozenset({
    "cwd",
    "raw_prompt",
    "session_id",
    "source_path",
    "transcript_path",
})
_REQUIRED_CASE_FIELDS = frozenset({
    "id",
    "split",
    "category",
    "language",
    "query",
    "expected_item_ids",
    "prohibited_item_ids",
    "expected_admission",
    "expected_answerability",
    "expected_temporal",
    "expected_abstention",
    "expected_injection",
    "project_scope",
    "source_kind",
    "source_digest",
    "memory_items",
    "hook_expectation",
})


@dataclass(frozen=True)
class HookExpectation:
    applicable: bool
    cwd: str | None = None
    expected_status: HookExpectedStatus | None = None
    expected_item_ids: tuple[str, ...] = ()
    prohibited_item_ids: tuple[str, ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class RecallQualityCase:
    id: str
    split: RecallQualitySplit
    category: str
    language: str
    query: str
    expected_item_ids: tuple[str, ...]
    prohibited_item_ids: tuple[str, ...]
    expected_admission: bool
    expected_answerability: str
    expected_temporal: str
    expected_abstention: bool
    expected_injection: bool
    project_scope: dict[str, Any] | None
    source_kind: str
    source_digest: str
    memory_items: tuple[dict[str, Any], ...]
    hook_expectation: HookExpectation


@dataclass(frozen=True)
class RecallQualityCorpus:
    schema_version: int
    corpus_version: str
    append_only: bool
    cases: tuple[RecallQualityCase, ...]
    sha256: str


def load_recall_quality_corpus(path: Path) -> RecallQualityCorpus:
    raw = Path(path).read_bytes()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("malformed recall quality corpus") from exc
    if not isinstance(payload, dict):
        raise ValueError("recall quality corpus must be an object")
    if payload.get("schema_version") != 2:
        raise ValueError("unsupported recall quality schema version")
    if payload.get("append_only") is not True:
        raise ValueError("recall quality corpus must be append-only")
    corpus_version = payload.get("corpus_version")
    if not isinstance(corpus_version, str) or not corpus_version.strip():
        raise ValueError("recall quality corpus_version must be non-empty")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("recall quality cases must be a non-empty list")

    cases: list[RecallQualityCase] = []
    seen_ids: set[str] = set()
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, dict):
            raise ValueError(f"recall quality case #{index} must be an object")
        forbidden = sorted(_FORBIDDEN_SOURCE_FIELDS & raw_case.keys())
        if forbidden:
            raise ValueError("forbidden source field: " + ", ".join(forbidden))
        missing = sorted(_REQUIRED_CASE_FIELDS - raw_case.keys())
        if missing:
            raise ValueError("missing recall quality case field: " + ", ".join(missing))
        case = _parse_case(raw_case)
        if case.id in seen_ids:
            raise ValueError(f"duplicate recall quality case id: {case.id}")
        seen_ids.add(case.id)
        cases.append(case)

    return RecallQualityCorpus(
        schema_version=2,
        corpus_version=corpus_version.strip(),
        append_only=True,
        cases=tuple(cases),
        sha256="sha256:" + hashlib.sha256(raw).hexdigest(),
    )


def _parse_case(data: dict[str, Any]) -> RecallQualityCase:
    split = data["split"]
    if split not in _SPLITS:
        raise ValueError(f"unsupported recall quality split: {split!r}")
    answerability = data["expected_answerability"]
    if answerability not in _ANSWERABILITY:
        raise ValueError(f"unsupported answerability expectation: {answerability!r}")
    temporal = data["expected_temporal"]
    if temporal not in _TEMPORAL:
        raise ValueError(f"unsupported temporal expectation: {temporal!r}")
    for field in ("expected_admission", "expected_abstention", "expected_injection"):
        if type(data[field]) is not bool:
            raise ValueError(f"{field} must be boolean")
    expected = _string_tuple(data["expected_item_ids"], "expected_item_ids")
    prohibited = _string_tuple(data["prohibited_item_ids"], "prohibited_item_ids")
    if set(expected) & set(prohibited):
        raise ValueError("expected and prohibited item ids overlap")
    digest = data["source_digest"]
    if not isinstance(digest, str) or not _DIGEST_RE.fullmatch(digest):
        raise ValueError("source_digest must be a sha256 digest")
    project_scope = data["project_scope"]
    if project_scope is not None and not isinstance(project_scope, dict):
        raise ValueError("project_scope must be an object or null")
    memory_items = data["memory_items"]
    if not isinstance(memory_items, list) or any(not isinstance(row, dict) for row in memory_items):
        raise ValueError("memory_items must be an object list")
    text_fields = ("id", "category", "language", "query", "source_kind")
    for field in text_fields:
        if not isinstance(data[field], str) or not data[field].strip():
            raise ValueError(f"{field} must be non-empty")
    return RecallQualityCase(
        id=data["id"].strip(),
        split=split,
        category=data["category"].strip(),
        language=data["language"].strip(),
        query=data["query"].strip(),
        expected_item_ids=expected,
        prohibited_item_ids=prohibited,
        expected_admission=data["expected_admission"],
        expected_answerability=answerability,
        expected_temporal=temporal,
        expected_abstention=data["expected_abstention"],
        expected_injection=data["expected_injection"],
        project_scope=dict(project_scope) if project_scope is not None else None,
        source_kind=data["source_kind"].strip(),
        source_digest=digest,
        memory_items=tuple(dict(row) for row in memory_items),
        hook_expectation=_parse_hook_expectation(data["hook_expectation"]),
    )


def _parse_hook_expectation(value: Any) -> HookExpectation:
    if not isinstance(value, dict) or type(value.get("applicable")) is not bool:
        raise ValueError("hook_expectation must declare boolean applicable")
    if value["applicable"]:
        required = {
            "applicable",
            "cwd",
            "expected_status",
            "expected_item_ids",
            "prohibited_item_ids",
        }
        if set(value) != required:
            raise ValueError(
                "applicable hook expectation must use the complete contract"
            )
        cwd = value["cwd"]
        status = value["expected_status"]
        if not isinstance(cwd, str) or not cwd.startswith("/sanitized/"):
            raise ValueError("hook expectation cwd must be sanitized")
        if status not in {"injected", "empty"}:
            raise ValueError("unsupported hook expected status")
        expected = _string_tuple(
            value["expected_item_ids"],
            "hook expected_item_ids",
        )
        prohibited = _string_tuple(
            value["prohibited_item_ids"],
            "hook prohibited_item_ids",
        )
        if set(expected) & set(prohibited):
            raise ValueError("hook expected and prohibited item ids overlap")
        if (status == "injected") != bool(expected):
            raise ValueError("hook injected expectation requires expected item ids")
        return HookExpectation(
            applicable=True,
            cwd=cwd,
            expected_status=status,
            expected_item_ids=expected,
            prohibited_item_ids=prohibited,
        )
    if set(value) != {"applicable", "reason"}:
        raise ValueError("not-applicable hook expectation only accepts reason")
    reason = value["reason"]
    if reason not in _HOOK_NOT_APPLICABLE_REASONS:
        raise ValueError("unsupported hook not-applicable reason")
    return HookExpectation(applicable=False, reason=reason)


def _string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(row, str) or not row for row in value):
        raise ValueError(f"{field} must be a string list")
    return tuple(dict.fromkeys(value))


__all__ = [
    "HookExpectation",
    "HookExpectedStatus",
    "RecallQualityCase",
    "RecallQualityCorpus",
    "RecallQualitySplit",
    "load_recall_quality_corpus",
]
