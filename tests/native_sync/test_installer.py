import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_native_installer_dry_run_reports_hub_without_touching_legacy(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path)}
    r = subprocess.run(
        [
            "sh",
            str(ROOT / "install.sh"),
            "--native",
            "--hub",
            "https://team.example.com",
            "--dry-run",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
    )
    assert r.returncode == 0, r.stderr
    assert "https://team.example.com" in r.stdout
    assert "native" in r.stdout.lower()
    assert not (tmp_path / ".agent-memory-hub").exists()


def test_native_installer_requires_explicit_hub(tmp_path):
    r = subprocess.run(
        ["sh", str(ROOT / "install.sh"), "--native", "--dry-run"],
        cwd=ROOT,
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
    )
    assert r.returncode != 0
    assert "--hub" in r.stderr
