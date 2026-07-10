"""Regression tests for descriptor-anchored untrusted file reads."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


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
