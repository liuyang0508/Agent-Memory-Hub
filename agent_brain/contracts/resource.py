from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent_brain.contracts.memory_enums import Sensitivity


class ResourceKind(str, Enum):
    file = "file"
    url = "url"
    image = "image"
    pdf = "pdf"
    audio = "audio"
    video = "video"
    document = "document"
    web = "web"
    other = "other"


class ExtractionKind(str, Enum):
    text = "text"
    metadata = "metadata"
    ocr = "ocr"
    asr = "asr"
    vlm_caption = "vlm_caption"
    summary = "summary"
    outline = "outline"
    segment = "segment"


_RESOURCE_ID_PATTERN = re.compile(r"^res-\d{8}-\d{6}-[^\s/\\]{1,200}$")
_EXTRACTION_ID_PATTERN = re.compile(r"^ext-\d{8}-\d{6}-[^\s/\\]{1,200}$")
_SHA256_PATTERN = re.compile(r"^[a-fA-F0-9]{64}$")


def _slug(text: str) -> str:
    return re.sub(r"[/\\]+", "-", "-".join(text.lower().split()))[:30].strip("-") or (
        uuid.uuid4().hex[:6]
    )


def make_resource_id(title: str, when: datetime | None = None) -> str:
    when = when or datetime.now(timezone.utc).astimezone()
    return f"res-{when:%Y%m%d-%H%M%S}-{_slug(title)}-{uuid.uuid4().hex[:8]}"


def make_extraction_id(title: str, when: datetime | None = None) -> str:
    when = when or datetime.now(timezone.utc).astimezone()
    return f"ext-{when:%Y%m%d-%H%M%S}-{_slug(title)}-{uuid.uuid4().hex[:8]}"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class ResourceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    id: str
    kind: ResourceKind
    uri: str
    title: str
    mime_type: str | None = None
    sha256: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    project: str | None = None
    tenant_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    sensitivity: Sensitivity = Sensitivity.internal
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not _RESOURCE_ID_PATTERN.match(value):
            raise ValueError(f"id must match {_RESOURCE_ID_PATTERN.pattern}, got {value!r}")
        return value

    @field_validator("created_at")
    @classmethod
    def _ensure_tz_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @field_validator("sha256")
    @classmethod
    def _validate_sha256(cls, value: str | None) -> str | None:
        if value is not None and not _SHA256_PATTERN.match(value):
            raise ValueError("sha256 must be 64 hex characters")
        return value.lower() if value is not None else None


class ExtractionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    id: str
    resource_id: str
    kind: ExtractionKind
    extractor: str
    content_text: str
    content_sha256: str
    extractor_version: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    source_locator: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not _EXTRACTION_ID_PATTERN.match(value):
            raise ValueError(f"id must match {_EXTRACTION_ID_PATTERN.pattern}, got {value!r}")
        return value

    @field_validator("resource_id")
    @classmethod
    def _validate_resource_id(cls, value: str) -> str:
        if not _RESOURCE_ID_PATTERN.match(value):
            raise ValueError(f"resource_id must match {_RESOURCE_ID_PATTERN.pattern}, got {value!r}")
        return value

    @field_validator("created_at")
    @classmethod
    def _ensure_tz_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @field_validator("content_sha256")
    @classmethod
    def _validate_content_sha256(cls, value: str) -> str:
        if not _SHA256_PATTERN.match(value):
            raise ValueError("content_sha256 must be 64 hex characters")
        return value.lower()

    @model_validator(mode="after")
    def _ensure_content_hash_matches(self) -> ExtractionRecord:
        expected = sha256_text(self.content_text)
        if self.content_sha256 != expected:
            raise ValueError("content_sha256 does not match content_text")
        return self


__all__ = [
    "ExtractionKind",
    "ExtractionRecord",
    "ResourceKind",
    "ResourceRecord",
    "make_extraction_id",
    "make_resource_id",
    "sha256_file",
    "sha256_text",
]
