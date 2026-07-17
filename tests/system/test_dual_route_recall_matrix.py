from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib.util
import inspect
import json
import math
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.memory.context.context_firewall_types import ContextCandidate
from agent_brain.memory.context.injection_gateway import InjectionResult, build_injection_context
from agent_brain.memory.context.injection_query_context import InjectionQueryContext
from agent_brain.memory.recall.admission import build_recall_request
from agent_brain.memory.recall.retrieval import Retriever, SearchFilter
from agent_brain.memory.recall.routed_types import RouteEvidence
from agent_brain.platform.indexing.index import HubIndex


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "dual_route_recall_cases.json"
PRECOMPUTED_EMBEDDING_PATH = (
    Path(__file__).parents[1] / "fixtures" / "dual_route_precomputed_embeddings.json"
)
PRECOMPUTED_EMBEDDING_DIGEST_PATH = PRECOMPUTED_EMBEDDING_PATH.with_suffix(".sha256")
ANSWERABILITY_AUDIT_PATH = (
    Path(__file__).parents[1] / "fixtures" / "dual_route_answerability_audit.json"
)
CALIBRATION_REPORT_PATH = (
    Path(__file__).parents[2]
    / "docs"
    / "evaluation"
    / "dual-route-calibration-report.json"
)
CALIBRATION_GATE_PATH = (
    Path(__file__).parents[2] / "scripts" / "check-dual-route-calibration.py"
)
GENERATOR_PATH = Path(__file__).parents[2] / "scripts" / "generate-dual-route-embedding-fixture.py"
GENERATOR_LOCK_PATH = Path(__file__).parents[2] / "scripts" / "dual-route-embedding-generator.lock.txt"
CATEGORIES = {
    "semantic_paraphrase",
    "multilingual",
    "keyword_extraction_error",
    "exact_entity",
    "weak_or_no_value",
}

# Human-reviewed closed-set relevance contract. These notes document why the
# labeled item contains information that can answer the query; retrieval scores
# are deliberately not part of this audit.
ANSWERABILITY_AUDIT = {
    "semantic-zh-01": "item explains pre-answer retrieval of prior-session knowledge",
    "semantic-zh-02": "item explains restoration of saved technical decisions",
    "semantic-zh-03": "item covers missing knowledge learned in earlier sessions",
    "semantic-zh-04": "item covers recovery of an established technical route",
    "semantic-zh-05": "item explains automatic pre-answer experience injection",
    "semantic-zh-06": "item explains retrieval of accumulated experience",
    "semantic-zh-07": "item covers restoring knowledge from past sessions",
    "semantic-zh-08": "item covers returning historical knowledge to the answer",
    "semantic-en-multi-09": "two items separately answer the requested Atlas rollout decision and verification result",
    "multi-zh-01": "item states the browser proxy thirty-second timeout",
    "multi-ru-02": "item states when the browser proxy request expires",
    "multi-ar-03": "item states the seven-day workspace file retention period",
    "multi-th-04": "item states how many days workspace files are retained",
    "multi-ja-05": "item states the runtime installer path",
    "multi-ko-06": "item states where the install script is located",
    "multi-mix-07": "item states the recall cache ten-minute TTL",
    "multi-hi-08": "item states the cache TTL despite the cross-language query",
    "keyword-zh-01": "item lists narrative order and algorithm explanation changes",
    "keyword-zh-02": "item states the revised README reading order",
    "keyword-zh-03": "item explains runtime integration and maintenance flow",
    "keyword-zh-04": "item connects recall observability with maintenance",
    "keyword-zh-05": "item states why and how the formula explanation changed",
    "keyword-zh-06": "item lists the second-pass introduction changes",
    "keyword-zh-07": "item states the revised Chinese narrative sequence",
    "keyword-mix-08": "item lists narrative and formula polish in README.zh.md",
    "entity-01": "item directly identifies compiler error E0583",
    "entity-02": "item directly identifies CVE-2026-1234",
    "entity-03": "item directly identifies PR42",
    "entity-04": "item directly identifies install-with-skills.sh",
    "entity-05": "item directly identifies the requested memory detail URI",
    "entity-06": "item directly identifies the routed-recall feature flag",
    "entity-07": "item directly identifies host 47.96.229.35",
    "entity-08": "item directly identifies commit bb9128a",
}


def _cases() -> list[dict[str, Any]]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_dual_route_fixture_schema_and_distribution() -> None:
    cases = _cases()
    required = {
        "id",
        "category",
        "query",
        "expected_item_ids",
        "expect_admission",
        "expect_injection",
        "legacy_false_negative",
        "prohibited_item_ids",
    }
    counts = Counter(case["category"] for case in cases)

    assert len(cases) >= 40
    assert len({case["id"] for case in cases}) == len(cases)
    assert all(required <= case.keys() for case in cases)
    assert all(isinstance(case["query"], str) and case["query"].strip() for case in cases)
    assert all(isinstance(case["expected_item_ids"], list) for case in cases)
    assert all(isinstance(case["prohibited_item_ids"], list) for case in cases)
    assert all(type(case["expect_admission"]) is bool for case in cases)
    assert all(type(case["expect_injection"]) is bool for case in cases)
    assert all(type(case["legacy_false_negative"]) is bool for case in cases)
    assert set(counts) == CATEGORIES
    assert all(counts[category] >= 8 for category in CATEGORIES)
    assert sum(bool(case["legacy_false_negative"]) for case in cases) >= 3
    positive_case_ids = {
        case["id"] for case in cases if case["expected_item_ids"]
    }
    assert set(ANSWERABILITY_AUDIT) == positive_case_ids
    assert all(note.strip() for note in ANSWERABILITY_AUDIT.values())
    known_gaps = {
        case["id"]: case["known_capability_gap"]
        for case in cases
        if "known_capability_gap" in case
    }
    assert known_gaps == {}
    assert all(
        set(case["expected_item_ids"])
        == {
            item["id"]
            for item in case.get(
                "brain_items",
                [case["brain_item"]] if "brain_item" in case else [],
            )
        }
        for case in cases
        if case["expected_item_ids"]
    )
    assert {
        "safety-private",
        "safety-secret",
        "safety-review",
        "safety-superseded",
        "safety-scope",
        "safety-gateway-error",
    } <= {case["id"] for case in cases}
    hard_negative_ids = {
        item["id"]
        for case in cases
        for item in case.get("hard_negative_items", [])
    }
    assert len(hard_negative_ids) >= 3
    calibrated_categories = {
        "semantic_paraphrase",
        "multilingual",
        "keyword_extraction_error",
    }
    calibrated_cases = [case for case in cases if case["category"] in calibrated_categories]
    assert all(
        case.get("calibration_split") in {"calibration", "heldout"}
        for case in calibrated_cases
    )
    target_splits: dict[str, set[str]] = {}
    for case in calibrated_cases:
        for item_id in case["expected_item_ids"]:
            target_splits.setdefault(item_id, set()).add(case["calibration_split"])
    assert all(len(splits) == 1 for splits in target_splits.values()), target_splits
    for category in calibrated_categories:
        category_cases = [case for case in calibrated_cases if case["category"] == category]
        targets_by_split = {
            split: {
                item_id
                for case in category_cases
                if case["calibration_split"] == split
                for item_id in case["expected_item_ids"]
            }
            for split in ("calibration", "heldout")
        }
        assert targets_by_split["calibration"], (category, targets_by_split)
        assert targets_by_split["heldout"], (category, targets_by_split)
        assert len(targets_by_split["calibration"] | targets_by_split["heldout"]) >= 3
    gateway_case = next(case for case in cases if case["id"] == "safety-gateway-error")
    assert gateway_case["gateway_exception_test"] == (
        "test_gateway_exception_never_exposes_raw_candidate"
    )


def test_answerability_audit_binds_query_and_searchable_item_hashes() -> None:
    audit = json.loads(ANSWERABILITY_AUDIT_PATH.read_text(encoding="utf-8"))

    _validate_answerability_audit(_cases(), audit)


def test_answerability_audit_rejects_searchable_content_mutation() -> None:
    cases = json.loads(json.dumps(_cases()))
    audit = json.loads(ANSWERABILITY_AUDIT_PATH.read_text(encoding="utf-8"))
    target = next(case for case in cases if case["id"] == "semantic-zh-01")
    target["brain_item"]["summary"] += " mutated"

    with pytest.raises(ValueError, match="searchable content hash"):
        _validate_answerability_audit(cases, audit)


def test_calibration_report_separates_calibration_and_heldout_quality() -> None:
    report = json.loads(CALIBRATION_REPORT_PATH.read_text(encoding="utf-8"))

    assert report["splits"]["calibration"] == {
        "case_count": 15,
        "expected_item_count": 15,
        "tp": 15,
        "fp": 0,
        "fn": 0,
        "precision": 1.0,
        "recall": 1.0,
    }
    assert report["splits"]["heldout"] == {
        "case_count": 10,
        "expected_item_count": 11,
        "tp": 11,
        "fp": 0,
        "fn": 0,
        "precision": 1.0,
        "recall": 1.0,
    }
    assert report["model"]["revision"] == (
        "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
    )
    assert report["calibration_passed"] is True
    assert report["unresolved_gap_count"] == 0
    assert report["gaps"] == []


def test_calibration_report_matches_independent_split_run(tmp_path: Path) -> None:
    committed = json.loads(CALIBRATION_REPORT_PATH.read_text(encoding="utf-8"))

    assert _evaluate_calibration_report(tmp_path) == committed


def test_calibration_release_gate_passes_after_known_gap_is_closed() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(CALIBRATION_GATE_PATH),
            "--report",
            str(CALIBRATION_REPORT_PATH),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "calibration_passed": True,
        "release_gate": "passed",
        "report_schema_version": 1,
        "unresolved_gap_count": 0,
    }


def _searchable_item_text(raw: dict[str, Any]) -> str:
    return " ".join(
        (
            str(raw.get("title", "")),
            str(raw.get("summary", "")),
            str(raw.get("body", "")),
            *(str(tag) for tag in raw.get("tags", [])),
        )
    )


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _expected_raw_items(case: dict[str, Any]) -> list[dict[str, Any]]:
    if "brain_items" in case:
        return list(case["brain_items"])
    if "brain_item" in case:
        return [case["brain_item"]]
    return []


def _validate_answerability_audit(
    cases: list[dict[str, Any]],
    audit: dict[str, Any],
) -> None:
    if audit.get("schema_version") != 1:
        raise ValueError("answerability audit schema version mismatch")
    if audit.get("algorithm") != "sha256:utf-8":
        raise ValueError("answerability audit hash algorithm mismatch")

    positive_cases = {
        case["id"]: case for case in cases if case.get("expected_item_ids")
    }
    audit_cases = audit.get("cases")
    if not isinstance(audit_cases, dict) or set(audit_cases) != set(positive_cases):
        raise ValueError("answerability audit case set mismatch")

    for case_id, case in positive_cases.items():
        entry = audit_cases[case_id]
        if set(entry) != {"note", "query_sha256", "item_searchable_sha256"}:
            raise ValueError(f"answerability audit fields mismatch for {case_id}")
        if entry["note"] != ANSWERABILITY_AUDIT[case_id]:
            raise ValueError(f"answerability audit note mismatch for {case_id}")
        if entry["query_sha256"] != _sha256_text(case["query"]):
            raise ValueError(f"query hash mismatch for {case_id}")

        expected_item_ids = set(case["expected_item_ids"])
        raw_items = {
            raw["id"]: raw
            for raw in _expected_raw_items(case)
            if raw["id"] in expected_item_ids
        }
        expected_hashes = {
            item_id: _sha256_text(_searchable_item_text(raw_items[item_id]))
            for item_id in sorted(expected_item_ids)
        }
        if entry["item_searchable_sha256"] != expected_hashes:
            raise ValueError(f"searchable content hash mismatch for {case_id}")


def _fixture_embedding_texts() -> tuple[str, ...]:
    return _load_embedding_generator().extract_case_texts(_cases())


def _load_embedding_generator():
    spec = importlib.util.spec_from_file_location("dual_route_embedding_generator", GENERATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_precomputed_embedding_fixture_has_provenance_and_no_label_leakage() -> None:
    generator = _load_embedding_generator()
    signature = inspect.signature(generator.generate_precomputed_embeddings)
    assert tuple(signature.parameters) == ("texts", "encode", "provenance")
    generator_source = GENERATOR_PATH.read_text(encoding="utf-8")
    assert all(
        field not in generator_source
        for field in (
            "expected_item_ids",
            "legacy_false_negative",
            "prohibited_item_ids",
            "calibration_split",
            "known_capability_gap",
        )
    )
    assert not (
        PRECOMPUTED_EMBEDDING_PATH.parent / "dual_route_semantic_lexicon.json"
    ).exists()

    payload = json.loads(PRECOMPUTED_EMBEDDING_PATH.read_text(encoding="utf-8"))
    texts = _fixture_embedding_texts()
    expected_hashes = {hashlib.sha256(text.encode("utf-8")).hexdigest() for text in texts}
    assert set(payload["embeddings"]) == expected_hashes
    assert all(
        len(content_hash) == 64
        and all(character in "0123456789abcdef" for character in content_hash)
        for content_hash in payload["embeddings"]
    )
    assert payload["content_hash"] == "sha256:utf-8"
    assert payload["model"]["id"] == (
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )
    assert payload["model"]["revision"] == "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
    assert payload["model"]["dimension"] == 384
    assert payload["model"]["normalized"] is True
    assert payload["model"]["snapshot_path_suffix"] == (
        "snapshots/e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
    )
    assert payload["model"]["snapshot_digest"].startswith("sha256:")
    assert len(payload["model"]["snapshot_digest"]) == 71
    generator_metadata = payload["generator"]
    assert {
        key: generator_metadata[key]
        for key in ("path", "version", "encoder", "lockfile", "float_round_digits")
    } == {
        "path": "scripts/generate-dual-route-embedding-fixture.py",
        "version": 2,
        "encoder": "sentence-transformers==3.4.1",
        "lockfile": "scripts/dual-route-embedding-generator.lock.txt",
        "float_round_digits": 8,
    }
    assert generator_metadata["runtime"] == {
        "python_implementation": "CPython",
        "python_version": "3.12.4",
        "platform_system": "Darwin",
        "platform_machine": "arm64",
    }
    assert generator_metadata["regeneration"] == {
        "working_directory": "repository-root",
        "argv": [
            "python",
            "scripts/generate-dual-route-embedding-fixture.py",
            "--cases",
            "tests/fixtures/dual_route_recall_cases.json",
            "--output",
            "tests/fixtures/dual_route_precomputed_embeddings.json",
            "--model-path",
            "~/.cache/huggingface/hub/models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2/snapshots/e8f8c211226b894fcb81acc59f3b34ba3efd5f42",
            "--model-id",
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            "--revision",
            "e8f8c211226b894fcb81acc59f3b34ba3efd5f42",
        ],
    }
    assert generator_metadata["verification"] == {
        "working_directory": "repository-root",
        "argv": [
            "python",
            "scripts/generate-dual-route-embedding-fixture.py",
            "--cases",
            "tests/fixtures/dual_route_recall_cases.json",
            "--output",
            "tests/fixtures/dual_route_precomputed_embeddings.json",
            "--verify-existing",
        ],
    }
    assert all(len(vector) == 384 for vector in payload["embeddings"].values())
    assert all(
        math.sqrt(sum(value * value for value in vector)) == pytest.approx(1.0, abs=1e-5)
        for vector in payload["embeddings"].values()
    )
    with pytest.raises(ValueError, match="provenance"):
        generator.generate_precomputed_embeddings(
            ["raw text only"],
            lambda _texts: [[1.0, *([0.0] * 383)]],
            {
                **payload["model"],
                "expected_item_ids": ["forbidden-label-channel"],
            },
        )

    altered_cases = json.loads(json.dumps(_cases()))
    for case in altered_cases:
        case["expected_item_ids"] = ["annotation-must-not-enter-extraction"]
        case["legacy_false_negative"] = not case["legacy_false_negative"]
        case["prohibited_item_ids"] = ["annotation-must-not-enter-extraction"]
        case["calibration_split"] = "annotation-must-not-enter-extraction"
        case["known_capability_gap"] = "annotation-must-not-enter-extraction"
    assert generator.extract_case_texts(altered_cases) == generator.extract_case_texts(_cases())


def test_embedding_fixture_digest_lock_and_snapshot_revision_are_bound(tmp_path: Path) -> None:
    generator = _load_embedding_generator()
    payload_bytes = PRECOMPUTED_EMBEDDING_PATH.read_bytes()
    expected_digest = "sha256:" + hashlib.sha256(payload_bytes).hexdigest()

    assert PRECOMPUTED_EMBEDDING_DIGEST_PATH.read_text(encoding="utf-8").strip() == (
        expected_digest
    )
    lock_lines = {
        line.strip()
        for line in GENERATOR_LOCK_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert "sentence-transformers==3.4.1" in lock_lines
    assert all("==" in line for line in lock_lines)

    revision = "frozen-revision"
    snapshot = tmp_path / "snapshots" / revision
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text('{"model":"fixture"}\n', encoding="utf-8")
    first = generator.snapshot_content_digest(snapshot, revision)
    assert first.startswith("sha256:")
    (snapshot / "config.json").write_text('{"model":"tampered"}\n', encoding="utf-8")
    assert generator.snapshot_content_digest(snapshot, revision) != first
    with pytest.raises(ValueError, match="revision"):
        generator.snapshot_content_digest(snapshot, "different-revision")


def test_embedding_fixture_has_read_only_offline_verification_entry() -> None:
    fixture_before = PRECOMPUTED_EMBEDDING_PATH.read_bytes()
    digest_before = PRECOMPUTED_EMBEDDING_DIGEST_PATH.read_bytes()

    completed = subprocess.run(
        [
            sys.executable,
            str(GENERATOR_PATH),
            "--cases",
            str(FIXTURE_PATH),
            "--output",
            str(PRECOMPUTED_EMBEDDING_PATH),
            "--verify-existing",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "digest": PRECOMPUTED_EMBEDDING_DIGEST_PATH.read_text(
            encoding="utf-8"
        ).strip(),
        "embedding_count": len(_fixture_embedding_texts()),
        "model_revision": "e8f8c211226b894fcb81acc59f3b34ba3efd5f42",
        "snapshot_verified": False,
        "verified": True,
    }
    assert PRECOMPUTED_EMBEDDING_PATH.read_bytes() == fixture_before
    assert PRECOMPUTED_EMBEDDING_DIGEST_PATH.read_bytes() == digest_before


def test_embedding_fixture_offline_verification_rejects_tampering(tmp_path: Path) -> None:
    generator = _load_embedding_generator()
    fixture_copy = tmp_path / PRECOMPUTED_EMBEDDING_PATH.name
    fixture_copy.write_bytes(PRECOMPUTED_EMBEDDING_PATH.read_bytes() + b" ")
    fixture_copy.with_suffix(".sha256").write_bytes(
        PRECOMPUTED_EMBEDDING_DIGEST_PATH.read_bytes()
    )

    with pytest.raises(ValueError, match="digest"):
        generator.verify_existing_fixture(FIXTURE_PATH, fixture_copy)


def test_precomputed_provider_rejects_tampered_payload(tmp_path: Path) -> None:
    fixture_copy = tmp_path / PRECOMPUTED_EMBEDDING_PATH.name
    digest_copy = fixture_copy.with_suffix(".sha256")
    fixture_copy.write_bytes(PRECOMPUTED_EMBEDDING_PATH.read_bytes() + b" ")
    digest_copy.write_bytes(PRECOMPUTED_EMBEDDING_DIGEST_PATH.read_bytes())

    with pytest.raises(ValueError, match="digest"):
        _PrecomputedSemanticEmbedder(fixture_copy)


def test_precomputed_provider_resolves_only_by_content_hash() -> None:
    provider = _PrecomputedSemanticEmbedder()
    text = _fixture_embedding_texts()[0]
    payload = json.loads(PRECOMPUTED_EMBEDDING_PATH.read_text(encoding="utf-8"))
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    assert provider.embed(text) == payload["embeddings"][content_hash]
    with pytest.raises(KeyError):
        provider.embed("unseen text is not mapped to any label")


class _PrecomputedSemanticEmbedder:
    """Offline CI provider keyed only by searchable-text SHA-256."""

    degraded = False

    def __init__(self, path: Path = PRECOMPUTED_EMBEDDING_PATH) -> None:
        payload_bytes = path.read_bytes()
        digest_path = path.with_suffix(".sha256")
        expected_digest = digest_path.read_text(encoding="utf-8").strip()
        actual_digest = "sha256:" + hashlib.sha256(payload_bytes).hexdigest()
        if expected_digest != actual_digest:
            raise ValueError("precomputed embedding payload digest mismatch")
        payload = json.loads(payload_bytes)
        self.dim = int(payload["model"]["dimension"])
        self._embeddings = payload["embeddings"]

    def embed(self, text: str) -> list[float]:
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return list(self._embeddings[content_hash])


def _memory_item(data: dict[str, Any]) -> tuple[MemoryItem, str]:
    payload = dict(data)
    body = str(payload.pop("body", ""))
    payload.setdefault("created_at", "2026-07-15T12:00:00+00:00")
    payload.setdefault("confidence", 0.9)
    payload.setdefault("sensitivity", "internal")
    payload.setdefault("tags", [])
    payload.setdefault("project", None)
    return MemoryItem.model_validate(payload), body


@dataclass(frozen=True)
class _Outcome:
    candidates: frozenset[str]
    injected: frozenset[str]
    routes: tuple[str, ...]
    semantic_similarities: tuple[tuple[str, float], ...] = ()
    exclusion_reasons: tuple[tuple[str, tuple[str, ...]], ...] = ()


def _seed_fixture_brain(
    tmp_path: Path,
) -> tuple[HubIndex, dict[str, tuple[MemoryItem, str]], _PrecomputedSemanticEmbedder]:
    embedder = _PrecomputedSemanticEmbedder()
    index = HubIndex(tmp_path / "fixture-index.db", embedding_dim=embedder.dim)
    items: dict[str, tuple[MemoryItem, str]] = {}
    for case in _cases():
        raw_items = [
            case.get("brain_item"),
            *case.get("brain_items", []),
            *case.get("hard_negative_items", []),
        ]
        for raw in raw_items:
            if raw is None:
                continue
            item, body = _memory_item(raw)
            previous = items.get(item.id)
            assert previous is None or previous == (item, body), item.id
            if previous is None:
                items[item.id] = (item, body)
                index.upsert(
                    item,
                    body,
                    embedding=embedder.embed(_searchable_item_text(raw)),
                )
    return index, items, embedder


def _gateway_result(
    hits: list[Any],
    *,
    items: dict[str, tuple[MemoryItem, str]],
    request: Any,
    evidence: dict[str, RouteEvidence],
) -> InjectionResult:
    candidates = [
        ContextCandidate(items[hit.id][0], body=items[hit.id][1], score=hit.score)
        for hit in hits
        if hit.id in items
    ]
    return build_injection_context(
        candidates,
        query_context=InjectionQueryContext(
            raw_query=request.raw_query,
            admission=request.admission,
            query_signal=request.query_signal,
            evidence_by_id=evidence,
        ),
        current_scope={"cwd": "/repo/current", "adapter": "codex"},
        max_items=10,
    )


def _gateway_ids(result: InjectionResult) -> frozenset[str]:
    return frozenset(entry.decision.candidate.item.id for entry in result.included)


def _is_legacy_false_negative(case: dict[str, Any], old: _Outcome) -> bool:
    expected = frozenset(case.get("expected_item_ids", ()))
    return bool(
        case.get("expect_injection")
        and expected
        and expected - old.injected
    )


def _is_fixed_by_routed_recall(
    case: dict[str, Any],
    old: _Outcome,
    new: _Outcome,
) -> bool:
    expected = frozenset(case.get("expected_item_ids", ()))
    return _is_legacy_false_negative(case, old) and expected <= new.injected


def _unexpected_negative_injections(
    case: dict[str, Any],
    outcome: _Outcome,
    *,
    corpus_ids: frozenset[str],
) -> frozenset[str]:
    if case.get("expect_injection"):
        return frozenset()
    return outcome.injected & corpus_ids


def test_legacy_false_negative_is_only_an_old_outcome_label() -> None:
    case = {
        "expected_item_ids": ["decision", "verification"],
        "expect_injection": True,
    }
    old = _Outcome(frozenset(), frozenset({"decision"}), ())
    new = _Outcome(frozenset(), frozenset(), ())

    assert _is_legacy_false_negative(case, old) is True
    assert _is_fixed_by_routed_recall(case, old, new) is False


def test_negative_row_counts_any_safe_corpus_injection_as_false_positive() -> None:
    case = {"expect_injection": False}
    outcome = _Outcome(
        frozenset({"safe-corpus-item"}),
        frozenset({"safe-corpus-item"}),
        (),
    )

    assert _unexpected_negative_injections(
        case,
        outcome,
        corpus_ids=frozenset({"safe-corpus-item", "another-safe-item"}),
    ) == frozenset({"safe-corpus-item"})


def _legacy_outcome(
    retriever: Retriever,
    case: dict[str, Any],
    items: dict[str, tuple[MemoryItem, str]],
) -> _Outcome:
    from agent_brain.interfaces.cli.routed_query import _generate_candidates

    request = build_recall_request(
        case["query"],
        adapter="codex",
        cwd="/repo/current",
        enable_technical_anchors=False,
    )
    result = _generate_candidates(
        request=request,
        retriever=retriever,
        top_k=10,
        filters=SearchFilter(),
        use_routed=False,
    )
    gateway = _gateway_result(
        result.hits,
        items=items,
        request=request,
        evidence=dict(result.evidence_by_id),
    )
    return _Outcome(
        frozenset(hit.id for hit in result.hits),
        _gateway_ids(gateway),
        tuple(
            f"{trace.route}:{trace.status}:{trace.reason}:{trace.candidate_count}"
            for trace in result.routes
        ),
        tuple(
            (item_id, evidence.semantic_similarity)
            for item_id, evidence in result.evidence_by_id.items()
            if evidence.semantic_similarity is not None
        ),
        tuple(
            (decision.candidate.item.id, decision.reasons)
            for decision in gateway.excluded
        ),
    )


def _routed_outcome(
    retriever: Retriever,
    case: dict[str, Any],
    items: dict[str, tuple[MemoryItem, str]],
) -> _Outcome:
    request = build_recall_request(case["query"], adapter="codex", cwd="/repo/current")
    result = retriever.search_routed(
        request,
        top_k=10,
        filters=SearchFilter(),
        record_access=False,
    )
    gateway = _gateway_result(
        result.hits,
        items=items,
        request=request,
        evidence=dict(result.evidence_by_id),
    )
    return _Outcome(
        frozenset(hit.id for hit in result.hits),
        _gateway_ids(gateway),
        tuple(
            f"{trace.route}:{trace.status}:{trace.reason}:{trace.candidate_count}"
            for trace in result.routes
        ),
        tuple(
            (item_id, evidence.semantic_similarity)
            for item_id, evidence in result.evidence_by_id.items()
            if evidence.semantic_similarity is not None
        ),
        tuple(
            (decision.candidate.item.id, decision.reasons)
            for decision in gateway.excluded
        ),
    )


def _split_quality(
    rows: list[tuple[dict[str, Any], _Outcome]],
    split: str,
) -> dict[str, int | float]:
    selected = [(case, outcome) for case, outcome in rows if case["calibration_split"] == split]
    tp = 0
    fp = 0
    fn = 0
    expected_item_count = 0
    for case, outcome in selected:
        expected = set(case["expected_item_ids"])
        allowed = set(case.get("allowed_related_item_ids", []))
        injected = set(outcome.injected)
        expected_item_count += len(expected)
        tp += len(expected & injected)
        fp += len(injected - expected - allowed)
        fn += len(expected - injected)
    return {
        "case_count": len(selected),
        "expected_item_count": expected_item_count,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": tp / (tp + fp) if tp + fp else 1.0,
        "recall": tp / (tp + fn) if tp + fn else 1.0,
    }


def _evaluate_calibration_report(tmp_path: Path) -> dict[str, Any]:
    cases = [case for case in _cases() if case.get("calibration_split")]
    index, items, embedder = _seed_fixture_brain(tmp_path)
    retriever = Retriever(
        index,
        embedder,
        rerank=False,
        apply_decay=False,
        record_access=False,
    )
    try:
        rows = [(case, _routed_outcome(retriever, case, items)) for case in cases]
    finally:
        index.close()

    gaps = [
        {
            "id": case["id"],
            "reason": gap["reason"],
            "embedding_evidence": gap["embedding_evidence"],
            "verifier_evidence": gap["verifier_evidence"],
            "upgrade_condition": gap["upgrade_condition"],
        }
        for case in cases
        if (gap := case.get("known_capability_gap"))
    ]
    embedding_payload = json.loads(
        PRECOMPUTED_EMBEDDING_PATH.read_text(encoding="utf-8")
    )
    splits = {
        split: _split_quality(rows, split)
        for split in ("calibration", "heldout")
    }
    calibration_passed = not gaps and all(
        metrics["fp"] == 0 and metrics["fn"] == 0
        for metrics in splits.values()
    )
    return {
        "schema_version": 1,
        "cases_sha256": "sha256:" + hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),
        "model": {
            key: embedding_payload["model"][key]
            for key in ("id", "revision", "snapshot_digest")
        },
        "splits": splits,
        "calibration_passed": calibration_passed,
        "unresolved_gap_count": len(gaps),
        "gaps": gaps,
    }


def test_dual_route_candidate_and_injection_governance_matrix(tmp_path: Path) -> None:
    cases = _cases()
    index, items, embedder = _seed_fixture_brain(tmp_path)
    routed = Retriever(
        index,
        embedder,
        rerank=False,
        apply_decay=False,
        record_access=False,
    )
    rows: list[tuple[dict[str, Any], _Outcome, _Outcome]] = []
    try:
        for case in cases:
            if case.get("gateway_exception_test"):
                continue
            request = build_recall_request(case["query"], adapter="codex")
            assert request.admission.allowed is case["expect_admission"], case["id"]
            rows.append(
                (case, _legacy_outcome(routed, case, items), _routed_outcome(routed, case, items))
            )
    finally:
        index.close()

    known_gap_rows = [
        (case, old, new)
        for case, old, new in rows
        if case.get("known_capability_gap")
    ]
    positives = [
        (case, old, new)
        for case, old, new in rows
        if case["expected_item_ids"] and not case.get("known_capability_gap")
    ]
    label_mismatches = [
        (
            case["id"],
            case["legacy_false_negative"],
            _is_legacy_false_negative(case, old),
        )
        for case, old, _new in rows
        if case["expected_item_ids"]
        if case["legacy_false_negative"]
        != _is_legacy_false_negative(case, old)
    ]
    legacy_hits = sum(
        bool(set(case["expected_item_ids"]) & old.candidates) for case, old, _new in positives
    )
    routed_hits = sum(
        bool(set(case["expected_item_ids"]) & new.candidates) for case, _old, new in positives
    )
    fixed = [
        case["id"]
        for case, old, new in rows
        if _is_fixed_by_routed_recall(case, old, new)
    ]
    new_false_negatives = [
        case["id"]
        for case, _old, new in rows
        if case["expect_injection"]
        and not case.get("known_capability_gap")
        and not set(case["expected_item_ids"]) <= new.injected
    ]
    corpus_ids = frozenset(items)
    negative_false_positives = [
        (
            case["id"],
            sorted(
                _unexpected_negative_injections(
                    case,
                    new,
                    corpus_ids=corpus_ids,
                )
            ),
        )
        for case, _old, new in rows
        if _unexpected_negative_injections(
            case,
            new,
            corpus_ids=corpus_ids,
        )
    ]
    hard_negative_ids = {
        item["id"]
        for case in cases
        for item in case.get("hard_negative_items", [])
    }
    prohibited = [
        (
            case["id"],
            sorted((set(case["prohibited_item_ids"]) | hard_negative_ids) & new.injected),
        )
        for case, _old, new in rows
        if (set(case["prohibited_item_ids"]) | hard_negative_ids) & new.injected
    ]
    expected_misses = [
        {
            "id": case["id"],
            "routes": new.routes,
            "candidate_ids": sorted(new.candidates),
        }
        for case, _old, new in rows
        if case["expect_injection"]
        and not case.get("known_capability_gap")
        and not set(case["expected_item_ids"]) <= new.injected
    ]

    assert known_gap_rows == []
    calibration_summary = {
        "calibration_passed": not known_gap_rows,
        "unresolved_gaps": len(known_gap_rows),
    }
    assert calibration_summary == {
        "calibration_passed": True,
        "unresolved_gaps": 0,
    }

    assert routed_hits / len(positives) >= legacy_hits / len(positives), expected_misses
    assert len(fixed) >= 3, {"fixed": fixed, "misses": expected_misses}
    assert new_false_negatives == [], new_false_negatives
    assert negative_false_positives == [], negative_false_positives
    assert prohibited == [], prohibited
    assert expected_misses == [], expected_misses
    assert label_mismatches == [], label_mismatches

    per_case_quality = []
    for case, _old, new in positives:
        expected = set(case["expected_item_ids"])
        allowed = set(case.get("allowed_related_item_ids", []))
        injected = set(new.injected)
        true_positive_count = len(expected & injected)
        false_positive_count = len(injected - expected - allowed)
        false_negative_count = len(expected - injected)
        per_case_quality.append({
            "id": case["id"],
            "tp": true_positive_count,
            "fp": false_positive_count,
            "fn": false_negative_count,
            "injected": sorted(injected),
            "permitted": sorted(expected | allowed),
        })
    assert all(row["fp"] == 0 and row["fn"] == 0 for row in per_case_quality), (
        per_case_quality
    )
    micro_tp = sum(row["tp"] for row in per_case_quality)
    micro_fp = sum(row["fp"] for row in per_case_quality)
    micro_fn = sum(row["fn"] for row in per_case_quality)
    micro_precision = micro_tp / (micro_tp + micro_fp)
    micro_recall = micro_tp / (micro_tp + micro_fn)
    macro_precision = sum(
        row["tp"] / (row["tp"] + row["fp"])
        for row in per_case_quality
    ) / len(per_case_quality)
    macro_recall = sum(
        row["tp"] / (row["tp"] + row["fn"])
        for row in per_case_quality
    ) / len(per_case_quality)
    assert (micro_precision, macro_precision, micro_recall, macro_recall) == (
        1.0,
        1.0,
        1.0,
        1.0,
    )

    expected_targets = {
        item_id
        for case in cases
        for item_id in case["expected_item_ids"]
    }
    target_clusters = Counter(
        tuple(case["expected_item_ids"])
        for case in cases
        if case["expected_item_ids"]
    )
    assert len(expected_targets) >= 11
    assert len(target_clusters) >= 11
    targets_by_category = {
        category: {
            item_id
            for case in cases
            if case["category"] == category
            for item_id in case["expected_item_ids"]
        }
        for category in ("semantic_paraphrase", "multilingual", "keyword_extraction_error")
    }
    assert all(len(targets) >= 3 for targets in targets_by_category.values()), (
        targets_by_category
    )

    target_quality: dict[str, list[dict[str, Any]]] = {}
    for case, row in zip((case for case, _old, _new in positives), per_case_quality, strict=True):
        for item_id in case["expected_item_ids"]:
            target_quality.setdefault(item_id, []).append(row)
    target_macro_precision = sum(
        sum(row["tp"] for row in target_rows)
        / sum(row["tp"] + row["fp"] for row in target_rows)
        for target_rows in target_quality.values()
    ) / len(target_quality)
    target_macro_recall = sum(
        sum(row["tp"] for row in target_rows)
        / sum(row["tp"] + row["fn"] for row in target_rows)
        for target_rows in target_quality.values()
    ) / len(target_quality)
    assert (target_macro_precision, target_macro_recall) == (1.0, 1.0)

    positive_similarities = [
        similarity
        for case, _old, new in positives
        for item_id, similarity in new.semantic_similarities
        if item_id in set(case["expected_item_ids"])
    ]
    hard_negative_similarities = [
        similarity
        for _case, _old, new in rows
        for item_id, similarity in new.semantic_similarities
        if item_id in hard_negative_ids
    ]
    assert len(positive_similarities) == sum(
        len(case["expected_item_ids"])
        for case, _old, _new in positives
    )
    assert hard_negative_similarities
    threshold_only_positive_similarities = [
        similarity
        for case, _old, new in positives
        if not build_recall_request(case["query"], adapter="codex").query_signal.injectable
        for item_id, similarity in new.semantic_similarities
        if item_id in set(case["expected_item_ids"])
    ]
    threshold_only_hard_negative_similarities = [
        similarity
        for case, _old, new in rows
        if not build_recall_request(case["query"], adapter="codex").query_signal.injectable
        for item_id, similarity in new.semantic_similarities
        if item_id in hard_negative_ids
    ]
    distribution = {
        "positive_min": min(positive_similarities),
        "positive_max": max(positive_similarities),
        "hard_negative_min": min(hard_negative_similarities),
        "hard_negative_max": max(hard_negative_similarities),
    }
    assert min(threshold_only_positive_similarities) >= 0.25, distribution
    assert max(threshold_only_hard_negative_similarities) < 0.25, distribution

    overlapping_negatives = []
    for case, _old, new in rows:
        signal = build_recall_request(case["query"], adapter="codex").query_signal
        reasons_by_id = dict(new.exclusion_reasons)
        for item_id, similarity in new.semantic_similarities:
            if item_id not in hard_negative_ids or similarity < 0.25:
                continue
            overlapping_negatives.append(
                (case["id"], item_id, similarity, reasons_by_id.get(item_id, ()))
            )
            assert signal.injectable, overlapping_negatives
            reasons = set(reasons_by_id.get(item_id, ()))
            assert (
                "route_answerability_insufficient" in reasons
                or {"query_mismatch", "answerability_mismatch"} <= reasons
            ), overlapping_negatives
    assert overlapping_negatives, distribution


class _FakeDeadline:
    def __init__(self, current: float = 0.0) -> None:
        self.current = current

    def now(self) -> float:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += seconds

    def expired(self, deadline: float) -> bool:
        return self.now() >= deadline


def test_fake_deadline_semantic_timeout_preserves_completed_lexical_route(
) -> None:
    from tests.unit.test_routed_retrieval import _Embedder, _Index, _request, _retriever
    from agent_brain.platform.indexing.index_types import Hit

    clock = _FakeDeadline()

    class AdvancingEmbedder(_Embedder):
        def embed(self, query: str) -> list[float]:
            clock.advance(1.1)
            return super().embed(query)

    index = _Index()
    index.bm25_hits["rule_term"] = [Hit("term-hit", 2.0)]
    index.bm25_hits["raw"] = [Hit("raw-hit", 1.0)]

    result = _retriever(index, AdvancingEmbedder()).search_routed(
        _request(),
        top_k=10,
        clock=clock.now,
        semantic_deadline=1.0,
    )

    assert {hit.id for hit in result.hits} == {"term-hit", "raw-hit"}
    traces = {trace.route: trace for trace in result.routes}
    assert traces["lexical_terms"].status == "ok"
    assert traces["semantic_raw"].status == "timeout"
    assert traces["lexical_raw_fallback"].status == "ok"


def test_fake_overall_deadline_fails_closed_without_wall_clock() -> None:
    from agent_brain.interfaces.cli.routed_query import execute_routed_query
    from tests.unit.test_routed_retrieval import _Embedder, _Index, _retriever

    clock = _FakeDeadline()
    index = _Index()
    original_bm25 = index.bm25_search

    def advancing_bm25(*args: Any, **kwargs: Any) -> list[Any]:
        clock.advance(1.1)
        return original_bm25(*args, **kwargs)

    index.bm25_search = advancing_bm25  # type: ignore[method-assign]
    embedder = _Embedder()
    embedder.degraded = True

    payload = execute_routed_query(
        raw_query="meaningful overall deadline probe",
        store=object(),
        retriever=_retriever(index, embedder),
        top_k=10,
        filters=SearchFilter(),
        requested="auto",
        project=None,
        adapter="codex",
        session_id="deadline",
        cwd="/repo/current",
        clock=clock.now,
        overall_deadline=1.0,
    )

    assert payload.to_dict() == {
        "status": "timeout",
        "reason": "overall_timeout",
        "context": "",
        "routes": [],
    }


def test_deadline_expiry_after_render_has_zero_durable_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from agent_brain.interfaces.cli import routed_query
    from agent_brain.memory.recall.retrieval_types import RetrievedItem
    from agent_brain.memory.recall.routed_types import RoutedSearchResult, RouteTrace

    clock = _FakeDeadline()
    item, body = _memory_item(
        next(case["brain_item"] for case in _cases() if case["id"] == "entity-01")
    )

    class Store:
        def iter_all(self):
            return iter([(item, body)])

    class RetrieverStub:
        def __init__(self) -> None:
            self.accesses: list[str] = []

        def search_routed(self, request: Any, **_kwargs: Any) -> Any:
            hit = RetrievedItem(item.id, 1.0, bm25_rank=1, vector_rank=None)
            return RoutedSearchResult(
                [hit],
                (RouteTrace("lexical_terms", "ok", 0.0, 1, "route_completed"),),
                request.admission,
                {item.id: RouteEvidence(("lexical_terms",), None, None, 1, None)},
            )

        def record_accesses(self, hits: list[Any]) -> None:
            self.accesses.extend(hit.id for hit in hits)

    retriever = RetrieverStub()
    original_render = routed_query._render_included_context

    def render_then_expire(injection: Any) -> str:
        rendered = original_render(injection)
        clock.advance(1.1)
        return rendered

    cohorts: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    monkeypatch.setattr(routed_query, "_render_included_context", render_then_expire)
    monkeypatch.setattr(
        routed_query,
        "_record_injection_diagnostic",
        lambda **kwargs: diagnostics.append(kwargs),
    )
    monkeypatch.setattr(
        routed_query,
        "_maybe_record_cohort",
        lambda **kwargs: cohorts.append(kwargs),
    )
    monkeypatch.setattr(
        routed_query,
        "_maybe_record_gap",
        lambda **kwargs: gaps.append(kwargs),
    )

    payload = routed_query.execute_routed_query(
        raw_query="E0583",
        store=Store(),
        retriever=retriever,
        top_k=10,
        filters=SearchFilter(),
        requested="auto",
        project=None,
        adapter="codex",
        session_id="deadline-after-render",
        cwd="/repo/current",
        brain_dir=tmp_path,
        record_injection_cohort=True,
        record_recall_gap=True,
        clock=clock.now,
        overall_deadline=1.0,
    )

    assert payload.to_dict() == {
        "status": "timeout",
        "reason": "overall_timeout",
        "context": "",
        "routes": [],
    }
    assert retriever.accesses == []
    assert cohorts == []
    assert gaps == []
    assert diagnostics == []


@pytest.mark.parametrize(
    ("clock_value", "deadline"),
    [
        (float("nan"), 1.0),
        (float("inf"), 1.0),
        (0.0, float("nan")),
        (0.0, float("inf")),
    ],
)
def test_non_finite_deadline_inputs_fail_closed(
    clock_value: float,
    deadline: float,
) -> None:
    from agent_brain.interfaces.cli.routed_query import execute_routed_query

    payload = execute_routed_query(
        raw_query="deadline validation probe",
        store=object(),
        retriever=object(),
        top_k=10,
        filters=SearchFilter(),
        requested="auto",
        project=None,
        adapter="codex",
        session_id="deadline-validation",
        cwd="/repo/current",
        clock=lambda: clock_value,
        overall_deadline=deadline,
    )

    assert payload.to_dict() == {
        "status": "error",
        "reason": "internal_error",
        "context": "",
        "routes": [],
    }


def test_gateway_exception_never_exposes_raw_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_brain.interfaces.cli import routed_query
    from agent_brain.memory.recall.retrieval_types import RetrievedItem
    from agent_brain.memory.recall.routed_types import RoutedSearchResult, RouteTrace

    item, body = _memory_item(
        next(case["brain_item"] for case in _cases() if case["id"] == "safety-gateway-error")
    )

    class Store:
        def iter_all(self):
            return iter([(item, body)])

    class RetrieverStub:
        def search_routed(self, request: Any, **_kwargs: Any) -> Any:
            hit = RetrievedItem(item.id, 1.0, bm25_rank=1, vector_rank=None)
            return RoutedSearchResult(
                [hit],
                (RouteTrace("lexical_terms", "ok", 0.0, 1, "route_completed"),),
                request.admission,
                {item.id: RouteEvidence(("lexical_terms",), None, None, 1, None)},
            )

    def explode(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("gateway verifier failed")

    monkeypatch.setattr(routed_query, "build_injection_context", explode)
    payload = routed_query.execute_routed_query(
        raw_query="gateway failure safety probe",
        store=Store(),
        retriever=RetrieverStub(),
        top_k=10,
        filters=SearchFilter(),
        requested="auto",
        project=None,
        adapter="codex",
        session_id=None,
        cwd="/repo/current",
    )

    assert payload.status == "error"
    assert payload.context == ""
    assert item.id not in json.dumps(payload.to_dict())
