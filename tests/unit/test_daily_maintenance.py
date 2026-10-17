"""Stop maintenance must not hold up the agent or wedge after a crash."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

import pytest

from agent_brain.memory.governance import daily_maintenance as daily


def _state(brain: Path) -> tuple[Path, str]:
    state = brain / "runtime" / "daily-governance"
    state.mkdir(parents=True, exist_ok=True)
    return state, datetime.now(timezone.utc).strftime("%Y-%m-%d")


def test_abandoned_legacy_lock_does_not_skip_the_day(tmp_brain_dir):
    state, day = _state(tmp_brain_dir)
    (state / f"{day}.lock").touch()

    result = daily.run_daily_maintenance(tmp_brain_dir)

    assert result["status"] == "completed"
    assert json.loads((state / f"{day}.json").read_text())["status"] == "completed"
    assert daily.run_daily_maintenance(tmp_brain_dir)["status"] == "already_completed"


def test_stage_failure_is_logged_and_can_retry(tmp_brain_dir):
    state, day = _state(tmp_brain_dir)
    with patch.object(daily, "refresh_learning_profile", side_effect=RuntimeError("test failure")):
        with pytest.raises(RuntimeError, match="test failure"):
            daily.run_daily_maintenance(tmp_brain_dir)

    assert not (state / f"{day}.json").exists()
    events = [json.loads(line) for line in (state / f"{day}.events.jsonl").read_text().splitlines()]
    failure = next(event for event in events if event["status"] == "failed")
    assert failure["stage"] == "learning"
    assert failure["elapsed_seconds"] >= 0
    assert failure["error_type"] == "RuntimeError"
    assert daily.run_daily_maintenance(tmp_brain_dir)["status"] == "completed"


def test_background_launcher_detaches_all_standard_streams(tmp_brain_dir):
    with patch.object(daily.subprocess, "Popen") as popen:
        popen.return_value.pid = 12345
        result = daily.launch_daily_maintenance(tmp_brain_dir)

    assert result["status"] == "scheduled"
    args, kwargs = popen.call_args
    assert "agent_brain.memory.governance.daily_maintenance" in args[0]
    assert "--background" not in args[0]
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stdout"] == subprocess.DEVNULL
    assert kwargs["stderr"] != subprocess.PIPE
    assert kwargs["close_fds"] is True
    if os.name != "nt":
        assert kwargs["start_new_session"] is True


def test_live_lock_excludes_parallel_run_and_process_death_releases_it(tmp_brain_dir):
    state, _day = _state(tmp_brain_dir)
    code = (
        "import sys\nfrom pathlib import Path\n"
        "from agent_brain.memory.governance.daily_maintenance import _maintenance_lock\n"
        "with _maintenance_lock(Path(sys.argv[1])) as acquired:\n"
        " print(acquired, flush=True)\n"
        " sys.stdin.readline()\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(state)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert child.stdout.readline().strip() == "True"
        assert daily.run_daily_maintenance(tmp_brain_dir)["status"] == "already_running"
    finally:
        child.kill()
        child.communicate(timeout=5)
    assert daily.run_daily_maintenance(tmp_brain_dir)["status"] == "completed"


def test_background_cli_worker_completes_after_launcher_exits(tmp_brain_dir):
    state, day = _state(tmp_brain_dir)
    result = subprocess.run(
        [sys.executable, "-m", "agent_brain.memory.governance.daily_maintenance",
         "--brain-dir", str(tmp_brain_dir), "--background"],
        text=True, capture_output=True, timeout=4,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "scheduled"
    completed = state / f"{day}.json"
    deadline = time.monotonic() + 5
    while not completed.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert completed.exists(), (state / f"{day}.stderr.log").read_text()
    assert json.loads(completed.read_text())["status"] == "completed"


@pytest.mark.parametrize("session_seen", [False, True])
def test_stop_hook_does_not_wait_for_daily_maintenance(tmp_path, session_seen):
    """A slow maintenance command would exhaust the host's Stop timeout."""
    wrapper = tmp_path / "python-wrapper"
    invoked = tmp_path / "maintenance-args.json"
    wrapper.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys, time\n"
        "from pathlib import Path\n"
        "if len(sys.argv) > 2 and sys.argv[1] == '-m':\n"
        " if sys.argv[2] == 'agent_brain.memory.governance.daily_maintenance':\n"
        f"  Path({str(invoked)!r}).write_text(json.dumps(sys.argv[3:]))\n"
        "  if '--background' not in sys.argv: time.sleep(12)\n"
        " sys.exit(0)\n"
        "os.execv(sys.executable, [sys.executable, *sys.argv[1:]])\n"
    )
    wrapper.chmod(0o700)
    if session_seen:
        flags = tmp_path / "brain/.session-flags"
        flags.mkdir(parents=True)
        (flags / "slow-maintenance").touch()
    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit/hooks/session-end-signal.sh"
    env = {
        **os.environ, "BRAIN_DIR": str(tmp_path / "brain"),
        "AGENT_MEMORY_HUB_PYTHON": str(wrapper), "MEMORY_PYTHON": str(wrapper),
    }
    result = subprocess.run(
        ["bash", str(script)], input=json.dumps({"session_id": "slow-maintenance"}),
        # Match the host's 10s budget; import startup can exceed 4s on busy CI.
        text=True, capture_output=True, env=env, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert "--background" in json.loads(invoked.read_text())
