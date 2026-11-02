"""Killable network request boundary: DNS/socket drips cannot overrun the Hook budget."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from .transport import HubHTTPError, Transport
from .common import runtime_argv


def fetch(transport: Any, query: str, *, limit: int = 8) -> dict[str, Any]:
    """Prefer the hybrid hook channel; a Hub without it (HTTP 404) still answers via search."""
    try:
        result = transport.recall(query, limit=limit)
        items = result.get("items") if isinstance(result, dict) else None
        if not isinstance(items, list):
            raise ValueError("invalid recall response")
        trace_id = result.get("trace_id")
        return {
            "items": items,
            "trace_id": trace_id if isinstance(trace_id, str) and len(trace_id) == 24 else None,
            "channel": "recall",
        }
    except HubHTTPError as exc:
        if exc.http_status != 404:
            raise
    return {"items": transport.search(query, limit=limit), "trace_id": None, "channel": "search"}


def bounded_search(state: Path, query: str, *, timeout: float) -> dict[str, Any]:
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
    response = json.loads(result.stdout)
    if not isinstance(response, dict) or not isinstance(response.get("items"), list):
        raise ValueError("invalid recall response")
    return response


if __name__ == "__main__":
    try:
        config = json.loads((Path(sys.argv[1]) / "config.json").read_text())
        payload = json.load(sys.stdin)
        transport = Transport(config["hub"], config["identity"]["token"], timeout=1.0)
        print(json.dumps(fetch(transport, payload["query"], limit=8)))
    except PermissionError:
        raise SystemExit(3)
    except Exception:
        raise SystemExit(1)
