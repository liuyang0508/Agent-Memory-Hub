"""Outbound event logging for audit trail."""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
from typing import List


@dataclass
class OutboundEvent:
    """Represents an outbound data transfer event."""
    timestamp: str  # ISO format datetime string
    destination: str  # Where data was sent (e.g., "github.com", "api.example.com")
    payload_type: str  # Type of payload (e.g., "memory_item", "skill_file", "config")
    size_bytes: int  # Size of the payload in bytes
    source_tool: str  # Tool that initiated the transfer (e.g., "memory write", "skill sync")
    approved_by: str | None = None  # User who approved the transfer (if applicable)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "OutboundEvent":
        """Create from dictionary."""
        return cls(**data)


def _get_audit_log_dir() -> Path:
    """Get the audit log directory path."""
    brain_dir = Path.home() / ".agent-memory-hub"
    audit_dir = brain_dir / "audit-log"
    return audit_dir


def log_outbound_event(event: OutboundEvent) -> Path:
    """Log an outbound event to the audit log directory.

    Args:
        event: The outbound event to log.

    Returns:
        Path to the created JSON file.
    """
    audit_dir = _get_audit_log_dir()
    audit_dir.mkdir(parents=True, exist_ok=True)

    # Generate filename based on timestamp
    ts = datetime.fromisoformat(event.timestamp)
    filename = f"outbound-{ts.strftime('%Y%m%d-%H%M%S')}-{event.destination.replace('.', '_')}.json"
    filepath = audit_dir / filename

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(event.to_dict(), f, indent=2, ensure_ascii=False)

    return filepath


def list_outbound_events(since_days: int = 30) -> List[OutboundEvent]:
    """List outbound events from the last N days.

    Args:
        since_days: Number of days to look back (default: 30).

    Returns:
        List of OutboundEvent objects, sorted by timestamp descending.
    """
    audit_dir = _get_audit_log_dir()

    if not audit_dir.exists():
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    events = []

    for filepath in audit_dir.glob("outbound-*.json"):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
                event = OutboundEvent.from_dict(data)

                # Parse timestamp and check if within range
                event_ts = datetime.fromisoformat(event.timestamp)
                if event_ts >= cutoff:
                    events.append(event)
        except (json.JSONDecodeError, ValueError, KeyError):
            # Skip malformed files
            continue

    # Sort by timestamp descending (most recent first)
    events.sort(key=lambda e: e.timestamp, reverse=True)
    return events
