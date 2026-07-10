"""Runtime event evidence for adapter truth-contract reporting.

The event log records only mechanical hook facts. It intentionally avoids prompt
text, memory bodies, tool arguments, and other high-risk content.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from agent_brain.platform.bounded_jsonl import iter_bounded_jsonl


RUNTIME_EVENTS_RELATIVE_PATH = "runtime/adapter-events.jsonl"


@dataclass(frozen=True)
class AdapterRuntimeEvent:
    adapter: str
    event_name: str
    timestamp: str
    session_id: str | None = None
    cwd: str | None = None
    source: str = "hook"

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


@dataclass(frozen=True)
class AdapterRuntimeSummary:
    observed: bool
    count: int
    last_event: dict[str, str | None] | None


def runtime_events_path(brain_dir: Path) -> Path:
    return Path(brain_dir) / RUNTIME_EVENTS_RELATIVE_PATH


def record_runtime_event(
    brain_dir: Path,
    *,
    adapter: str,
    event_name: str,
    session_id: str | None = None,
    cwd: str | None = None,
    source: str = "hook",
    now: datetime | None = None,
) -> AdapterRuntimeEvent:
    timestamp = _timestamp(now)
    event = AdapterRuntimeEvent(
        adapter=adapter,
        event_name=event_name,
        timestamp=timestamp,
        session_id=session_id or None,
        cwd=cwd or None,
        source=source,
    )
    path = runtime_events_path(brain_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    return event


def iter_runtime_events(
    brain_dir: Path,
    *,
    adapter: str | None = None,
    limit: int | None = None,
) -> Iterator[AdapterRuntimeEvent]:
    path = runtime_events_path(brain_dir)
    events: list[AdapterRuntimeEvent] = []
    for data in iter_bounded_jsonl(path):
        try:
            event = AdapterRuntimeEvent(
                adapter=str(data["adapter"]),
                event_name=str(data["event_name"]),
                timestamp=str(data["timestamp"]),
                session_id=data.get("session_id"),
                cwd=data.get("cwd"),
                source=str(data.get("source") or "hook"),
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if adapter and event.adapter != adapter:
            continue
        events.append(event)
    if limit is not None:
        events = events[-limit:]
    return iter(events)


def runtime_event_summary(brain_dir: Path, adapter: str) -> AdapterRuntimeSummary:
    events = list(iter_runtime_events(brain_dir, adapter=adapter))
    last = events[-1].to_dict() if events else None
    return AdapterRuntimeSummary(
        observed=bool(events),
        count=len(events),
        last_event=last,
    )


def _timestamp(now: datetime | None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record adapter runtime evidence.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    record_parser = subparsers.add_parser("record")
    record_parser.add_argument("--brain-dir", type=Path, default=Path.home() / ".agent-memory-hub")
    record_parser.add_argument("--adapter", required=True)
    record_parser.add_argument("--event", required=True)
    record_parser.add_argument("--session")
    record_parser.add_argument("--cwd")
    record_parser.add_argument("--source", default="hook")
    args = parser.parse_args(argv)

    if args.command == "record":
        event = record_runtime_event(
            args.brain_dir,
            adapter=args.adapter,
            event_name=args.event,
            session_id=args.session,
            cwd=args.cwd,
            source=args.source,
        )
        print(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
