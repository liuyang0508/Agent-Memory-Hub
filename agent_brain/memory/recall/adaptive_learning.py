"""Bounded, replay-gated ranking profiles learned from task outcomes."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_brain.memory.context.injection_cohorts import iter_injection_cohorts
from agent_brain.memory.governance.recall_events import (
    TaskOutcome,
    iter_task_outcome_feedback_applications,
    iter_task_outcomes,
)
from agent_brain.memory.recall.retrieval_types import RetrievedItem


PROFILE_RELATIVE_PATH = "runtime/adaptive-learning/profile.json"
PREVIOUS_PROFILE_RELATIVE_PATH = "runtime/adaptive-learning/profile.previous.json"
REPORT_RELATIVE_PATH = "runtime/adaptive-learning/last-run.json"
_ITEM_ID = re.compile(r"mem-[a-z0-9][a-z0-9-]{0,127}")
_MIN_MULTIPLIER = 0.75
_MAX_MULTIPLIER = 1.25


@dataclass(frozen=True)
class AdaptiveLearningReport:
    status: str
    evidence_cases: int
    profile_count: int
    weighted_items: int
    baseline_recall_at_1: float
    candidate_recall_at_1: float
    baseline_mrr: float
    candidate_mrr: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def profile_path(brain_dir: Path) -> Path:
    return Path(brain_dir) / PROFILE_RELATIVE_PATH


def previous_profile_path(brain_dir: Path) -> Path:
    return Path(brain_dir) / PREVIOUS_PROFILE_RELATIVE_PATH


def load_learning_profile(brain_dir: Path) -> dict[str, object]:
    return _load_profile_file(profile_path(brain_dir))


def refresh_learning_profile(brain_dir: Path) -> AdaptiveLearningReport:
    """Rebuild and activate a profile only when saved-trace replay does not regress."""

    brain = Path(brain_dir)
    current = load_learning_profile(brain)
    candidate = _build_candidate(brain)
    cases = _evaluation_cases(brain)
    baseline = _evaluate(cases, current)
    proposed = _evaluate(cases, candidate)
    profile_count, weighted_items = _profile_counts(candidate)

    if _profile_signature(candidate) == _profile_signature(current):
        status = "no_change"
    elif not cases:
        status = "insufficient_evidence"
    elif (
        proposed["recall_at_1"] + 1e-12 < baseline["recall_at_1"]
        or proposed["mrr"] + 1e-12 < baseline["mrr"]
    ):
        status = "rejected_regression"
    else:
        active = profile_path(brain)
        previous = previous_profile_path(brain)
        if active.exists() and current:
            previous.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(active, previous)
            os.chmod(previous, 0o600)
        candidate["evaluation"] = {
            "evidence_cases": len(cases),
            "baseline": baseline,
            "candidate": proposed,
        }
        _atomic_write_json(active, candidate)
        status = "activated"

    report = AdaptiveLearningReport(
        status=status,
        evidence_cases=len(cases),
        profile_count=profile_count,
        weighted_items=weighted_items,
        baseline_recall_at_1=baseline["recall_at_1"],
        candidate_recall_at_1=proposed["recall_at_1"],
        baseline_mrr=baseline["mrr"],
        candidate_mrr=proposed["mrr"],
    )
    _atomic_write_json(brain / REPORT_RELATIVE_PATH, report.to_dict())
    return report


def rollback_learning_profile(brain_dir: Path) -> bool:
    """Restore the last active profile snapshot, if one exists."""

    brain = Path(brain_dir)
    previous = previous_profile_path(brain)
    if not previous.exists():
        return False
    _atomic_write_json(profile_path(brain), _load_profile_file(previous))
    previous.unlink()
    return True


def apply_learning_profile_weight(
    candidates: list[RetrievedItem],
    *,
    brain_dir: Path,
    adapter: str,
    project: str | None,
    profile: dict[str, object] | None = None,
) -> list[RetrievedItem]:
    """Apply the exact adapter/project profile, falling back to adapter-global."""

    if not candidates:
        return candidates
    data = profile if profile is not None else load_learning_profile(brain_dir)
    exact = _weights_for(data, adapter=adapter, project=project)
    fallback = _weights_for(data, adapter=adapter, project=None)
    if not exact and not fallback:
        return candidates
    weighted = [
        replace(
            candidate,
            score=candidate.score * exact.get(
                candidate.id,
                fallback.get(candidate.id, 1.0),
            ),
        )
        for candidate in candidates
    ]
    weighted.sort(key=lambda candidate: candidate.score, reverse=True)
    return weighted


def _build_candidate(brain_dir: Path) -> dict[str, object]:
    outcomes = {outcome.outcome_id: outcome for outcome in iter_task_outcomes(brain_dir)}
    stats: dict[tuple[str, str | None], dict[str, list[int]]] = {}
    evidence_counts: dict[tuple[str, str | None], int] = {}
    for application in iter_task_outcome_feedback_applications(brain_dir):
        if not application.applied:
            continue
        outcome = outcomes.get(application.outcome_id)
        if outcome is None:
            continue
        adapter = _adapter(outcome.adapter)
        project = _project(outcome.project)
        keys = {(adapter, None), (adapter, project)}
        for key in keys:
            bucket = stats.setdefault(key, {})
            evidence_counts[key] = evidence_counts.get(key, 0) + 1
            for item_id in application.adopted_ids:
                if _valid_item_id(item_id):
                    bucket.setdefault(item_id, [0, 0])[0] += 1
            for item_id in application.rejected_ids:
                if _valid_item_id(item_id):
                    bucket.setdefault(item_id, [0, 0])[1] += 1

    profiles: list[dict[str, object]] = []
    for adapter, project in sorted(stats, key=lambda key: (key[0], key[1] or "")):
        weights = {
            item_id: _multiplier(adopted=counts[0], rejected=counts[1])
            for item_id, counts in sorted(stats[(adapter, project)].items())
        }
        weights = {item_id: value for item_id, value in weights.items() if value != 1.0}
        if not weights:
            continue
        profiles.append(
            {
                "adapter": adapter,
                "project": project,
                "evidence_count": evidence_counts[(adapter, project)],
                "weights": weights,
            }
        )
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "profiles": profiles,
    }


def _evaluation_cases(
    brain_dir: Path,
) -> list[tuple[TaskOutcome, dict[str, float], tuple[str, ...]]]:
    outcomes = {outcome.outcome_id: outcome for outcome in iter_task_outcomes(brain_dir)}
    cohorts = {
        cohort.cohort_id: cohort
        for cohort in iter_injection_cohorts(brain_dir)
    }
    cases: list[tuple[TaskOutcome, dict[str, float], tuple[str, ...]]] = []
    for application in iter_task_outcome_feedback_applications(brain_dir):
        if not application.applied or not application.adopted_ids:
            continue
        outcome = outcomes.get(application.outcome_id)
        if outcome is None or not outcome.cohort_id:
            continue
        cohort = cohorts.get(outcome.cohort_id)
        if cohort is None:
            continue
        scores = _cohort_scores(cohort.item_ids, cohort.pack_metrics)
        if not scores or any(item_id not in scores for item_id in application.adopted_ids):
            continue
        cases.append((outcome, scores, application.adopted_ids))
    return cases


def _evaluate(
    cases: list[tuple[TaskOutcome, dict[str, float], tuple[str, ...]]],
    profile: dict[str, object],
) -> dict[str, float]:
    if not cases:
        return {"recall_at_1": 0.0, "mrr": 0.0}
    recall_at_1 = 0
    reciprocal_rank = 0.0
    for outcome, scores, adopted_ids in cases:
        weights = _weights_for(
            profile,
            adapter=outcome.adapter,
            project=outcome.project,
        )
        fallback = _weights_for(profile, adapter=outcome.adapter, project=None)
        ranking = sorted(
            scores,
            key=lambda item_id: (
                scores[item_id] * weights.get(item_id, fallback.get(item_id, 1.0)),
                item_id,
            ),
            reverse=True,
        )
        expected = set(adopted_ids)
        rank = next(
            (position for position, item_id in enumerate(ranking, start=1) if item_id in expected),
            None,
        )
        if rank == 1:
            recall_at_1 += 1
        if rank is not None:
            reciprocal_rank += 1.0 / rank
    count = len(cases)
    return {
        "recall_at_1": round(recall_at_1 / count, 6),
        "mrr": round(reciprocal_rank / count, 6),
    }


def _cohort_scores(
    item_ids: tuple[str, ...],
    pack_metrics: dict[str, object] | None,
) -> dict[str, float]:
    if not isinstance(pack_metrics, dict):
        return {}
    raw = pack_metrics.get("retrieval_trace")
    rows: Iterable[tuple[object, object]]
    if isinstance(raw, list) and len(raw) == len(item_ids):
        rows = zip(item_ids, raw)
    elif isinstance(raw, dict):
        rows = raw.items()
    else:
        return {}
    result: dict[str, float] = {}
    for item_id, trace in rows:
        if not _valid_item_id(str(item_id)) or not isinstance(trace, dict):
            continue
        score = _pre_learning_score(trace)
        if (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(score)
        ):
            continue
        result[str(item_id)] = score
    return result


def _pre_learning_score(trace: dict[str, object]) -> object:
    stages = trace.get("stages")
    if isinstance(stages, list):
        for stage in stages:
            if (
                isinstance(stage, dict)
                and stage.get("name") == "adaptive_learning"
                and stage.get("before_score") is not None
            ):
                return stage["before_score"]
    return trace.get("final_score")


def _weights_for(
    profile: dict[str, object],
    *,
    adapter: str,
    project: str | None,
) -> dict[str, float]:
    normalized_adapter = _adapter(adapter)
    normalized_project = _project(project)
    rows = profile.get("profiles")
    if not isinstance(rows, list):
        return {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        if _adapter(row.get("adapter")) != normalized_adapter:
            continue
        if _project(row.get("project")) != normalized_project:
            continue
        weights = row.get("weights")
        if not isinstance(weights, dict):
            return {}
        return {
            str(item_id): float(value)
            for item_id, value in weights.items()
            if _valid_item_id(str(item_id))
            and type(value) in {int, float}
            and math.isfinite(float(value))
            and _MIN_MULTIPLIER <= float(value) <= _MAX_MULTIPLIER
        }
    return {}


def _load_profile_file(path: Path) -> dict[str, object]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        return {}
    return data


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _multiplier(*, adopted: int, rejected: int) -> float:
    value = 1.0 + max(0, adopted) * 0.03 - max(0, rejected) * 0.08
    return round(min(_MAX_MULTIPLIER, max(_MIN_MULTIPLIER, value)), 3)


def _profile_signature(profile: dict[str, object]) -> object:
    return profile.get("profiles") if isinstance(profile, dict) else None


def _profile_counts(profile: dict[str, object]) -> tuple[int, int]:
    rows = profile.get("profiles")
    if not isinstance(rows, list):
        return 0, 0
    weighted = sum(
        len(row.get("weights", {}))
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("weights"), dict)
    )
    return len(rows), weighted


def _adapter(value: object) -> str:
    text = str(value or "unknown").strip().lower()
    return text if re.fullmatch(r"[a-z0-9_.-]{1,32}", text) else "unknown"


def _project(value: object) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).strip().split())
    return text[:80] or None


def _valid_item_id(value: str) -> bool:
    return bool(_ITEM_ID.fullmatch(value))


__all__ = [
    "AdaptiveLearningReport",
    "PROFILE_RELATIVE_PATH",
    "apply_learning_profile_weight",
    "load_learning_profile",
    "profile_path",
    "refresh_learning_profile",
    "rollback_learning_profile",
]
