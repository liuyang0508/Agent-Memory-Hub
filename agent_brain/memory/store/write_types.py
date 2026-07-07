from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class WriteResult:
    """Outcome of a funneled write."""

    status: str
    item_id: str | None = None
    path: str | None = None
    indexed: bool = False
    degraded: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    findings: list[dict] | None = None


__all__ = ["WriteResult"]
