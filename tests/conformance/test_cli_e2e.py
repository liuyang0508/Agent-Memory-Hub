import os
import subprocess
import sys
from pathlib import Path

import agent_brain


ROOT = Path(__file__).resolve().parents[2]
VENV_BIN = ROOT / ".venv" / "bin"
MEMORY_CMD = [str(VENV_BIN / "memory")] if (VENV_BIN / "memory").exists() else [
    sys.executable,
    "-m",
    "agent_brain.interfaces.cli",
]


def test_cli_version():
    result = subprocess.run(
        [*MEMORY_CMD, "version"],
        capture_output=True, text=True, check=True,
        cwd=ROOT,
    )
    assert result.stdout.strip() == agent_brain.__version__


def test_cli_write_then_search(tmp_path: Path, monkeypatch):
    env = {**os.environ, "BRAIN_DIR": str(tmp_path / "brain"), "MEMORY_HUB_TEST_EMBEDDING": "1"}
    result = subprocess.run(
        [
            *MEMORY_CMD,
            "write",
            "--type", "fact",
            "--title", "CLI test fact",
            "--summary", "Round trip via CLI",
            "--body", "**事实**: CLI works",
            "--tags", "cli,test",
        ],
        capture_output=True, text=True, check=True, env=env, cwd=ROOT,
    )
    assert "mem-" in result.stdout

    result = subprocess.run(
        [*MEMORY_CMD, "search", "CLI works", "--top-k", "5"],
        capture_output=True, text=True, check=True, env=env, cwd=ROOT,
    )
    assert "CLI test fact" in result.stdout
