"""Shared governance pipeline report value objects."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GovernanceIssue:
    """Represents a governance issue found in a memory item."""
    item_id: str
    issue_type: str  # 'duplicate' | 'noise' | 'expired' | 'low_quality'
    severity: str    # 'warning' | 'error'
    description: str
    suggestion: str


@dataclass
class GovernanceReport:
    """Summary report of governance scan results."""
    scanned_items: int
    issues: list[GovernanceIssue] = field(default_factory=list)
    duplicates: int = 0
    noise: int = 0
    expired: int = 0
    low_quality: int = 0

    @property
    def total_issues(self) -> int:
        return len(self.issues)

    @property
    def healthy(self) -> bool:
        return all(issue.severity != "error" for issue in self.issues)


__all__ = ["GovernanceIssue", "GovernanceReport"]
