"""Retrieval smoke harnesses for LongMemEval-S datasets."""

from __future__ import annotations

import json
import re
import tempfile
import uuid
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.memory.recall.embedding_text import embedding_text_for_item
from agent_brain.memory.recall.retrieval import Retriever, SearchFilter
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex


TOKEN_RE = re.compile(r"[A-Za-z0-9_\u4e00-\u9fff]+")


def run_longmemeval_retrieval_smoke(
    *,
    dataset_file: Path = Path(".cache/external/LongMemEval/data/longmemeval_s_cleaned.json"),
    max_cases: int = 5,
    top_ks: tuple[int, ...] = (5, 10),
) -> dict[str, Any]:
    dataset_file = Path(dataset_file)
    samples, total_available_cases = _load_samples(dataset_file, max_cases=max_cases)
    cases = [_score_sample(sample, top_ks=top_ks) for sample in samples]
    run_scope = _run_scope(case_count=len(cases), total_available_cases=total_available_cases)
    return {
        "status": "passed",
        "dataset_file": str(dataset_file),
        "case_count": len(cases),
        "total_available_cases": total_available_cases,
        "max_cases": max_cases,
        "run_scope": run_scope,
        "mode": "retrieval-only lexical R@K full"
        if run_scope == "full-rk"
        else "retrieval-only lexical smoke",
        "metrics": _aggregate_metrics(cases, top_ks=top_ks),
        "cases": cases,
    }


def run_longmemeval_amh_ranking(
    *,
    dataset_file: Path = Path(".cache/external/LongMemEval/data/longmemeval_s_cleaned.json"),
    max_cases: int = 5,
    top_ks: tuple[int, ...] = (5, 10),
    workspace_dir: Path | None = None,
) -> dict[str, Any]:
    """Materialize LongMemEval sessions as AMH items and rank with Retriever."""

    dataset_file = Path(dataset_file)
    samples, total_available_cases = _load_samples(dataset_file, max_cases=max_cases)
    run_scope = _run_scope(case_count=len(samples), total_available_cases=total_available_cases)
    workspace_context = (
        nullcontext(Path(workspace_dir))
        if workspace_dir is not None
        else tempfile.TemporaryDirectory(prefix="longmemeval-amh-ranking-")
    )
    with workspace_context as raw_workspace:
        workspace = Path(raw_workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        run_workspace = workspace / f"run-{run_id}-{uuid.uuid4().hex[:8]}"
        run_workspace.mkdir(parents=True, exist_ok=False)
        cases = [
            _score_sample_with_amh(
                sample,
                top_ks=top_ks,
                workspace=run_workspace / f"case-{case_index:04d}",
                case_index=case_index,
            )
            for case_index, sample in enumerate(samples, start=1)
        ]
        return {
            "status": "passed",
            "dataset_file": str(dataset_file),
            "workspace_dir": str(workspace),
            "run_workspace_dir": str(run_workspace),
            "case_count": len(cases),
            "total_available_cases": total_available_cases,
            "max_cases": max_cases,
            "run_scope": run_scope,
            "mode": "amh-ranking",
            "ranking_backend": "AMH HubIndex + Retriever BM25/RRF pipeline",
            "retriever_config": {
                "bm25_weight": 1.0,
                "vector_weight": 0.0,
                "query_expansion": True,
                "apply_decay": False,
                "record_access": False,
            },
            "metrics": _aggregate_metrics(cases, top_ks=top_ks),
            "cases": cases,
        }


def _load_samples(dataset_file: Path, *, max_cases: int) -> tuple[list[dict[str, Any]], int]:
    with dataset_file.open(encoding="utf-8") as file:
        loaded = json.load(file)
    if not isinstance(loaded, list):
        raise ValueError(f"Expected LongMemEval JSON list at {dataset_file}")
    return loaded[:max_cases], len(loaded)


def _run_scope(*, case_count: int, total_available_cases: int) -> str:
    return "full-rk" if total_available_cases > 0 and case_count >= total_available_cases else "smoke"


def _score_sample(sample: dict[str, Any], *, top_ks: tuple[int, ...]) -> dict[str, Any]:
    question = str(sample.get("question") or "")
    query_tokens = _tokenize(question)
    session_ids = [str(value) for value in sample.get("haystack_session_ids") or []]
    sessions = sample.get("haystack_sessions") or []
    answer_session_ids = [str(value) for value in sample.get("answer_session_ids") or []]
    answer_set = set(answer_session_ids)

    scored_sessions = []
    for index, session_id in enumerate(session_ids):
        session = sessions[index] if index < len(sessions) else ""
        session_text = _session_text(session)
        score = _lexical_score(query_tokens, session_text)
        scored_sessions.append((session_id, score, index))

    ranked = sorted(scored_sessions, key=lambda item: (-item[1], item[2], item[0]))
    ranked_session_ids = [session_id for session_id, _score, _index in ranked]
    reciprocal_rank = 0.0
    for rank, session_id in enumerate(ranked_session_ids, start=1):
        if session_id in answer_set:
            reciprocal_rank = 1.0 / rank
            break

    recalls = {
        f"recall_at_{top_k}": 1.0
        if answer_set.intersection(ranked_session_ids[:top_k])
        else 0.0
        for top_k in top_ks
    }
    return {
        "question_id": str(sample.get("question_id") or ""),
        "question": question,
        "answer": sample.get("answer"),
        "answer_session_ids": answer_session_ids,
        "ranked_session_ids": ranked_session_ids[: max(top_ks)],
        "reciprocal_rank": reciprocal_rank,
        **recalls,
    }


def _score_sample_with_amh(
    sample: dict[str, Any],
    *,
    top_ks: tuple[int, ...],
    workspace: Path,
    case_index: int,
) -> dict[str, Any]:
    question = str(sample.get("question") or "")
    question_id = str(sample.get("question_id") or f"case-{case_index:04d}")
    session_ids = [str(value) for value in sample.get("haystack_session_ids") or []]
    sessions = sample.get("haystack_sessions") or []
    answer_session_ids = [str(value) for value in sample.get("answer_session_ids") or []]
    answer_set = set(answer_session_ids)

    workspace.mkdir(parents=True, exist_ok=True)
    items_store = ItemsStore(workspace / "items")
    embedder = HashingEmbedder()
    index = HubIndex(workspace / "index.db", embedding_dim=embedder.dim)
    item_to_session: dict[str, str] = {}
    try:
        for session_index, session_id in enumerate(session_ids, start=1):
            session = sessions[session_index - 1] if session_index - 1 < len(sessions) else ""
            session_text = _session_text(session)
            item = _longmemeval_session_item(
                question_id=question_id,
                session_id=session_id,
                session_text=session_text,
                case_index=case_index,
                session_index=session_index,
            )
            items_store.write(item, session_text)
            index.upsert(item, session_text, embedder.embed(embedding_text_for_item(item)))
            item_to_session[item.id] = session_id

        retriever = Retriever(
            index=index,
            embedder=embedder,
            bm25_weight=1.0,
            vector_weight=0.0,
            query_expansion=True,
            apply_decay=False,
            record_access=False,
        )
        results = retriever.search(
            question,
            top_k=max(top_ks),
            filters=SearchFilter(
                type="fact",
                project="longmemeval",
                tags=[f"question-{question_id}"],
                include_superseded=True,
            ),
        )
        ranked_item_ids = [result.id for result in results]
        ranked_session_ids = [item_to_session[item_id] for item_id in ranked_item_ids]
    finally:
        index.close()

    reciprocal_rank = 0.0
    for rank, session_id in enumerate(ranked_session_ids, start=1):
        if session_id in answer_set:
            reciprocal_rank = 1.0 / rank
            break

    recalls = {
        f"recall_at_{top_k}": 1.0
        if answer_set.intersection(ranked_session_ids[:top_k])
        else 0.0
        for top_k in top_ks
    }
    return {
        "question_id": question_id,
        "question": question,
        "answer": sample.get("answer"),
        "answer_session_ids": answer_session_ids,
        "ranked_item_ids": ranked_item_ids,
        "ranked_session_ids": ranked_session_ids,
        "reciprocal_rank": reciprocal_rank,
        **recalls,
    }


def _longmemeval_session_item(
    *,
    question_id: str,
    session_id: str,
    session_text: str,
    case_index: int,
    session_index: int,
) -> MemoryItem:
    item_id = f"mem-20260701-000000-longmemeval-{case_index:04d}-{session_index:04d}"
    locator = f"LongMemEval session {session_id}: {_compact(session_text, limit=320)}"
    overview = session_text.strip()
    return MemoryItem.model_validate(
        {
            "id": item_id,
            "type": "fact",
            "created_at": datetime(2026, 7, 1, tzinfo=timezone.utc).isoformat(),
            "agent": "benchmark",
            "session": question_id,
            "project": "longmemeval",
            "tags": ["longmemeval", "session", f"question-{question_id}"],
            "sensitivity": "public",
            "title": f"LongMemEval session {session_id}",
            "summary": locator,
            "refs": {"urls": ["https://huggingface.co/datasets/xiaowu0162/LongMemEval"]},
            "confidence": 0.9,
            "abstraction": "L0",
            "maturity": "raw",
            "context_views": {
                "locator": locator,
                "overview": overview,
                "detail_uri": f"memory://longmemeval/{question_id}/{session_id}",
            },
            "source": {"kind": "benchmark", "extractor": "longmemeval"},
        }
    )


def _compact(text: str, *, limit: int) -> str:
    one_line = " ".join(text.split())
    if len(one_line) <= limit:
        return one_line
    return one_line[: limit - 3].rstrip() + "..."


def _aggregate_metrics(cases: list[dict[str, Any]], *, top_ks: tuple[int, ...]) -> dict[str, float]:
    if not cases:
        return {**{f"recall_at_{top_k}": 0.0 for top_k in top_ks}, "mrr": 0.0}
    metrics = {
        f"recall_at_{top_k}": sum(case[f"recall_at_{top_k}"] for case in cases) / len(cases)
        for top_k in top_ks
    }
    metrics["mrr"] = sum(case["reciprocal_rank"] for case in cases) / len(cases)
    return metrics


def _lexical_score(query_tokens: set[str], session_text: str) -> float:
    if not query_tokens:
        return 0.0
    session_tokens = _tokenize(session_text)
    overlap = query_tokens.intersection(session_tokens)
    return float(len(overlap))


def _tokenize(text: str) -> set[str]:
    return {token.lower() for token in TOKEN_RE.findall(text)}


def _session_text(session: Any) -> str:
    if isinstance(session, str):
        return session
    if isinstance(session, dict):
        return _message_text(session)
    if isinstance(session, Iterable):
        return "\n".join(_message_text(message) for message in session)
    return str(session)


def _message_text(message: Any) -> str:
    if isinstance(message, str):
        return message
    if isinstance(message, dict):
        role = str(message.get("role") or "")
        content = str(message.get("content") or message.get("text") or "")
        return f"{role}: {content}".strip()
    return str(message)


__all__ = ["run_longmemeval_amh_ranking", "run_longmemeval_retrieval_smoke"]
