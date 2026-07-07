"""YAML config helpers for the Aider adapter."""
from __future__ import annotations

from pathlib import Path

import yaml


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError as exc:
        raise RuntimeError(
            f"refuse to overwrite malformed {path} — fix it by hand first: {exc}"
        ) from exc


def _atomic_write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        yaml.dump(data, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )
    tmp.replace(path)


__all__ = ["_atomic_write_yaml", "_read_yaml"]
