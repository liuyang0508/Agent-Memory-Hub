"""Resolve the stable AMH checkout used by long-lived adapter configuration."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Literal


RuntimeAuthoritySource = Literal[
    "explicit",
    "managed-shim",
    "module-fallback",
    "invalid-managed-shim",
]

_MANAGED_SHIM = re.compile(r'^#!/bin/sh\nexec "([^"]+)" "\$@"\n$')
_MEMORY_TARGET_SUFFIX = Path(".venv/bin/memory")


@dataclass(frozen=True)
class RuntimeAuthority:
    root: Path
    source: RuntimeAuthoritySource
    error: str | None = None

    @property
    def valid(self) -> bool:
        return self.error is None

    def require(self) -> Path:
        if self.error is not None:
            raise RuntimeError(self.error)
        return self.root


def managed_memory_shim_path() -> Path:
    user_bin = os.environ.get("AGENT_MEMORY_HUB_BIN")
    return (Path(user_bin) if user_bin else Path.home() / ".local" / "bin") / "memory"


def _root_valid(root: Path) -> bool:
    return (
        (root / "pyproject.toml").is_file()
        and (root / "agent_runtime_kit" / "hooks" / "inject-context.sh").is_file()
        and (root / "agent_runtime_kit" / "hooks" / "session-end-signal.sh").is_file()
    )


def _invalid(module_root: Path, detail: str) -> RuntimeAuthority:
    return RuntimeAuthority(
        root=module_root.resolve(),
        source="invalid-managed-shim",
        error=f"{detail}; run memory doctor --fix before repairing adapters",
    )


def resolve_runtime_authority(
    *,
    explicit_repo_dir: Path | None,
    module_repo_dir: Path,
    shim_path: Path | None = None,
) -> RuntimeAuthority:
    module_root = Path(module_repo_dir).expanduser()
    if explicit_repo_dir is not None:
        root = Path(explicit_repo_dir).expanduser().resolve()
        if not _root_valid(root):
            return RuntimeAuthority(root, "explicit", f"invalid AMH runtime root: {root}")
        return RuntimeAuthority(root, "explicit")

    shim = shim_path or managed_memory_shim_path()
    if not shim.exists():
        root = module_root.resolve()
        if not _root_valid(root):
            return RuntimeAuthority(root, "module-fallback", f"invalid AMH runtime root: {root}")
        return RuntimeAuthority(root, "module-fallback")

    try:
        content = shim.read_text()
    except OSError as exc:
        return _invalid(module_root, f"cannot read managed memory shim {shim}: {exc}")

    match = _MANAGED_SHIM.fullmatch(content)
    if match is None:
        return _invalid(module_root, f"malformed managed memory shim: {shim}")

    target = Path(match.group(1)).expanduser()
    if not target.is_absolute():
        return _invalid(module_root, f"managed memory shim target must be absolute: {target}")
    if not target.is_file():
        return _invalid(module_root, f"managed memory shim target does not exist: {target}")
    if target.parts[-len(_MEMORY_TARGET_SUFFIX.parts) :] != _MEMORY_TARGET_SUFFIX.parts:
        return _invalid(module_root, f"managed memory shim target has an unexpected layout: {target}")

    root = target.parents[len(_MEMORY_TARGET_SUFFIX.parts) - 1].resolve()
    if not _root_valid(root):
        return _invalid(module_root, f"invalid AMH runtime root: {root}")
    return RuntimeAuthority(root, "managed-shim")
