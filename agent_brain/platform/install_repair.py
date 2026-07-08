"""Explicit install/update repair helpers.

Hooks must never update themselves at runtime. This module centralizes the
manual repair path used by ``memory doctor --fix`` and ``memory self-update``:
rewrite the CLI shim, reinstall core adapters, and optionally rerun the local
installer from the current checkout.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shlex
import subprocess


CORE_HOOK_ADAPTERS = ("codex", "claude_code")


@dataclass(frozen=True)
class RepairAction:
    name: str
    status: str
    detail: str

    @property
    def failed(self) -> bool:
        return self.status == "error"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def memory_shim_path() -> Path:
    user_bin = os.environ.get("AGENT_MEMORY_HUB_BIN")
    if user_bin:
        return Path(user_bin) / "memory"
    return Path.home() / ".local" / "bin" / "memory"


def venv_memory_path(root: Path | None = None) -> Path:
    base = root or repo_root()
    return base / ".venv" / "bin" / "memory"


def installer_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / "install.sh"


def installer_command(root: Path | None = None, *, minimal: bool = True) -> list[str]:
    command = ["sh", str(installer_path(root))]
    if minimal:
        command.append("--minimal")
    return command


def planned_installer_action(root: Path | None = None, *, minimal: bool = True) -> RepairAction:
    command = installer_command(root, minimal=minimal)
    return RepairAction("installer", "dry-run", shlex.join(command))


def run_installer(root: Path | None = None, *, minimal: bool = True) -> list[RepairAction]:
    script = installer_path(root)
    if not script.exists():
        return [RepairAction("installer", "error", f"missing: {script}")]
    command = installer_command(root, minimal=minimal)
    result = subprocess.run(command, capture_output=True, text=True, cwd=str(script.parent))
    detail = shlex.join(command)
    if result.stdout.strip():
        detail = f"{detail}\n{result.stdout.strip()[-1200:]}"
    if result.stderr.strip():
        detail = f"{detail}\nstderr:\n{result.stderr.strip()[-1200:]}"
    status = "ok" if result.returncode == 0 else "error"
    return [RepairAction("installer", status, detail)]


def repair_memory_cli_shim(
    root: Path | None = None,
    *,
    dry_run: bool = False,
) -> list[RepairAction]:
    target = venv_memory_path(root)
    shim = memory_shim_path()
    if not target.exists():
        return [RepairAction("memory CLI shim", "error", f"venv memory missing: {target}")]

    body = f'#!/bin/sh\nexec "{target}" "$@"\n'
    if shim.exists():
        try:
            if shim.read_text(encoding="utf-8", errors="replace") == body:
                return [RepairAction("memory CLI shim", "ok", f"already points to {target}")]
        except OSError as exc:
            return [RepairAction("memory CLI shim", "error", f"cannot read {shim}: {exc}")]

    if dry_run:
        return [RepairAction("memory CLI shim", "dry-run", f"would write {shim} -> {target}")]

    try:
        shim.parent.mkdir(parents=True, exist_ok=True)
        tmp = shim.with_suffix(shim.suffix + ".tmp")
        tmp.write_text(body, encoding="utf-8")
        tmp.chmod(0o755)
        tmp.replace(shim)
        shim.chmod(0o755)
    except OSError as exc:
        return [RepairAction("memory CLI shim", "error", f"cannot write {shim}: {exc}")]
    return [RepairAction("memory CLI shim", "fixed", f"{shim} -> {target}")]


def repair_adapters(
    brain_dir: Path,
    *,
    adapters: tuple[str, ...] = CORE_HOOK_ADAPTERS,
    dry_run: bool = False,
) -> list[RepairAction]:
    if dry_run:
        return [
            RepairAction(f"{name} adapter", "dry-run", f"would run: memory adapter install {name}")
            for name in adapters
        ]

    from agent_brain.agent_integrations import discover_adapters
    from agent_brain.agent_integrations.registry import get_adapter, resolve_adapter_name

    discover_adapters()
    actions: list[RepairAction] = []
    for name in adapters:
        try:
            canonical_name, _alias_used = resolve_adapter_name(name)
            adapter = get_adapter(canonical_name, brain_dir)
            detail = adapter.install()
        except Exception as exc:  # keep doctor --fix fail-open across optional clients
            actions.append(RepairAction(f"{name} adapter", "error", str(exc)))
            continue
        actions.append(RepairAction(f"{canonical_name} adapter", "fixed", detail))
    return actions


def repair_installation(
    brain_dir: Path,
    *,
    root: Path | None = None,
    adapters: tuple[str, ...] = CORE_HOOK_ADAPTERS,
    dry_run: bool = False,
) -> list[RepairAction]:
    actions = repair_memory_cli_shim(root, dry_run=dry_run)
    actions.extend(repair_adapters(brain_dir, adapters=adapters, dry_run=dry_run))
    return actions


def has_failures(actions: list[RepairAction]) -> bool:
    return any(action.failed for action in actions)


__all__ = [
    "CORE_HOOK_ADAPTERS",
    "RepairAction",
    "has_failures",
    "installer_command",
    "installer_path",
    "memory_shim_path",
    "planned_installer_action",
    "repair_adapters",
    "repair_installation",
    "repair_memory_cli_shim",
    "repo_root",
    "run_installer",
    "venv_memory_path",
]
