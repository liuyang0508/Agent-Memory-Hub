#!/usr/bin/env python3
"""One-time generator for the offline dual-route semantic fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Callable, Sequence

_MODEL_PROVENANCE_FIELDS = frozenset({"id", "revision", "dimension", "normalized"})


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
        for raw in case.get("hard_negative_items", []):
            if isinstance(raw, dict):
                values.append(_searchable_item_text(raw))
    return tuple(dict.fromkeys(values))


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
        "generator": {
            "path": "scripts/generate-dual-route-embedding-fixture.py",
            "version": 1,
            "encoder": "sentence-transformers==3.4.1",
            "float_round_digits": 8,
        },
        "embeddings": dict(sorted(embeddings.items())),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--revision", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    from sentence_transformers import SentenceTransformer

    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not all(isinstance(case, dict) for case in cases):
        raise ValueError("cases fixture must be a JSON object list")
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
    payload = generate_precomputed_embeddings(
        texts,
        encode,
        {
            "id": args.model_id,
            "revision": args.revision,
            "dimension": dimension,
            "normalized": True,
        },
    )
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
