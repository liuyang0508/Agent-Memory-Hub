from __future__ import annotations

import os
import secrets
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml


def secret_key(brain_dir: Path) -> str:
    secret_path = brain_dir / ".web_secret"
    if secret_path.exists():
        return secret_path.read_text().strip()
    key = secrets.token_urlsafe(32)
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    secret_path.write_text(key)
    return key


def load_users(brain_dir: Path) -> list[dict[str, Any]]:
    users_file = brain_dir / "users.yaml"
    if not users_file.exists():
        return []
    return yaml.safe_load(users_file.read_text(encoding="utf-8")) or []


def save_users(
    brain_dir: Path,
    users: list[dict[str, Any]],
    *,
    replace: Callable[[str, str], None] = os.replace,
) -> None:
    users_file = brain_dir / "users.yaml"
    users_file.parent.mkdir(parents=True, exist_ok=True)
    data = yaml.safe_dump(users, allow_unicode=True)
    # Atomic write (P3-8): serialize to a temp file in the same directory,
    # fsync it, then replace onto the target. A crash mid-write can no longer
    # truncate or corrupt users.yaml.
    fd, tmp_name = tempfile.mkstemp(
        dir=str(users_file.parent), prefix=".users.", suffix=".yaml.tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        replace(tmp_name, str(users_file))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
