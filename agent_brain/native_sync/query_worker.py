"""Killable network request boundary: DNS/socket drips cannot overrun the Hook budget."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from .transport import Transport
from .common import runtime_argv


def bounded_search(state: Path, query: str, *, timeout: float) -> list[dict[str, Any]]:
    """Kill the isolated network process at the deadline; propagate explicit denial separately from outage."""
    try:
        result = subprocess.run(
            runtime_argv("agent_brain.native_sync.query_worker", str(state.resolve())),
            input=json.dumps({"query": query}),
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise TimeoutError("cloud recall deadline exceeded") from None
    if result.returncode == 3:
        raise PermissionError("device rejected")
    if result.returncode:
        raise OSError("cloud recall unavailable")
    items = json.loads(result.stdout)
    if not isinstance(items, list):
        raise ValueError("invalid recall response")
    return items


if __name__ == "__main__":
    try:
        config = json.loads((Path(sys.argv[1]) / "config.json").read_text())
        payload = json.load(sys.stdin)
        print(
            json.dumps(
                Transport(config["hub"], config["identity"]["token"], timeout=0.55).search(
                    payload["query"]
                )
            )
        )
    except PermissionError:
        raise SystemExit(3)
    except Exception:
        raise SystemExit(1)
