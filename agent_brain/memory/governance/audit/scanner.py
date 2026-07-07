"""Core skill audit scanner engine."""
from functools import lru_cache
from pathlib import Path
import re
from typing import Optional

from .report import AuditReport, Finding
from .rules import RuleSet, AuditRule


@lru_cache(maxsize=1)
def _builtin_scanner() -> "SkillScanner":
    from .rules import load_builtin_rules

    return SkillScanner(rules=load_builtin_rules())


def audit_memory_text(text: str) -> AuditReport:
    """Audit memory content with builtin rules — the write-path 防进 gate.

    Returns an AuditReport; ``report.passed`` is False when any critical/high
    finding is present. Callers refuse the write (fail-closed) unless the user
    explicitly opts out.
    """
    findings = _builtin_scanner().scan_text(text or "")
    return AuditReport(
        scanned_files=1,
        total_findings=len(findings),
        critical=sum(1 for f in findings if f.severity == "critical"),
        high=sum(1 for f in findings if f.severity == "high"),
        medium=sum(1 for f in findings if f.severity == "medium"),
        low=sum(1 for f in findings if f.severity == "low"),
        findings=findings,
    )


class SkillScanner:
    """Skill audit scanner that checks files against security rules."""

    MAX_FILE_SIZE = 1 * 1024 * 1024  # 1MB

    def __init__(self, rules: Optional[RuleSet] = None):
        """Initialize scanner with optional rule set."""
        self.rules = rules

    def scan_file(self, path: Path) -> list[Finding]:
        """Scan a single file against all rules.

        Args:
            path: Path to the file to scan.

        Returns:
            List of findings found in the file.
        """
        findings = []

        # Skip files larger than 1MB
        try:
            file_size = path.stat().st_size
            if file_size > self.MAX_FILE_SIZE:
                return findings
        except OSError:
            return findings

        # Read file line by line
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                for line_number, line in enumerate(f, 1):
                    if self.rules:
                        for rule in self.rules.rules:
                            if re.search(rule.pattern, line):
                                findings.append(Finding(
                                    rule_id=rule.id,
                                    severity=rule.severity,
                                    category=rule.category,
                                    file_path=str(path),
                                    line_number=line_number,
                                    line_content=line.rstrip('\n\r'),
                                    description=rule.description,
                                    remediation=rule.remediation,
                                ))
        except UnicodeDecodeError:
            # Skip binary files
            return findings
        except OSError:
            return findings

        return findings

    def scan_text(self, text: str, label: str = "<memory>") -> list[Finding]:
        """Scan an in-memory string against all rules (line by line).

        Used by the write-path "防进" gate, which audits memory body content
        before it is persisted rather than only auditing files after the fact.
        """
        findings: list[Finding] = []
        if not self.rules:
            return findings
        for line_number, line in enumerate(text.splitlines(), 1):
            for rule in self.rules.rules:
                if re.search(rule.pattern, line):
                    findings.append(Finding(
                        rule_id=rule.id,
                        severity=rule.severity,
                        category=rule.category,
                        file_path=label,
                        line_number=line_number,
                        line_content=line.rstrip('\n\r'),
                        description=rule.description,
                        remediation=rule.remediation,
                    ))
        return findings

    def scan_directory(self, path: Path, glob: str = '**/*') -> AuditReport:
        """Scan all files in a directory recursively.

        Args:
            path: Path to the directory to scan.
            glob: Glob pattern for file matching (default: '**/*').

        Returns:
            AuditReport with aggregated findings.
        """
        all_findings = []
        scanned_files = 0

        # Directories to skip
        skip_dirs = {'.git', 'node_modules', '__pycache__', '.venv', 'venv'}

        for file_path in path.glob(glob):
            # Skip directories
            if file_path.is_dir():
                continue

            # Skip files in excluded directories
            parts = file_path.parts
            if any(part in skip_dirs for part in parts):
                continue

            # Scan the file
            findings = self.scan_file(file_path)
            if findings or file_path.suffix in ['.py', '.js', '.ts', '.md', '.yaml', '.yml', '.json', '.sh']:
                scanned_files += 1
                all_findings.extend(findings)

        # Count findings by severity
        critical = sum(1 for f in all_findings if f.severity == 'critical')
        high = sum(1 for f in all_findings if f.severity == 'high')
        medium = sum(1 for f in all_findings if f.severity == 'medium')
        low = sum(1 for f in all_findings if f.severity == 'low')

        return AuditReport(
            scanned_files=scanned_files,
            total_findings=len(all_findings),
            critical=critical,
            high=high,
            medium=medium,
            low=low,
            findings=all_findings,
        )
