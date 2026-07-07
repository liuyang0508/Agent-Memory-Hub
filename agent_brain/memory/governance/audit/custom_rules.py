"""Custom audit rules loader.

Users can define additional audit rules in YAML files at
``~/.agent-memory-hub/rules/`` (or any path).  These are merged into the
scan pipeline alongside the 30+ builtin rules.

Supported rule types:
  - ``pattern``: regex match against file content (same as builtin rules)
  - ``check``: named semantic checks (tags_contain_project, min_body_length, etc.)

Example ``~/.agent-memory-hub/rules/custom_rules.yaml``::

    rules:
      - id: no-internal-urls
        severity: high
        category: secrets
        pattern: "https://internal\\.corp\\."
        description: "Internal URL detected"
        remediation: "Remove internal URLs before committing to brain pool"
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from .rules import AuditRule, RuleSet, load_builtin_rules

DEFAULT_CUSTOM_DIR = Path(
    os.environ.get("BRAIN_DIR", Path.home() / ".agent-memory-hub")
) / "rules"


def load_custom_rules(rules_dir: Path | None = None) -> list[AuditRule]:
    """Load all custom rule YAML files from a directory.

    Each file must contain a top-level ``rules:`` list.  Files are loaded
    in sorted order so rule IDs are deterministic.
    """
    rules_dir = rules_dir or DEFAULT_CUSTOM_DIR
    if not rules_dir.is_dir():
        return []

    custom_rules: list[AuditRule] = []
    for path in sorted(rules_dir.glob("*.yaml")):
        custom_rules.extend(_load_one(path))
    for path in sorted(rules_dir.glob("*.yml")):
        custom_rules.extend(_load_one(path))
    return custom_rules


def _load_one(path: Path) -> list[AuditRule]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        return []
    if not isinstance(data, dict) or "rules" not in data:
        return []

    rules: list[AuditRule] = []
    for raw in data["rules"]:
        if not isinstance(raw, dict):
            continue
        try:
            rules.append(AuditRule(**raw))
        except Exception:
            continue
    return rules


def load_merged_rules(
    custom_dir: Path | None = None,
    extra_files: list[Path] | None = None,
) -> RuleSet:
    """Load builtin rules + custom directory rules + any extra files.

    Returns a single merged RuleSet.  Custom rules with the same ``id``
    as a builtin rule override the builtin (severity/pattern update).
    """
    builtin = load_builtin_rules()
    rules_by_id = {r.id: r for r in builtin.rules}

    for rule in load_custom_rules(custom_dir):
        rules_by_id[rule.id] = rule

    for path in extra_files or []:
        if path.exists():
            for rule in _load_one(path):
                rules_by_id[rule.id] = rule

    return RuleSet(
        version=builtin.version,
        rules=list(rules_by_id.values()),
    )
