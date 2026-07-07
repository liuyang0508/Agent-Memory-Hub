"""Evidence registry for adapter support-level claims."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


SupportLevel = Literal["verified", "install-ready", "docs-only", "wip"]


@dataclass(frozen=True)
class AdapterEvidence:
    support_level: SupportLevel
    limitations: tuple[str, ...]
    evidence_paths: tuple[str, ...] = ()
    evidence_level: SupportLevel | None = None


WIP_EVIDENCE = AdapterEvidence(
    support_level="wip",
    limitations=("install path not implemented",),
)

DEFAULT_READY_EVIDENCE = AdapterEvidence(
    support_level="install-ready",
    limitations=("real-client verification not recorded in capability matrix",),
)

ADAPTER_EVIDENCE: dict[str, AdapterEvidence] = {
    "aone_copilot": AdapterEvidence(
        support_level="install-ready",
        limitations=(
            "IntelliJ IDEA Aone Copilot plugin sidecar implemented; plugin runtime/tool bridge not verified",
        ),
        evidence_paths=(
            "tests/unit/test_adapters.py",
            "/Applications/IntelliJ IDEA Ultimate.app",
        ),
        evidence_level="install-ready",
    ),
    "aider": AdapterEvidence(
        support_level="install-ready",
        limitations=(
            "config doctor implemented; local Aider config missing; runtime not verified",
        ),
        evidence_paths=(
            "tests/unit/test_adapters.py",
            "tests/unit/test_cli_adapter.py",
            "agent_brain/agent_integrations/aider.py",
            "agent_brain/agent_integrations/aider_diagnostics.py",
        ),
        evidence_level="install-ready",
    ),
    "claude_code": AdapterEvidence(
        support_level="install-ready",
        limitations=(
            "real Claude Code config doctor passed; runtime hook event not recorded",
        ),
        evidence_paths=(
            "tests/unit/test_adapters.py",
            "tests/unit/test_cli_adapter.py",
            "agent_brain/agent_integrations/claude_code.py",
            "agent_brain/agent_integrations/claude_code_diagnostics.py",
        ),
        evidence_level="install-ready",
    ),
    "codex": AdapterEvidence(
        support_level="install-ready",
        limitations=("real Codex config doctor passed; hook runtime event not yet observed",),
        evidence_paths=(
            "tests/unit/test_adapters.py",
            "tests/unit/test_cli_adapter.py",
            "agent_brain/agent_integrations/codex.py",
            "agent_brain/agent_integrations/codex_hooks.py",
            "agent_brain/agent_integrations/codex_diagnostics.py",
        ),
        evidence_level="install-ready",
    ),
    "cline": AdapterEvidence(
        support_level="install-ready",
        limitations=(
            "config doctor implemented; local Cline MCP config missing; runtime not verified",
        ),
        evidence_paths=(
            "tests/unit/test_adapters.py",
            "tests/unit/test_cli_adapter.py",
            "agent_brain/agent_integrations/cline.py",
            "agent_brain/agent_integrations/mcp_config_diagnostics.py",
        ),
        evidence_level="install-ready",
    ),
    "continue_dev": AdapterEvidence(
        support_level="install-ready",
        limitations=(
            "official Continue global config.yaml MCP path implemented; real runtime not verified",
        ),
        evidence_paths=(
            "tests/unit/test_adapters.py",
            "tests/unit/test_cli_adapter.py",
            "agent_brain/agent_integrations/continue_dev.py",
        ),
        evidence_level="install-ready",
    ),
    "cursor": AdapterEvidence(
        support_level="install-ready",
        limitations=(
            "config doctor implemented; local Cursor MCP config malformed; runtime not verified",
        ),
        evidence_paths=(
            "tests/unit/test_adapters.py",
            "tests/unit/test_cli_adapter.py",
            "agent_brain/agent_integrations/cursor.py",
            "agent_brain/agent_integrations/mcp_config_diagnostics.py",
        ),
        evidence_level="install-ready",
    ),
    "hermes_agent": AdapterEvidence(
        support_level="install-ready",
        limitations=(
            "Hermes MCP server config installed; Hermes provider listing/upstream runtime not verified",
        ),
        evidence_paths=(
            "tests/unit/test_adapters.py",
            "agent_brain/agent_integrations/hermes/provider.py",
            "https://hermes-agent.nousresearch.com/docs/user-guide/features/tool-calling-mcp",
        ),
        evidence_level="install-ready",
    ),
    "github_copilot": AdapterEvidence(
        support_level="install-ready",
        limitations=(
            "repository-level copilot-instructions.md installer implemented; "
            "real Copilot runtime not verified",
        ),
        evidence_paths=(
            "tests/unit/test_adapters.py",
            "tests/unit/test_cli_adapter.py",
            "agent_brain/agent_integrations/github_copilot.py",
        ),
        evidence_level="install-ready",
    ),
    "openclaw": AdapterEvidence(
        support_level="install-ready",
        limitations=(
            "official OpenClaw MCP registry CLI path implemented; real runtime not verified",
        ),
        evidence_paths=(
            "tests/unit/test_adapters.py",
            "https://docs.openclaw.ai/cli/mcp",
        ),
        evidence_level="install-ready",
    ),
    "openhuman": AdapterEvidence(
        support_level="install-ready",
        limitations=(
            "agentmemory backend config bridge implemented; real OpenHuman runtime not verified",
        ),
        evidence_paths=(
            "tests/unit/test_adapters.py",
            "https://github.com/tinyhumansai/openhuman",
        ),
        evidence_level="install-ready",
    ),
    "opensquilla": AdapterEvidence(
        support_level="install-ready",
        limitations=(
            "OpenSquilla config.toml MCP entry implemented; real runtime not verified",
        ),
        evidence_paths=(
            "tests/unit/test_adapters.py",
            "https://github.com/opensquilla/opensquilla",
        ),
        evidence_level="install-ready",
    ),
    "wukong": AdapterEvidence(
        support_level="install-ready",
        limitations=(
            "scoped Wukong MCP install is implemented; verified requires a local Wukong CLI runtime probe record",
        ),
        evidence_paths=(
            "tests/unit/test_adapters.py",
            "tests/unit/test_adapter_robustness_p36.py",
            "tests/unit/test_cli_adapter.py",
            "agent_brain/agent_integrations/wukong.py",
            "<rewinddesktop-repo>/tauri-app/src-tauri/src/mcp/config.rs",
        ),
        evidence_level="install-ready",
    ),
    "qoder": AdapterEvidence(
        support_level="install-ready",
        limitations=(
            "official Qoder hooks, awareness path, and SharedClientCache MCP config implemented; AMH context effectiveness not verified",
        ),
        evidence_paths=(
            "tests/unit/test_adapters.py",
            "tests/unit/test_cli_adapter.py",
            "agent_brain/agent_integrations/qoder.py",
            "agent_brain/agent_integrations/qoder_diagnostics.py",
        ),
        evidence_level="install-ready",
    ),
    "qoder_work": AdapterEvidence(
        support_level="install-ready",
        limitations=(
            "QoderWork hooks, awareness/main, and custom MCP config implemented; verified requires transcript-level AMH context evidence",
        ),
        evidence_paths=(
            "tests/unit/test_adapters.py",
            "tests/unit/test_cli_adapter.py",
            "QoderWork built-in guide-mcp.md",
        ),
        evidence_level="install-ready",
    ),
}


def evidence_for_adapter(name: str, *, is_wip: bool) -> AdapterEvidence:
    if is_wip:
        return WIP_EVIDENCE
    return ADAPTER_EVIDENCE.get(name, DEFAULT_READY_EVIDENCE)
