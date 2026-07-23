"""Regression tests for descriptor-anchored untrusted file reads."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_brain.platform import secure_io


def test_hardened_fallback_directory_round_trips_regular_file(tmp_path: Path) -> None:
    root = secure_io.HardenedFallbackDirectory.open_or_create(tmp_path / "brain")
    records = root.child("records", create=True)

    records.exclusive_create("record.json", b'{"ok":true}\n')

    assert records.read_regular("record.json") == b'{"ok":true}\n'
    assert records.names(suffix=".json") == ("record.json",)


def test_hardened_fallback_directory_rejects_symlinked_component(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    brain = tmp_path / "brain"
    brain.mkdir()
    (brain / "records").symlink_to(outside, target_is_directory=True)
    root = secure_io.HardenedFallbackDirectory.open_or_create(brain)

    with pytest.raises(OSError):
        root.child("records", create=True)

    assert list(outside.iterdir()) == []


def test_hardened_fallback_exclusive_create_rejects_broken_symlink(
    tmp_path: Path,
) -> None:
    records = secure_io.HardenedFallbackDirectory.open_or_create(tmp_path / "records")
    outside = tmp_path / "outside.json"
    (records.path / "record.json").symlink_to(outside)

    with pytest.raises(OSError):
        records.exclusive_create("record.json", b"private")

    assert not outside.exists()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO requires POSIX")
def test_hardened_fallback_read_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    records = secure_io.HardenedFallbackDirectory.open_or_create(tmp_path / "records")
    os.mkfifo(records.path / "record.json")

    with pytest.raises(OSError):
        records.read_regular("record.json")


def test_hardened_fallback_exclusive_create_fails_closed_on_target_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = secure_io.HardenedFallbackDirectory.open_or_create(tmp_path / "records")
    target = records.path / "record.json"
    outside = tmp_path / "outside.json"
    real_open = secure_io.os.open

    def race_open(path, flags, *args, **kwargs):
        if Path(path) == target and flags & os.O_CREAT and not target.exists():
            target.symlink_to(outside)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(secure_io.os, "open", race_open)

    with pytest.raises(OSError):
        records.exclusive_create("record.json", b"private")

    assert not outside.exists()


def test_hardened_fallback_rejects_windows_reparse_attribute(tmp_path: Path) -> None:
    reparse = SimpleNamespace(
        st_mode=stat.S_IFDIR | 0o700,
        st_dev=1,
        st_ino=2,
        st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
    )

    assert secure_io._is_safe_fallback_directory(tmp_path, reparse) is False


def test_hardened_fallback_rejects_windows_junction_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened = SimpleNamespace(
        st_mode=stat.S_IFDIR | 0o700,
        st_dev=1,
        st_ino=2,
        st_file_attributes=0,
    )
    monkeypatch.setattr(os.path, "isjunction", lambda _path: True, raising=False)

    assert secure_io._is_safe_fallback_directory(tmp_path, opened) is False


def test_trusted_macos_root_alias_is_disabled_off_macos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(secure_io.sys, "platform", "linux")

    def fail_if_called(_path: str) -> str:
        raise AssertionError("non-macOS paths must not inspect root aliases")

    monkeypatch.setattr(secure_io.os, "readlink", fail_if_called)

    path = Path("/var/example")
    assert secure_io._trusted_macos_root_alias(path) == path


def test_trusted_macos_root_alias_rejects_unverified_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(secure_io.sys, "platform", "darwin")
    monkeypatch.setattr(secure_io.os, "readlink", lambda _path: "attacker")

    path = Path("/var/example")
    assert secure_io._trusted_macos_root_alias(path) == path


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO requires POSIX")
def test_open_regular_file_at_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "poison.json"
    os.mkfifo(fifo)
    script = """
import sys
from pathlib import Path
from agent_brain.platform.secure_io import (
    close_descriptor,
    open_directory_path_without_symlinks,
    open_regular_file_at,
)

directory = open_directory_path_without_symlinks(Path(sys.argv[1]))
try:
    try:
        target = open_regular_file_at(directory, "poison.json")
    except OSError:
        pass
    else:
        close_descriptor(target)
        raise SystemExit(2)
finally:
    close_descriptor(directory)
"""

    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        cwd=Path(__file__).parents[2],
        check=False,
        timeout=1,
    )

    assert completed.returncode == 0
