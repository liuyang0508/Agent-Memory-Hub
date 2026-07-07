"""MuleRun adapter — WIP.

No stable public MuleRun hook or MCP configuration contract is recorded in the
repo yet, so this remains a discoverable WIP adapter rather than an install
path based on guessed file locations.
"""

from pathlib import Path

from . import AdapterConfig, WIPAdapter
from .registry import register_adapter


class MuleRunAdapter(WIPAdapter):
    """Adapter placeholder for MuleRun once a stable install contract exists."""

    def get_config(self) -> AdapterConfig:
        return AdapterConfig(
            agent_name="mulerun",
            config_dir=Path.home() / ".mulerun",
            hook_type="file",
            inject_method="rules_file",
            supports_hooks=False,
            supports_mcp=False,
        )


register_adapter("mulerun", MuleRunAdapter)
