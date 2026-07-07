"""
Rule loading engine for Skill Audit.
Loads and validates audit rules from YAML files.
"""

import re
from pathlib import Path
from typing import List

import yaml
from pydantic import BaseModel, Field, field_validator


class AuditRule(BaseModel):
    """Represents a single audit rule."""
    id: str = Field(..., description="Unique rule identifier")
    severity: str = Field(..., description="Rule severity level")
    category: str = Field(..., description="Rule category")
    pattern: str = Field(..., description="Regex pattern to match")
    description: str = Field(..., description="Rule description")
    remediation: str = Field(..., description="Remediation guidance")

    @field_validator('severity')
    @classmethod
    def validate_severity(cls, v: str) -> str:
        valid_severities = {'critical', 'high', 'medium', 'low'}
        if v not in valid_severities:
            raise ValueError(f"Severity must be one of {valid_severities}, got '{v}'")
        return v

    @field_validator('category')
    @classmethod
    def validate_category(cls, v: str) -> str:
        valid_categories = {'outbound', 'filesystem', 'exec', 'secrets', 'injection', 'resource'}
        if v not in valid_categories:
            raise ValueError(f"Category must be one of {valid_categories}, got '{v}'")
        return v

    @field_validator('pattern')
    @classmethod
    def validate_pattern(cls, v: str) -> str:
        try:
            re.compile(v)
        except re.error as e:
            raise ValueError(f"Invalid regex pattern: {e}")
        return v


class RuleSet(BaseModel):
    """Represents a collection of audit rules."""
    version: str = Field(..., description="Rule set version")
    rules: List[AuditRule] = Field(..., description="List of audit rules")


def load_builtin_rules() -> RuleSet:
    """Load builtin rules from package-internal skill-rules.yaml."""
    rules_file = Path(__file__).parent / "skill_rules.yaml"
    
    if not rules_file.exists():
        raise FileNotFoundError(f"Builtin rules file not found: {rules_file}")
    
    with open(rules_file, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    
    return RuleSet(**data)


def load_rules_from_file(path: str) -> RuleSet:
    """Load rules from a custom file path."""
    rules_file = Path(path)
    
    if not rules_file.exists():
        raise FileNotFoundError(f"Rules file not found: {rules_file}")
    
    with open(rules_file, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    
    return RuleSet(**data)
