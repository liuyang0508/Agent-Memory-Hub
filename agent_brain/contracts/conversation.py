from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent_brain.contracts.memory_enums import Sensitivity
from agent_brain.contracts.resource import sha256_text


class ConversationTier(str, Enum):
    hot = "hot"
    warm = "warm"
    cold = "cold"
    frozen = "frozen"


_SAFE_SUFFIX_PATTERN = re.compile(r"[^a-zA-Z0-9_.-]+")
_CONVERSATION_ID_PATTERN = re.compile(r"^conv-[a-f0-9]{16}-[a-zA-Z0-9_.-]{1,80}$")
_MESSAGE_ID_PATTERN = re.compile(r"^cmsg-[a-f0-9]{24}$")
_SHA256_PATTERN = re.compile(r"^[a-fA-F0-9]{64}$")


def _slug(text: str, *, fallback: str = "session") -> str:
    slug = _SAFE_SUFFIX_PATTERN.sub("-", text.strip())[:80].strip("-._")
    return slug or fallback


def make_conversation_id(source_agent: str, session_id: str | None) -> str:
    """Deterministic conversation id for one agent/session evidence stream."""
    session = session_id or "unknown-session"
    digest = hashlib.sha256(f"{source_agent}\0{session}".encode("utf-8")).hexdigest()[:16]
    return f"conv-{digest}-{_slug(session)}"


def make_message_id(
    *,
    conversation_id: str,
    role: str,
    content_text: str,
    source_uri: str | None = None,
    source_offset_start: int | None = None,
    source_offset_end: int | None = None,
) -> str:
    """Content-addressed message id with source offsets when available."""
    payload = "\0".join([
        conversation_id,
        role,
        source_uri or "",
        "" if source_offset_start is None else str(source_offset_start),
        "" if source_offset_end is None else str(source_offset_end),
        content_text,
    ])
    return "cmsg-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


class ConversationRetention(BaseModel):
    model_config = ConfigDict(extra="ignore")

    last_accessed: datetime | None = None
    access_count: int = 0
    half_life_days: int = Field(default=30, ge=1)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("last_accessed")
    @classmethod
    def _ensure_last_accessed_tz_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class ConversationMessageRecord(BaseModel):
    """Raw conversation evidence message.

    This is deliberately separate from MemoryItem: messages are source evidence,
    not automatically injectable shared knowledge.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    schema_version: str = "1"
    id: str
    conversation_id: str
    source_agent: str
    session_id: str | None = None
    role: str
    content_text: str
    content_sha256: str
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source_uri: str | None = None
    source_path: str | None = None
    source_offset_start: int | None = Field(default=None, ge=0)
    source_offset_end: int | None = Field(default=None, ge=0)
    project: str | None = None
    cwd: str | None = None
    tenant_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    sensitivity: Sensitivity = Sensitivity.internal
    tier: ConversationTier = ConversationTier.hot
    retention: ConversationRetention = Field(default_factory=ConversationRetention)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not _MESSAGE_ID_PATTERN.match(value):
            raise ValueError(f"id must match {_MESSAGE_ID_PATTERN.pattern}, got {value!r}")
        return value

    @field_validator("conversation_id")
    @classmethod
    def _validate_conversation_id(cls, value: str) -> str:
        if not _CONVERSATION_ID_PATTERN.match(value):
            raise ValueError(
                f"conversation_id must match {_CONVERSATION_ID_PATTERN.pattern}, got {value!r}"
            )
        return value

    @field_validator("content_sha256")
    @classmethod
    def _validate_content_sha256(cls, value: str) -> str:
        if not _SHA256_PATTERN.match(value):
            raise ValueError("content_sha256 must be 64 hex characters")
        return value.lower()

    @field_validator("observed_at")
    @classmethod
    def _ensure_observed_tz_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @model_validator(mode="after")
    def _ensure_content_hash_matches(self) -> ConversationMessageRecord:
        if self.content_sha256 != sha256_text(self.content_text):
            raise ValueError("content_sha256 does not match content_text")
        return self

    @model_validator(mode="after")
    def _ensure_offsets_are_ordered(self) -> ConversationMessageRecord:
        if (
            self.source_offset_start is not None
            and self.source_offset_end is not None
            and self.source_offset_end < self.source_offset_start
        ):
            raise ValueError("source_offset_end must be >= source_offset_start")
        return self


class ConversationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    conversation_id: str
    source_agent: str
    session_id: str | None = None
    project: str | None = None
    message_count: int
    first_observed_at: datetime
    last_observed_at: datetime
    tier: ConversationTier


__all__ = [
    "ConversationMessageRecord",
    "ConversationRetention",
    "ConversationSummary",
    "ConversationTier",
    "make_conversation_id",
    "make_message_id",
]
