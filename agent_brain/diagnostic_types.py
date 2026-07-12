"""Dependency-neutral adapter diagnostic value types."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


CheckStatus = Literal["ok", "warn", "error"]


@dataclass(frozen=True)
class AdapterDiagnosticCheck:
    name: str
    status: CheckStatus
    detail: str
    fix: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


__all__ = ["AdapterDiagnosticCheck", "CheckStatus"]
