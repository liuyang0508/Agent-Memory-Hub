"""Deterministic clustering for recall gap sidecar records."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from agent_brain.memory.governance.recall_events import GapRecord, iter_gap_records


@dataclass(frozen=True)
class GapClusterProfile:
    root_cause: str
    trigger_terms: tuple[str, ...]
    exclusions: tuple[str, ...]
    risk_level: str
    suggested_owner: str

    def to_dict(self) -> dict[str, object]:
        return {
            "root_cause": self.root_cause,
            "trigger_terms": list(self.trigger_terms),
            "exclusions": list(self.exclusions),
            "risk_level": self.risk_level,
            "suggested_owner": self.suggested_owner,
        }


@dataclass(frozen=True)
class GapCluster:
    cluster_id: str
    title: str
    size: int
    labels: tuple[str, ...]
    gap_ids: tuple[str, ...]
    sample_query_digests: tuple[str, ...]
    reason_counts: dict[str, int]
    profile: GapClusterProfile

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["labels"] = list(self.labels)
        data["gap_ids"] = list(self.gap_ids)
        data["sample_query_digests"] = list(self.sample_query_digests)
        data["profile"] = self.profile.to_dict()
        return data


@dataclass(frozen=True)
class GapClusterReport:
    total_gaps: int
    cluster_count: int
    clusters: tuple[GapCluster, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "total_gaps": self.total_gaps,
            "cluster_count": self.cluster_count,
            "clusters": [cluster.to_dict() for cluster in self.clusters],
        }


@dataclass(frozen=True)
class GapReplayCase:
    gap_id: str
    cluster_id: str
    query_digest: str
    query_shape: str
    reason: str
    evidence: tuple[str, ...]
    adapter: str
    session_digest: str | None
    scope_digest: str | None
    expected_root_cause: str
    expected_owner: str
    expected_risk: str

    @property
    def query(self) -> str:
        return self.query_digest

    @property
    def normalized_query(self) -> str:
        return self.query_shape

    @property
    def session_id(self) -> str | None:
        return self.session_digest

    @property
    def cwd(self) -> str | None:
        return self.scope_digest

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["evidence"] = list(self.evidence)
        return data


@dataclass(frozen=True)
class GapReplayCohort:
    root_cause: str
    matched_gap_count: int
    deduped_query_count: int
    cases: tuple[GapReplayCase, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "root_cause": self.root_cause,
            "matched_gap_count": self.matched_gap_count,
            "deduped_query_count": self.deduped_query_count,
            "cases": [case.to_dict() for case in self.cases],
        }


@dataclass(frozen=True)
class _GapFeatures:
    gap: GapRecord
    normalized_text: str
    labels: frozenset[str]
    tokens: frozenset[str]


_RULE_LABELS: dict[str, tuple[str, ...]] = {
    "browser": ("browser", "浏览器"),
    "permission": ("permission denied", "not permitted", "operation not permitted", "permission", "权限", "受限"),
    "stale-state": ("stale", "outdated", "历史记忆", "已修复", "已经修复", "already fixed", "fixed"),
    "quota": ("quota", "额度", "限额", "424"),
    "model": ("model", "模型", "model-invalid"),
    "auth": ("auth", "login", "token", "登录", "鉴权", "认证"),
    "install": ("install", "安装", "setup"),
    "scope": ("cwd", "branch", "adapter", "repo", "scope", "作用域"),
    "query-gate": ("query_not_injectable", "query_signal", "too_weak"),
    "context-rejected": (
        "all_candidates_rejected",
        "only_rejected",
        "partial_candidates_rejected",
        "missing_source",
        "negative_feedback",
        "superseded",
    ),
}
_NOISE_TOKENS = {
    "ascii",
    "the",
    "and",
    "but",
    "still",
    "says",
    "with",
    "from",
    "that",
    "this",
    "memory",
    "recall",
    "cjk",
    "intent",
    "labels",
    "lang",
    "length",
    "long",
    "medium",
    "mixed",
    "none",
    "other",
    "question",
    "short",
    "statement",
}


def build_gap_cluster_report(
    brain_dir: Path,
    *,
    top_n: int | None = None,
    min_size: int = 1,
) -> GapClusterReport:
    gaps = list(iter_gap_records(brain_dir))
    features = [_features(gap) for gap in gaps]
    parent = {i: i for i in range(len(features))}

    for i, left in enumerate(features):
        for j in range(i + 1, len(features)):
            if _same_cluster(left, features[j]):
                _union(parent, i, j)

    grouped: dict[int, list[_GapFeatures]] = {}
    for i, feature in enumerate(features):
        grouped.setdefault(_find(parent, i), []).append(feature)

    clusters = [
        _build_cluster(group)
        for group in grouped.values()
        if len(group) >= min_size
    ]
    clusters.sort(key=lambda cluster: (-cluster.size, cluster.title, cluster.cluster_id))
    if top_n is not None:
        clusters = clusters[: max(0, top_n)]
    return GapClusterReport(
        total_gaps=len(gaps),
        cluster_count=len(clusters),
        clusters=tuple(clusters),
    )


def build_gap_replay_cohort(
    brain_dir: Path,
    *,
    root_cause: str,
    limit: int | None = None,
) -> GapReplayCohort:
    """Export deduped gap digests for replay triage by operational root cause."""
    report = build_gap_cluster_report(brain_dir)
    gap_by_id = {gap.gap_id: gap for gap in iter_gap_records(brain_dir)}
    matched_gap_count = 0
    seen_queries: set[str] = set()
    cases: list[GapReplayCase] = []

    for cluster in report.clusters:
        if cluster.profile.root_cause != root_cause:
            continue
        matched_gap_count += len(cluster.gap_ids)
        for gap_id in cluster.gap_ids:
            gap = gap_by_id.get(gap_id)
            if gap is None:
                continue
            query_key = gap.query_digest
            if query_key in seen_queries:
                continue
            seen_queries.add(query_key)
            if limit is not None and len(cases) >= max(0, limit):
                continue
            cases.append(
                GapReplayCase(
                    gap_id=gap.gap_id,
                    cluster_id=cluster.cluster_id,
                    query_digest=gap.query_digest,
                    query_shape=gap.query_shape,
                    reason=gap.reason,
                    evidence=gap.evidence,
                    adapter=gap.adapter,
                    session_digest=gap.session_digest,
                    scope_digest=gap.scope_digest,
                    expected_root_cause=cluster.profile.root_cause,
                    expected_owner=cluster.profile.suggested_owner,
                    expected_risk=cluster.profile.risk_level,
                )
            )

    return GapReplayCohort(
        root_cause=root_cause,
        matched_gap_count=matched_gap_count,
        deduped_query_count=len(seen_queries),
        cases=tuple(cases),
    )


def _features(gap: GapRecord) -> _GapFeatures:
    text = _normalize_text(" ".join([
        gap.query_shape,
        gap.reason,
        " ".join(gap.evidence),
    ]))
    labels = _labels(text)
    return _GapFeatures(
        gap=gap,
        normalized_text=text,
        labels=frozenset(labels),
        tokens=frozenset(_tokens(text)),
    )


def _same_cluster(left: _GapFeatures, right: _GapFeatures) -> bool:
    if left.labels and right.labels and left.labels & right.labels:
        return True
    if not left.tokens or not right.tokens:
        return False
    overlap = len(left.tokens & right.tokens)
    union = len(left.tokens | right.tokens)
    return union > 0 and overlap / union >= 0.45


def _build_cluster(group: list[_GapFeatures]) -> GapCluster:
    labels = tuple(sorted(set().union(*(feature.labels for feature in group))))
    gap_ids = tuple(feature.gap.gap_id for feature in group)
    title = _cluster_title(labels, group)
    digest = hashlib.sha256("|".join(sorted(gap_ids)).encode("utf-8")).hexdigest()[:10]
    reason_counts = dict(Counter(feature.gap.reason for feature in group))
    return GapCluster(
        cluster_id=f"gapc-{digest}",
        title=title,
        size=len(group),
        labels=labels,
        gap_ids=gap_ids,
        sample_query_digests=tuple(feature.gap.query_digest for feature in group[:3]),
        reason_counts=reason_counts,
        profile=_build_profile(labels, reason_counts, group),
    )


def _build_profile(
    labels: tuple[str, ...],
    reason_counts: dict[str, int],
    group: list[_GapFeatures],
) -> GapClusterProfile:
    label_set = set(labels)
    root_cause = _root_cause(label_set, reason_counts)
    return GapClusterProfile(
        root_cause=root_cause,
        trigger_terms=_trigger_terms(labels, group),
        exclusions=_exclusions(root_cause),
        risk_level=_risk_level(label_set, reason_counts),
        suggested_owner=_suggested_owner(root_cause, label_set, reason_counts),
    )


def _root_cause(labels: set[str], reason_counts: dict[str, int]) -> str:
    if "query-gate" in labels or "query_not_injectable" in reason_counts:
        return "query_gate_underqualified"
    if (
        "context-rejected" in labels
        or "stale-state" in labels
        or {"only_rejected", "all_candidates_rejected", "partial_candidates_rejected"}
        & set(reason_counts)
    ):
        return "stale_or_rejected_context"
    if "empty_recall" in reason_counts:
        return "knowledge_missing_or_not_retrieved"
    if labels & {"auth", "install", "model", "quota"}:
        return "domain_knowledge_gap"
    return "unclassified_recall_gap"


def _trigger_terms(labels: tuple[str, ...], group: list[_GapFeatures]) -> tuple[str, ...]:
    common = Counter(token for feature in group for token in feature.tokens)
    terms = list(labels[:6])
    for term, _count in common.most_common(6):
        if term not in terms:
            terms.append(term)
    return tuple(terms[:6])


def _exclusions(root_cause: str) -> tuple[str, ...]:
    if root_cause == "query_gate_underqualified":
        return (
            "Do not treat query gate skips as knowledge gaps until prompt specificity is checked.",
        )
    if root_cause == "stale_or_rejected_context":
        return (
            "Do not inject stale memory without newer live evidence or source refs.",
            "Do not relax the firewall before inspecting rejected ids and reasons.",
        )
    if root_cause in {"domain_knowledge_gap", "knowledge_missing_or_not_retrieved"}:
        return (
            "Do not merge distinct product, version, or root-cause variants without source evidence.",
        )
    return ("Require human review before turning this cluster into a memory or answer.",)


def _risk_level(labels: set[str], reason_counts: dict[str, int]) -> str:
    if (
        labels & {"auth", "context-rejected", "model", "permission", "quota", "stale-state"}
        or {"only_rejected", "all_candidates_rejected", "partial_candidates_rejected"}
        & set(reason_counts)
    ):
        return "high"
    if "query-gate" in labels or "empty_recall" in reason_counts:
        return "medium"
    return "low"


def _suggested_owner(
    root_cause: str,
    labels: set[str],
    reason_counts: dict[str, int],
) -> str:
    if root_cause == "query_gate_underqualified":
        return "retrieval-policy"
    if root_cause == "stale_or_rejected_context":
        return "memory-quality"
    if root_cause in {"domain_knowledge_gap", "knowledge_missing_or_not_retrieved"}:
        return "knowledge-base"
    if labels & {"auth", "install", "model", "quota"} or "empty_recall" in reason_counts:
        return "knowledge-base"
    return "triage"


def _cluster_title(labels: tuple[str, ...], group: list[_GapFeatures]) -> str:
    if labels:
        return " / ".join(labels[:3])
    common = Counter(token for feature in group for token in feature.tokens)
    terms = [term for term, _count in common.most_common(3)]
    return " / ".join(terms) if terms else "unlabeled gap"


def _labels(text: str) -> set[str]:
    labels: set[str] = set()
    for label, terms in _RULE_LABELS.items():
        if any(term in text for term in terms):
            labels.add(label)
    return labels


def _tokens(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9][a-z0-9._+-]{1,}", text)
    return [token for token in tokens if token not in _NOISE_TOKENS and not token.isdigit()]


def _normalize_text(text: str) -> str:
    value = text.lower()
    value = re.sub(r"\b[a-f0-9]{16,}\b", " <hash> ", value)
    value = re.sub(r"\b(?:trace|request|session|span)[_-]?id[:=]?[a-z0-9._-]+\b", " <id> ", value)
    value = re.sub(r"/[a-z0-9._~+/-]{3,}", " <path> ", value)
    value = re.sub(r"\b\d{4}-\d{2}-\d{2}[t ][0-9:.-]+z?\b", " <time> ", value)
    value = re.sub(r"\b\d{6,}\b", " <num> ", value)
    return re.sub(r"\s+", " ", value).strip()


def _find(parent: dict[int, int], node: int) -> int:
    while parent[node] != node:
        parent[node] = parent[parent[node]]
        node = parent[node]
    return node


def _union(parent: dict[int, int], left: int, right: int) -> None:
    left_root = _find(parent, left)
    right_root = _find(parent, right)
    if left_root != right_root:
        parent[right_root] = left_root


__all__ = [
    "GapCluster",
    "GapClusterProfile",
    "GapClusterReport",
    "GapReplayCase",
    "GapReplayCohort",
    "build_gap_cluster_report",
    "build_gap_replay_cohort",
]
