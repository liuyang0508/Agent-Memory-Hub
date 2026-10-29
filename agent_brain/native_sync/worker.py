"""Stdlib-only worker, usable in a local daemon or a plugin runtime with durable state."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from .client import NativeClient
from .common import atomic_json, runtime_argv


def work(state: Path, interval: float = 10) -> None:
    import fcntl

    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / "daemon.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        c = NativeClient(state)
        next_run = 0.0
        backoff = interval
        while True:
            wake = state / "wake"
            if not (state / "paused").exists() and (time.monotonic() >= next_run or wake.exists()):
                wake.unlink(missing_ok=True)
                try:
                    c.config = json.loads((state / "config.json").read_text())
                    c.sync()
                    backoff = interval
                except PermissionError:
                    c.revoke_local()
                    atomic_json(
                        state / "status.json",
                        {"error": "device revoked; re-enroll required", "time": time.time()},
                    )
                    backoff = 60
                except Exception as exc:
                    atomic_json(
                        state / "status.json", {"error": type(exc).__name__, "time": time.time()}
                    )
                    backoff = min(max(interval, backoff * 2), 60)
                next_run = time.monotonic() + backoff
            time.sleep(0.5)


def ensure_worker(state: Path) -> None:
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
