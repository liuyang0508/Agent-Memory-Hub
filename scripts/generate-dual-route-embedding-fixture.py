#!/usr/bin/env python3
"""One-time generator for the offline dual-route semantic fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import platform
import sys
from typing import Any, Callable, Sequence

_MODEL_PROVENANCE_FIELDS = frozenset({
    "id",
    "revision",
    "dimension",
    "normalized",
    "snapshot_path_suffix",
    "snapshot_digest",
})
_REPO_ROOT = Path(__file__).resolve().parents[1]
_GENERATOR_PATH = "scripts/generate-dual-route-embedding-fixture.py"
_CASES_PATH = "tests/fixtures/dual_route_recall_cases.json"
_OUTPUT_PATH = "tests/fixtures/dual_route_precomputed_embeddings.json"
_LOCKFILE_PATH = "scripts/dual-route-embedding-generator.lock.txt"
_ENCODER_VERSION = "sentence-transformers==3.4.1"


def _searchable_item_text(raw: dict[str, Any]) -> str:
    return " ".join(
        (
            str(raw.get("title", "")),
            str(raw.get("summary", "")),
            str(raw.get("body", "")),
            *(str(tag) for tag in raw.get("tags", [])),
        )
    )


def extract_case_texts(cases: Sequence[dict[str, Any]]) -> tuple[str, ...]:
    """Derive only retrieval inputs from committed cases, ignoring annotations."""
    repo_root = str(_REPO_ROOT)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from agent_brain.memory.recall.admission import build_recall_request

    values: list[str] = []
    for case in cases:
        query = str(case["query"])
        values.append(query)
        request = build_recall_request(query, adapter="fixture-generator")
        if request.query_signal.injectable and request.lexical_terms:
            values.append("|".join(request.lexical_terms))
        for field in ("brain_item",):
            raw = case.get(field)
            if isinstance(raw, dict):
                values.append(_searchable_item_text(raw))
        for raw in case.get("brain_items", []):
            if isinstance(raw, dict):
                values.append(_searchable_item_text(raw))
        for raw in case.get("hard_negative_items", []):
            if isinstance(raw, dict):
                values.append(_searchable_item_text(raw))
    return tuple(dict.fromkeys(values))


def snapshot_content_digest(model_path: Path, revision: str) -> str:
    """Hash every named file in a pinned Hugging Face snapshot."""
    if not revision or Path(revision).name != revision:
        raise ValueError("revision must be one path segment")
    if model_path.name != revision or model_path.parent.name != "snapshots":
        raise ValueError("model path must end with snapshots/<revision>")
    files = sorted(
        (path for path in model_path.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(model_path).as_posix(),
    )
    if not files:
        raise ValueError("model snapshot must contain files")
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(model_path).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return "sha256:" + digest.hexdigest()


def generate_precomputed_embeddings(
    texts: Sequence[str],
    encode: Callable[[list[str]], Any],
    provenance: dict[str, object],
) -> dict[str, object]:
    """Encode unique raw texts without accepting labels, IDs, or expectations."""
    if set(provenance) != _MODEL_PROVENANCE_FIELDS:
        raise ValueError("provenance must contain only frozen model metadata")
    if provenance["normalized"] is not True:
        raise ValueError("provenance must declare normalized embeddings")
    unique_texts = tuple(dict.fromkeys(str(text) for text in texts))
    if not unique_texts or any(not text for text in unique_texts):
        raise ValueError("texts must contain non-empty strings")
    vectors = encode(list(unique_texts))
    if len(vectors) != len(unique_texts):
        raise ValueError("encoder result count does not match text count")

    dimension = int(provenance["dimension"])
    embeddings: dict[str, list[float]] = {}
    for text, raw_vector in zip(unique_texts, vectors, strict=True):
        vector = [round(float(value), 8) for value in raw_vector]
        if len(vector) != dimension:
            raise ValueError("encoder dimension does not match provenance")
        if not all(math.isfinite(value) for value in vector):
            raise ValueError("encoder returned a non-finite value")
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if content_hash in embeddings:
            raise ValueError("content hash collision")
        embeddings[content_hash] = vector

    return {
        "schema_version": 1,
        "content_hash": "sha256:utf-8",
        "model": dict(provenance),
        "generator": _generator_metadata(provenance),
        "embeddings": dict(sorted(embeddings.items())),
    }


def _generator_metadata(provenance: dict[str, object]) -> dict[str, object]:
    model_id = str(provenance["id"])
    revision = str(provenance["revision"])
    model_cache_path = (
        "~/.cache/huggingface/hub/models--"
        + model_id.replace("/", "--")
        + f"/snapshots/{revision}"
    )
    return {
        "path": _GENERATOR_PATH,
        "version": 2,
        "encoder": _ENCODER_VERSION,
        "lockfile": _LOCKFILE_PATH,
        "float_round_digits": 8,
        "runtime": {
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "platform_system": platform.system(),
            "platform_machine": platform.machine(),
        },
        "regeneration": {
            "working_directory": "repository-root",
            "argv": [
                "python",
                _GENERATOR_PATH,
                "--cases",
                _CASES_PATH,
                "--output",
                _OUTPUT_PATH,
                "--model-path",
                model_cache_path,
                "--model-id",
                model_id,
                "--revision",
                revision,
            ],
        },
        "verification": {
            "working_directory": "repository-root",
            "argv": [
                "python",
                _GENERATOR_PATH,
                "--cases",
                _CASES_PATH,
                "--output",
                _OUTPUT_PATH,
                "--verify-existing",
            ],
        },
    }


def _read_cases(path: Path) -> list[dict[str, Any]]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not all(isinstance(case, dict) for case in cases):
        raise ValueError("cases fixture must be a JSON object list")
    return cases


def verify_existing_fixture(
    cases_path: Path,
    output_path: Path,
    *,
    model_path: Path | None = None,
) -> dict[str, object]:
    """Verify a frozen fixture without loading or executing the encoder."""
    payload_bytes = output_path.read_bytes()
    actual_digest = "sha256:" + hashlib.sha256(payload_bytes).hexdigest()
    expected_digest = output_path.with_suffix(".sha256").read_text(
        encoding="utf-8"
    ).strip()
    if expected_digest != actual_digest:
        raise ValueError("precomputed embedding payload digest mismatch")

    payload = json.loads(payload_bytes)
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "content_hash",
        "model",
        "generator",
        "embeddings",
    }:
        raise ValueError("precomputed embedding payload schema mismatch")
    if payload["schema_version"] != 1 or payload["content_hash"] != "sha256:utf-8":
        raise ValueError("precomputed embedding payload contract mismatch")

    model = payload["model"]
    if not isinstance(model, dict) or set(model) != _MODEL_PROVENANCE_FIELDS:
        raise ValueError("precomputed embedding model provenance mismatch")
    if model["normalized"] is not True:
        raise ValueError("precomputed embeddings must be normalized")
    dimension = int(model["dimension"])
    if dimension <= 0:
        raise ValueError("precomputed embedding dimension must be positive")

    generator = payload["generator"]
    if not isinstance(generator, dict):
        raise ValueError("precomputed embedding generator provenance mismatch")
    expected_generator = _generator_metadata(model)
    expected_generator["runtime"] = generator.get("runtime")
    if generator != expected_generator:
        raise ValueError("precomputed embedding generator provenance mismatch")
    runtime = generator.get("runtime")
    if not isinstance(runtime, dict) or set(runtime) != {
        "python_implementation",
        "python_version",
        "platform_system",
        "platform_machine",
    } or any(not isinstance(value, str) or not value for value in runtime.values()):
        raise ValueError("precomputed embedding runtime provenance mismatch")

    lock_lines = {
        line.strip()
        for line in (_REPO_ROOT / _LOCKFILE_PATH).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    if _ENCODER_VERSION not in lock_lines:
        raise ValueError("precomputed embedding generator lock mismatch")

    embeddings = payload["embeddings"]
    if not isinstance(embeddings, dict):
        raise ValueError("precomputed embeddings must be an object")
    expected_hashes = {
        hashlib.sha256(text.encode("utf-8")).hexdigest()
        for text in extract_case_texts(_read_cases(cases_path))
    }
    if set(embeddings) != expected_hashes:
        raise ValueError("precomputed embedding text coverage mismatch")
    for vector in embeddings.values():
        if (
            not isinstance(vector, list)
            or len(vector) != dimension
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in vector
            )
        ):
            raise ValueError("precomputed embedding vector mismatch")
        if not math.isclose(
            math.sqrt(sum(float(value) ** 2 for value in vector)),
            1.0,
            abs_tol=1e-5,
        ):
            raise ValueError("precomputed embedding normalization mismatch")

    snapshot_verified = False
    if model_path is not None:
        revision = str(model["revision"])
        if snapshot_content_digest(model_path, revision) != model["snapshot_digest"]:
            raise ValueError("model snapshot digest mismatch")
        snapshot_verified = True
    return {
        "digest": actual_digest,
        "embedding_count": len(embeddings),
        "model_revision": str(model["revision"]),
        "snapshot_verified": snapshot_verified,
        "verified": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-path", type=Path)
    parser.add_argument("--model-id")
    parser.add_argument("--revision")
    parser.add_argument(
        "--verify-existing",
        action="store_true",
        help="verify the committed payload without loading the encoder",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.verify_existing:
        report = verify_existing_fixture(
            args.cases,
            args.output,
            model_path=args.model_path,
        )
        print(json.dumps(report, sort_keys=True))
        return 0
    if args.model_path is None or args.model_id is None or args.revision is None:
        parser.error("generation requires --model-path, --model-id, and --revision")
    from sentence_transformers import SentenceTransformer

    cases = _read_cases(args.cases)
    texts = extract_case_texts(cases)
    model = SentenceTransformer(str(args.model_path), local_files_only=True)

    def encode(values: list[str]):
        return model.encode(
            values,
            batch_size=16,
            show_progress_bar=False,
            normalize_embeddings=True,
        )

    dimension = int(model.get_sentence_embedding_dimension())
    snapshot_digest = snapshot_content_digest(args.model_path, args.revision)
    payload = generate_precomputed_embeddings(
        texts,
        encode,
        {
            "id": args.model_id,
            "revision": args.revision,
            "dimension": dimension,
            "normalized": True,
            "snapshot_path_suffix": f"snapshots/{args.revision}",
            "snapshot_digest": snapshot_digest,
        },
    )
    payload_bytes = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    args.output.write_bytes(payload_bytes)
    args.output.with_suffix(".sha256").write_text(
        "sha256:" + hashlib.sha256(payload_bytes).hexdigest() + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
