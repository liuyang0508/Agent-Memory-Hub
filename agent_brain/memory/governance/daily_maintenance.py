"""At-most-once-per-day safe local governance runner for lifecycle hooks."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import errno
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import time
from collections.abc import Iterator

from agent_brain.memory.context.injection_closures import (
    CLOSED_BY_DAILY_MAINTENANCE,
    close_stale_cohorts,
)
from agent_brain.memory.governance.auto_governance import AutoGovernanceCycle
from agent_brain.memory.governance.outcome_feedback import (
    apply_task_outcome_feedback_batch,
)
from agent_brain.memory.recall.adaptive_learning import refresh_learning_profile
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.platform.indexing.index import HubIndex


@contextmanager
def _maintenance_lock(state_dir: Path) -> Iterator[bool]:
    """Kernel-owned, nonblocking lock; process death releases it automatically.

    Keep the inode in place so a contender cannot lock a replacement file while
    another process still holds the old one. One lock also covers UTC midnight.
    Legacy empty YYYY-MM-DD.lock files no longer decide whether work can run.
    """
    descriptor = os.open(state_dir / "maintenance.lock", os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            if os.name == "nt":
                getattr(msvcrt, "locking")(descriptor, getattr(msvcrt, "LK_NBLCK"), 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                raise
        yield acquired
    finally:
        # close releases flock / the Windows byte-range lock, including on error.
        os.close(descriptor)


def _event(path: Path, **fields: object) -> None:
    record = {"at": datetime.now(timezone.utc).isoformat(), "pid": os.getpid(), **fields}
    with os.fdopen(os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600), "a") as log:
        log.write(json.dumps(record, ensure_ascii=False) + "\n")


@contextmanager
def _stage(path: Path, name: str, timings: dict[str, float]) -> Iterator[None]:
    start = time.monotonic()
    _event(path, stage=name, status="started")
    try:
        yield
    except BaseException as exc:
        _event(path, stage=name, status="failed", error_type=type(exc).__name__,
               elapsed_seconds=round(time.monotonic() - start, 4))
        raise
    else:
        _event(path, stage=name, status="completed",
               elapsed_seconds=round(time.monotonic() - start, 4))
    finally:
        timings[name] = round(time.monotonic() - start, 4)


def launch_daily_maintenance(brain_dir: Path) -> dict[str, object]:
    """Schedule maintenance without retaining the host hook's pipes or session."""
    brain = Path(brain_dir).resolve()
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    state_dir = brain / "runtime" / "daily-governance"
    state_dir.mkdir(parents=True, exist_ok=True)
    if (state_dir / f"{day}.json").exists():
        return {"status": "already_completed", "day": day}
    # Worker exceptions must remain inspectable after the Stop hook has exited.
    with os.fdopen(os.open(state_dir / f"{day}.stderr.log",
                           os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600), "a") as log:
        options = ({"creationflags": getattr(subprocess, "DETACHED_PROCESS")
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP")}
                   if os.name == "nt" else {"start_new_session": True})
        try:
            worker = subprocess.Popen(
                [sys.executable, "-m", "agent_brain.memory.governance.daily_maintenance",
                 "--brain-dir", str(brain)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log,
                close_fds=True, **options,
            )
        except OSError as exc:
            _event(state_dir / f"{day}.events.jsonl", stage="launch", status="failed",
                   error_type=type(exc).__name__)
            raise
    return {"status": "scheduled", "day": day, "pid": worker.pid}


def run_daily_maintenance(brain_dir: Path) -> dict[str, object]:
    brain = Path(brain_dir)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    state_dir = brain / "runtime" / "daily-governance"
    state_dir.mkdir(parents=True, exist_ok=True)
    completed = state_dir / f"{day}.json"
    if completed.exists():
        return {"status": "already_completed", "day": day}
    with _maintenance_lock(state_dir) as acquired:
        if not acquired:
            return {"status": "already_running", "day": day}
        if completed.exists():
            return {"status": "already_completed", "day": day}
        return _run_maintenance(brain, day, completed)


def _run_maintenance(brain: Path, day: str, completed: Path) -> dict[str, object]:
    index = None
    events = completed.with_suffix(".events.jsonl")
    timings: dict[str, float] = {}
    try:
        with _stage(events, "index", timings):
            if (brain / "index.db").exists():
                index = HubIndex(brain / "index.db")
        items_store = ItemsStore(brain / "items")
        with _stage(events, "feedback", timings):
            feedback = apply_task_outcome_feedback_batch(
                brain, items_store=items_store, index=index,
            )
        with _stage(events, "cohort_closures", timings):
            closures = close_stale_cohorts(brain, closed_by=CLOSED_BY_DAILY_MAINTENANCE)
        with _stage(events, "learning", timings):
            learning = refresh_learning_profile(brain)
        with _stage(events, "governance", timings):
            report = AutoGovernanceCycle(
                brain_dir=brain,
                items_store=items_store,
                index=index,
                include_index=False,
                include_evolve=True,
            ).run(apply=True)
        payload = {
            "status": "completed",
            "day": day,
            "stage_seconds": timings,
            "applied_count": report.applied_count,
            "feedback_applied_count": feedback.applied_count,
            "cohort_closures": closures.to_dict(),
            "learning": learning.to_dict(),
            "blocked_count": report.blocked_count,
            "review_required_count": report.review_required_count,
            "applied_actions": [
                {
                    "action": action.action,
                    "item_ids": action.item_ids,
                    "details": action.details,
                }
                for action in report.actions
                if action.applied
            ],
            "review_actions": [
                {
                    "action": action.action,
                    "item_ids": action.item_ids,
                    "details": action.details,
                }
                for action in report.actions
                if action.risk == "review_required"
            ],
        }
        temporary = completed.with_suffix(".json.tmp")
        with os.fdopen(os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600),
                       "w", encoding="utf-8") as output:
            output.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, completed)
        return payload
    finally:
        if index is not None:
            index.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--brain-dir", type=Path, required=True)
    parser.add_argument("--background", action="store_true",
                        help="Detach a worker and return immediately (for lifecycle hooks)")
    args = parser.parse_args(argv)
    runner = launch_daily_maintenance if args.background else run_daily_maintenance
    print(json.dumps(runner(args.brain_dir), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["launch_daily_maintenance", "run_daily_maintenance"]
