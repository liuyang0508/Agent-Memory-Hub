"""Tests for audit rules loading and validation."""

import pytest
from agent_brain.memory.governance.audit.rules import (
    AuditRule,
    RuleSet,
    load_builtin_rules,
    load_rules_from_file,
)


class TestLoadBuiltinRules:
    """Test builtin rules loading."""

    def test_load_builtin_rules_returns_30_plus_rules(self):
        """Verify that builtin rules contain at least 30 rules."""
        ruleset = load_builtin_rules()
        assert len(ruleset.rules) >= 30, f"Expected at least 30 rules, got {len(ruleset.rules)}"

    def test_rule_severities_are_valid(self):
        """Verify all rule severities are valid values."""
        ruleset = load_builtin_rules()
        valid_severities = {'critical', 'high', 'medium', 'low'}
        
        for rule in ruleset.rules:
            assert rule.severity in valid_severities, (
                f"Rule '{rule.id}' has invalid severity: '{rule.severity}'"
            )

    def test_rule_categories_are_valid(self):
        """Verify all rule categories are valid values."""
        ruleset = load_builtin_rules()
        valid_categories = {'outbound', 'filesystem', 'exec', 'secrets', 'injection', 'resource'}
        
        for rule in ruleset.rules:
            assert rule.category in valid_categories, (
                f"Rule '{rule.id}' has invalid category: '{rule.category}'"
            )

    def test_rule_patterns_are_valid_regex(self):
        """Verify all rule patterns are valid regex."""
        ruleset = load_builtin_rules()
        
        for rule in ruleset.rules:
            # The AuditRule model already validates regex in the field_validator
            # If we get here without exception, the pattern is valid
            assert rule.pattern is not None
            assert len(rule.pattern) > 0


class TestAuditRuleModel:
    """Test AuditRule model validation."""

    def test_invalid_severity_raises_error(self):
        """Verify that invalid severity raises ValueError."""
        with pytest.raises(ValueError, match="Severity must be one of"):
            AuditRule(
                id="test-rule",
                severity="invalid",
                category="outbound",
                pattern="test",
                description="Test rule",
                remediation="Fix it"
            )

    def test_invalid_category_raises_error(self):
        """Verify that invalid category raises ValueError."""
        with pytest.raises(ValueError, match="Category must be one of"):
            AuditRule(
                id="test-rule",
                severity="high",
                category="invalid",
                pattern="test",
                description="Test rule",
                remediation="Fix it"
            )

    def test_invalid_regex_pattern_raises_error(self):
        """Verify that invalid regex pattern raises ValueError."""
        with pytest.raises(ValueError, match="Invalid regex pattern"):
            AuditRule(
                id="test-rule",
                severity="high",
                category="outbound",
                pattern="[invalid(regex",
                description="Test rule",
                remediation="Fix it"
            )


class TestLoadRulesFromFile:
    """Test loading rules from custom file."""

    def test_load_nonexistent_file_raises_error(self, tmp_path):
        """Verify that loading from nonexistent file raises FileNotFoundError."""
        nonexistent_file = tmp_path / "nonexistent.yaml"
        
        with pytest.raises(FileNotFoundError):
            load_rules_from_file(str(nonexistent_file))
