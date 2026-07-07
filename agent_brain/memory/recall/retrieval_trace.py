from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RetrievalStageTrace:
    """One retrieval pipeline stage's visible effect on a final hit."""

    name: str
    before_rank: int | None
    after_rank: int | None
    before_score: float | None
    after_score: float | None
    effect: str

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "before_rank": self.before_rank,
            "after_rank": self.after_rank,
            "before_score": self.before_score,
            "after_score": self.after_score,
            "effect": self.effect,
        }


@dataclass(frozen=True)
class RetrievalTrace:
    """Explain how one returned memory reached its final retrieval position."""

    initial_bm25_rank: int | None
    initial_vector_rank: int | None
    initial_score: float
    final_rank: int
    final_score: float
    stages: tuple[RetrievalStageTrace, ...] = ()
    signals: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "initial_bm25_rank": self.initial_bm25_rank,
            "initial_vector_rank": self.initial_vector_rank,
            "initial_score": self.initial_score,
            "final_rank": self.final_rank,
            "final_score": self.final_score,
            "stages": [stage.to_dict() for stage in self.stages],
            "signals": list(self.signals),
        }

    def compact(self) -> str:
        origins: list[str] = []
        if self.initial_bm25_rank is not None:
            origins.append(f"bm25#{self.initial_bm25_rank}")
        if self.initial_vector_rank is not None:
            origins.append(f"vector#{self.initial_vector_rank}")
        if not origins:
            origins.append("added")
        stage_bits = [
            f"{stage.name}:{stage.effect}"
            for stage in self.stages
            if stage.effect != "kept"
        ]
        flow = " ".join(stage_bits[:6]) if stage_bits else "kept"
        return (
            f"rrf({','.join(origins)}) "
            f"score={self.initial_score:.4f}->final#{self.final_rank}:{self.final_score:.4f}; "
            f"{flow}"
        )


__all__ = ["RetrievalStageTrace", "RetrievalTrace"]
