"""The one-command Hub installer ships with its release and refuses unverified files."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HUB = ROOT / "deploy/native-memory/hub.sh"
UPGRADE = ROOT / "deploy/native-memory/upgrade.sh"
WORKFLOW = ROOT / ".github/workflows/release-installers.yml"


def test_scripts_parse():
    for script in (HUB, UPGRADE):
        assert subprocess.run(["bash", "-n", str(script)]).returncode == 0


def test_release_bakes_version_and_ships_what_hub_sh_verifies():
    text = HUB.read_text()
    assert 'VERSION="__AMH_VERSION__"' in text and 'RELEASE_SHA="__AMH_RELEASE_SHA__"' in text
    for name in ("checksums.txt", "upgrade.sh", "semantic-model.json"):
        assert name in text
    flow = WORKFLOW.read_text()
    for name in ("hub.sh", "upgrade.sh", "semantic-model.json"):
        assert f"dist/{name}" in flow
    assert "__AMH_RELEASE_SHA__" in flow and "hub.sh upgrade.sh semantic-model.json" in flow


def test_upgrade_accepts_the_python_hub_sh_found():
    text = UPGRADE.read_text()
    assert "AMH_PYTHON" in text and "python3.11 -m venv" not in text


def test_hub_sh_stops_before_any_change_without_root_or_version(tmp_path):
    script = tmp_path / "hub.sh"
    script.write_text(HUB.read_text())
    result = subprocess.run(["bash", str(script)], capture_output=True, text=True)
    assert result.returncode != 0
    assert "root" in result.stderr or "version" in result.stderr
