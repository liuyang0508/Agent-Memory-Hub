"""Stable Python runtime selection for adapter-managed launchers."""

from __future__ import annotations

import sys
from pathlib import Path


def amh_python_executable(repo_dir: Path | None = None) -> str:
    """Return the Python executable adapters should write into long-lived config.

    Adapter config files are consumed later by GUI apps, MCP clients, or other
    agents.  They must not drift just because the installer was invoked from a
    different project venv.  Prefer the Agent Memory Hub repo venv when present
    and fall back to the current interpreter only for source checkouts without a
    local venv.
    """
    root = repo_dir or Path(__file__).resolve().parents[2]
    candidates = (
        root / ".venv" / "bin" / "python",
        root / ".venv" / "Scripts" / "python.exe",
    )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable


__all__ = ["amh_python_executable"]
