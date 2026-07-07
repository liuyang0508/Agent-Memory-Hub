"""Unit tests for skill audit scanner."""
import pytest
from pathlib import Path
import tempfile

from agent_brain.memory.governance.audit.rules import AuditRule, RuleSet
from agent_brain.memory.governance.audit.scanner import SkillScanner, Finding, AuditReport


@pytest.fixture
def sample_rules():
    """Create sample rules for testing."""
    return RuleSet(
        version="1.0",
        rules=[
            AuditRule(
                id='SEC-001',
                severity='critical',
                category='outbound',
                pattern=r'\bcurl\b|\bwget\b|\brequests\.\w+\(',
                description='Detects outbound network calls using curl/wget/requests',
                remediation='Use approved API endpoints instead of direct HTTP calls',
            ),
            AuditRule(
                id='SEC-002',
                severity='high',
                category='exec',
                pattern=r'\beval\s*\(|\bexec\s*\(',
                description='Detects dangerous code execution functions',
                remediation='Avoid using eval/exec; use safer alternatives',
            ),
        ]
    )


@pytest.fixture
def scanner(sample_rules):
    """Create scanner with sample rules."""
    return SkillScanner(rules=sample_rules)


@pytest.fixture
def temp_dir():
    """Create temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestScanFile:
    """Tests for scan_file method."""

    def test_scan_file_detects_curl_outbound(self, scanner, temp_dir):
        """Test that scanner detects curl outbound calls."""
        test_file = temp_dir / 'test_curl.py'
        test_file.write_text('import subprocess\nresult = subprocess.run(["curl", "https://example.com"])\n')

        findings = scanner.scan_file(test_file)

        assert len(findings) == 1
        assert findings[0].rule_id == 'SEC-001'
        assert findings[0].severity == 'critical'
        assert findings[0].category == 'outbound'
        assert 'curl' in findings[0].line_content

    def test_scan_file_detects_eval_exec(self, scanner, temp_dir):
        """Test that scanner detects eval/exec usage."""
        test_file = temp_dir / 'test_eval.py'
        test_file.write_text('user_input = input()\nresult = eval(user_input)\n')

        findings = scanner.scan_file(test_file)

        assert len(findings) == 1
        assert findings[0].rule_id == 'SEC-002'
        assert findings[0].severity == 'high'
        assert findings[0].category == 'exec'
        assert 'eval' in findings[0].line_content

    def test_scan_file_clean_passes(self, scanner, temp_dir):
        """Test that clean files produce no findings."""
        test_file = temp_dir / 'test_clean.py'
        test_file.write_text('def hello():\n    print("Hello, World!")\n')

        findings = scanner.scan_file(test_file)

        assert len(findings) == 0


class TestScanDirectory:
    """Tests for scan_directory method."""

    def test_scan_directory_aggregates_findings(self, scanner, temp_dir):
        """Test that directory scanning aggregates findings from multiple files."""
        # Create multiple test files
        file1 = temp_dir / 'file1.py'
        file1.write_text('result = eval(user_input)\n')

        file2 = temp_dir / 'file2.py'
        file2.write_text('import requests\nresponse = requests.get("https://api.example.com")\n')

        file3 = temp_dir / 'file3.py'
        file3.write_text('def clean_function():\n    return 42\n')

        report = scanner.scan_directory(temp_dir)

        assert report.scanned_files >= 3
        assert report.total_findings == 2
        assert report.critical == 1  # curl/requests from file2
        assert report.high == 1  # eval from file1


class TestAuditReport:
    """Tests for AuditReport class."""

    def test_report_types_are_split_and_reexported(self):
        from agent_brain.memory.governance.audit import report as report_mod
        from agent_brain.memory.governance.audit import scanner as scanner_mod

        assert scanner_mod.Finding is report_mod.Finding
        assert scanner_mod.AuditReport is report_mod.AuditReport

    def test_report_passed_when_no_critical_high(self):
        """Test that report passes when there are no critical/high findings."""
        report = AuditReport(
            scanned_files=10,
            total_findings=2,
            critical=0,
            high=0,
            medium=1,
            low=1,
            findings=[],
        )

        assert report.passed is True

    def test_report_failed_when_critical_exists(self):
        """Test that report fails when there are critical findings."""
        report = AuditReport(
            scanned_files=10,
            total_findings=1,
            critical=1,
            high=0,
            medium=0,
            low=0,
            findings=[],
        )

        assert report.passed is False

    def test_report_to_markdown_format(self):
        """Test markdown report generation."""
        finding = Finding(
            rule_id='SEC-001',
            severity='critical',
            category='outbound-network',
            file_path='test.py',
            line_number=5,
            line_content='curl https://example.com',
            description='Test description',
            remediation='Test remediation',
        )

        report = AuditReport(
            scanned_files=1,
            total_findings=1,
            critical=1,
            high=0,
            medium=0,
            low=0,
            findings=[finding],
        )

        markdown = report.to_markdown()

        assert '# Skill Audit Report' in markdown
        assert '## Summary' in markdown
        assert '**Scanned Files**: 1' in markdown
        assert '**Critical**: 1' in markdown
        assert '❌ No' in markdown  # Failed status
        assert '## Findings' in markdown
        assert 'SEC-001' in markdown
        assert 'test.py:5' in markdown
