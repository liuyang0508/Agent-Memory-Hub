"""Continuous Hopfield-style associative memory for recall.

This is the modern Hopfield/attention form: a query attends over stored
patterns, softmax weights create an attractor, and the strongest associations
are returned with an explainable weight. It is intentionally lightweight so it
can run inside retrieval without adding a neural runtime dependency.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class HopfieldAssociation:
    id: str
    weight: float
    similarity: float
    score: float


@dataclass(frozen=True)
class HopfieldRecall:
    attractor: list[float]
    associations: tuple[HopfieldAssociation, ...]


class ContinuousHopfieldMemory:
    """A small continuous associative memory over normalized vectors."""

    def __init__(
        self,
        patterns: dict[str, list[float]],
        *,
        beta: float = 8.0,
        normalize: bool = True,
    ) -> None:
        self.beta = beta
        self.normalize = normalize
        self.patterns = {
            item_id: _normalize(vector) if normalize else list(vector)
            for item_id, vector in patterns.items()
        }

    def recall(self, query: list[float], *, top_k: int = 10) -> HopfieldRecall:
        """Recall associations and the resulting attractor for ``query``."""
        if not self.patterns:
            return HopfieldRecall(attractor=[], associations=())
        q = _normalize(query) if self.normalize else list(query)
        ids = list(self.patterns)
        similarities = [_cosine(q, self.patterns[item_id]) for item_id in ids]
        weights = _softmax([self.beta * sim for sim in similarities])
        attractor = _weighted_sum([self.patterns[item_id] for item_id in ids], weights)
        associations = [
            HopfieldAssociation(
                id=item_id,
                weight=weight,
                similarity=similarity,
                score=weight * max(0.0, similarity),
            )
            for item_id, weight, similarity in zip(ids, weights, similarities, strict=True)
        ]
        associations.sort(key=lambda assoc: assoc.score, reverse=True)
        return HopfieldRecall(
            attractor=attractor,
            associations=tuple(associations[:top_k]),
        )


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        return [0.0 for _ in vector]
    return [value / norm for value in vector]


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True))


def _softmax(values: list[float]) -> list[float]:
    if not values:
        return []
    offset = max(values)
    exp_values = [math.exp(value - offset) for value in values]
    total = sum(exp_values)
    if total <= 0:
        return [1.0 / len(values) for _ in values]
    return [value / total for value in exp_values]


def _weighted_sum(vectors: list[list[float]], weights: list[float]) -> list[float]:
    if not vectors:
        return []
    dim = len(vectors[0])
    result = [0.0] * dim
    for vector, weight in zip(vectors, weights, strict=True):
        for index, value in enumerate(vector):
            result[index] += value * weight
    return result


__all__ = [
    "ContinuousHopfieldMemory",
    "HopfieldAssociation",
    "HopfieldRecall",
]
