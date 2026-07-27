"""Opt-in, privacy-bounded product telemetry for the public adoption dashboard.

The core remains network-free unless the user explicitly enables this feature
with ``AMH_TELEMETRY=1`` or ``python -m ... enable``.  Payloads never contain
usernames, paths, prompts, memory content, session IDs, or IP addresses.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from agent_brain._version import __version__
from agent_brain.memory.governance.audit.outbound import OutboundEvent, log_outbound_event


DEFAULT_ENDPOINT = "https://aihub0508.com/api/v1/telemetry/event"
ACTIVE_INTERVAL_SECONDS = 60 * 60
_SAFE_VALUE = re.compile(r"^[a-zA-Z0-9_.+-]{1,64}$")
_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def _brain_dir() -> Path:
    return Path(os.environ.get("BRAIN_DIR", "~/.agent-memory-hub")).expanduser()


def _config_path() -> Path:
    return _brain_dir() / "product-telemetry.json"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _read_config() -> dict[str, Any]:
    try:
        data = json.loads(_config_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_config(config: dict[str, Any]) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(payload)
    os.replace(temporary, path)
    path.chmod(0o600)


def _env_preference() -> bool | None:
    raw = os.environ.get("AMH_TELEMETRY")
    if raw is None or not raw.strip():
        return None
    normalized = raw.strip().lower()
    if normalized in _TRUTHY:
        return True
    if normalized in _FALSY:
        return False
    return None


def configure_from_environment() -> dict[str, Any]:
    """Apply an explicit environment preference and preserve prior consent otherwise."""

    config = _read_config()
    preference = _env_preference()
    if preference is not None:
        config["enabled"] = preference
    if preference is True and not _valid_anonymous_id(config.get("anonymous_id")):
        config["anonymous_id"] = uuid.uuid4().hex
    if preference is not None:
        config["updated_at"] = _utc_now().isoformat()
        _write_config(config)
    return config


def enable() -> dict[str, Any]:
    config = _read_config()
    config["enabled"] = True
    if not _valid_anonymous_id(config.get("anonymous_id")):
        config["anonymous_id"] = uuid.uuid4().hex
    config["updated_at"] = _utc_now().isoformat()
    _write_config(config)
    return config


def disable() -> dict[str, Any]:
    config = _read_config()
    config["enabled"] = False
    config["updated_at"] = _utc_now().isoformat()
    _write_config(config)
    return config


def status() -> dict[str, Any]:
    config = configure_from_environment()
    return {
        "enabled": config.get("enabled") is True,
        "anonymous_id": _display_id(config.get("anonymous_id")),
        "endpoint": _endpoint(),
        "last_install_at": config.get("last_install_at"),
        "last_active_at": config.get("last_active_at"),
    }


def emit(event: str, *, channel: str = "unknown", adapter: str = "unknown") -> bool:
    """Send one bounded event, returning ``False`` for disabled or failed delivery."""

    if event not in {"install", "active"}:
        raise ValueError(f"unsupported telemetry event: {event}")
    config = configure_from_environment()
    if config.get("enabled") is not True:
        return False
    anonymous_id = config.get("anonymous_id")
    if not _valid_anonymous_id(anonymous_id):
        anonymous_id = uuid.uuid4().hex
        config["anonymous_id"] = anonymous_id

    now = _utc_now()
    timestamp_key = f"last_{event}_at"
    if event == "active" and not _interval_elapsed(config.get(timestamp_key), now):
        return False

    # Reserve the interval before network I/O so concurrent hooks do not fan out.
    config[timestamp_key] = now.isoformat()
    _write_config(config)

    payload = {
        "schema_version": 1,
        "anonymous_id": anonymous_id,
        "event": event,
        "product_version": _safe_value(__version__),
        "platform": _safe_value(platform.system().lower()),
        "architecture": _safe_value(platform.machine().lower()),
        "channel": _safe_value(channel),
        "adapter": _safe_value(adapter),
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    request = urllib.request.Request(
        _endpoint(),
        data=encoded,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": f"agent-memory-hub/{__version__}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=2.0) as response:
            delivered = 200 <= int(response.status) < 300
    except (OSError, ValueError, urllib.error.URLError):
        return False
    if delivered:
        _log_outbound(len(encoded))
    return delivered


def _endpoint() -> str:
    candidate = os.environ.get("AMH_TELEMETRY_ENDPOINT", DEFAULT_ENDPOINT).strip()
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return DEFAULT_ENDPOINT
    return candidate


def _valid_anonymous_id(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) is not None


def _display_id(value: object) -> str | None:
    return f"anon-{value[:8]}" if isinstance(value, str) and _valid_anonymous_id(value) else None


def _safe_value(value: object) -> str:
    normalized = str(value or "unknown").strip().lower()
    return normalized if _SAFE_VALUE.fullmatch(normalized) else "unknown"


def _interval_elapsed(value: object, now: datetime) -> bool:
    if not isinstance(value, str):
        return True
    try:
        previous = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return True
    return (now - previous.astimezone(timezone.utc)).total_seconds() >= ACTIVE_INTERVAL_SECONDS


def _log_outbound(size_bytes: int) -> None:
    destination = urlparse(_endpoint()).hostname or "unknown"
    try:
        log_outbound_event(
            OutboundEvent(
                timestamp=_utc_now().isoformat(),
                destination=destination,
                payload_type="anonymous_product_telemetry",
                size_bytes=size_bytes,
                source_tool="agent-memory-hub product telemetry",
                approved_by="local opt-in",
            )
        )
    except OSError:
        pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage anonymous Agent Memory Hub telemetry")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("enable")
    subparsers.add_parser("disable")
    subparsers.add_parser("status")
    for command in ("install", "active"):
        event_parser = subparsers.add_parser(command)
        event_parser.add_argument("--channel", default=os.environ.get("AMH_INSTALL_SOURCE", "unknown"))
        event_parser.add_argument("--adapter", default=os.environ.get("AGENT_MEMORY_HUB_ADAPTER", "unknown"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "enable":
        config = enable()
        print(json.dumps({"enabled": True, "anonymous_id": _display_id(config["anonymous_id"])}))
        return 0
    if args.command == "disable":
        disable()
        print(json.dumps({"enabled": False}))
        return 0
    if args.command == "status":
        print(json.dumps(status(), ensure_ascii=False, sort_keys=True))
        return 0
    emit(args.command, channel=args.channel, adapter=args.adapter)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
