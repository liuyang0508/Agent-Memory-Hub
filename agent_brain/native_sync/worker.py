"""Stdlib-only worker, usable in a local daemon or a plugin runtime with durable state."""

from __future__ import annotations

import argparse
import json
import math
import random
import subprocess
import time
from pathlib import Path
from typing import Any
from .client import NativeClient
from .common import atomic_json, runtime_argv


def work(state: Path, interval: float = 10) -> None:
    """Run one worker per profile; Hook wakeups coalesce and never bypass failure cooldown.

    Sync owns its own transaction/lock boundaries. This loop owns retries only;
    restart is safe because neither queued uploads nor replica cursors live in RAM.
    Jitter disperses reconnects when an organization's Hub recovers after an outage.
    """
    import fcntl

    if not math.isfinite(interval) or not 0 < interval <= 60:
        raise ValueError("worker interval must be greater than zero and at most 60 seconds")
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / "daemon.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        c = NativeClient(state)
        config_path = state / "config.json"
        last_config: Any = json.loads(config_path.read_text()) if config_path.exists() else None
        next_run = 0.0
        backoff = interval
        cooling_down = False
        while True:
            wake = state / "wake"
            due = time.monotonic() >= next_run or (wake.exists() and not cooling_down)
            if not (state / "paused").exists() and due:
                wake.unlink(missing_ok=True)
                try:
                    config = json.loads(config_path.read_text())
                    if config != last_config:
                        # Rebuild on any config change: a re-enrolment issues a new
                        # device token, and this long-lived process would otherwise
                        # keep authenticating with the old one — a permanent 401
                        # followed by revoke_local() and a 60s cooldown, forever.
                        last_config = config
                        c = NativeClient(state)
                    c.sync()
                    backoff = interval
                    cooling_down = False
                except PermissionError:
                    c.revoke_local()
                    atomic_json(
                        state / "status.json",
                        {"error": "device revoked; re-enroll required", "time": time.time()},
                    )
                    backoff = 60
                    cooling_down = True
                except Exception as exc:
                    atomic_json(
                        state / "status.json", {"error": type(exc).__name__, "time": time.time()}
                    )
                    backoff = min(max(interval, backoff * 2), 60)
                    cooling_down = True
                # Preserve the wake marker during cooldown; one catch-up pass will
                # observe every file's latest state once the deadline has elapsed.
                delay = random.uniform(backoff / 2, backoff) if cooling_down else interval
                next_run = time.monotonic() + delay
            time.sleep(0.5)


def ensure_worker(state: Path) -> None:
    """Best-effort detached startup; the child takes the authoritative daemon lock.

    Simultaneous callers may spawn more than one child, but only one can sync.
    No credential or source body is written to the process arguments or log here.
    """
    import fcntl

    if not (state / "config.json").exists() or (state / "paused").exists():
        return
    with (state / "daemon.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
    with (state / "worker.log").open("a") as log:
        subprocess.Popen(
            runtime_argv("agent_brain.native_sync.worker", "--state", str(state.resolve())),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    work(parser.parse_args().state)
