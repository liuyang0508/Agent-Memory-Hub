"""Read-only readiness report for the next governance pass."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from agent_brain.memory.context.query_signal import diagnose_injection_query
from agent_brain.memory.store.items_store import ItemsStore


Status = str


@dataclass(frozen=True)
class ReadinessCheck:
    id: str
    status: Status
    title: str
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReadinessLane:
    id: str
    title: str
    status: Status
    metrics: dict[str, Any]
    checks: list[ReadinessCheck]
    next_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "metrics": self.metrics,
            "checks": [check.to_dict() for check in self.checks],
            "next_actions": self.next_actions,
        }


@dataclass(frozen=True)
class GovernanceReadinessReport:
    generated_at: str
    overall_status: Status
    lanes: list[ReadinessLane]
    next_actions: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "overall_status": self.overall_status,
            "lanes": [lane.to_dict() for lane in self.lanes],
            "next_actions": self.next_actions,
        }


QUERY_SIGNAL_AUDIT_CASES_PATH = Path(__file__).with_name("query_signal_adversarial_cases.json")


def load_query_signal_audit_cases() -> tuple[dict[str, Any], ...]:
    """Load adversarial query-signal readiness cases shipped with the package."""
    try:
        raw = json.loads(QUERY_SIGNAL_AUDIT_CASES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot load query signal audit cases: {exc}") from exc
    if not isinstance(raw, list):
        raise RuntimeError("query signal audit cases must be a JSON list")
    cases: list[dict[str, Any]] = []
    for index, case in enumerate(raw):
        if not isinstance(case, dict):
            raise RuntimeError(f"query signal audit case #{index} must be an object")
        cases.append(case)
    return tuple(cases)


def build_governance_readiness_report(
    brain_dir: Path,
    *,
    repo_root: Path,
) -> GovernanceReadinessReport:
    lanes = [
        _release_lane(repo_root),
        _query_signal_lane(brain_dir),
        _memory_lifecycle_lane(brain_dir),
    ]
    next_actions = _unique(
        action for lane in lanes for action in lane.next_actions
    )
    return GovernanceReadinessReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        overall_status=_worst_status(lane.status for lane in lanes),
        lanes=lanes,
        next_actions=next_actions,
    )


def render_governance_readiness_markdown(report: GovernanceReadinessReport) -> str:
    lines = [
        "# Governance Readiness",
        "",
        f"**Overall status**: `{report.overall_status}`",
        f"**Generated at**: `{report.generated_at}`",
        "",
    ]
    for lane in report.lanes:
        lines.extend([
            f"## {lane.title}",
            "",
            f"**Status**: `{lane.status}`",
            "",
            "| Check | Status | Detail |",
            "|---|---|---|",
        ])
        for check in lane.checks:
            lines.append(f"| {check.title} | `{check.status}` | {check.detail} |")
        if lane.metrics:
            lines.extend(["", "**Metrics**:", ""])
            for key, value in lane.metrics.items():
                lines.append(f"- `{key}`: {value}")
        if lane.next_actions:
            lines.extend(["", "**Next actions**:", ""])
            for action in lane.next_actions:
                lines.append(f"- `{action}`")
        lines.append("")
    if report.next_actions:
        lines.extend(["## Suggested Command Queue", ""])
        for action in report.next_actions:
            lines.append(f"- `{action}`")
    return "\n".join(lines).rstrip() + "\n"


def _release_lane(repo_root: Path) -> ReadinessLane:
    local_files = {
        "install_sh": repo_root / "install.sh",
        "install_ps1": repo_root / "install.ps1",
        "homebrew_cask": repo_root / "Casks" / "agent-memory-hub.rb",
        "npm_package_json": repo_root / "packaging" / "npm" / "package.json",
        "release_publishing_doc": repo_root / "docs" / "release-publishing.md",
    }
    checks = [
        _file_check("install_sh", "install.sh asset", local_files["install_sh"]),
        _file_check("install_ps1", "install.ps1 asset", local_files["install_ps1"]),
        _file_check("homebrew_cask", "Homebrew cask", local_files["homebrew_cask"]),
        _file_check(
            "npm_package_json",
            "npm package metadata",
            local_files["npm_package_json"],
            missing_status="warn",
        ),
        _file_check(
            "release_publishing_doc",
            "release publishing doc",
            local_files["release_publishing_doc"],
        ),
        _public_hygiene_check(repo_root),
    ]
    next_actions = []
    if not local_files["npm_package_json"].exists():
        next_actions.append("prepare packaging/npm/package.json before npm publish")
    if any(check.status == "fail" for check in checks):
        next_actions.append("fix release readiness failures before tagging a release")
    return ReadinessLane(
        id="release",
        title="发布可用性",
        status=_worst_status(check.status for check in checks),
        metrics={
            "checked_files": len(local_files),
            "missing_or_warn_files": sum(
                1 for path in local_files.values() if not path.exists()
            ),
        },
        checks=checks,
        next_actions=next_actions,
    )


def _query_signal_lane(brain_dir: Path) -> ReadinessLane:
    cases = load_query_signal_audit_cases()
    checks: list[ReadinessCheck] = []
    under_extracted = 0
    injectable = 0
    blocked = 0
    category_counts: dict[str, int] = {}
    for case in cases:
        category = str(case.get("category") or "uncategorized")
        category_counts[category] = category_counts.get(category, 0) + 1
        diagnostic = diagnose_injection_query(case["prompt"], brain_dir=brain_dir)
        terms = tuple(str(term) for term in diagnostic.terms)
        lower_terms = tuple(term.lower() for term in terms)
        expected_terms = tuple(str(term).lower() for term in case.get("expected_terms", ()))
        missing_terms = [
            term for term in expected_terms
            if not any(term in candidate or candidate in term for candidate in lower_terms)
        ]
        if diagnostic.injectable:
            injectable += 1
        else:
            blocked += 1
        expected_injectable = bool(case["expected_injectable"])
        expected_reason = case.get("expected_reason")
        reason_mismatch = (
            expected_reason is not None
            and str(diagnostic.reason) != str(expected_reason)
        )
        is_under_extracted = (
            diagnostic.injectable != expected_injectable
            or len(terms) < int(case["min_terms"])
            or bool(missing_terms)
            or reason_mismatch
        )
        if is_under_extracted:
            under_extracted += 1
        status = "warn" if is_under_extracted else "pass"
        checks.append(ReadinessCheck(
            id=str(case["id"]),
            status=status,
            title=str(case["id"]).replace("_", " "),
            detail=(
                f"category={category}, "
                f"decision={diagnostic.decision}, "
                f"terms={list(terms)}, missing={missing_terms}, "
                f"reason={diagnostic.reason}"
            ),
            evidence={
                **diagnostic.to_dict(),
                "category": category,
                "expected_injectable": expected_injectable,
                "expected_reason": expected_reason,
                "reason_mismatch": reason_mismatch,
            },
        ))
    return ReadinessLane(
        id="query_signal",
        title="长任务召回入口",
        status="warn" if under_extracted else "pass",
        metrics={
            "case_count": len(cases),
            "category_counts": dict(sorted(category_counts.items())),
            "injectable_cases": injectable,
            "blocked_cases": blocked,
            "under_extracted_cases": under_extracted,
        },
        checks=checks,
        next_actions=(
            ["add or tune query_signal cases before changing retrieval ranking"]
            if under_extracted
            else []
        ),
    )


def _memory_lifecycle_lane(brain_dir: Path) -> ReadinessLane:
    store = ItemsStore(brain_dir / "items")
    items = [item for item, _body in store.iter_all()]
    now = datetime.now(timezone.utc)
    by_type: dict[str, int] = {}
    stale_signal_count = 0
    low_confidence_count = 0
    untagged_count = 0
    raw_count = 0
    private_or_secret_count = 0
    superseded_count = 0
    for item in items:
        item_type = str(item.type)
        by_type[item_type] = by_type.get(item_type, 0) + 1
        age_days = (now - item.created_at).days
        if item_type in {"signal", "handoff"} and age_days > 30:
            stale_signal_count += 1
        if item.confidence < 0.5:
            low_confidence_count += 1
        if not item.tags:
            untagged_count += 1
        if str(item.abstraction) == "L0" or str(item.maturity) == "raw":
            raw_count += 1
        if str(item.sensitivity) in {"private", "secret"}:
            private_or_secret_count += 1
        if item.superseded_by:
            superseded_count += 1
    metrics = {
        "total_items": len(items),
        "by_type": dict(sorted(by_type.items())),
        "stale_signal_count": stale_signal_count,
        "low_confidence_count": low_confidence_count,
        "untagged_count": untagged_count,
        "raw_count": raw_count,
        "private_or_secret_count": private_or_secret_count,
        "superseded_count": superseded_count,
    }
    checks = [
        _threshold_check(
            "stale_signal_count",
            "stale signal / handoff",
            stale_signal_count,
            "memory govern plan --category lifecycle",
        ),
        _threshold_check(
            "low_confidence_count",
            "low confidence",
            low_confidence_count,
            "memory govern maturity --format table",
        ),
        _threshold_check(
            "untagged_count",
            "untagged items",
            untagged_count,
            "memory govern run --format markdown",
        ),
        _threshold_check(
            "private_or_secret_count",
            "private / secret items",
            private_or_secret_count,
            "memory search --context-firewall",
        ),
    ]
    status = _worst_status(check.status for check in checks)
    return ReadinessLane(
        id="memory_lifecycle",
        title="记忆生命周期",
        status=status,
        metrics=metrics,
        checks=checks,
        next_actions=["memory govern plan --format markdown"] if status != "pass" else [],
    )


def _file_check(
    check_id: str,
    title: str,
    path: Path,
    *,
    missing_status: Status = "fail",
) -> ReadinessCheck:
    if path.exists():
        return ReadinessCheck(check_id, "pass", title, f"found: {path.as_posix()}")
    return ReadinessCheck(check_id, missing_status, title, f"missing: {path.as_posix()}")


def _public_hygiene_check(repo_root: Path) -> ReadinessCheck:
    try:
        from agent_brain.evaluation.public_hygiene import (
            format_findings,
            scan_git_public_surface,
        )

        findings = scan_git_public_surface(repo_root)
    except Exception as exc:  # noqa: BLE001 - readiness should not crash on non-git trees
        return ReadinessCheck(
            "public_hygiene",
            "warn",
            "public hygiene",
            f"scan unavailable: {exc}",
        )
    if findings:
        return ReadinessCheck(
            "public_hygiene",
            "fail",
            "public hygiene",
            format_findings(findings[:5]),
            evidence={"finding_count": len(findings)},
        )
    return ReadinessCheck("public_hygiene", "pass", "public hygiene", "no tracked findings")


def _threshold_check(check_id: str, title: str, count: int, command: str) -> ReadinessCheck:
    if count == 0:
        return ReadinessCheck(check_id, "pass", title, "0 items")
    return ReadinessCheck(
        check_id,
        "warn",
        title,
        f"{count} item(s); inspect with `{command}`",
        evidence={"count": count, "command": command},
    )


def _worst_status(statuses: Any) -> Status:
    order = {"pass": 0, "ok": 0, "warn": 1, "fail": 2, "error": 2}
    worst = "pass"
    for status in statuses:
        if order.get(str(status), 1) > order.get(worst, 0):
            worst = "fail" if str(status) == "error" else str(status)
    return worst


def _unique(values: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value)
        if text and text not in seen:
            out.append(text)
            seen.add(text)
    return out


__all__ = [
    "GovernanceReadinessReport",
    "QUERY_SIGNAL_AUDIT_CASES_PATH",
    "ReadinessCheck",
    "ReadinessLane",
    "build_governance_readiness_report",
    "load_query_signal_audit_cases",
    "render_governance_readiness_markdown",
]
