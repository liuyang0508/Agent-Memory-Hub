"""Shared index value objects."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Hit:
    id: str
    score: float


__all__ = ["Hit"]
