"""Wire contracts: clients cannot supply authenticated producer fields."""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .common import PRODUCTS


class AssetInput(BaseModel):
    """Untrusted source revision; authenticated member/tenant fields are deliberately not accepted."""

    model_config = ConfigDict(extra="forbid")
    asset_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    base_revision: int = Field(default=0, ge=0)
    source_store: str = Field(min_length=1, max_length=128)
    product: str
    project: str = Field(default="", max_length=256)
    kind: Literal["memory", "transcript", "rule", "skill", "config"] = "memory"
    path: str = Field(max_length=2048)
    title: str = Field(default="", max_length=512)
    body: str = Field(default="", max_length=1_000_000)
    encoding: Literal["utf8", "base64"] = "utf8"
    document_id: str = Field(default="", max_length=128)
    document_hash: str = Field(default="", max_length=64)
    part_index: int = Field(default=0, ge=0, le=100000)
    part_count: int = Field(default=1, ge=1, le=100000)
    index_text: str | None = Field(default=None, max_length=1_000_000)
    deleted: bool = False
    producer_unknown: bool = False
    upstream: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def valid_parts(self) -> "AssetInput":
        if self.part_index >= self.part_count:
            raise ValueError("part index must be smaller than part count")
        if self.part_count > 1 and (not self.document_id or len(self.document_hash) != 64):
            raise ValueError("multipart assets require document identity and hash")
        return self

    @field_validator("product")
    @classmethod
    def known_product(cls, value: str) -> str:
        if value not in PRODUCTS:
            raise ValueError("unknown product")
        return value


class EnrollInput(BaseModel):
    """One-use invitation plus a display-only device name, never the vendor account password."""

    model_config = ConfigDict(extra="forbid")
    invite: str = Field(min_length=16, max_length=256)
    device_name: str = Field(min_length=1, max_length=160)


class SearchInput(BaseModel):
    """Bounded organization query; project_hint ranks while project_filter explicitly restricts results."""

    model_config = ConfigDict(extra="forbid")
    query: str = Field(max_length=8000)
    project_hint: str = Field(default="", max_length=256)
    project_filter: str | None = Field(default=None, max_length=256)
    limit: int = Field(default=12, ge=1, le=50)


class RuntimeInviteInput(BaseModel):
    """Identity and lifetime are server-owned; no caller-selected membership fields."""

    model_config = ConfigDict(extra="forbid")
