from __future__ import annotations

import hashlib
import os
import re
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
QUICKSTART_SCRIPT = PROJECT_ROOT / "benchmarks" / "quickstart-60s.sh"


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _tree_manifest(root: Path) -> tuple[tuple[str, str, str], ...]:
    """Return a path manifest plus content hashes for a host-owned tree."""
    entries: list[tuple[str, str, str]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            entries.append((relative, "symlink", os.readlink(path)))
        elif path.is_dir():
            entries.append((relative, "dir", ""))
        else:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            entries.append((relative, "file", digest))
    return tuple(entries)


@dataclass
class QuickstartFixture:
    repo: Path
    script: Path
    env: dict[str, str]
    host_roots: tuple[Path, Path, Path]
    host_manifests: tuple[tuple[tuple[str, str, str], ...], ...]

    def assert_host_unchanged(self) -> None:
        assert tuple(_tree_manifest(root) for root in self.host_roots) == self.host_manifests


@pytest.fixture
def quickstart_fixture(tmp_path: Path) -> QuickstartFixture:
    host_home = tmp_path / "host-home"
    host_bin = tmp_path / "host-bin"
    host_pip_target = tmp_path / "host-pip-target"
    outer_tmp = tmp_path / "outer-tmp"

    (host_home / ".claude" / "commands").mkdir(parents=True)
    (host_home / ".claude" / "commands" / "remember.md").write_text(
        "host remember command\n", encoding="utf-8"
    )
    (host_home / "host-only.txt").write_text("host home sentinel\n", encoding="utf-8")
    host_bin.mkdir()
    (host_bin / "memory").write_text("host memory shim\n", encoding="utf-8")
    host_pip_target.mkdir()
    (host_pip_target / "host-package.txt").write_text(
        "host pip target sentinel\n", encoding="utf-8"
    )
    outer_tmp.mkdir()

    repo = tmp_path / "fixture-repo"
    (repo / "benchmarks").mkdir(parents=True)
    shutil.copy2(QUICKSTART_SCRIPT, repo / "benchmarks" / "quickstart-60s.sh")

    _write_executable(
        repo / "install.sh",
        """#!/usr/bin/env bash
set -u

BIN_DIR="${AGENT_MEMORY_HUB_BIN:-$HOME/.local/bin}"
mkdir -p "$BIN_DIR" "$HOME/.claude/commands"
printf '#!/bin/sh\\nexit 0\\n' > "$BIN_DIR/memory"
chmod +x "$BIN_DIR/memory"
printf 'fixture remember command\\n' > "$HOME/.claude/commands/remember.md"

if [ "${PIP_TARGET+x}" = x ]; then
  mkdir -p "$PIP_TARGET"
  printf 'polluted\\n' > "$PIP_TARGET/quickstart-pollution.txt"
fi

{
  printf 'HOME=%s\\n' "$HOME"
  printf 'BRAIN_DIR=%s\\n' "${BRAIN_DIR-<unset>}"
  printf 'AGENT_MEMORY_HUB_BIN=%s\\n' "${AGENT_MEMORY_HUB_BIN-<unset>}"
  printf 'AGENT_MEMORY_HUB_HOME=%s\\n' "${AGENT_MEMORY_HUB_HOME-<unset>}"
  printf 'TMPDIR=%s\\n' "${TMPDIR-<unset>}"
  printf 'XDG_CONFIG_HOME=%s\\n' "${XDG_CONFIG_HOME-<unset>}"
  printf 'XDG_CACHE_HOME=%s\\n' "${XDG_CACHE_HOME-<unset>}"
  printf 'XDG_DATA_HOME=%s\\n' "${XDG_DATA_HOME-<unset>}"
  printf 'XDG_STATE_HOME=%s\\n' "${XDG_STATE_HOME-<unset>}"
  printf 'PIP_CONFIG_FILE=%s\\n' "${PIP_CONFIG_FILE-<unset>}"
  printf 'PIP_CACHE_DIR=%s\\n' "${PIP_CACHE_DIR-<unset>}"
  printf 'PYTHONUSERBASE=%s\\n' "${PYTHONUSERBASE-<unset>}"
  printf 'CARGO_HOME=%s\\n' "${CARGO_HOME-<unset>}"
  printf 'RUSTUP_HOME=%s\\n' "${RUSTUP_HOME-<unset>}"
  printf 'PIP_TARGET=%s\\n' "${PIP_TARGET-<unset>}"
  printf 'PIP_PREFIX=%s\\n' "${PIP_PREFIX-<unset>}"
  printf 'PIP_ROOT=%s\\n' "${PIP_ROOT-<unset>}"
  printf 'PYTHONHOME=%s\\n' "${PYTHONHOME-<unset>}"
  printf 'VIRTUAL_ENV=%s\\n' "${VIRTUAL_ENV-<unset>}"
  printf 'UV_PROJECT_ENVIRONMENT=%s\\n' "${UV_PROJECT_ENVIRONMENT-<unset>}"
  printf 'PATH=%s\\n' "$PATH"
  printf 'LC_ALL=%s\\n' "${LC_ALL-<unset>}"
  printf 'HTTPS_PROXY=%s\\n' "${HTTPS_PROXY-<unset>}"
  printf 'AMH_TEST_PRESERVED=%s\\n' "${AMH_TEST_PRESERVED-<unset>}"
} > "$HOME/install-env.txt"

case "${FAKE_INSTALL_MODE:-success}" in
  exit42)
    exit 42
    ;;
  wait)
    trap 'exit 130' INT
    trap 'exit 143' TERM
    sleep 60 &
    child_pid=$!
    printf '%s\\n' "$child_pid" > "${FAKE_CHILD_PID_FILE:?}"
    : > "${FAKE_SIGNAL_READY_FILE:?}"
    wait "$child_pid"
    ;;
esac
""",
    )
    _write_executable(
        repo / "agent_runtime_kit" / "tools" / "search-memory.sh",
        """#!/usr/bin/env bash
set -u
{
  printf 'HOME=%s\\n' "$HOME"
  printf 'BRAIN_DIR=%s\\n' "${BRAIN_DIR-<unset>}"
  printf 'AGENT_MEMORY_HUB_BIN=%s\\n' "${AGENT_MEMORY_HUB_BIN-<unset>}"
  printf 'AGENT_MEMORY_HUB_HOME=%s\\n' "${AGENT_MEMORY_HUB_HOME-<unset>}"
  printf 'TMPDIR=%s\\n' "${TMPDIR-<unset>}"
  printf 'PIP_TARGET=%s\\n' "${PIP_TARGET-<unset>}"
} > "$HOME/search-env.txt"
printf 'search-line-1\\nsearch-line-2\\nsearch-line-3\\nsearch-line-4\\n'
exit "${FAKE_SEARCH_EXIT:-0}"
""",
    )

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "quickstart@example.test"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "Quickstart Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "quickstart fixture"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    inherited = tmp_path / "inherited-env"
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(host_home),
            "AGENT_MEMORY_HUB_BIN": str(host_bin),
            "PIP_TARGET": str(host_pip_target),
            "PIP_PREFIX": str(inherited / "pip-prefix"),
            "PIP_ROOT": str(inherited / "pip-root"),
            "PYTHONHOME": str(inherited / "python-home"),
            "VIRTUAL_ENV": str(inherited / "venv"),
            "UV_PROJECT_ENVIRONMENT": str(inherited / "uv-project"),
            "PIP_CONFIG_FILE": str(inherited / "pip.conf"),
            "PIP_CACHE_DIR": str(inherited / "pip-cache"),
            "PYTHONUSERBASE": str(inherited / "python-user-base"),
            "CARGO_HOME": str(inherited / "cargo"),
            "RUSTUP_HOME": str(inherited / "rustup"),
            "XDG_CONFIG_HOME": str(inherited / "xdg-config"),
            "XDG_CACHE_HOME": str(inherited / "xdg-cache"),
            "XDG_DATA_HOME": str(inherited / "xdg-data"),
            "XDG_STATE_HOME": str(inherited / "xdg-state"),
            "TMPDIR": str(outer_tmp),
            "LC_ALL": "C",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "AMH_TEST_PRESERVED": "preserved",
            "AMH_QUICKSTART_TARGET_SECONDS": "120",
        }
    )
    host_roots = (host_home, host_bin, host_pip_target)
    return QuickstartFixture(
        repo=repo,
        script=repo / "benchmarks" / "quickstart-60s.sh",
        env=env,
        host_roots=host_roots,
        host_manifests=tuple(_tree_manifest(root) for root in host_roots),
    )


def _run_quickstart(
    fixture: QuickstartFixture, *args: str, env_updates: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = fixture.env.copy()
    if env_updates:
        env.update(env_updates)
    return subprocess.run(
        [str(fixture.script), *args],
        cwd=fixture.repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def _bench_root(output: str) -> Path:
    match = re.search(r"^Tmp:\s+(.+)$", output, flags=re.MULTILINE)
    assert match, f"quickstart did not print its benchmark root:\n{output}"
    return Path(match.group(1).strip())


def _read_env(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines())


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_default_success_preserves_host_trees_and_removes_benchmark_root(
    quickstart_fixture: QuickstartFixture,
) -> None:
    result = _run_quickstart(quickstart_fixture)
    output = result.stdout + result.stderr
    bench_root = _bench_root(output)

    assert result.returncode == 0, output
    assert "✅ PASS" in output
    assert not bench_root.exists()
    quickstart_fixture.assert_host_unchanged()


def test_keep_contains_every_managed_output_inside_printed_benchmark_root(
    quickstart_fixture: QuickstartFixture,
) -> None:
    result = _run_quickstart(quickstart_fixture, "--keep")
    output = result.stdout + result.stderr
    bench_root = _bench_root(output)

    assert result.returncode == 0, output
    assert bench_root.is_dir()
    for relative in (
        "home",
        "brain",
        "cache/pip",
        "tmp",
        "xdg-config",
        "xdg-data",
        "xdg-state",
        "pyuserbase",
        "cargo",
        "rustup",
        "agent-memory-hub",
    ):
        assert (bench_root / relative).is_dir(), relative

    expected_env = {
        "HOME": str(bench_root / "home"),
        "BRAIN_DIR": str(bench_root / "brain"),
        "AGENT_MEMORY_HUB_BIN": str(bench_root / "home" / ".local" / "bin"),
        "AGENT_MEMORY_HUB_HOME": str(bench_root / "agent-memory-hub"),
        "TMPDIR": str(bench_root / "tmp"),
        "XDG_CONFIG_HOME": str(bench_root / "xdg-config"),
        "XDG_CACHE_HOME": str(bench_root / "cache"),
        "XDG_DATA_HOME": str(bench_root / "xdg-data"),
        "XDG_STATE_HOME": str(bench_root / "xdg-state"),
        "PIP_CONFIG_FILE": "/dev/null",
        "PIP_CACHE_DIR": str(bench_root / "cache" / "pip"),
        "PYTHONUSERBASE": str(bench_root / "pyuserbase"),
        "CARGO_HOME": str(bench_root / "cargo"),
        "RUSTUP_HOME": str(bench_root / "rustup"),
        "PIP_TARGET": "<unset>",
        "PIP_PREFIX": "<unset>",
        "PIP_ROOT": "<unset>",
        "PYTHONHOME": "<unset>",
        "VIRTUAL_ENV": "<unset>",
        "UV_PROJECT_ENVIRONMENT": "<unset>",
        "PATH": quickstart_fixture.env["PATH"],
        "LC_ALL": "C",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "AMH_TEST_PRESERVED": "preserved",
    }
    install_env = _read_env(bench_root / "home" / "install-env.txt")
    assert install_env == expected_env

    search_env = _read_env(bench_root / "home" / "search-env.txt")
    assert search_env == {
        key: expected_env[key]
        for key in (
            "HOME",
            "BRAIN_DIR",
            "AGENT_MEMORY_HUB_BIN",
            "AGENT_MEMORY_HUB_HOME",
            "TMPDIR",
            "PIP_TARGET",
        )
    }
    shim = bench_root / "home" / ".local" / "bin" / "memory"
    assert shim.is_file()
    assert shim.is_relative_to(bench_root)
    assert (bench_root / "home" / ".claude" / "commands" / "remember.md").is_file()
    assert "search-line-4" not in output
    quickstart_fixture.assert_host_unchanged()


def test_install_failure_is_nonzero_cleans_up_and_preserves_host(
    quickstart_fixture: QuickstartFixture,
) -> None:
    result = _run_quickstart(
        quickstart_fixture, env_updates={"FAKE_INSTALL_MODE": "exit42"}
    )
    output = result.stdout + result.stderr
    bench_root = _bench_root(output)

    assert result.returncode == 1, output
    assert "install.sh failed" in output
    assert "✅ PASS" not in output
    assert not bench_root.exists()
    quickstart_fixture.assert_host_unchanged()


def test_first_search_failure_is_nonzero_cleans_up_and_preserves_host(
    quickstart_fixture: QuickstartFixture,
) -> None:
    result = _run_quickstart(quickstart_fixture, env_updates={"FAKE_SEARCH_EXIT": "43"})
    output = result.stdout + result.stderr
    bench_root = _bench_root(output)

    assert result.returncode == 1, output
    assert "first search failed" in output
    assert "✅ PASS" not in output
    assert not bench_root.exists()
    quickstart_fixture.assert_host_unchanged()


@pytest.mark.parametrize(
    ("sent_signal", "expected_returncode"),
    [(signal.SIGINT, 130), (signal.SIGTERM, 143)],
)
def test_signal_exit_cleans_only_benchmark_root_and_preserves_host(
    quickstart_fixture: QuickstartFixture,
    tmp_path: Path,
    sent_signal: signal.Signals,
    expected_returncode: int,
) -> None:
    ready_file = tmp_path / f"install-ready-{sent_signal.name}"
    child_pid_file = tmp_path / f"install-child-{sent_signal.name}"
    env = quickstart_fixture.env.copy()
    env.update(
        {
            "FAKE_INSTALL_MODE": "wait",
            "FAKE_SIGNAL_READY_FILE": str(ready_file),
            "FAKE_CHILD_PID_FILE": str(child_pid_file),
        }
    )
    process = subprocess.Popen(
        [str(quickstart_fixture.script)],
        cwd=quickstart_fixture.repo,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    output = ""
    child_pid: int | None = None
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not ready_file.exists():
            assert process.poll() is None, "quickstart exited before fake install became ready"
            time.sleep(0.05)
        assert ready_file.exists(), "fake install never became ready for signal"
        child_pid = int(child_pid_file.read_text(encoding="utf-8").strip())

        process.send_signal(sent_signal)
        output, _ = process.communicate(timeout=10)

        child_deadline = time.monotonic() + 3
        while time.monotonic() < child_deadline and _pid_exists(child_pid):
            time.sleep(0.05)
        assert not _pid_exists(child_pid), "isolated phase left its child process running"
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        if child_pid is not None and _pid_exists(child_pid):
            os.kill(child_pid, signal.SIGKILL)

    bench_root = _bench_root(output)
    assert process.returncode == expected_returncode, output
    assert "✅ PASS" not in output
    assert not bench_root.exists()
    quickstart_fixture.assert_host_unchanged()
