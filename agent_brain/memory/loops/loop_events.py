from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from agent_brain.memory.loops.loop_types import LoopEvent
from agent_brain.platform.bounded_jsonl import iter_bounded_jsonl


LOOP_EVENTS_RELATIVE_PATH = "runtime/loop-events.jsonl"


def loop_events_path(brain_dir: Path) -> Path:
    return Path(brain_dir) / LOOP_EVENTS_RELATIVE_PATH


def append_loop_event(brain_dir: Path, event: LoopEvent) -> None:
    path = loop_events_path(brain_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")


def iter_loop_events(brain_dir: Path, *, loop_id: str | None = None) -> Iterator[LoopEvent]:
    path = loop_events_path(brain_dir)
    for data in iter_bounded_jsonl(path):
        try:
            event = LoopEvent.from_dict(data)
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if loop_id and event.loop_id != loop_id:
            continue
        yield event
