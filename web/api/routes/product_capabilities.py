"""Productized memory capability routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from agent_brain.evaluation.compression_gate import (
    CompressionCase,
    evaluate_compression_cases,
    load_builtin_compression_cases,
)
from agent_brain.evaluation.ml_advisory_gate import (
    MLAdvisoryCase,
    evaluate_ml_advisory_cases,
    load_builtin_ml_advisory_cases,
)
from agent_brain.evaluation.retrieval_gate import RetrievalCase, evaluate_rankings
from agent_brain.platform.headroom_integration import (
    compress_with_headroom,
    headroom_status,
    retrieve_compressed_original,
)
from agent_brain.product.hierarchical_memory import build_hierarchical_memory
from agent_brain.product.memory_profiles import export_memory_profile
from web._base import _brain_dir, _components
from web.auth import CurrentUser, get_current_user, require_admin


router = APIRouter()


class MemoryProfileRequest(BaseModel):
    target: str = "codex"
    output_root: str | None = None
    project: str | None = None
    max_items: int = Field(default=24, ge=0, le=200)
    apply: bool = False


class HierarchyBuildRequest(BaseModel):
    apply: bool = False
    max_topics: int = Field(default=24, ge=1, le=200)
    max_items_per_node: int = Field(default=8, ge=1, le=50)


class RetrievalGateCase(BaseModel):
    query: str
    expected_ids: list[str]
    weight: float = 1.0


class RetrievalGateRequest(BaseModel):
    cases: list[RetrievalGateCase]
    top_k: int = Field(default=10, ge=1, le=100)
    min_recall_at_1: float = Field(default=0.6, ge=0.0, le=1.0)
    min_mrr: float = Field(default=0.6, ge=0.0, le=1.0)


class CompressionGateCase(BaseModel):
    name: str
    text: str
    query: str | None = None
    budget_chars: int = Field(default=1200, ge=1, le=20000)
    detail_uri: str | None = "memory://compression-gate/body"
    expected_content_type: str | None = None
    expected_strategy: str | None = None
    must_keep: list[str] = Field(default_factory=list)
    must_drop: list[str] = Field(default_factory=list)
    max_compression_ratio: float = Field(default=1.0, ge=0.0, le=1.0)
    min_tokens_saved: int = Field(default=0, ge=0)
    require_reversible: bool = True


class CompressionGateRequest(BaseModel):
    cases: list[CompressionGateCase] = Field(default_factory=list)
    min_pass_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    max_mean_compression_ratio: float = Field(default=0.8, ge=0.0, le=1.0)
    min_mean_tokens_saved: float = Field(default=1.0, ge=0.0)


class MLAdvisoryGateCase(BaseModel):
    name: str
    baseline_score: float = Field(ge=0.0, le=1.0)
    candidate_score: float = Field(ge=0.0, le=1.0)
    candidate_mode: str = "advisory"
    required_gates: list[str] = Field(default_factory=lambda: ["retrieval", "compression", "privacy"])
    passed_gates: list[str] = Field(default_factory=list)
    min_delta: float = Field(default=0.03, ge=0.0, le=1.0)
    expected_recommendation: str = "hold"
    expected_allows_default: bool = False
    max_latency_ms: float | None = Field(default=250.0, ge=0.0)
    candidate_latency_ms: float | None = Field(default=80.0, ge=0.0)
    privacy_mode: str = "local"


class MLAdvisoryGateRequest(BaseModel):
    cases: list[MLAdvisoryGateCase] = Field(default_factory=list)
    min_pass_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    max_unsafe_promotions: int = Field(default=0, ge=0)


class HeadroomCompressRequest(BaseModel):
    text: str
    budget_chars: int = Field(default=1200, ge=1, le=20000)
    detail_uri: str | None = None
    query: str | None = None


@router.post("/api/memory-profiles/export")
async def memory_profile_export(
    payload: MemoryProfileRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """Render or apply a managed agent memory profile."""

    require_admin(user)
    output_root = Path(payload.output_root) if payload.output_root else None
    return export_memory_profile(
        _brain_dir(),
        target=payload.target,
        output_root=output_root,
        project=payload.project,
        max_items=payload.max_items,
        apply=payload.apply,
    ).to_dict()


@router.get("/api/hierarchical-memory")
async def hierarchical_memory(user: CurrentUser = Depends(get_current_user)):
    """Return the current deterministic L2/L3 hierarchy preview."""

    require_admin(user)
    return build_hierarchical_memory(_brain_dir(), apply=False).to_dict()


@router.post("/api/hierarchical-memory/build")
async def hierarchical_memory_build(
    payload: HierarchyBuildRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """Build the deterministic L2/L3 hierarchy sidecar."""

    require_admin(user)
    return build_hierarchical_memory(
        _brain_dir(),
        apply=payload.apply,
        max_topics=payload.max_topics,
        max_items_per_node=payload.max_items_per_node,
    ).to_dict()


@router.post("/api/retrieval-gate")
async def retrieval_gate(
    payload: RetrievalGateRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """Run a retrieval benchmark gate against the current index."""

    require_admin(user)
    _store, _idx, retriever, _embedder = _components()
    cases = [
        RetrievalCase(query=case.query, expected_ids=case.expected_ids, weight=case.weight)
        for case in payload.cases
    ]

    def search(query: str, depth: int) -> list[str]:
        return [hit.id for hit in retriever.search(query, top_k=depth)]

    return evaluate_rankings(
        cases,
        search,
        top_k=payload.top_k,
        min_recall_at_1=payload.min_recall_at_1,
        min_mrr=payload.min_mrr,
    ).to_dict()


@router.post("/api/compression-gate")
async def compression_gate(
    payload: CompressionGateRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """Run the few-shot compression quality gate."""

    cases = [
        CompressionCase(
            name=case.name,
            text=case.text,
            query=case.query,
            budget_chars=case.budget_chars,
            detail_uri=case.detail_uri,
            expected_content_type=case.expected_content_type,
            expected_strategy=case.expected_strategy,
            must_keep=tuple(case.must_keep),
            must_drop=tuple(case.must_drop),
            max_compression_ratio=case.max_compression_ratio,
            min_tokens_saved=case.min_tokens_saved,
            require_reversible=case.require_reversible,
        )
        for case in payload.cases
    ] or load_builtin_compression_cases()
    return evaluate_compression_cases(
        cases,
        min_pass_rate=payload.min_pass_rate,
        max_mean_compression_ratio=payload.max_mean_compression_ratio,
        min_mean_tokens_saved=payload.min_mean_tokens_saved,
    ).to_dict()


@router.post("/api/ml-advisory-gate")
async def ml_advisory_gate(
    payload: MLAdvisoryGateRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """Run the few-shot ML/DL advisory gate."""

    cases = [
        MLAdvisoryCase(
            name=case.name,
            baseline_score=case.baseline_score,
            candidate_score=case.candidate_score,
            candidate_mode=case.candidate_mode,
            required_gates=tuple(case.required_gates),
            passed_gates=tuple(case.passed_gates),
            min_delta=case.min_delta,
            expected_recommendation=case.expected_recommendation,
            expected_allows_default=case.expected_allows_default,
            max_latency_ms=case.max_latency_ms,
            candidate_latency_ms=case.candidate_latency_ms,
            privacy_mode=case.privacy_mode,
        )
        for case in payload.cases
    ] or load_builtin_ml_advisory_cases()
    return evaluate_ml_advisory_cases(
        cases,
        min_pass_rate=payload.min_pass_rate,
        max_unsafe_promotions=payload.max_unsafe_promotions,
    ).to_dict()


@router.get("/api/headroom/status")
async def headroom_status_route(user: CurrentUser = Depends(get_current_user)):
    """Return optional Headroom bridge availability."""

    return headroom_status().to_dict()


@router.post("/api/headroom/compress")
async def headroom_compress_route(
    payload: HeadroomCompressRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """Compress text through Headroom when available, else AMH-local adaptive compression."""

    return compress_with_headroom(
        payload.text,
        budget_chars=payload.budget_chars,
        detail_uri=payload.detail_uri,
        query=payload.query,
        brain_dir=_brain_dir(),
    ).to_dict()


@router.get("/api/headroom/retrieve/{key}")
async def headroom_retrieve_route(
    key: str,
    user: CurrentUser = Depends(get_current_user),
):
    """Retrieve an AMH-local compression sidecar by CCR key."""

    require_admin(user)
    text = retrieve_compressed_original(key, brain_dir=_brain_dir())
    if text is None:
        raise HTTPException(status_code=404, detail="compressed original not found")
    return {"key": key, "text": text}
