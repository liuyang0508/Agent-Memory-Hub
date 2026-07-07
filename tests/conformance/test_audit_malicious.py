"""Conformance tests for skill audit scanner against malicious and clean samples."""
from pathlib import Path

import pytest

from agent_brain.memory.governance.audit.rules import load_builtin_rules
from agent_brain.memory.governance.audit.scanner import SkillScanner


@pytest.fixture
def scanner():
    """Create scanner with built-in rules."""
    rules = load_builtin_rules()
    return SkillScanner(rules=rules)


@pytest.fixture
def malicious_skills_dir(fixtures_dir: Path) -> Path:
    """Path to malicious skills directory."""
    return fixtures_dir / "malicious_skills"


@pytest.fixture
def clean_skills_dir(fixtures_dir: Path) -> Path:
    """Path to clean skills directory."""
    return fixtures_dir / "clean_skills"


class TestMaliciousSamples:
    """Test that scanner detects all malicious samples."""

    def test_scanner_detects_all_malicious_samples(
        self, scanner: SkillScanner, malicious_skills_dir: Path
    ):
        """Each malicious sample should produce at least 1 finding."""
        if not malicious_skills_dir.exists():
            pytest.skip(f"Malicious skills directory not found: {malicious_skills_dir}")

        md_files = list(malicious_skills_dir.glob("*.md"))
        assert len(md_files) >= 30, f"Expected at least 30 malicious samples, found {len(md_files)}"

        undetected = []
        for file_path in md_files:
            findings = scanner.scan_file(file_path)
            if len(findings) == 0:
                undetected.append(file_path.name)

        if undetected:
            pytest.fail(
                f"The following malicious samples produced no findings:\n"
                + "\n".join(f"  - {name}" for name in undetected)
            )

    def test_scanner_report_summary_accurate(self, scanner: SkillScanner, malicious_skills_dir: Path):
        """Scanning malicious directory should result in failed report."""
        if not malicious_skills_dir.exists():
            pytest.skip(f"Malicious skills directory not found: {malicious_skills_dir}")

        report = scanner.scan_directory(malicious_skills_dir)

        assert report.passed is False, "Report should fail when scanning malicious samples"
        assert report.total_findings > 0, "Should have at least one finding"
        assert report.critical > 0 or report.high > 0, "Should have critical or high severity findings"


class TestCleanSamples:
    """Test that scanner passes all clean samples."""

    def test_scanner_passes_all_clean_samples(
        self, scanner: SkillScanner, clean_skills_dir: Path
    ):
        """Each clean sample should produce zero findings."""
        if not clean_skills_dir.exists():
            pytest.skip(f"Clean skills directory not found: {clean_skills_dir}")

        md_files = list(clean_skills_dir.glob("*.md"))
        assert len(md_files) >= 3, f"Expected at least 3 clean samples, found {len(md_files)}"

        flagged = []
        for file_path in md_files:
            findings = scanner.scan_file(file_path)
            if len(findings) > 0:
                flagged.append((file_path.name, findings))

        if flagged:
            details = "\n".join(
                f"  - {name}: {len(findings)} finding(s)" for name, findings in flagged
            )
            pytest.fail(
                f"The following clean samples were incorrectly flagged:\n{details}"
            )
