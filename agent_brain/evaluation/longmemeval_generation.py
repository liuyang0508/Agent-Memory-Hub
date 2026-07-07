"""Answer-generation harness for LongMemEval-S full QA runs."""

from __future__ import annotations

import json
import os
import re
import string
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_brain.evaluation.longmemeval_retrieval import _score_sample, _session_text


def build_generation_prompt(
    sample: dict[str, Any],
    *,
    ranked_session_ids: list[str],
    top_k_context: int = 5,
) -> str:
    """Build a compact LongMemEval answer-generation prompt from ranked sessions."""

    session_by_id = {
        str(session_id): sample.get("haystack_sessions", [])[index]
        for index, session_id in enumerate(sample.get("haystack_session_ids") or [])
        if index < len(sample.get("haystack_sessions") or [])
    }
    context_blocks = []
    for session_id in ranked_session_ids[: max(1, int(top_k_context))]:
        if session_id not in session_by_id:
            continue
        context_blocks.append(f"Session {session_id}:\n{_session_text(session_by_id[session_id])}")

    context = "\n\n".join(context_blocks).strip()
    question = str(sample.get("question") or "").strip()
    return (
        "Answer the question using only the provided memory sessions. "
        "If the sessions do not contain enough information, say that the answer is not available.\n\n"
        f"{context}\n\n"
        f"Question: {question}\n"
        "Answer:"
    ).strip()


def build_result_row(
    sample: dict[str, Any],
    *,
    generated_answer: str,
    ranked_session_ids: list[str],
) -> dict[str, Any]:
    """Build a MemoryData/LongMemEval-judge compatible result row."""

    question_id = str(sample.get("question_id") or "")
    question_type = str(sample.get("question_type") or "")
    return {
        "query": str(sample.get("question") or ""),
        "answer": sample.get("answer"),
        "output": str(generated_answer or "").strip(),
        "question_id": question_id,
        "qa_pair_id": question_id,
        "question_type": question_type,
        "ranked_session_ids": [str(session_id) for session_id in ranked_session_ids],
        "eval_metadata": {
            "dataset": "longmemeval_s_cleaned",
            "question_id": question_id,
            "qa_pair_id": question_id,
            "question_type": question_type,
        },
    }


def build_generation_payload(
    *,
    dataset_file: Path,
    rows: list[dict[str, Any]],
    total_available_cases: int,
    model: str,
    base_url: str | None,
    top_k_context: int,
    start_index: int = 0,
    max_concurrency: int = 1,
) -> dict[str, Any]:
    """Build the persisted generation result payload."""

    metrics = _aggregate_generation_metrics(rows)
    return {
        "status": "passed" if rows else "missing",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_file": str(dataset_file),
        "case_count": len(rows),
        "total_available_cases": total_available_cases,
        "start_index": int(start_index),
        "run_scope": "full-qa" if len(rows) == total_available_cases else "smoke",
        "model": model,
        "base_url": base_url or "",
        "top_k_context": int(top_k_context),
        "max_concurrency": int(max_concurrency),
        "dataset_config": {
            "dataset": "LongMemEval",
            "sub_dataset": "longmemeval_s",
        },
        "averaged_metrics": metrics,
        "data": rows,
    }


def run_longmemeval_generation(
    *,
    dataset_file: Path,
    output_file: Path,
    ranking_report: Path | None = None,
    start_index: int = 0,
    max_cases: int | None = None,
    top_k_context: int = 5,
    max_concurrency: int = 1,
    model: str,
    base_url: str | None = None,
    api_key_env: str = "OPENAI_API_KEY",
    client: Any | None = None,
) -> dict[str, Any]:
    """Run LongMemEval-S answer generation with an OpenAI-compatible chat endpoint."""

    dataset_file = Path(dataset_file)
    all_samples = _load_samples(dataset_file)
    total_available_cases = len(all_samples)
    start_index = max(0, int(start_index))
    samples = all_samples[start_index:]
    if max_cases is not None:
        samples = samples[: int(max_cases)]
    ranking_by_question_id = _load_ranking_by_question_id(ranking_report)

    if client is None:
        from openai import OpenAI

        api_key = os.environ.get(api_key_env) or os.environ.get("OPENAI_API_KEY") or "ollama"
        client_kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        client = OpenAI(**client_kwargs)

    rows = []
    max_concurrency = max(1, int(max_concurrency))

    def generate_one(sample: dict[str, Any]) -> dict[str, Any]:
        ranked_session_ids = _ranked_session_ids(sample, ranking_by_question_id=ranking_by_question_id)
        prompt = build_generation_prompt(
            sample,
            ranked_session_ids=ranked_session_ids,
            top_k_context=top_k_context,
        )
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        generated_answer = response.choices[0].message.content or ""
        return build_result_row(
            sample,
            generated_answer=generated_answer,
            ranked_session_ids=ranked_session_ids,
        )

    if max_concurrency == 1:
        for sample in samples:
            rows.append(generate_one(sample))
            payload = build_generation_payload(
                dataset_file=dataset_file,
                rows=rows,
                total_available_cases=total_available_cases,
                model=model,
                base_url=base_url,
                top_k_context=top_k_context,
                start_index=start_index,
                max_concurrency=max_concurrency,
            )
            _write_json(output_file, payload)
    else:
        completed: dict[int, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=max_concurrency) as executor:
            futures = {
                executor.submit(generate_one, sample): index
                for index, sample in enumerate(samples)
            }
            for future in as_completed(futures):
                completed[futures[future]] = future.result()
                rows = [completed[index] for index in sorted(completed)]
                payload = build_generation_payload(
                    dataset_file=dataset_file,
                    rows=rows,
                    total_available_cases=total_available_cases,
                    model=model,
                    base_url=base_url,
                    top_k_context=top_k_context,
                    start_index=start_index,
                    max_concurrency=max_concurrency,
                )
                _write_json(output_file, payload)

        rows = [completed[index] for index in sorted(completed)]

    payload = build_generation_payload(
        dataset_file=dataset_file,
        rows=rows,
        total_available_cases=total_available_cases,
        model=model,
        base_url=base_url,
        top_k_context=top_k_context,
        start_index=start_index,
        max_concurrency=max_concurrency,
    )
    _write_json(output_file, payload)
    return payload


def _load_samples(dataset_file: Path) -> list[dict[str, Any]]:
    loaded = json.loads(Path(dataset_file).read_text(encoding="utf-8"))
    if not isinstance(loaded, list):
        raise ValueError(f"Expected LongMemEval JSON list at {dataset_file}")
    return loaded


def _load_ranking_by_question_id(ranking_report: Path | None) -> dict[str, list[str]]:
    if not ranking_report:
        return {}
    try:
        payload = json.loads(Path(ranking_report).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rows = payload.get("cases") or []
    return {
        str(row.get("question_id")): [str(value) for value in row.get("ranked_session_ids") or []]
        for row in rows
        if isinstance(row, dict) and row.get("question_id")
    }


def _ranked_session_ids(
    sample: dict[str, Any],
    *,
    ranking_by_question_id: dict[str, list[str]],
) -> list[str]:
    question_id = str(sample.get("question_id") or "")
    ranked = ranking_by_question_id.get(question_id)
    if ranked:
        return ranked
    scored = _score_sample(sample, top_ks=(10,))
    return [str(session_id) for session_id in scored.get("ranked_session_ids") or []]


def _aggregate_generation_metrics(rows: list[dict[str, Any]]) -> dict[str, float]:
    if not rows:
        return {
            "exact_match": 0.0,
            "substring_exact_match": 0.0,
            "f1": 0.0,
            "rougeL_f1": 0.0,
            "rougeL_recall": 0.0,
        }
    exact_matches = []
    substring_exact_matches = []
    f1_scores = []
    rouge_l_f1_scores = []
    rouge_l_recall_scores = []
    for row in rows:
        prediction = str(row.get("output") or "")
        answers = _ground_truth_answers(row.get("answer"))
        exact_matches.append(_metric_max(_exact_match, prediction, answers))
        substring_exact_matches.append(_metric_max(_substring_exact_match, prediction, answers))
        f1_scores.append(_metric_max(_token_f1, prediction, answers))
        rouge_l_scores = [_rouge_l(prediction, answer) for answer in answers]
        rouge_l_f1_scores.append(max(score["f1"] for score in rouge_l_scores))
        rouge_l_recall_scores.append(max(score["recall"] for score in rouge_l_scores))
    return {
        "exact_match": 100.0 * sum(exact_matches) / len(exact_matches),
        "substring_exact_match": 100.0 * sum(substring_exact_matches) / len(substring_exact_matches),
        "f1": 100.0 * sum(f1_scores) / len(f1_scores),
        "rougeL_f1": 100.0 * sum(rouge_l_f1_scores) / len(rouge_l_f1_scores),
        "rougeL_recall": 100.0 * sum(rouge_l_recall_scores) / len(rouge_l_recall_scores),
    }


def _exact_match(prediction: str, answer: str) -> float:
    return 1.0 if _normalize_metric_text(prediction) == _normalize_metric_text(answer) else 0.0


def _token_f1(prediction: str, answer: str) -> float:
    normalized_prediction = _normalize_metric_text(prediction)
    normalized_answer = _normalize_metric_text(answer)
    special_answers = {"yes", "no", "noanswer"}
    if (
        normalized_prediction in special_answers or normalized_answer in special_answers
    ) and normalized_prediction != normalized_answer:
        return 0.0
    pred_tokens = _metric_tokens(prediction)
    answer_tokens = _metric_tokens(answer)
    if not pred_tokens and not answer_tokens:
        return 1.0
    if not pred_tokens or not answer_tokens:
        return 0.0
    common = Counter(pred_tokens) & Counter(answer_tokens)
    num_same = sum(common.values())
    if not num_same:
        return 0.0
    precision = num_same / len(pred_tokens)
    recall = num_same / len(answer_tokens)
    return 2 * precision * recall / (precision + recall)


def _substring_exact_match(prediction: str, answer: str) -> float:
    normalized_prediction = _normalize_metric_text(prediction)
    normalized_answer = _normalize_metric_text(answer)
    if not normalized_answer:
        return 1.0 if not normalized_prediction else 0.0
    return 1.0 if normalized_answer in normalized_prediction else 0.0


def _rouge_l(prediction: str, answer: str) -> dict[str, float]:
    pred_tokens = _metric_tokens(prediction)
    answer_tokens = _metric_tokens(answer)
    if not pred_tokens and not answer_tokens:
        return {"f1": 1.0, "recall": 1.0}
    if not pred_tokens or not answer_tokens:
        return {"f1": 0.0, "recall": 0.0}
    lcs = _lcs_length(pred_tokens, answer_tokens)
    if not lcs:
        return {"f1": 0.0, "recall": 0.0}
    precision = lcs / len(pred_tokens)
    recall = lcs / len(answer_tokens)
    return {"f1": 2 * precision * recall / (precision + recall), "recall": recall}


def _metric_tokens(value: str) -> list[str]:
    return _normalize_metric_text(value).split()


def _normalize_metric_text(value: object) -> str:
    text = "" if value is None else str(value).lower()
    text = "".join(char for char in text if char not in string.punctuation)
    text = re.sub(r"\b(a|an|the)\b", " ", text)
    return " ".join(text.split())


def _ground_truth_answers(value: object) -> list[str]:
    if value is None:
        return [""]
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        answers: list[str] = []
        for item in value:
            answers.extend(_ground_truth_answers(item))
        return answers or [""]
    return [str(value)]


def _metric_max(metric_function: Any, prediction: str, answers: list[str]) -> float:
    return max(metric_function(prediction, answer) for answer in answers)


def _lcs_length(left: list[str], right: list[str]) -> int:
    previous = [0] * (len(right) + 1)
    for left_token in left:
        current = [0]
        for index, right_token in enumerate(right, start=1):
            if left_token == right_token:
                current.append(previous[index - 1] + 1)
            else:
                current.append(max(previous[index], current[-1]))
        previous = current
    return previous[-1]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


__all__ = [
    "build_generation_payload",
    "build_generation_prompt",
    "build_result_row",
    "run_longmemeval_generation",
]
