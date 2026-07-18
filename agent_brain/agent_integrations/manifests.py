"""Versioned, static capability manifests for agent adapters.

The manifest describes what an adapter implementation promises.  It does not
inspect user configuration or promote an adapter to verified; live truth is
projected separately by :mod:`agent_brain.agent_integrations.capabilities`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from agent_brain._version import __version__

from . import AdapterBase


MANIFEST_SCHEMA_VERSION = "amh-adapter-manifest/v1"
DEFAULT_EVIDENCE_TTL_SECONDS = 7 * 24 * 60 * 60


@dataclass(frozen=True)
class AdapterLifecycleCommands:
    install: str
    verify: str
    doctor: str
    repair: str
    upgrade: str
    uninstall: str


@dataclass(frozen=True)
class AdapterEvidencePolicy:
    runtime_types: tuple[str, ...]
    runtime_ttl_seconds: int
    context_ttl_seconds: int
    verification_ttl_seconds: int


@dataclass(frozen=True)
class AdapterManifest:
    schema_version: str
    adapter_id: str
    adapter_version: str
    platforms: tuple[str, ...]
    client_version_range: str
    hook_events: tuple[str, ...]
    payload_schema: str
    output_protocol: str
    channels: tuple[str, ...]
    lifecycle: AdapterLifecycleCommands
    evidence: AdapterEvidencePolicy
    feature_flag: str
    degrade_mode: str
    rollback_mode: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


_ALL_PLATFORMS = ("darwin", "linux", "windows")
_PLATFORMS_BY_ADAPTER: dict[str, tuple[str, ...]] = {
    "aone_copilot": ("darwin", "linux", "windows"),
    "wukong": _ALL_PLATFORMS,
}
_CLIENT_VERSION_RANGES: dict[str, str] = {
    # Client vendors do not all expose a stable semver contract.  Recording
    # that the range is not enforced is more truthful than inventing a floor.
    name: "not-enforced"
    for name in (
        "aider",
        "aone_copilot",
        "claude_code",
        "cline",
        "codex",
        "continue_dev",
        "cursor",
        "github_copilot",
        "hermes_agent",
        "mulerun",
        "openclaw",
        "openhuman",
        "opensquilla",
        "qoder",
        "qoder_work",
        "wukong",
    )
}
_OUTPUT_PROTOCOLS = {
    "claude_code": "claude-hook-json/v1",
    "codex": "codex-hook-json/v1",
    "qoder": "qoder-hook-json/v1",
    "qoder_work": "qoder-hook-json/v1",
}
_AWARENESS_ADAPTERS = {
    "aider",
    "aone_copilot",
    "claude_code",
    "codex",
    "github_copilot",
    "openhuman",
    "qoder",
    "qoder_work",
    "wukong",
}


def manifest_for_adapter(name: str, adapter: AdapterBase) -> AdapterManifest:
    """Build the static v1 manifest for one registered adapter."""

    cfg = adapter.get_config()
    hook_events = tuple(str(event) for event in getattr(adapter, "HOOK_EVENTS", ()))
    channels: list[str] = ["cli"]
    if name in _AWARENESS_ADAPTERS:
        channels.append("awareness")
    if cfg.supports_hooks:
        channels.append("hook")
    if cfg.supports_mcp:
        channels.append("mcp")
    commands = AdapterLifecycleCommands(
        install=f"memory adapter install {name}",
        verify=f"memory adapter verify {name}",
        doctor=f"memory adapter doctor {name}",
        repair=f"memory adapter repair {name}",
        upgrade=f"memory adapter upgrade {name}",
        uninstall=f"memory adapter uninstall {name}",
    )
    runtime_types: list[str] = []
    if cfg.supports_hooks:
        runtime_types.append("hook_event")
    if cfg.supports_mcp:
        runtime_types.append("mcp_probe")
    runtime_types.append("injection_cohort")
    return AdapterManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        adapter_id=name,
        adapter_version=__version__,
        platforms=_PLATFORMS_BY_ADAPTER.get(name, _ALL_PLATFORMS),
        client_version_range=_CLIENT_VERSION_RANGES[name],
        hook_events=hook_events,
        payload_schema="client-hook-payload/v1" if hook_events else "none",
        output_protocol=_OUTPUT_PROTOCOLS.get(name, "none"),
        channels=tuple(sorted(channels)),
        lifecycle=commands,
        evidence=AdapterEvidencePolicy(
            runtime_types=tuple(runtime_types),
            runtime_ttl_seconds=DEFAULT_EVIDENCE_TTL_SECONDS,
            context_ttl_seconds=DEFAULT_EVIDENCE_TTL_SECONDS,
            verification_ttl_seconds=DEFAULT_EVIDENCE_TTL_SECONDS,
        ),
        feature_flag=f"adapter.{name}.enabled",
        degrade_mode="disable-adapter-keep-core",
        rollback_mode="restore-hub-owned-snapshot",
    )


def manifests_for_all(brain_dir: Path) -> list[AdapterManifest]:
    """Return manifests for every registry entry in canonical order."""

    from . import discover_adapters
    from .registry import get_adapter

    return [
        manifest_for_adapter(name, get_adapter(name, brain_dir))
        for name in discover_adapters()
    ]


__all__ = [
    "AdapterEvidencePolicy",
    "AdapterLifecycleCommands",
    "AdapterManifest",
    "DEFAULT_EVIDENCE_TTL_SECONDS",
    "MANIFEST_SCHEMA_VERSION",
    "manifest_for_adapter",
    "manifests_for_all",
]
