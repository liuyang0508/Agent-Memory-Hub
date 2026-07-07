"""Aider adapter diagnostic checks."""

from __future__ import annotations

from pathlib import Path

from .aider_config import _read_yaml
from .diagnostics import AdapterDiagnosticCheck


def diagnose_read_directive(conf_path: Path, digest_path: Path) -> AdapterDiagnosticCheck:
    if not conf_path.exists():
        return AdapterDiagnosticCheck(
            name="Aider read directive",
            status="error",
            detail=f"missing: {conf_path}",
            fix="run: memory adapter install aider",
        )
    try:
        config = _read_yaml(conf_path)
    except RuntimeError as exc:
        return AdapterDiagnosticCheck(
            name="Aider read directive",
            status="error",
            detail=str(exc),
            fix="repair YAML by hand, then run: memory adapter install aider",
        )

    read_list = config.get("read", [])
    if not isinstance(read_list, list):
        return AdapterDiagnosticCheck(
            name="Aider read directive",
            status="error",
            detail="read field is not a list",
            fix="run: memory adapter install aider",
        )
    if str(digest_path) not in read_list:
        return AdapterDiagnosticCheck(
            name="Aider read directive",
            status="error",
            detail=f"missing digest path in read list: {digest_path}",
            fix="run: memory adapter install aider",
        )
    return AdapterDiagnosticCheck(
        name="Aider read directive",
        status="ok",
        detail=f"read list includes {digest_path}",
    )


def diagnose_digest(digest_path: Path) -> AdapterDiagnosticCheck:
    if not digest_path.exists():
        return AdapterDiagnosticCheck(
            name="Aider brain digest",
            status="error",
            detail=f"missing: {digest_path}",
            fix="run: memory adapter install aider",
        )
    return AdapterDiagnosticCheck(
        name="Aider brain digest",
        status="ok",
        detail=f"found: {digest_path}",
    )


__all__ = ["diagnose_digest", "diagnose_read_directive"]
