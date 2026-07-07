"""Headroom-inspired deterministic context compression.

AMH does not need Headroom's proxy layer inside the core memory path, but its
ContentRouter/CCR shape maps well to AMH's context packs. This module keeps the
portable pieces: content detection, domain-specific compaction, metrics, and a
small CCR sidecar for inputs that do not already have a canonical detail URI.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_brain.memory.recall.retrieval_budget import estimate_tokens

CCR_MARKER_PREFIX = "<<amh-ccr:"
CCR_MARKER_SUFFIX = ">>"
DEFAULT_CCR_DIR = Path("runtime") / "compression-cache"

_SEARCH_RE = re.compile(r"^(.+?)(:|-)(\d+)(:|-)(.*)$")
_DIFF_HEADER_RE = re.compile(r"^(diff --git|---\s+a/|\+\+\+\s+b/|@@\s)")
_IMPORTANT_TERMS = (
    "error",
    "failed",
    "failure",
    "fatal",
    "critical",
    "exception",
    "traceback",
    "warning",
    "warn",
    "decision",
    "must",
    "should",
    "blocked",
    "security",
    "secret",
    "token",
    "api key",
    "refused",
)


@dataclass(frozen=True)
class AdaptiveCompressionResult:
    text: str
    strategy: str
    content_type: str
    original_chars: int
    compressed_chars: int
    original_tokens: int
    compressed_tokens: int
    reversible: bool
    detail_uri: str | None = None
    ccr_key: str | None = None
    ccr_marker: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def compression_ratio(self) -> float:
        if self.original_chars <= 0:
            return 1.0
        return round(self.compressed_chars / self.original_chars, 6)

    @property
    def tokens_saved(self) -> int:
        return max(0, self.original_tokens - self.compressed_tokens)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "text": self.text,
            "strategy": self.strategy,
            "content_type": self.content_type,
            "original_chars": self.original_chars,
            "compressed_chars": self.compressed_chars,
            "original_tokens": self.original_tokens,
            "compressed_tokens": self.compressed_tokens,
            "compression_ratio": self.compression_ratio,
            "tokens_saved": self.tokens_saved,
            "reversible": self.reversible,
            "detail_uri": self.detail_uri,
            "ccr_key": self.ccr_key,
            "ccr_marker": self.ccr_marker,
            "metrics": dict(self.metrics),
        }
        payload["metrics"].setdefault("tokens_saved", self.tokens_saved)
        payload["metrics"].setdefault("compression_ratio", self.compression_ratio)
        return payload


def compress_text(
    text: str,
    *,
    budget_chars: int = 1200,
    detail_uri: str | None = None,
    query: str | None = None,
    brain_dir: Path | None = None,
) -> AdaptiveCompressionResult:
    """Compress text with a deterministic content router.

    Compression is accepted only when it shortens the payload or when a CCR
    marker/detail URI is needed for reversibility.
    """

    original = text or ""
    content_type = detect_content_type(original)
    body_budget = max(1, budget_chars)
    if content_type == "search_results":
        compressed, strategy, metrics = _compress_search_results(original, body_budget, query=query)
    elif content_type == "build_log":
        compressed, strategy, metrics = _compress_log(original, body_budget)
    elif content_type == "git_diff":
        compressed, strategy, metrics = _compress_diff(original, body_budget, query=query)
    elif content_type == "json_array":
        compressed, strategy, metrics = _compress_json_array(original, body_budget, query=query)
    else:
        compressed, strategy, metrics = _compress_text_lines(original, body_budget)

    if len(compressed) >= len(original) and not detail_uri and brain_dir is None:
        compressed = _fit_chars(original, body_budget)
        strategy = "passthrough_truncate" if len(compressed) < len(original) else "passthrough"
        metrics.setdefault("accepted", strategy != "passthrough")

    ccr_key = None
    ccr_marker = None
    if detail_uri is None and brain_dir is not None and compressed != original:
        ccr_key = store_compressed_original(brain_dir, original, content_type=content_type, strategy=strategy)
        ccr_marker = marker_for(ccr_key)
        compressed = _append_marker_with_budget(compressed, ccr_marker, body_budget)

    result = _result(
        original=original,
        compressed=compressed,
        strategy=strategy,
        content_type=content_type,
        detail_uri=detail_uri,
        ccr_key=ccr_key,
        ccr_marker=ccr_marker,
        metrics=metrics,
    )
    return result


def detect_content_type(text: str) -> str:
    stripped = (text or "").strip()
    if not stripped:
        return "plain_text"
    try:
        payload = json.loads(stripped)
    except Exception:
        payload = None
    if isinstance(payload, list) and payload and all(isinstance(item, dict) for item in payload):
        return "json_array"
    lines = [line for line in stripped.splitlines() if line.strip()]
    if _looks_like_diff(lines):
        return "git_diff"
    if _looks_like_search_results(lines):
        return "search_results"
    if _looks_like_log(lines):
        return "build_log"
    return "plain_text"


def store_compressed_original(
    brain_dir: Path,
    text: str,
    *,
    content_type: str,
    strategy: str,
) -> str:
    key = hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]
    path = _cache_path(brain_dir, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "key": key,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "content_type": content_type,
        "strategy": strategy,
        "text": text,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return key


def retrieve_compressed_original(brain_dir: Path, key: str | None) -> str | None:
    if not key:
        return None
    path = _cache_path(brain_dir, key)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    text = payload.get("text")
    return text if isinstance(text, str) else None


def marker_for(key: str) -> str:
    return f"{CCR_MARKER_PREFIX}{key}{CCR_MARKER_SUFFIX}"


def _looks_like_search_results(lines: list[str]) -> bool:
    matches = sum(1 for line in lines[:50] if _SEARCH_RE.match(line))
    return matches >= 2 and matches / max(1, min(len(lines), 50)) >= 0.4


def _looks_like_log(lines: list[str]) -> bool:
    sample = "\n".join(lines[:120]).lower()
    markers = (
        "error",
        "failed",
        "fatal",
        "traceback",
        "exception",
        "npm err!",
        "cargo error",
        "short test summary",
        "warning",
    )
    return any(marker in sample for marker in markers) and len(lines) >= 4


def _looks_like_diff(lines: list[str]) -> bool:
    if any(_DIFF_HEADER_RE.match(line) for line in lines[:30]):
        return True
    changes = sum(1 for line in lines[:100] if line.startswith(("+", "-")) and not line.startswith(("+++", "---")))
    return changes >= 4 and any(line.startswith("@@") for line in lines[:100])


def _compress_search_results(text: str, budget_chars: int, *, query: str | None) -> tuple[str, str, dict[str, Any]]:
    query_terms = _terms(query or "")
    groups: dict[str, list[tuple[int, str, float]]] = defaultdict(list)
    parsed = 0
    for raw in text.splitlines():
        match = _SEARCH_RE.match(raw)
        if not match:
            continue
        path, _sep1, line_no, _sep2, content = match.groups()
        score = _score_line(content, query_terms, context="search")
        groups[path].append((int(line_no), content.strip(), score))
        parsed += 1
    ranked_files = sorted(groups.items(), key=lambda pair: _search_file_score(pair[1]), reverse=True)
    chunks: list[str] = []
    omitted_total = 0
    for file_path, rows in ranked_files:
        selected = _select_ranked_rows(rows, max_rows=2)
        omitted = max(0, len(rows) - len(selected))
        chunk_lines = [file_path]
        for line_no, content, _score in selected:
            chunk_lines.append(f"  {line_no}:{_fit_chars(content, 100)}")
        if omitted:
            chunk_lines.append(f"  [... omitted {omitted} matches]")
        chunk = "\n".join(chunk_lines)
        candidate = "\n\n".join([*chunks, chunk]) if chunks else chunk
        if len(candidate) <= budget_chars or not chunks:
            chunks.append(chunk)
            omitted_total += omitted
        else:
            omitted_total += len(rows)
    compressed = "\n\n".join(chunks)
    if omitted_total and "omitted" not in compressed:
        compressed = _append_line_with_budget(compressed, f"[... omitted {omitted_total} matches]", budget_chars)
    return _fit_chars(compressed, budget_chars), "search_topn", {
        "files_seen": len(groups),
        "matches_seen": parsed,
        "matches_omitted": omitted_total,
    }


def _search_file_score(rows: list[tuple[int, str, float]]) -> float:
    if not rows:
        return 0.0
    max_score = max(row[2] for row in rows)
    density_bonus = min(len(rows), 3) * 0.03
    return max_score + density_bonus


def _compress_log(text: str, budget_chars: int) -> tuple[str, str, dict[str, Any]]:
    lines = [line.rstrip() for line in text.splitlines()]
    selected: list[tuple[int, str, float]] = []
    in_traceback = False
    for idx, line in enumerate(lines):
        lower = line.lower()
        score = _score_line(line, (), context="log")
        if "traceback (most recent call last)" in lower:
            in_traceback = True
            score = max(score, 1.0)
        elif in_traceback:
            if line.startswith((" ", "\t")) or "error" in lower or "exception" in lower or lower.endswith("refused"):
                score = max(score, 0.9)
            elif line.strip():
                in_traceback = False
        if "short test summary" in lower or lower.startswith(("failed ", "error ")):
            score = max(score, 0.95)
        if score > 0:
            selected.append((idx, line, score))
    selected = _select_ranked_rows(selected, max_rows=18)
    output = "\n".join(line for _idx, line, _score in selected)
    omitted = max(0, len(lines) - len(selected))
    if omitted:
        output = _append_line_with_budget(output, f"[... omitted {omitted} log lines]", budget_chars)
    return _fit_chars(output, budget_chars), "log_errors", {
        "lines_seen": len(lines),
        "lines_kept": len(selected),
        "lines_omitted": omitted,
    }


def _compress_diff(text: str, budget_chars: int, *, query: str | None) -> tuple[str, str, dict[str, Any]]:
    query_terms = _terms(query or "")
    lines = [line.rstrip() for line in text.splitlines()]
    selected: list[tuple[int, str, float]] = []
    for idx, line in enumerate(lines):
        score = 0.0
        if _DIFF_HEADER_RE.match(line) or line.startswith(("+++", "---")):
            score = 0.8
        elif line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            score = max(0.7, _score_line(line, query_terms, context="diff"))
        elif query_terms and any(term in line.lower() for term in query_terms):
            score = 0.5
        if score > 0:
            selected.append((idx, line, score))
    selected = _select_ranked_rows(selected, max_rows=30)
    omitted = max(0, len(lines) - len(selected))
    output = "\n".join(line for _idx, line, _score in selected)
    if omitted:
        output = _append_line_with_budget(output, f"[... omitted {omitted} diff lines]", budget_chars)
    return _fit_chars(output, budget_chars), "diff_hunks", {
        "lines_seen": len(lines),
        "lines_kept": len(selected),
        "lines_omitted": omitted,
    }


def _compress_json_array(text: str, budget_chars: int, *, query: str | None) -> tuple[str, str, dict[str, Any]]:
    try:
        rows = json.loads(text)
    except Exception:
        return _compress_text_lines(text, budget_chars)
    query_terms = _terms(query or "")
    scored: list[tuple[int, dict[str, Any], float]] = []
    for idx, row in enumerate(rows):
        row_text = json.dumps(row, ensure_ascii=False, sort_keys=True)
        scored.append((idx, row, _score_line(row_text, query_terms, context="json")))
    selected = _select_ranked_rows(scored, max_rows=5)
    compact_rows = [row for _idx, row, _score in selected]
    payload = {
        "kept": compact_rows,
        "omitted": max(0, len(rows) - len(compact_rows)),
    }
    return _fit_chars(json.dumps(payload, ensure_ascii=False, sort_keys=True), budget_chars), "json_sample", {
        "items_seen": len(rows),
        "items_kept": len(compact_rows),
    }


def _compress_text_lines(text: str, budget_chars: int) -> tuple[str, str, dict[str, Any]]:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if not lines:
        return "", "plain_empty", {"lines_seen": 0}
    keep: list[str] = []
    keep.extend(lines[:3])
    keep.extend(line for line in lines if _score_line(line, (), context="text") > 0)
    keep.extend(lines[-2:])
    deduped = _dedupe(keep)
    omitted = max(0, len(lines) - len(deduped))
    output = "\n".join(deduped)
    if omitted:
        output = _append_line_with_budget(output, f"[... omitted {omitted} lines]", budget_chars)
    return _fit_chars(output, budget_chars), "important_lines", {
        "lines_seen": len(lines),
        "lines_kept": len(deduped),
        "lines_omitted": omitted,
    }


def _select_ranked_rows(rows: list[tuple[Any, Any, float]], *, max_rows: int) -> list[tuple[Any, Any, float]]:
    if len(rows) <= max_rows:
        return rows
    must_keep = [rows[0], rows[-1]]
    middle = sorted(rows[1:-1], key=lambda row: row[2], reverse=True)
    selected = [*must_keep, *middle[: max(0, max_rows - len(must_keep))]]
    return sorted(_dedupe_rows(selected), key=lambda row: row[0])


def _dedupe_rows(rows: list[tuple[Any, Any, float]]) -> list[tuple[Any, Any, float]]:
    out = []
    seen = set()
    for row in rows:
        key = (row[0], json.dumps(row[1], ensure_ascii=False, sort_keys=True) if isinstance(row[1], dict) else row[1])
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _score_line(line: str, query_terms: tuple[str, ...], *, context: str) -> float:
    lower = line.lower()
    score = 0.0
    if query_terms:
        overlap = sum(1 for term in query_terms if term in lower)
        score += min(0.6, overlap * 0.25)
    if any(term in lower for term in _IMPORTANT_TERMS):
        score += 0.5
    if context == "log" and re.match(r"^\s*(error|failed|fatal|warning|warn)\b", lower):
        score += 0.4
    if context == "search" and re.search(r"\b(def|class|test_|assert|return)\b", lower):
        score += 0.2
    return min(score, 1.0)


def _terms(query: str) -> tuple[str, ...]:
    return tuple(term for term in re.findall(r"[a-zA-Z0-9_]{3,}", query.lower()) if term)


def _append_marker_with_budget(text: str, marker: str, budget_chars: int) -> str:
    suffix = f"\n{marker}"
    head_budget = max(0, budget_chars - len(suffix))
    return f"{_fit_chars(text, head_budget)}{suffix}".strip()


def _append_line_with_budget(text: str, line: str, budget_chars: int) -> str:
    suffix = f"\n{line}" if text else line
    if len(text) + len(suffix) <= budget_chars:
        return f"{text}{suffix}" if text else line
    head_budget = max(0, budget_chars - len(suffix))
    return f"{_fit_chars(text, head_budget)}{suffix}".strip()


def _fit_chars(text: str, budget_chars: int) -> str:
    if len(text) <= budget_chars:
        return text
    if budget_chars <= 3:
        return text[:budget_chars]
    return text[: budget_chars - 3].rstrip() + "..."


def _dedupe(lines: list[str]) -> list[str]:
    out = []
    seen = set()
    for line in lines:
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
    return out


def _cache_path(brain_dir: Path, key: str) -> Path:
    return Path(brain_dir) / DEFAULT_CCR_DIR / f"{key}.json"


def _result(
    *,
    original: str,
    compressed: str,
    strategy: str,
    content_type: str,
    detail_uri: str | None,
    ccr_key: str | None,
    ccr_marker: str | None,
    metrics: dict[str, Any],
) -> AdaptiveCompressionResult:
    original_tokens = estimate_tokens(original)
    compressed_tokens = estimate_tokens(compressed)
    enriched_metrics = dict(metrics)
    enriched_metrics.update(
        {
            "tokens_saved": max(0, original_tokens - compressed_tokens),
            "compression_ratio": round(len(compressed) / max(1, len(original)), 6),
        }
    )
    return AdaptiveCompressionResult(
        text=compressed,
        strategy=strategy,
        content_type=content_type,
        original_chars=len(original),
        compressed_chars=len(compressed),
        original_tokens=original_tokens,
        compressed_tokens=compressed_tokens,
        reversible=bool(detail_uri or ccr_key),
        detail_uri=detail_uri,
        ccr_key=ccr_key,
        ccr_marker=ccr_marker,
        metrics=enriched_metrics,
    )


__all__ = [
    "AdaptiveCompressionResult",
    "CCR_MARKER_PREFIX",
    "DEFAULT_CCR_DIR",
    "compress_text",
    "detect_content_type",
    "marker_for",
    "retrieve_compressed_original",
    "store_compressed_original",
]
