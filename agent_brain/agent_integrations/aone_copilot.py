"""Aone Copilot adapter for the IntelliJ IDEA plugin.

Aone Copilot is not a standalone CLI agent on this machine; it is installed as
an IntelliJ IDEA plugin.  The current integration therefore writes a JetBrains
configuration sidecar that gives the plugin/user an AMH Awareness Channel.

This adapter deliberately does not claim hooks or MCP runtime access.  If the
plugin later exposes an MCP/tool bridge, that should become a separate
tool-channel implementation behind this same adapter key.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import AdapterBase, AdapterConfig
from .awareness import (
    diagnose_awareness_block,
    install_awareness_block,
    render_awareness_block,
    uninstall_awareness_block,
)
from .diagnostics import AdapterDiagnosticCheck, AdapterDiagnosticReport, overall_status
from .registry import register_adapter

SERVER_NAME = "agent-memory-hub"
SIDECAR_FILENAME = "agent-memory-hub-aone-copilot.md"
AONE_PLUGIN_DIR_NAMES = ("aone-copilot-idea", "Aone-Idea")
IDEA_CONFIG_PREFIXES = ("IntelliJIdea", "IdeaIC")


def _default_jetbrains_config_root() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "JetBrains"
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "JetBrains"
    return Path.home() / ".config" / "JetBrains"


JETBRAINS_CONFIG_ROOT = _default_jetbrains_config_root()


class AoneCopilotAdapter(AdapterBase):
    """Install-ready adapter for Aone Copilot inside IntelliJ IDEA."""

    def get_config(self) -> AdapterConfig:
        return AdapterConfig(
            agent_name="aone_copilot",
            config_dir=JETBRAINS_CONFIG_ROOT,
            hook_type="file",
            inject_method="rules_file",
            supports_hooks=False,
            supports_mcp=False,
        )

    def install(self) -> str:
        idea_config = _select_idea_config()
        if idea_config is None:
            raise FileNotFoundError(
                f"IntelliJ IDEA config directory not found under {JETBRAINS_CONFIG_ROOT}"
            )
        sidecar = _sidecar_path(idea_config)
        changed = install_awareness_block(sidecar, self._awareness_block())
        plugin_msg = _plugin_detail(idea_config)
        if changed:
            return (
                f"aone_copilot adapter: installed awareness sidecar in {sidecar} | "
                f"{plugin_msg}"
            )
        return (
            f"aone_copilot adapter: awareness sidecar already installed in {sidecar} | "
            f"{plugin_msg}"
        )

    def uninstall(self) -> str:
        idea_config = _select_idea_config()
        if idea_config is None:
            return (
                "aone_copilot adapter: IntelliJ IDEA config directory not found, "
                "nothing to remove"
            )
        sidecar = _sidecar_path(idea_config)
        if uninstall_awareness_block(sidecar):
            if sidecar.exists() and not sidecar.read_text(encoding="utf-8").strip():
                sidecar.unlink()
            return f"aone_copilot adapter: removed awareness sidecar from {sidecar}"
        return f"aone_copilot adapter: no awareness sidecar found in {sidecar}"

    def inject_context(self, query: str) -> str:
        idea_config = _select_idea_config()
        sidecar = _sidecar_path(idea_config) if idea_config is not None else None
        return (
            "# Aone Copilot context is exposed through an IntelliJ IDEA sidecar\n"
            f"# Sidecar: {sidecar or '<IntelliJ IDEA config not found>'}\n"
            f"# Query hint: {query}\n"
            f"# Data: {self.brain_dir}\n"
        )

    def get_install_instructions(self) -> str:
        return (
            "## Aone Copilot Adapter\n\n"
            "Aone Copilot is treated as an IntelliJ IDEA plugin, not a standalone "
            "CLI agent. The adapter installs an AMH Awareness Channel sidecar into "
            "the active JetBrains IntelliJ IDEA config directory:\n\n"
            f"    <JetBrains>/IntelliJIdea*/options/{SIDECAR_FILENAME}\n\n"
            "Run:\n\n"
            "    memory adapter install aone_copilot\n\n"
            "This does not claim hooks or MCP until the plugin exposes a verified "
            "tool bridge."
        )

    def diagnose(self) -> AdapterDiagnosticReport:
        idea_config = _select_idea_config()
        checks = [
            _diagnose_plugin(idea_config),
            self._diagnose_sidecar(idea_config),
        ]
        return AdapterDiagnosticReport(
            adapter="aone_copilot",
            overall_status=overall_status(checks),
            checks=checks,
            brain_dir=self.brain_dir,
        )

    def _awareness_block(self) -> str:
        return render_awareness_block(
            agent_name="IntelliJ IDEA Aone Copilot",
            brain_dir=self.brain_dir,
            tool_channel=(
                "JetBrains IntelliJ IDEA plugin sidecar; no AMH hook/MCP bridge is verified yet"
            ),
            mcp_tools_available=False,
            extra_guidance=(
                "Aone Copilot is detected from the IntelliJ IDEA plugin directory.",
                "Use this sidecar as the static awareness layer until the plugin exposes a real tool channel.",
            ),
        )

    def _diagnose_sidecar(self, idea_config: Path | None) -> AdapterDiagnosticCheck:
        if idea_config is None:
            return AdapterDiagnosticCheck(
                name="Aone Copilot awareness sidecar",
                status="error",
                detail=f"IntelliJ IDEA config directory not found under {JETBRAINS_CONFIG_ROOT}",
                fix="install IntelliJ IDEA/Aone Copilot plugin, then run: memory adapter install aone_copilot",
            )
        return diagnose_awareness_block(
            check_name="Aone Copilot awareness sidecar",
            path=_sidecar_path(idea_config),
            brain_dir=self.brain_dir,
            install_command="memory adapter install aone_copilot",
        )


def _sidecar_path(idea_config: Path) -> Path:
    return idea_config / "options" / SIDECAR_FILENAME


def _idea_config_dirs() -> list[Path]:
    if not JETBRAINS_CONFIG_ROOT.exists():
        return []
    configs = [
        path
        for path in JETBRAINS_CONFIG_ROOT.iterdir()
        if path.is_dir() and path.name.startswith(IDEA_CONFIG_PREFIXES)
    ]
    return sorted(configs, key=lambda path: path.name, reverse=True)


def _select_idea_config() -> Path | None:
    configs = _idea_config_dirs()
    if not configs:
        return None
    with_plugin = [config for config in configs if _has_aone_plugin(config)]
    if with_plugin:
        return with_plugin[0]
    return configs[0]


def _has_aone_plugin(idea_config: Path) -> bool:
    plugins = idea_config / "plugins"
    return any((plugins / name).exists() for name in AONE_PLUGIN_DIR_NAMES)


def _plugin_detail(idea_config: Path) -> str:
    plugins = idea_config / "plugins"
    for name in AONE_PLUGIN_DIR_NAMES:
        path = plugins / name
        if path.exists():
            return f"Aone Copilot IntelliJ plugin detected at {path}"
    return f"Aone Copilot IntelliJ plugin not detected under {plugins}"


def _diagnose_plugin(idea_config: Path | None) -> AdapterDiagnosticCheck:
    if idea_config is None:
        return AdapterDiagnosticCheck(
            name="Aone Copilot IntelliJ plugin",
            status="error",
            detail=f"IntelliJ IDEA config directory not found under {JETBRAINS_CONFIG_ROOT}",
            fix="install IntelliJ IDEA and the Aone Copilot IDEA plugin",
        )
    if _has_aone_plugin(idea_config):
        return AdapterDiagnosticCheck(
            name="Aone Copilot IntelliJ plugin",
            status="ok",
            detail=_plugin_detail(idea_config),
        )
    return AdapterDiagnosticCheck(
        name="Aone Copilot IntelliJ plugin",
        status="error",
        detail=_plugin_detail(idea_config),
        fix="install/enable the Aone Copilot plugin in IntelliJ IDEA",
    )


register_adapter(
    "aone_copilot",
    AoneCopilotAdapter,
    display_names=("Aone Copilot", "IntelliJ IDEA Aone Copilot"),
    aliases=("aone_idea", "idea_aone_copilot"),
)
