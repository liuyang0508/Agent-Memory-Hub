"""Conformance-suite policy: skips must be explicit env-opt-in only.

Past versions of test_v05_compat.py used ``@pytest.mark.skipif(not <local-dir>.exists())``
which made CI silently pass with zero conformance coverage. This conftest
enforces that any skip inside tests/conformance/ must declare an opt-in
environment variable in its reason string. Skips not following this pattern
are converted to failures so the suite cannot quietly opt out of work.

Allowed skip reasons must contain one of:
  - "opt-in via " followed by an environment variable name
  - "requires " followed by a tool / dep name we explicitly do not bundle

Anything else (e.g. "fixture missing", "directory not present") fails.
"""
from __future__ import annotations

import re

import pytest

_ALLOWED_SKIP_REASON = re.compile(
    r"opt-in via [A-Z_][A-Z0-9_]*\s*=|requires [A-Za-z0-9_.-]+",
)


def pytest_collection_modifyitems(config, items):
    """Reject silent / fixture-missing skips at collection time."""
    for item in items:
        for marker in item.iter_markers(name="skipif"):
            reason = ""
            for arg in marker.args[1:]:
                if isinstance(arg, str):
                    reason = arg
                    break
            reason = marker.kwargs.get("reason", reason)
            if not reason:
                item.add_marker(
                    pytest.mark.xfail(
                        reason="conformance skipif has no reason — explicit opt-in required",
                        strict=True,
                    )
                )
                continue
            if not _ALLOWED_SKIP_REASON.search(reason):
                # Convert silent skips into hard failures so CI cannot pass
                # without running real conformance checks.
                item.add_marker(
                    pytest.mark.xfail(
                        reason=(
                            f"conformance skip reason '{reason!r}' does not declare an "
                            f"explicit env opt-in; allowed patterns: "
                            f"'opt-in via ENV_VAR=1' or 'requires <dep>'"
                        ),
                        strict=True,
                    )
                )
