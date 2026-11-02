"""Continuous Hopfield expansion over candidate vectors; pure Python, no model calls.

A query attractor is formed from the seeds (softmax over their scores), one
Hopfield/attention step pulls it towards stored patterns, and patterns that
were not seeds are returned by their similarity to the attractor. Works on
plain lists so the native Hub can use it without a numeric runtime.
"""

from __future__ import annotations

import math


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    return [v / norm for v in vector] if norm > 0 else [0.0 for _ in vector]


def _dot(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right)) if len(left) == len(right) else 0.0


def _softmax(values: list[float]) -> list[float]:
    if not values:
        return []
    offset = max(values)
    weights = [math.exp(v - offset) for v in values]
    total = sum(weights)
    return [w / total for w in weights] if total > 0 else [1.0 / len(values)] * len(values)


def _blend(vectors: list[list[float]], weights: list[float]) -> list[float]:
    result = [0.0] * len(vectors[0])
    for vector, weight in zip(vectors, weights):
        for index, value in enumerate(vector):
            result[index] += value * weight
    return result


def hopfield_expand(
    seeds: list[tuple[str, float, list[float]]],
    patterns: dict[str, list[float]],
    *,
    beta: float = 8.0,
    min_similarity: float = 0.0,
    limit: int = 20,
) -> tuple[list[float], list[tuple[str, float]]]:
    """Return (attractor, [(pattern_id, cosine)]) for non-seed patterns, best first.

    seeds carry (id, score, vector); scores only weight the initial query.
    An empty seed set or pattern store yields no attractor and no associations.
    """
    seeds = [(key, score, vector) for key, score, vector in seeds if vector and any(vector)]
    if not seeds or not patterns or limit <= 0:
        return [], []
    dimension = len(seeds[0][2])
    stored = {
        key: _normalize(vector)
        for key, vector in patterns.items()
        if len(vector) == dimension and any(vector)
    }
    if not stored:
        return [], []
    query = _normalize(
        _blend(
            [_normalize(vector) for _, _, vector in seeds],
            _softmax([float(score) for _, score, _ in seeds]),
        )
    )
    ids = list(stored)
    attention = _softmax([beta * _dot(query, stored[key]) for key in ids])
    attractor = _normalize(_blend([stored[key] for key in ids], attention))
    seen = {key for key, _, _ in seeds}
    found = [
        (key, _dot(attractor, stored[key]))
        for key in ids
        if key not in seen and math.isfinite(_dot(attractor, stored[key]))
    ]
    found = [(key, sim) for key, sim in found if sim >= min_similarity]
    found.sort(key=lambda pair: (-pair[1], pair[0]))
    return attractor, found[:limit]


__all__ = ["hopfield_expand"]
