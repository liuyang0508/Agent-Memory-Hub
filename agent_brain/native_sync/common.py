"""Small, dependency-free primitives shared by the native client and Hub."""

from __future__ import annotations
import math
from collections.abc import Mapping
from typing import Any


import hashlib
import json
import os
import re
import tempfile
from pathlib import Path


PAIRING_DEFAULT_TTL = 15 * 60
PAIRING_MAX_TTL = 7 * 24 * 60 * 60


PRODUCTS = {
    "claude-code": "Claude Code",
    "codex-cli": "Codex CLI",
    "codex": "Codex",
    "codex-shared": "Codex",  # Accept records written by earlier clients.
    "cowork": "Claude Cowork",
    "codex-desktop": "Codex 客户端",
    "chatgpt-desktop": "ChatGPT 客户端",
}

KINDS = {
    "memory": "记忆",
    "rule": "规则",
    "skill": "技能",
    "config": "配置",
    "transcript": "会话记录",
}


def display_name(kind: str) -> str:
    """Human label for a source kind; unknown kinds stay visible rather than being hidden."""
    return KINDS.get(kind, kind)


def source_product(product: str) -> str:
    """Use the current source label without rewriting historical identities or bodies."""
    return "codex" if product == "codex-shared" else product


def digest(value: str) -> str:
    """Return the stable UTF-8 SHA-256 used for content/ownership comparison, not identity."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def atomic_json(path: Path, value: object) -> None:
    """Serialize state through atomic_text; credentials inherit private temporary-file permissions."""
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def atomic_text(path: Path, value: str) -> None:
    """Encode UTF-8 and delegate to the same atomic replacement used for binary resources."""
    atomic_bytes(path, value.encode("utf-8"))


def atomic_bytes(path: Path, value: bytes) -> None:
    """Replace one file atomically after fsync; preserve its mtime when bytes are unchanged.

    Reject symlink destinations. The operation protects readers from partial bytes;
    it is not a multi-file transaction or a guarantee against filesystem/power loss.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("refusing to replace symlink")
    if path.exists() and path.read_bytes() == value:
        return
    fd, temp = tempfile.mkstemp(prefix=".amh-native-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def tokens(text: str) -> list[str]:
    """ASCII words plus CJK unigrams/bigrams; no model download or legacy index."""
    words = re.findall(r"[a-z0-9_]+", text.lower())
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        words.extend(run)
        words.extend(run[i : i + 2] for i in range(len(run) - 1))
    return list(dict.fromkeys(words))


def legacy_memory_command(command: str) -> bool:
    """Identify known memory-business callbacks, not every script in the runtime kit."""
    scripts = (
        "inject-context.sh",
        "inject-discipline.sh",
        "session-end-signal.sh",
        "lifecycle-event.sh",
    )
    return any(
        prefix + name in command
        for prefix in ("agent_runtime_kit/hooks/", "agent-memory-hub/hooks/")
        for name in scripts
    )


def runtime_argv(module: str, *arguments: str) -> list[str]:
    """Launch this installed code, independent of a same-named package in the task cwd."""
    import sys

    root = str(Path(__file__).resolve().parents[2])
    bootstrap = (
        "import runpy,sys; "
        f"sys.path.insert(0, {root!r}); "
        f"runpy.run_module({module!r}, run_name='__main__')"
    )
    return [sys.executable, "-I", "-c", bootstrap, *arguments]


# --- prompt-time recall budget ------------------------------------------------
# One prompt-time recall has a total budget (process start, network, packing).
# The default suits a nearby Hub; a long path (another country, a flaky link)
# needs more, and the host's own hook timeout must stay ahead of it.
RECALL_BUDGET_KEY = "recall_timeout_seconds"
RECALL_BUDGET_ENV = "AMH_RECALL_TIMEOUT"
RECALL_BUDGET_DEFAULT = 1.2
RECALL_BUDGET_MIN = 0.3
RECALL_BUDGET_MAX = 10.0
WORKER_START_MARGIN = 0.2  # spawning the request subprocess costs this much of the budget
HOOK_TIMEOUT_MARGIN = 0.8  # packing and delivery after the recall, before the host gives up


def recall_budget(config: Any, *, environ: Mapping[str, str] | None = None) -> float:
    """Seconds one prompt-time recall may take: AMH_RECALL_TIMEOUT, else config.json, clamped."""
    environ = os.environ if environ is None else environ
    raw: Any = environ.get(RECALL_BUDGET_ENV)
    if raw in (None, ""):
        raw = config.get(RECALL_BUDGET_KEY) if isinstance(config, dict) else None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return RECALL_BUDGET_DEFAULT
    if not math.isfinite(value):
        return RECALL_BUDGET_DEFAULT
    return min(max(value, RECALL_BUDGET_MIN), RECALL_BUDGET_MAX)


def request_timeout(budget: float) -> float:
    """Network time left for a request issued from a freshly spawned subprocess."""
    return max(RECALL_BUDGET_MIN, budget - WORKER_START_MARGIN)


def hook_timeout(budget: float) -> int:
    """Whole seconds the host may wait for the prompt hook: the budget plus delivery headroom."""
    return max(2, math.ceil(budget + HOOK_TIMEOUT_MARGIN))
