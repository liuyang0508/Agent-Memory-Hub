import os
import shutil
import subprocess
from pathlib import Path

import pytest

REAL_BRAIN = Path(os.path.expanduser("~/.agent-memory-hub"))
VENV_BIN = Path(__file__).resolve().parents[2] / ".venv" / "bin"
MEMORY_CMD = str(VENV_BIN / "memory")


@pytest.mark.skipif(
    not os.environ.get("LOCAL_BRAIN_CONFORMANCE"),
    reason="opt-in via LOCAL_BRAIN_CONFORMANCE=1; not a CI default",
)
def test_reindex_real_brain_smoke(tmp_path: Path):
    """Reindex the real brain pool into a tmp index.db. Should not crash."""
    fake_brain = tmp_path / "brain"
    shutil.copytree(REAL_BRAIN / "items", fake_brain / "items")
    env = {**os.environ, "BRAIN_DIR": str(fake_brain), "MEMORY_HUB_TEST_EMBEDDING": "1"}
    result = subprocess.run(
        [MEMORY_CMD, "reindex"],
        capture_output=True, text=True, check=True, env=env,
    )
    assert "reindexed" in result.stdout
    count = int(result.stdout.split()[1])
    assert count >= 100, f"expected ≥100 items, got {count}"
