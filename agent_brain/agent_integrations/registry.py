"""Adapter registry for M4 Self-evolve architecture."""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass
from pathlib import Path
from typing import Type

from . import AdapterBase


@dataclass(frozen=True)
class AdapterMetadata:
    key: str
    display_names: tuple[str, ...]
    aliases: tuple[str, ...] = ()


# Registry mapping agent name to adapter class
ADAPTER_REGISTRY: dict[str, Type[AdapterBase]] = {}
ADAPTER_METADATA: dict[str, AdapterMetadata] = {}
ADAPTER_ALIASES: dict[str, str] = {}

# Submodules in this package that are not adapters; auto-discovery skips them.
_NON_ADAPTER_MODULES = {"registry"}

# Set once discover_adapters() has imported every adapter submodule.
_DISCOVERED = False


def discover_adapters(force: bool = False) -> list[str]:
    """Import every adapter submodule in this package so each module-level
    ``register_adapter(...)`` runs and populates ADAPTER_REGISTRY.

    Idempotent and cheap to call repeatedly (guarded by ``_DISCOVERED``).
    Returns the sorted list of registered adapter names.
    """
    global _DISCOVERED
    if not _DISCOVERED or force:
        package = importlib.import_module(__package__)
        for mod in pkgutil.iter_modules(package.__path__):
            if mod.ispkg or mod.name in _NON_ADAPTER_MODULES or mod.name.startswith("_"):
                continue
            importlib.import_module(f"{__package__}.{mod.name}")
        _DISCOVERED = True
    return sorted(ADAPTER_REGISTRY.keys())


def register_adapter(
    name: str,
    adapter_class: Type[AdapterBase],
    *,
    display_names: tuple[str, ...] | list[str] | None = None,
    aliases: tuple[str, ...] | list[str] = (),
) -> None:
    """Register an adapter class with a given name.
    
    Args:
        name: The agent name (e.g., 'claude_code', 'cursor')
        adapter_class: The adapter class to register
    """
    ADAPTER_REGISTRY[name] = adapter_class
    metadata = AdapterMetadata(
        key=name,
        display_names=tuple(display_names or (name,)),
        aliases=tuple(aliases),
    )
    ADAPTER_METADATA[name] = metadata
    for alias in metadata.aliases:
        existing = ADAPTER_ALIASES.get(alias)
        if existing is not None and existing != name:
            raise ValueError(
                f"Adapter alias '{alias}' already maps to '{existing}', cannot map to '{name}'"
            )
        ADAPTER_ALIASES[alias] = name


def resolve_adapter_name(name: str) -> tuple[str, str | None]:
    """Resolve a canonical adapter key or alias to (canonical, alias_used)."""
    discover_adapters()
    if name in ADAPTER_REGISTRY:
        return name, None
    canonical = ADAPTER_ALIASES.get(name)
    if canonical:
        return canonical, name
    available = ", ".join(sorted((*ADAPTER_REGISTRY.keys(), *ADAPTER_ALIASES.keys())))
    raise ValueError(f"Unknown adapter '{name}'. Available adapters: {available}")


def metadata_for_adapter(name: str) -> AdapterMetadata:
    """Return display metadata for a canonical adapter key or alias."""
    canonical, _alias_used = resolve_adapter_name(name)
    return ADAPTER_METADATA.get(
        canonical,
        AdapterMetadata(key=canonical, display_names=(canonical,), aliases=()),
    )


def get_adapter(name: str, brain_dir: Path) -> AdapterBase:
    """Get an adapter instance by name.
    
    Args:
        name: The agent name
        brain_dir: Path to the brain directory
        
    Returns:
        An instance of the requested adapter
        
    Raises:
        ValueError: If the adapter is not found
    """
    canonical, _alias_used = resolve_adapter_name(name)
    adapter_class = ADAPTER_REGISTRY[canonical]
    return adapter_class(brain_dir=brain_dir)


def list_adapters() -> list[str]:
    """List all registered adapter names.
    
    Returns:
        List of adapter names
    """
    discover_adapters()
    return sorted(ADAPTER_REGISTRY.keys())
