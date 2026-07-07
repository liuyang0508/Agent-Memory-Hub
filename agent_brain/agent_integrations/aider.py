"""Aider adapter — writes a brain-pool read-file directive into
``~/.aider.conf.yml`` so every aider session loads the brain context.

Aider reads ``~/.aider.conf.yml`` (global) for CLI defaults.  We add a
``read:`` entry pointing at a generated digest file that summarises the
brain pool, giving aider context without MCP.

Install is idempotent.  Uninstall removes only our entries.
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import AdapterBase, AdapterConfig
from .aider_config import _atomic_write_yaml, _read_yaml
from .aider_diagnostics import diagnose_digest, diagnose_read_directive
from .diagnostics import AdapterDiagnosticReport, overall_status
from .registry import register_adapter

AIDER_CONF = Path.home() / ".aider.conf.yml"
DIGEST_FILENAME = "aider_brain_digest.md"


class AiderAdapter(AdapterBase):
    """Real-install adapter for Aider via .aider.conf.yml read-file injection."""

    def __init__(self, brain_dir: Path, repo_dir: Path | None = None):
        super().__init__(brain_dir)
        self.repo_dir = repo_dir or Path(__file__).resolve().parents[2]
        self.digest_path = self.brain_dir / DIGEST_FILENAME

    def get_config(self) -> AdapterConfig:
        return AdapterConfig(
            agent_name="aider",
            config_dir=Path.home() / ".aider",
            hook_type="file",
            inject_method="system_prompt",
            supports_hooks=False,
            supports_mcp=False,
        )

    def install(self) -> str:
        self._ensure_digest()
        config = _read_yaml(AIDER_CONF)

        read_list = config.get("read", [])
        if not isinstance(read_list, list):
            read_list = [read_list] if read_list else []

        digest_str = str(self.digest_path)
        if digest_str in read_list:
            return f"aider adapter: already installed in {AIDER_CONF}"

        read_list.append(digest_str)
        config["read"] = read_list
        _atomic_write_yaml(AIDER_CONF, config)
        return f"aider adapter: installed read-file directive in {AIDER_CONF}"

    def uninstall(self) -> str:
        if not AIDER_CONF.exists():
            return f"aider adapter: {AIDER_CONF} does not exist, nothing to remove"
        config = _read_yaml(AIDER_CONF)
        read_list = config.get("read", [])
        if not isinstance(read_list, list):
            return "aider adapter: no read list, nothing to remove"

        digest_str = str(self.digest_path)
        if digest_str not in read_list:
            return "aider adapter: no hub entry in read list, nothing to remove"

        read_list.remove(digest_str)
        if read_list:
            config["read"] = read_list
        else:
            del config["read"]
        _atomic_write_yaml(AIDER_CONF, config)
        return f"aider adapter: removed read-file directive from {AIDER_CONF}"

    def inject_context(self, query: str) -> str:
        return (
            f"# Aider context injection via brain digest\n"
            f"# Read file: {self.digest_path}\n"
            f"# Query hint: {query}\n"
            f"# Data: {self.brain_dir}\n"
        )

    def get_install_instructions(self) -> str:
        return (
            "## Aider Adapter\n\n"
            f"Adds a `read:` entry in `{AIDER_CONF}` pointing to a brain-pool\n"
            f"digest file at `{self.digest_path}`.\n\n"
            "This gives aider read-only context from the brain pool on every session.\n\n"
            "Run programmatically:\n\n"
            "    from agent_brain.agent_integrations.aider import AiderAdapter\n"
            "    AiderAdapter(brain_dir=Path.home() / '.agent-memory-hub').install()\n\n"
            "Idempotent — re-running is a no-op if already present.\n"
            "To remove: call `.uninstall()`."
        )

    def diagnose(self) -> AdapterDiagnosticReport:
        checks = [
            diagnose_read_directive(AIDER_CONF, self.digest_path),
            diagnose_digest(self.digest_path),
        ]
        return AdapterDiagnosticReport(
            adapter="aider",
            overall_status=overall_status(checks),
            checks=checks,
        )

    def _ensure_digest(self) -> None:
        """Write a minimal digest file so aider has something to read."""
        self.digest_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.digest_path.exists():
            self.digest_path.write_text(
                "# Agent Memory Hub — Brain Digest (auto-generated)\n\n"
                "This file is managed by agent-memory-hub.\n"
                "It provides context from the shared brain pool.\n\n"
                f"Brain directory: {self.brain_dir}\n"
                f"Search tool: {sys.executable} -m agent_brain search <query>\n",
                encoding="utf-8",
            )


register_adapter("aider", AiderAdapter)
