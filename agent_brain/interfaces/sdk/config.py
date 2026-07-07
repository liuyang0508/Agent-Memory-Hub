from __future__ import annotations

import os
from pathlib import Path


def resolve_brain_dir(brain_dir: str | Path | None = None) -> Path:
    if brain_dir is None:
        brain_dir = os.environ.get(
            "BRAIN_DIR",
            os.path.expanduser("~/.agent-memory-hub"),
        )
    return Path(brain_dir)


__all__ = ["resolve_brain_dir"]
