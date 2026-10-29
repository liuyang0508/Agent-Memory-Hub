"""Killable network request boundary: DNS/socket drips cannot overrun the Hook budget."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from .transport import Transport


def bounded_search(state: Path, query: str, *, timeout: float) -> list[dict[str, Any]]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    try:
        result = subprocess.run(
            [sys.executable, "-m", "agent_brain.native_sync.query_worker", str(state)],
            input=json.dumps({"query": query}),
            text=True,
            capture_output=True,
            timeout=timeout,
            env=env,
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
