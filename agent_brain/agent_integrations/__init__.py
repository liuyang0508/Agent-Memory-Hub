"""Agent adapters for M4 Self-evolve architecture."""

import importlib
import pkgutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AdapterConfig:
    """Configuration for an agent adapter."""
    agent_name: str
    config_dir: Path          # agent 配置目录
    hook_type: str            # 'file' | 'command' | 'mcp' | 'env'
    inject_method: str        # 'system_prompt' | 'rules_file' | 'env_var' | 'mcp_tool'
    supports_hooks: bool      # agent 是否支持 hook
    supports_mcp: bool        # agent 是否支持 MCP


class AdapterBase(ABC):
    """Base class for all agent adapters."""

    def __init__(self, brain_dir: Path):
        self.brain_dir = brain_dir

    def owned_paths(self) -> tuple[Path, ...]:
        """Return files whose AMH-owned portions participate in rollback.

        Adapters that do not yet support transactional upgrade keep the empty
        default.  A returned config file may also contain user-owned entries;
        snapshots are private rollback material and uninstall remains
        responsible for removing only AMH-owned blocks.
        """

        return ()

    @abstractmethod
    def get_config(self) -> AdapterConfig:
        """Return the adapter configuration."""
        ...

    @abstractmethod
    def install(self) -> str:
        """Install adapter for this agent. Returns a human-readable result message."""
        ...

    @abstractmethod
    def inject_context(self, query: str) -> str:
        """Generate context injection text for a given query."""
        ...

    @abstractmethod
    def get_install_instructions(self) -> str:
        """Return human-readable install instructions."""
        ...


class WIPAdapter(AdapterBase):
    """Base class for adapters that have a plugin-architecture stub but no real
    install path yet. install() / inject_context() / get_install_instructions()
    all raise NotImplementedError with a clear message so users get a hard
    failure rather than a silent no-op string.

    Subclasses must still implement get_config() — that's needed by the
    registry for discovery / capability inspection even before install works.

    Promote an adapter out of WIP by inheriting AdapterBase directly and
    providing the three real methods. See claude_code.py / codex.py for
    reference implementations.
    """

    def install(self) -> str:
        agent = self.get_config().agent_name
        raise NotImplementedError(
            f"{agent} adapter: install() not yet implemented. "
            f"Current status: plugin-architecture stub from v1.0 M4. "
            f"Real hook/config-file install path is on the v1.2 roadmap. "
            f"Contributions welcome — see CONTRIBUTING.md and the reference "
            f"implementations in claude_code.py / codex.py."
        )

    def inject_context(self, query: str) -> str:
        agent = self.get_config().agent_name
        raise NotImplementedError(
            f"{agent} adapter: inject_context() not yet implemented. "
            f"Install path must land first (see install())."
        )

    def get_install_instructions(self) -> str:
        cfg = self.get_config()
        return (
            f"## {cfg.agent_name} Adapter — WIP\n\n"
            f"This adapter is a v1.0 M4 plugin-architecture stub. The real install\n"
            f"path is not yet implemented.\n\n"
            f"- Agent config dir (when implemented): `{cfg.config_dir}`\n"
            f"- Hook type: `{cfg.hook_type}`\n"
            f"- Inject method: `{cfg.inject_method}`\n"
            f"- Supports hooks: `{cfg.supports_hooks}`\n"
            f"- Supports MCP: `{cfg.supports_mcp}`\n\n"
            f"Calling `.install()` on this adapter raises NotImplementedError\n"
            f"(by design — silent no-op stubs caused confusion in v1.0).\n\n"
            f"Reference implementations are at\n"
            f"`agent_brain/agent_integrations/claude_code.py` and\n"
            f"`agent_brain/agent_integrations/codex.py`."
        )


def discover_adapters() -> list[str]:
    """Auto-discover and import every adapter submodule so that each module's
    ``register_adapter(...)`` call runs and populates the registry.

    Adapter modules register themselves purely as an import side effect (see
    the ``register_adapter(...)`` line at the bottom of e.g. claude_code.py /
    codex.py). Until something imports them ``list_adapters()`` is empty, so
    the CLI / MCP layers must call this once before listing or instantiating
    adapters.

    Idempotent: re-importing an already-loaded module is a no-op and
    register_adapter just re-sets the same key. Returns the sorted list of
    registered adapter names. Import errors are intentionally NOT swallowed so
    a broken adapter surfaces loudly rather than silently vanishing.
    """
    from .registry import list_adapters

    for module_info in pkgutil.iter_modules(__path__):
        if module_info.ispkg:
            continue
        name = module_info.name
        if name.startswith("_") or name == "registry":
            continue
        importlib.import_module(f"{__name__}.{name}")
    return list_adapters()


# Auto-discover and register every adapter submodule so that a bare
# `import agent_brain.agent_integrations` sees all adapters without
# callers importing each module by hand.
# Done last, after AdapterBase / AdapterConfig / WIPAdapter are defined, so
# the submodule imports inside discover_adapters() can resolve them.
from .registry import discover_adapters as _discover_adapters  # noqa: E402

_discover_adapters()
