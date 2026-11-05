"""Killable network request boundary: DNS/socket drips cannot overrun the Hook budget."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from .transport import HubHTTPError, Transport
from .common import recall_budget, request_timeout, runtime_argv


def _recall(transport: Any, query: str, limit: int, asking: dict[str, Any] | None) -> Any:
    if not asking:
        return transport.recall(query, limit=limit)
    try:
        return transport.recall(query, limit=limit, asking=asking)
    except HubHTTPError as exc:
        # A Hub before asking-context-v1 rejects the extra field; the recall
        # itself is still answered, only the insight hints are lost.
        if exc.http_status != 422:
            raise
        return transport.recall(query, limit=limit)


def fetch(
    transport: Any, query: str, *, limit: int = 8, asking: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Prefer the hybrid hook channel; a Hub or gateway without it still answers via search.

    404/405/501 mean the path does not exist on this Hub. A denial is retried on
    the legacy path first: a gateway that only guards the newer path must not
    look like a revoked device, which would wipe this machine's recall state.
    """
    try:
        result = _recall(transport, query, limit, asking)
        items = result.get("items") if isinstance(result, dict) else None
        if not isinstance(items, list):
            raise ValueError("invalid recall response")
        trace_id = result.get("trace_id")
        raw: dict[str, Any] = (
            result["retrieval"] if isinstance(result.get("retrieval"), dict) else {}
        )
        reasons: list[Any] = raw["reasons"] if isinstance(raw.get("reasons"), list) else []
        hints = result.get("hints") if isinstance(result.get("hints"), list) else []
        return {
            "items": items,
            # Title-only tier (Hub 1.4.18+): names the model may read on demand.
            "hints": [
                {
                    "asset_id": str(h["asset_id"])[:128],
                    "revision": h["revision"],
                    "title": str(h.get("title") or "")[:200],
                    "path": str(h.get("path") or "")[:200],
                    "product": str(h.get("product") or "")[:40],
                    "kind": str(h.get("kind") or "")[:20],
                    "probability": h.get("probability")
                    if isinstance(h.get("probability"), (int, float))
                    else None,
                }
                for h in hints[:4]
                if isinstance(h, dict)
                and isinstance(h.get("asset_id"), str)
                and type(h.get("revision")) is int
            ],
            "self_feedback_rate": result.get("self_feedback_rate")
            if isinstance(result.get("self_feedback_rate"), (int, float))
            else 0.0,
            "trace_id": trace_id if isinstance(trace_id, str) and len(trace_id) == 24 else None,
            "channel": "recall",
            # Shown to the member: how the Hub ranked this answer and why it degraded.
            "retrieval": {
                "mode": raw.get("mode") if raw.get("mode") in ("hybrid", "lexical") else None,
                "reasons": [str(r)[:80] for r in reasons[:4]],
            },
        }
    except HubHTTPError as exc:
        if exc.http_status not in (404, 405, 501):
            raise
    except PermissionError:
        pass  # Only a denial on /search below proves the device itself was rejected.
    return {"items": transport.search(query, limit=limit), "trace_id": None, "channel": "search"}


def bounded_search(
    state: Path, query: str, *, timeout: float, asking: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Kill the isolated network process at the deadline; propagate explicit denial separately from outage."""
    from .profiles import recall_allowed

    if not recall_allowed(state):
        return {"items": [], "trace_id": None, "channel": "disabled"}
    try:
        result = subprocess.run(
            runtime_argv("agent_brain.native_sync.query_worker", str(state.resolve())),
            input=json.dumps({"query": query, "asking": asking}),
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


def recall(
    state: Path, query: str, *, budget: float, asking: dict[str, Any] | None = None
) -> tuple[dict[str, Any], str]:
    """The resident service first (one warm round trip); the killable subprocess when it is absent.

    Returns the response and which path answered. A service that exists but
    fails hands the error straight back: it already spent the budget.
    """
    from .recall_service import ServiceUnavailable, request
    from .profiles import recall_allowed

    if not recall_allowed(state):
        return {"items": [], "trace_id": None, "channel": "disabled"}, "disabled"
    started = time.monotonic()
    try:
        message = {
            "query": query,
            "asking": asking,
            "limit": 8,
            "timeout": max(0.1, budget - 0.05),
        }
        result = request(state, message, timeout=budget)
        if not recall_allowed(state):
            return {"items": [], "trace_id": None, "channel": "disabled"}, "disabled"
        return result, "service"
    except ServiceUnavailable:
        pass
    remaining = budget - (time.monotonic() - started)
    if remaining < 0.1:
        raise TimeoutError("cloud recall deadline exceeded")
    return bounded_search(state, query, timeout=remaining, asking=asking), "worker"


if __name__ == "__main__":
    try:
        from .profiles import recall_allowed

        if not recall_allowed(Path(sys.argv[1])):
            print(json.dumps({"items": [], "trace_id": None, "channel": "disabled"}))
            raise SystemExit(0)
        config = json.loads((Path(sys.argv[1]) / "config.json").read_text())
        payload = json.load(sys.stdin)
        transport = Transport(
            config["hub"],
            config["identity"]["token"],
            timeout=request_timeout(recall_budget(config)),
        )
        asking = payload.get("asking")
        print(
            json.dumps(
                fetch(
                    transport,
                    payload["query"],
                    limit=8,
                    asking=asking if isinstance(asking, dict) else None,
                )
            )
        )
    except PermissionError as exc:
        raise SystemExit(1 if exc.errno is not None else 3)
    except Exception:
        raise SystemExit(1)
