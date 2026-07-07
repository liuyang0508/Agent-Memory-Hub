"""Tests for custom audit rules loading and merging."""

from __future__ import annotations

from pathlib import Path

import yaml

from agent_brain.memory.governance.audit.custom_rules import load_custom_rules, load_merged_rules
from agent_brain.memory.governance.audit.rules import load_builtin_rules


def _write_rules_yaml(path: Path, rules: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump({"rules": rules}), encoding="utf-8")


VALID_RULE = {
    "id": "test-custom-rule",
    "severity": "high",
    "category": "secrets",
    "pattern": r"PRIVATE_KEY",
    "description": "Custom rule: private key detected",
    "remediation": "Remove private keys",
}


class TestLoadCustomRules:
    def test_returns_empty_when_no_dir(self, tmp_path):
        rules = load_custom_rules(tmp_path / "nonexistent")
        assert rules == []

    def test_loads_single_yaml(self, tmp_path):
        rules_dir = tmp_path / "rules"
        _write_rules_yaml(rules_dir / "custom.yaml", [VALID_RULE])

        rules = load_custom_rules(rules_dir)
        assert len(rules) == 1
        assert rules[0].id == "test-custom-rule"
        assert rules[0].severity == "high"

    def test_loads_multiple_files_sorted(self, tmp_path):
        rules_dir = tmp_path / "rules"
        _write_rules_yaml(rules_dir / "b_rules.yaml", [VALID_RULE])
        rule2 = {**VALID_RULE, "id": "test-rule-2", "pattern": r"SECRET"}
        _write_rules_yaml(rules_dir / "a_rules.yaml", [rule2])

        rules = load_custom_rules(rules_dir)
        assert len(rules) == 2
        assert rules[0].id == "test-rule-2"
        assert rules[1].id == "test-custom-rule"

    def test_skips_malformed_yaml(self, tmp_path):
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "bad.yaml").write_text("{{invalid yaml", encoding="utf-8")
        _write_rules_yaml(rules_dir / "good.yaml", [VALID_RULE])

        rules = load_custom_rules(rules_dir)
        assert len(rules) == 1

    def test_skips_invalid_rules(self, tmp_path):
        rules_dir = tmp_path / "rules"
        bad_rule = {"id": "bad", "severity": "INVALID_LEVEL"}
        _write_rules_yaml(rules_dir / "mixed.yaml", [VALID_RULE, bad_rule])

        rules = load_custom_rules(rules_dir)
        assert len(rules) == 1
        assert rules[0].id == "test-custom-rule"

    def test_loads_yml_extension(self, tmp_path):
        rules_dir = tmp_path / "rules"
        _write_rules_yaml(rules_dir / "custom.yml", [VALID_RULE])

        rules = load_custom_rules(rules_dir)
        assert len(rules) == 1


class TestLoadMergedRules:
    def test_returns_builtins_when_no_custom(self, tmp_path):
        merged = load_merged_rules(custom_dir=tmp_path / "nonexistent")
        builtin = load_builtin_rules()
        assert len(merged.rules) == len(builtin.rules)

    def test_merges_custom_with_builtin(self, tmp_path):
        rules_dir = tmp_path / "rules"
        _write_rules_yaml(rules_dir / "custom.yaml", [VALID_RULE])

        builtin_count = len(load_builtin_rules().rules)
        merged = load_merged_rules(custom_dir=rules_dir)
        assert len(merged.rules) == builtin_count + 1

    def test_custom_overrides_builtin_by_id(self, tmp_path):
        builtin = load_builtin_rules()
        first_rule = builtin.rules[0]

        override = {
            "id": first_rule.id,
            "severity": "low",
            "category": first_rule.category,
            "pattern": first_rule.pattern,
            "description": "overridden description",
            "remediation": first_rule.remediation,
        }
        rules_dir = tmp_path / "rules"
        _write_rules_yaml(rules_dir / "override.yaml", [override])

        merged = load_merged_rules(custom_dir=rules_dir)
        matched = [r for r in merged.rules if r.id == first_rule.id]
        assert len(matched) == 1
        assert matched[0].severity == "low"
        assert matched[0].description == "overridden description"

    def test_extra_files_param(self, tmp_path):
        extra_file = tmp_path / "extra_rules.yaml"
        _write_rules_yaml(extra_file, [VALID_RULE])

        builtin_count = len(load_builtin_rules().rules)
        merged = load_merged_rules(
            custom_dir=tmp_path / "nonexistent",
            extra_files=[extra_file],
        )
        assert len(merged.rules) == builtin_count + 1
