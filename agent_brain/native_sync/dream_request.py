"""「现在做梦」 from the device side: the Hub's standing request and how a pass is started.

Stdlib only, besides ``common``: the background worker ships in the portable runtime
and must not pull in the dreaming pass itself (its model runner, screening, roles).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from .common import atomic_json

DIRECTORY = "dream"
REQUEST_PATH = "dream/request"
REQUEST_FILE = "request.json"
POLL_SECONDS = 60  # a designated device asks this often whether someone pressed 「现在做梦」
POLL_IDLE_SECONDS = 3600  # any other device, or a Hub without the route
PASS_SECONDS = 4 * 3600  # one request is not started twice within a pass's lease window


def call(transport: Any, path: str, data: Any = None, attempts: int = 4) -> Any:
    """One Hub request. A flaky link to the Hub is the common case, not the exception."""
    for attempt in range(attempts):
        try:
            return transport.call(path, data)
        except (OSError, ValueError) as exc:
            # A 4xx is the Hub's verdict, not the link's weather: say it at once.
            status = int(getattr(exc, "http_status", 0) or 0)
            if attempt == attempts - 1 or 400 <= status < 500:
                raise
            time.sleep(1.5 * (attempt + 1))
            last = exc
    raise last  # pragma: no cover


def request(transport: Any, action: str = "poll", **fields: str) -> dict[str, Any]:
    """The administrator's standing request for a pass, as the Hub sees it from here.

    ``poll`` says whether one waits for this device; ``claim`` takes it; ``finish``
    closes it with the outcome. A Hub too old to know the route answers 404, which
    ``supported`` carries; a refusal keeps its code, without the ``dream_`` prefix,
    in ``refusal``.
    """
    answer: dict[str, Any] = {
        "supported": True,
        "designated": False,
        "pending": False,
        "requested": None,
        "refusal": "",
    }
    if not hasattr(transport, "call"):
        return answer
    try:
        found = call(transport, REQUEST_PATH, {"action": action, **fields})
    except ValueError as exc:
        if getattr(exc, "http_status", None) == 404:
            return {**answer, "supported": False}
        code = str(getattr(exc, "code", ""))
        if code.startswith("dream_"):
            refusal = code[len("dream_") :]
            return {**answer, "designated": refusal != "not_designated", "refusal": refusal}
        raise
    return {**answer, **found}


def pick_up(
    state: Path, transport: Any, *, spawn: Callable[[Path], Any] | None = None
) -> dict[str, Any]:
    """Start a publishing pass if an administrator asked for one this device may run.

    The worker calls this on its clock. The pass itself claims the request and reports
    how it ended; a marker under ``dream/`` keeps one request from being started twice
    while the first pass is still running.
    """
    standing = request(transport, "poll")
    if not standing.get("pending"):
        return {**standing, "started": False}
    marker = state / DIRECTORY / REQUEST_FILE
    requested = float(standing.get("requested") or 0)
    try:
        last = json.loads(marker.read_text())
    except (OSError, ValueError):
        last = {}
    if (
        last.get("requested") == requested
        and float(last.get("started") or 0) > time.time() - PASS_SECONDS
    ):
        return {**standing, "started": False}
    marker.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_json(marker, {"requested": requested, "started": time.time()})
    (spawn or spawn_pass)(state)
    return {**standing, "started": True}


def spawn_pass(state: Path) -> None:
    """``amh-native dream --on-request`` as a detached process, logging under ``dream/``.

    The worker may have been started from a hook with a thin PATH; the pass needs the
    Claude Code binary, so it gets the PATH the nightly job uses.
    """
    from .common import runtime_argv

    home = Path.home()
    folder = state / DIRECTORY
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{home}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
    }
    with (folder / "request.log").open("a") as log:
        subprocess.Popen(
            runtime_argv(
                "agent_brain.native_sync",
                "dream",
                "--state",
                str(state.resolve()),
                "--on-request",
            ),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            env=env,
        )
