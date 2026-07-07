"""GitHub Copilot adapter for repository custom instructions.

GitHub Copilot supports repository-level custom instructions at
``.github/copilot-instructions.md``. This adapter installs a small static
Agent Memory Hub discipline block there. It does not claim hooks, MCP, or
runtime tool access.
"""

from __future__ import annotations

from pathlib import Path

from . import AdapterBase, AdapterConfig
from .diagnostics import AdapterDiagnosticCheck, AdapterDiagnosticReport, overall_status
from .registry import register_adapter

INSTRUCTIONS_PATH = Path.cwd() / ".github" / "copilot-instructions.md"
BEGIN = "<!-- BEGIN agent-memory-hub -->"
END = "<!-- END agent-memory-hub -->"


def _block_end(content: str, start: int) -> int:
    end_idx = content.find(END, start)
    if end_idx == -1:
        return len(content)
    return end_idx + len(END)


class GitHubCopilotAdapter(AdapterBase):
    """Install-ready adapter for GitHub Copilot repository instructions."""

    def get_config(self) -> AdapterConfig:
        return AdapterConfig(
            agent_name="github_copilot",
            config_dir=INSTRUCTIONS_PATH.parent,
            hook_type="file",
            inject_method="rules_file",
            supports_hooks=False,
            supports_mcp=False,
        )

    def install(self) -> str:
        INSTRUCTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        block = self._build_block()

        if INSTRUCTIONS_PATH.exists():
            content = INSTRUCTIONS_PATH.read_text(encoding="utf-8")
            if BEGIN in content:
                start = content.index(BEGIN)
                end = _block_end(content, start)
                old_block = content[start:end]
                if old_block == block:
                    return (
                        "github copilot adapter: already installed "
                        f"(up-to-date) in {INSTRUCTIONS_PATH}"
                    )
                content = content[:start] + block + content[end:]
                _atomic_write(INSTRUCTIONS_PATH, content)
                return f"github copilot adapter: updated hub block in {INSTRUCTIONS_PATH}"
        else:
            content = ""

        if content and not content.endswith("\n"):
            content += "\n"
        content += block + "\n"
        _atomic_write(INSTRUCTIONS_PATH, content)
        return f"github copilot adapter: installed hub block in {INSTRUCTIONS_PATH}"

    def uninstall(self) -> str:
        if not INSTRUCTIONS_PATH.exists():
            return f"github copilot adapter: {INSTRUCTIONS_PATH} does not exist, nothing to remove"
        content = INSTRUCTIONS_PATH.read_text(encoding="utf-8")
        if BEGIN not in content:
            return "github copilot adapter: no hub block found, nothing to remove"

        start = content.index(BEGIN)
        end = _block_end(content, start)
        before = content[:start].rstrip("\n")
        after = content[end:].lstrip("\n")
        cleaned = before + ("\n" if before and after else "") + after
        _atomic_write(INSTRUCTIONS_PATH, cleaned)
        return f"github copilot adapter: removed hub block from {INSTRUCTIONS_PATH}"

    def inject_context(self, query: str) -> str:
        return (
            "# GitHub Copilot context is static repository instructions\n"
            f"# Instructions file: {INSTRUCTIONS_PATH}\n"
            f"# Query hint: {query}\n"
            f"# Data: {self.brain_dir}\n"
        )

    def get_install_instructions(self) -> str:
        return (
            "## GitHub Copilot Adapter\n\n"
            f"Writes an Agent Memory Hub block into `{INSTRUCTIONS_PATH}`.\n\n"
            "GitHub Copilot reads this as repository-level custom instructions.\n"
            "Re-running install is idempotent; uninstall removes only the hub block."
        )

    def diagnose(self) -> AdapterDiagnosticReport:
        checks = [self._diagnose_instructions()]
        return AdapterDiagnosticReport(
            adapter="github_copilot",
            overall_status=overall_status(checks),
            checks=checks,
        )

    def _build_block(self) -> str:
        lines = [
            BEGIN,
            "# Agent Memory Hub",
            "",
            "This repository uses Agent Memory Hub as a shared brain.",
            "Before non-trivial work, inspect `agent_runtime_kit/AGENT_MEMORY_DISCIPLINE.md`.",
            (
                "When tools are available, search memory with "
                "`agent_runtime_kit/tools/search-memory.sh \"<query>\"` before making plans."
            ),
            (
                "Record durable decisions, artifacts, and handoffs with "
                "`agent_runtime_kit/tools/write-memory.sh` when they meet the memory discipline."
            ),
            "",
            f"Brain directory: `{self._brain_dir_hint()}`",
            "",
            "Limit: this is static repository guidance, not a hook or MCP runtime.",
            END,
        ]
        return "\n".join(lines)

    def _brain_dir_hint(self) -> str:
        try:
            home = Path.home().resolve()
            brain = self.brain_dir.expanduser().resolve()
            return f"~/{brain.relative_to(home)}"
        except (OSError, ValueError):
            return str(self.brain_dir)

    def _diagnose_instructions(self) -> AdapterDiagnosticCheck:
        if not INSTRUCTIONS_PATH.exists():
            return AdapterDiagnosticCheck(
                name="GitHub Copilot instructions",
                status="error",
                detail=f"missing: {INSTRUCTIONS_PATH}",
                fix="run: memory adapter install github_copilot",
            )

        content = INSTRUCTIONS_PATH.read_text(encoding="utf-8")
        if BEGIN not in content or END not in content:
            return AdapterDiagnosticCheck(
                name="GitHub Copilot instructions",
                status="error",
                detail=f"hub sentinel block missing or incomplete: {INSTRUCTIONS_PATH}",
                fix="run: memory adapter install github_copilot",
            )

        block = content[content.index(BEGIN):_block_end(content, content.index(BEGIN))]
        if self._brain_dir_hint() not in block:
            return AdapterDiagnosticCheck(
                name="GitHub Copilot instructions",
                status="error",
                detail="hub block points to a different brain directory",
                fix="run: memory adapter install github_copilot",
            )

        return AdapterDiagnosticCheck(
            name="GitHub Copilot instructions",
            status="ok",
            detail=f"hub block present in {INSTRUCTIONS_PATH}",
        )


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


register_adapter("github_copilot", GitHubCopilotAdapter)
