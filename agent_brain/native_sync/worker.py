"""Stdlib-only worker, usable in a local daemon or a plugin runtime with durable state."""

from __future__ import annotations

import argparse
import json
import math
import random
import subprocess
import time
from pathlib import Path
from typing import Any, TypedDict
from .client import NativeClient
from .common import atomic_json, runtime_argv
from .transport import Transport


class SyncSettings(TypedDict):
    request_timeout_seconds: float
    max_uploads: int
    pass_budget_seconds: float


def sync_settings(config: dict[str, Any]) -> SyncSettings:
    """Validated per-profile background settings; no Hook/MCP timeout changes."""
    values = config.get("sync_worker", {})
    allowed = {"request_timeout_seconds", "max_uploads", "pass_budget_seconds"}
    if not isinstance(values, dict) or set(values) - allowed:
        raise ValueError("invalid sync_worker configuration")
    timeout = values.get("request_timeout_seconds", 30)
    budget = values.get("pass_budget_seconds", 45)
    maximum = values.get("max_uploads", 32)
    for name, value, upper in (
        ("request_timeout_seconds", timeout, 120),
        ("pass_budget_seconds", budget, 300),
    ):
        if type(value) not in (int, float) or not math.isfinite(value) or not 1 <= value <= upper:
            raise ValueError(f"sync_worker.{name} must be finite and between 1 and {upper}")
    if type(maximum) is not int or not 1 <= maximum <= 1000:
        raise ValueError("sync_worker.max_uploads must be an integer between 1 and 1000")
    return {
        "request_timeout_seconds": float(timeout),
        "max_uploads": maximum,
        "pass_budget_seconds": float(budget),
    }


def background_client(state: Path, config: dict[str, Any], settings: SyncSettings) -> NativeClient:
    return NativeClient(
        state,
        transport=Transport(
            config["hub"], config["identity"]["token"], timeout=settings["request_timeout_seconds"]
        ),
    )


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
        config_path = state / "config.json"
        last_config = json.loads(config_path.read_text())
        settings = sync_settings(last_config)
        c = background_client(state, last_config, settings)
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
                        updated_settings = sync_settings(config)
                        updated_client = background_client(state, config, updated_settings)
                        c, settings, last_config = updated_client, updated_settings, config
                    c.sync(
                        max_uploads=settings["max_uploads"],
                        pass_budget_seconds=settings["pass_budget_seconds"],
                    )
                    backoff = interval
                    cooling_down = False
                except PermissionError:
                    c.revoke_local()
                    atomic_json(
                        state / "status.json",
                        {
                            "error": "device revoked; re-enroll required",
                            "time": time.time(),
                            "sync_limits": dict(settings),
                        },
                    )
                    backoff = 60
                    cooling_down = True
                except Exception as exc:
                    atomic_json(
                        state / "status.json",
                        {
                            "error": type(exc).__name__,
                            "time": time.time(),
                            "sync_limits": dict(settings),
                        },
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
