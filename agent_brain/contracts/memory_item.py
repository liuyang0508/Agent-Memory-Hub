from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent_brain.contracts.memory_enums import (
    DECAY_HALF_LIFE_DAYS as DECAY_HALF_LIFE_DAYS,
    TYPE_TO_DECAY_CLASS,
    AbstractionLayer,
    DecayClass,
    Maturity,
    MemoryType,
    Sensitivity,
)


class Retention(BaseModel):
    model_config = ConfigDict(extra="ignore")
    last_accessed: datetime | None = None
    access_count: int = 0
    decay_class: DecayClass = DecayClass.fact

    @field_validator("decay_class", mode="before")
    @classmethod
    def _coerce_decay_class(cls, value: Any) -> str:
        if isinstance(value, str):
            return value
        return str(value)


class Refs(BaseModel):
    model_config = ConfigDict(extra="ignore")
    files: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    mems: list[str] = Field(default_factory=list)
    commits: list[str] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=list)
    extractions: list[str] = Field(default_factory=list)


class Source(BaseModel):
    """Provenance of how an item entered the pool (orthogonal to type/abstraction).

    Used by the harvester for span-level dedup so the same transcript region is
    never archived twice. Optional + defaulted → backward-compatible with 0.4 items.
    """

    model_config = ConfigDict(extra="ignore")
    kind: str = "manual"            # manual | harvested | pending-replay | remember
    transcript_id: str | None = None
    span_hash: str | None = None    # sha256 of the normalized harvested span
    extractor: str | None = None    # mechanical | llm


class Validity(BaseModel):
    """Scope where a state observation was known to be valid.

    Optional + ignored-extra keeps old items loadable while letting newer
    runtime-state memories declare where they were observed.
    """

    model_config = ConfigDict(extra="ignore")
    observed_at: datetime | None = None
    ttl_hours: int | None = None
    cwd: str | None = None
    repo: str | None = None
    branch: str | None = None
    os: str | None = None
    adapter: str | None = None

    @field_validator("observed_at")
    @classmethod
    def _ensure_observed_tz_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class ContextViews(BaseModel):
    """Token-budgeted loading views for one memory item.

    locator is used for recall, overview for structured context, and detail_uri
    points at the canonical markdown body.
    """

    model_config = ConfigDict(extra="ignore")
    locator: str = ""
    overview: str = ""
    detail_uri: str = ""


_ID_PATTERN = re.compile(r"^mem-\d{8}-\d{6}-[^\s/\\]{1,200}$")
_ABSTRACTION_TO_MATURITY = {
    "L0": Maturity.raw,
    "L1": Maturity.consolidated,
    "L2": Maturity.skill,
}
_LEGACY_TIER_TO_MATURITY = {
    "raw": Maturity.raw,
    "consolidated": Maturity.consolidated,
    "skill": Maturity.skill,
}


class MemoryItem(BaseModel):
    """Memory item frontmatter schema — forward-only, no version numbering."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    id: str
    schema_version: str = Field(default="1", exclude=True)
    type: MemoryType
    created_at: datetime
    agent: str | None = None
    session: str | None = None
    project: str | None = None
    tenant_id: str | None = None
    auth_context: str | None = None
    tags: list[str] = Field(default_factory=list)
    sensitivity: Sensitivity = Sensitivity.internal
    title: str
    summary: str
    refs: Refs = Field(default_factory=Refs)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    retention: Retention = Field(default_factory=Retention)
    abstraction: AbstractionLayer = AbstractionLayer.L0
    maturity: Maturity = Maturity.raw
    context_views: ContextViews = Field(default_factory=ContextViews)
    source: Source = Field(default_factory=Source)
    validity: Validity = Field(default_factory=Validity)

    # Reflect2Evolve: maturity counters
    support_count: int = 0
    contradict_count: int = 0
    gain_score: float = 0.0

    # Reflect2Evolve: evolution chain
    evolved_from: list[str] = Field(default_factory=list)
    superseded_by: str | None = None
    version: int = 1

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not _ID_PATTERN.match(value):
            raise ValueError(f"id must match {_ID_PATTERN.pattern}, got {value!r}")
        return value

    @field_validator("created_at")
    @classmethod
    def _ensure_tz_aware(cls, value: datetime) -> datetime:
        """Normalize naive datetimes to tz-aware UTC.

        Hand-authored md, Obsidian imports, and date-only strings parse to
        naive datetimes, which crash every downstream comparison against a
        tz-aware ``datetime.now(timezone.utc)`` (drift staleness, governance
        TTL, cli/mcp gc + list_recent + stats, hermes sort). Already-aware
        values keep their original offset — aware-vs-aware comparison is safe.
        """
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @field_validator("schema_version", mode="before")
    @classmethod
    def _coerce_schema_version(cls, value: Any) -> str:
        if isinstance(value, (int, float)):
            return str(value)
        return value

    @field_validator("session", mode="before")
    @classmethod
    def _coerce_session(cls, value: Any) -> str | None:
        if isinstance(value, (int, float)):
            return str(value)
        return value

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, value: Any) -> float:
        if value is None:
            return 0.7
        return float(value)

    @model_validator(mode="before")
    @classmethod
    def _auto_fill_retention(cls, data: Any) -> Any:
        """Auto-fill confidence + retention defaults; auto-map decay_class from type."""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        data.setdefault("confidence", 0.7)
        if "retention" not in data:
            mem_type = data.get("type", "fact")
            if isinstance(mem_type, MemoryType):
                mem_type = mem_type.value
            dc = TYPE_TO_DECAY_CLASS.get(str(mem_type), "fact")
            data["retention"] = {"decay_class": dc, "access_count": 0}
        elif isinstance(data.get("retention"), dict):
            retention = dict(data["retention"])
            if str(retention.get("decay_class", "")).lower() == "durable":
                mem_type = data.get("type", "fact")
                if isinstance(mem_type, MemoryType):
                    mem_type = mem_type.value
                retention["decay_class"] = TYPE_TO_DECAY_CLASS.get(str(mem_type), "fact")
                data["retention"] = retention
        tier = data.pop("tier", None)
        if "maturity" not in data:
            if str(tier or "") in _LEGACY_TIER_TO_MATURITY:
                data["maturity"] = _LEGACY_TIER_TO_MATURITY[str(tier)].value
            else:
                abstraction = data.get("abstraction", AbstractionLayer.L0)
                data["maturity"] = _ABSTRACTION_TO_MATURITY.get(str(abstraction), Maturity.raw).value
        context_views = data.get("context_views") or {}
        if isinstance(context_views, dict):
            context_views = dict(context_views)
            context_views.setdefault("locator", data.get("summary", ""))
            context_views.setdefault("overview", "")
            if data.get("id"):
                context_views.setdefault("detail_uri", f"memory://items/{data['id']}/body")
            data["context_views"] = context_views
        return data
