"""Small, dependency-free primitives shared by the native client and Hub."""

from __future__ import annotations

from typing import Iterator
import sqlite3

import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

DB_TIMEOUT: ContextVar[float] = ContextVar("native_db_timeout", default=10.0)

PRODUCTS = {
    "claude-code": "Claude Code",
    "codex-cli": "Codex CLI",
    "codex-shared": "Codex（CLI/桌面共用存储，原始入口未区分）",
    "cowork": "Claude Cowork",
    "codex-desktop": "Codex 客户端",
    "chatgpt-desktop": "ChatGPT 客户端",
}


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def atomic_json(path: Path, value: object) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def atomic_text(path: Path, value: str) -> None:
    atomic_bytes(path, value.encode("utf-8"))


def atomic_bytes(path: Path, value: bytes) -> None:
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


@contextmanager
def database(path: Path) -> Iterator[sqlite3.Connection]:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(path, timeout=DB_TIMEOUT.get())
    os.chmod(path, 0o600)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    try:
        with db:
            yield db
    finally:
        db.close()


def tokens(text: str) -> list[str]:
    """ASCII words plus CJK unigrams/bigrams; no model download or legacy index."""
    words = re.findall(r"[a-z0-9_]+", text.lower())
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        words.extend(run)
        words.extend(run[i : i + 2] for i in range(len(run) - 1))
    return list(dict.fromkeys(words))
