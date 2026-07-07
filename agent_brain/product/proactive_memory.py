"""Mechanical proactive memory candidate queue."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Refs, Source
from agent_brain.memory.store.items_store import ItemsStore, make_item_id
from agent_brain.memory.store.write_service import WriteService


SIGNAL_TERMS = (
    "decision:",
    "decide",
    "blocked",
    "blocker",
    "handoff",
    "remember",
    "决定",
    "阻塞",
    "交接",
    "待办",
    "待处理",
)
SEMANTIC_TERMS = (
    "we decided",
    "decided to",
    "decision",
    "important rule",
    "rule:",
    "must",
    "should",
    "do not",
    "never",
    "always",
    "source of truth",
    "write service",
    "blocked by",
    "决定",
    "规则",
    "必须",
    "不要",
    "不能",
    "事实源",
)
REVIEW_TAGS = {"needs-review", "requires-review", "unverified-boundary"}


@dataclass(frozen=True)
class ProactiveCandidate:
    candidate_id: str
    status: str
    created_at: str
    source_item_ids: list[str]
    type: str
    title: str
    summary: str
    body: str
    confidence: float
    tags: list[str]
    risk_flags: list[str]
    reason: str
    reviewer: str | None = None
    item_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def candidates_path(brain_dir: Path) -> Path:
    return Path(brain_dir) / "review" / "proactive-candidates.jsonl"


def list_candidates(brain_dir: Path) -> dict[str, Any]:
    items = _read_candidates(brain_dir)
    return {
        "total": len(items),
        "pending": sum(1 for item in items if item.status == "pending"),
        "approved": sum(1 for item in items if item.status == "approved"),
        "rejected": sum(1 for item in items if item.status == "rejected"),
        "items": [item.to_dict() for item in items],
    }


def generate_candidates(brain_dir: Path, *, limit: int = 50) -> dict[str, Any]:
    existing = {candidate.candidate_id for candidate in _read_candidates(brain_dir)}
    created: list[ProactiveCandidate] = []
    store = ItemsStore(Path(brain_dir) / "items")
    items = sorted(store.iter_all(), key=lambda pair: pair[0].created_at, reverse=True)[:limit]
    for item, body in items:
        candidate = _candidate_from_item(item, body)
        if candidate is None or candidate.candidate_id in existing:
            continue
        created.append(candidate)
        existing.add(candidate.candidate_id)
    if created:
        _append_candidates(brain_dir, created)
    return {"created": len(created), "candidates": [candidate.to_dict() for candidate in created]}


def generate_semantic_candidates(brain_dir: Path, *, limit: int = 50) -> dict[str, Any]:
    """Generate semantic review candidates from reusable language patterns.

    This is intentionally sidecar-only. It proposes pending candidates with
    provenance and risk flags; approval still writes through WriteService.
    """

    existing = {candidate.candidate_id for candidate in _read_candidates(brain_dir)}
    created: list[ProactiveCandidate] = []
    store = ItemsStore(Path(brain_dir) / "items")
    items = sorted(store.iter_all(), key=lambda pair: pair[0].created_at, reverse=True)[:limit]
    for item, body in items:
        candidate = _semantic_candidate_from_item(item, body)
        if candidate is None or candidate.candidate_id in existing:
            continue
        created.append(candidate)
        existing.add(candidate.candidate_id)
    if created:
        _append_candidates(brain_dir, created)
    return {"created": len(created), "candidates": [candidate.to_dict() for candidate in created]}


def approve_candidate(brain_dir: Path, candidate_id: str, *, reviewer: str = "web") -> dict[str, Any]:
    candidates = _read_candidates(brain_dir)
    candidate = _find_candidate(candidates, candidate_id)
    if candidate.status != "pending":
        return {
            "status": candidate.status,
            "candidate_id": candidate_id,
            "item_id": candidate.item_id,
        }
    now = datetime.now(timezone.utc).astimezone()
    item_id = make_item_id(candidate.title, when=now, label="proactive")
    item = MemoryItem(
        id=item_id,
        type=MemoryType(candidate.type),
        created_at=now,
        agent="proactive-memory",
        tags=sorted(
            {tag for tag in candidate.tags if tag not in REVIEW_TAGS}
            | {"proactive", "review-approved"}
        ),
        title=candidate.title,
        summary=candidate.summary,
        refs=Refs(mems=candidate.source_item_ids),
        confidence=min(0.75, max(0.1, candidate.confidence)),
        source=Source(kind="remember", extractor="mechanical"),
    )
    body = f"{candidate.body}\n\n---\nSource candidates: {', '.join(candidate.source_item_ids)}"
    result = WriteService.for_brain(Path(brain_dir)).write(item=item, body=body)
    if result.status == "blocked":
        return {"status": "blocked", "candidate_id": candidate_id, "write_result": asdict(result)}
    updated = replace(candidate, status="approved", reviewer=reviewer, item_id=item_id)
    _write_candidates(
        brain_dir,
        [updated if item.candidate_id == candidate_id else item for item in candidates],
    )
    return {
        "status": "approved",
        "candidate_id": candidate_id,
        "item_id": item_id,
        "write_result": asdict(result),
    }


def reject_candidate(brain_dir: Path, candidate_id: str, *, reviewer: str = "web") -> dict[str, Any]:
    candidates = _read_candidates(brain_dir)
    candidate = _find_candidate(candidates, candidate_id)
    updated = replace(candidate, status="rejected", reviewer=reviewer)
    _write_candidates(
        brain_dir,
        [updated if item.candidate_id == candidate_id else item for item in candidates],
    )
    return {"status": "rejected", "candidate_id": candidate_id}


def _candidate_from_item(item: MemoryItem, body: str) -> ProactiveCandidate | None:
    text = f"{item.title}\n{item.summary}\n{body}".lower()
    tags = {tag.lower() for tag in item.tags}
    item_type = str(item.type)
    high_signal = (
        item_type in {"signal", "handoff"}
        or bool(tags & REVIEW_TAGS)
        or any(term in text for term in SIGNAL_TERMS)
    )
    if not high_signal:
        return None
    candidate_type = item_type if item_type in MemoryType.__members__.values() else item_type
    if candidate_type not in {member.value for member in MemoryType}:
        candidate_type = "fact"
    title = item.title if item.title.lower().startswith("handoff") else f"Review: {item.title}"
    summary = item.summary[:240]
    body_preview = body.strip()[:1200] or item.summary
    cid = _candidate_id(item.id, title, summary)
    reason = "high-signal memory item requires review before durable reuse"
    risk_flags = ["mechanical", "needs_review"]
    if item.confidence < 0.5:
        risk_flags.append("low_confidence")
    return ProactiveCandidate(
        candidate_id=cid,
        status="pending",
        created_at=datetime.now(timezone.utc).isoformat(),
        source_item_ids=[item.id],
        type=str(candidate_type),
        title=title,
        summary=summary,
        body=body_preview,
        confidence=min(item.confidence, 0.45),
        tags=sorted({*item.tags, "proactive", "needs-review"}),
        risk_flags=risk_flags,
        reason=reason,
    )


def _semantic_candidate_from_item(item: MemoryItem, body: str) -> ProactiveCandidate | None:
    text = f"{item.title}\n{item.summary}\n{body}"
    evidence = _semantic_evidence_lines(text)
    if not evidence:
        return None
    candidate_type = _semantic_candidate_type(evidence)
    title = f"Semantic: {item.title}"
    summary = _semantic_summary(evidence)
    candidate_body = (
        "**情境**\n"
        f"Semantic candidate extracted from `{item.id}`.\n\n"
        "**做了什么**\n"
        + "\n".join(f"- {line}" for line in evidence[:6])
        + "\n\n**结果**\nPending human review before durable reuse."
    )
    cid = _candidate_id(item.id, title, summary)
    return ProactiveCandidate(
        candidate_id=cid,
        status="pending",
        created_at=datetime.now(timezone.utc).isoformat(),
        source_item_ids=[item.id],
        type=candidate_type,
        title=title,
        summary=summary,
        body=candidate_body,
        confidence=min(max(item.confidence * 0.75, 0.35), 0.65),
        tags=sorted({*item.tags, "proactive", "semantic", "needs-review"}),
        risk_flags=["semantic", "needs_review", "candidate_only"],
        reason="semantic patterns indicate reusable memory, but require review before write",
    )


def _semantic_evidence_lines(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        line = " ".join(raw.strip("-* \t").split())
        if not line:
            continue
        lowered = line.lower()
        if any(term in lowered for term in SEMANTIC_TERMS):
            lines.append(line)
    return _dedupe(lines)[:8]


def _semantic_candidate_type(lines: list[str]) -> str:
    lowered = "\n".join(lines).lower()
    if "decision" in lowered or "decided" in lowered or "决定" in lowered:
        return "decision"
    if "rule" in lowered or "must" in lowered or "should" in lowered or "必须" in lowered:
        return "policy"
    return "fact"


def _semantic_summary(lines: list[str]) -> str:
    if not lines:
        return "Semantic candidate requires review."
    summary = lines[0]
    if len(summary) > 240:
        summary = summary[:239].rstrip() + "…"
    return summary


def _dedupe(lines: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
    return out


def _candidate_id(source_id: str, title: str, summary: str) -> str:
    digest = hashlib.sha256(f"{source_id}\n{title}\n{summary}".encode("utf-8")).hexdigest()[:16]
    return f"cand-{digest}"


def _find_candidate(candidates: list[ProactiveCandidate], candidate_id: str) -> ProactiveCandidate:
    for candidate in candidates:
        if candidate.candidate_id == candidate_id:
            return candidate
    raise FileNotFoundError(f"candidate not found: {candidate_id}")


def _read_candidates(brain_dir: Path) -> list[ProactiveCandidate]:
    path = candidates_path(brain_dir)
    if not path.exists():
        return []
    out: list[ProactiveCandidate] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(ProactiveCandidate(**json.loads(line)))
            except (TypeError, json.JSONDecodeError):
                continue
    return out


def _append_candidates(brain_dir: Path, candidates: list[ProactiveCandidate]) -> None:
    path = candidates_path(brain_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for candidate in candidates:
            fh.write(json.dumps(candidate.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")


def _write_candidates(brain_dir: Path, candidates: list[ProactiveCandidate]) -> None:
    path = candidates_path(brain_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for candidate in candidates:
            fh.write(json.dumps(candidate.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")


__all__ = [
    "ProactiveCandidate",
    "approve_candidate",
    "candidates_path",
    "generate_candidates",
    "generate_semantic_candidates",
    "list_candidates",
    "reject_candidate",
]
