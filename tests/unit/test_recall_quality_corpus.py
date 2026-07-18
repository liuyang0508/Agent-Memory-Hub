from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


FIXTURE = Path("tests/fixtures/recall_quality_production_replay_v1.json")
LEGACY_FIXTURE = Path("tests/fixtures/dual_route_recall_cases.json")


def test_production_replay_corpus_is_versioned_append_only_and_disjoint() -> None:
    from agent_brain.evaluation.recall_quality_corpus import load_recall_quality_corpus

    corpus = load_recall_quality_corpus(FIXTURE)
    legacy_queries = {
        row["query"]
        for row in json.loads(LEGACY_FIXTURE.read_text(encoding="utf-8"))
    }

    assert corpus.schema_version == 1
    assert corpus.corpus_version == "production-replay-v1"
    assert corpus.append_only is True
    assert len(corpus.cases) >= 12
    assert {case.split for case in corpus.cases} == {"production_replay"}
    assert len({case.id for case in corpus.cases}) == len(corpus.cases)
    assert not ({case.query for case in corpus.cases} & legacy_queries)
    assert {
        "keyword_extraction_error",
        "hook_recall",
        "weak_followup",
        "log_trace",
        "multilingual",
        "project_mismatch",
        "temporal",
        "abstention",
        "multimodal",
    } <= {case.category for case in corpus.cases}
    assert all(case.source_kind == "sanitized_runtime_replay" for case in corpus.cases)
    assert all(
        case.source_digest.startswith("sha256:")
        and len(case.source_digest) == len("sha256:") + 64
        for case in corpus.cases
    )
    assert corpus.sha256 == "sha256:" + hashlib.sha256(FIXTURE.read_bytes()).hexdigest()


def test_corpus_loader_fails_closed_on_unknown_split(tmp_path: Path) -> None:
    from agent_brain.evaluation.recall_quality_corpus import load_recall_quality_corpus

    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["cases"][0]["split"] = "training"
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported recall quality split"):
        load_recall_quality_corpus(invalid)


def test_corpus_loader_fails_closed_on_raw_source_fields(tmp_path: Path) -> None:
    from agent_brain.evaluation.recall_quality_corpus import load_recall_quality_corpus

    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["cases"][0]["session_id"] = "raw-session"
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="forbidden source field"):
        load_recall_quality_corpus(invalid)
