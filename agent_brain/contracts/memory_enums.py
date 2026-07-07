from __future__ import annotations

from enum import Enum


class MemoryType(str, Enum):
    fact = "fact"
    episode = "episode"
    decision = "decision"
    artifact = "artifact"
    signal = "signal"
    handoff = "handoff"
    policy = "policy"
    skill = "skill"


class AbstractionLayer(str, Enum):
    """Governance abstraction axis: how distilled an item is."""

    L0 = "L0"
    L1 = "L1"
    L2 = "L2"


class Maturity(str, Enum):
    """Memory maturity axis: how trustworthy/reusable an item has become."""

    raw = "raw"
    consolidated = "consolidated"
    skill = "skill"


class DecayClass(str, Enum):
    architecture = "architecture"
    decision = "decision"
    fact = "fact"
    episode = "episode"
    ephemeral = "ephemeral"


DECAY_HALF_LIFE_DAYS: dict[str, int] = {
    "architecture": 180,
    "decision": 90,
    "fact": 60,
    "episode": 30,
    "ephemeral": 7,
}

TYPE_TO_DECAY_CLASS: dict[str, str] = {
    "fact": "fact",
    "episode": "episode",
    "decision": "decision",
    "artifact": "architecture",
    "signal": "ephemeral",
    "handoff": "ephemeral",
    "policy": "decision",
    "skill": "architecture",
}


def memory_enum_value(value: object) -> str:
    """Return the serialized value for enum-backed memory fields."""
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


class Sensitivity(str, Enum):
    public = "public"
    internal = "internal"
    private = "private"
    secret = "secret"


__all__ = [
    "AbstractionLayer",
    "DECAY_HALF_LIFE_DAYS",
    "DecayClass",
    "Maturity",
    "MemoryType",
    "Sensitivity",
    "TYPE_TO_DECAY_CLASS",
    "memory_enum_value",
]
