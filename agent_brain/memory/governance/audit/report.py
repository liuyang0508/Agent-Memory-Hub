"""Audit finding and report value objects."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Finding:
    """Audit finding result."""
    rule_id: str
    severity: str  # critical/high/medium/low
    category: str
    file_path: str
    line_number: int
    line_content: str
    description: str
    remediation: str


@dataclass
class AuditReport:
    """Audit report summary."""
    scanned_files: int
    total_findings: int
    critical: int
    high: int
    medium: int
    low: int
    findings: list[Finding] = field(default_factory=list)
    passed: bool = True  # True if no critical/high

    def __post_init__(self):
        """Calculate passed status after initialization."""
        self.passed = self.critical == 0 and self.high == 0

    def to_dict(self) -> dict:
        """Convert report to dictionary."""
        return {
            "scanned_files": self.scanned_files,
            "total_findings": self.total_findings,
            "critical": self.critical,
            "high": self.high,
            "medium": self.medium,
            "low": self.low,
            "passed": self.passed,
            "findings": [
                {
                    "rule_id": finding.rule_id,
                    "severity": finding.severity,
                    "category": finding.category,
                    "file_path": finding.file_path,
                    "line_number": finding.line_number,
                    "line_content": finding.line_content,
                    "description": finding.description,
                    "remediation": finding.remediation,
                }
                for finding in self.findings
            ],
        }

    def to_markdown(self) -> str:
        """Generate markdown formatted audit report."""
        lines = []
        lines.append("# Skill Audit Report")
        lines.append("")
        lines.append("## Summary")
        lines.append("")
        lines.append(f"- **Scanned Files**: {self.scanned_files}")
        lines.append(f"- **Total Findings**: {self.total_findings}")
        lines.append(f"- **Critical**: {self.critical}")
        lines.append(f"- **High**: {self.high}")
        lines.append(f"- **Medium**: {self.medium}")
        lines.append(f"- **Low**: {self.low}")
        lines.append(f'- **Passed**: {"✅ Yes" if self.passed else "❌ No"}')
        lines.append("")

        if self.findings:
            lines.append("## Findings")
            lines.append("")
            for i, finding in enumerate(self.findings, 1):
                lines.append(f"### {i}. [{finding.severity.upper()}] {finding.rule_id}")
                lines.append("")
                lines.append(f"- **Category**: {finding.category}")
                lines.append(f"- **File**: `{finding.file_path}:{finding.line_number}`")
                lines.append(f"- **Description**: {finding.description}")
                lines.append(f"- **Line Content**: `{finding.line_content.strip()}`")
                lines.append(f"- **Remediation**: {finding.remediation}")
                lines.append("")

        return "\n".join(lines)


__all__ = ["AuditReport", "Finding"]
