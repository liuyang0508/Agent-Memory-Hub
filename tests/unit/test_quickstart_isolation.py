from __future__ import annotations

import hashlib
import os
import re
import shutil
import signal
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
QUICKSTART_SCRIPT = PROJECT_ROOT / "benchmarks" / "quickstart-60s.sh"
GIT_WRITE_OVERRIDE_KEYS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_INDEX_FILE",
    "GIT_TRACE",
    "GIT_TRACE2",
    "GIT_TRACE2_EVENT",
    "GIT_TRACE2_PERF",
    "GIT_TRACE_PERFORMANCE",
    "GIT_TRACE_PACKET",
    "GIT_TRACE_PACK_ACCESS",
    "GIT_TRACE_SETUP",
    "GIT_TRACE_CURL",
    "GIT_TRACE_SHALLOW",
    "GIT_TRACE_FSMONITOR",
    "GIT_TRACE_REFS",
    "GIT_TRACE_PACKFILE",
)


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


TreeEntry = tuple[str, str, int, str]
TreeManifest = tuple[TreeEntry, ...]


def _tree_manifest(root: Path) -> TreeManifest:
    """Return paths, modes, and content hashes for a host-owned tree."""
    entries: list[TreeEntry] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        mode = stat.S_IMODE(path.lstat().st_mode)
        if path.is_symlink():
            entries.append((relative, "symlink", mode, os.readlink(path)))
        elif path.is_dir():
            entries.append((relative, "dir", mode, ""))
        else:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            entries.append((relative, "file", mode, digest))
    return tuple(entries)


@dataclass
class QuickstartFixture:
    repo: Path
    script: Path
    env: dict[str, str]
    outer_tmp: Path
    host_roots: tuple[Path, Path, Path]
    host_manifests: tuple[TreeManifest, ...]

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
VENV_MEMORY="${AGENT_MEMORY_HUB_HOME:?}/.venv/bin/memory"
mkdir -p "$BIN_DIR" "$HOME/.claude/commands" "$(dirname "$VENV_MEMORY")"
{
  printf '%s\\n' '#!/bin/sh'
  printf '%s\\n' 'printf "fixture-memory-ok:%s\\n" "${1:-}"'
} > "$VENV_MEMORY"
chmod +x "$VENV_MEMORY"
{
  printf '#!/bin/sh\\n'
  printf 'exec "%s" "$@"\\n' "$VENV_MEMORY"
} > "$BIN_DIR/memory"
chmod +x "$BIN_DIR/memory"
printf 'fixture remember command\\n' > "$HOME/.claude/commands/remember.md"

if [ "${PIP_TARGET+x}" = x ]; then
  mkdir -p "$PIP_TARGET"
  printf 'polluted\\n' > "$PIP_TARGET/quickstart-pollution.txt"
fi
if [ -n "${PIP_LOG-}" ]; then
  mkdir -p "$(dirname "$PIP_LOG")"
  printf 'pip log escaped\\n' > "$PIP_LOG"
fi

{
  printf 'HOME=%s\\n' "$HOME"
  printf 'BRAIN_DIR=%s\\n' "${BRAIN_DIR-<unset>}"
  printf 'AGENT_MEMORY_HUB_BIN=%s\\n' "${AGENT_MEMORY_HUB_BIN-<unset>}"
  printf 'AGENT_MEMORY_HUB_HOME=%s\\n' "${AGENT_MEMORY_HUB_HOME-<unset>}"
  printf 'TMPDIR=%s\\n' "${TMPDIR-<unset>}"
  printf 'TMP=%s\\n' "${TMP-<unset>}"
  printf 'TEMP=%s\\n' "${TEMP-<unset>}"
  printf 'TEMPDIR=%s\\n' "${TEMPDIR-<unset>}"
  printf 'XDG_CONFIG_HOME=%s\\n' "${XDG_CONFIG_HOME-<unset>}"
  printf 'XDG_CACHE_HOME=%s\\n' "${XDG_CACHE_HOME-<unset>}"
  printf 'XDG_DATA_HOME=%s\\n' "${XDG_DATA_HOME-<unset>}"
  printf 'XDG_STATE_HOME=%s\\n' "${XDG_STATE_HOME-<unset>}"
  printf 'XDG_RUNTIME_DIR=%s\\n' "${XDG_RUNTIME_DIR-<unset>}"
  printf 'PIP_CONFIG_FILE=%s\\n' "${PIP_CONFIG_FILE-<unset>}"
  printf 'PIP_CACHE_DIR=%s\\n' "${PIP_CACHE_DIR-<unset>}"
  printf 'PIP_LOG=%s\\n' "${PIP_LOG-<unset>}"
  printf 'PIP_REPORT=%s\\n' "${PIP_REPORT-<unset>}"
  printf 'PIP_BUILD_TRACKER=%s\\n' "${PIP_BUILD_TRACKER-<unset>}"
  printf 'PIP_DOWNLOAD_CACHE=%s\\n' "${PIP_DOWNLOAD_CACHE-<unset>}"
  printf 'PIP_SRC=%s\\n' "${PIP_SRC-<unset>}"
  printf 'PYTHONUSERBASE=%s\\n' "${PYTHONUSERBASE-<unset>}"
  printf 'PYTHONPYCACHEPREFIX=%s\\n' "${PYTHONPYCACHEPREFIX-<unset>}"
  printf 'CARGO_HOME=%s\\n' "${CARGO_HOME-<unset>}"
  printf 'RUSTUP_HOME=%s\\n' "${RUSTUP_HOME-<unset>}"
  printf 'CARGO_TARGET_DIR=%s\\n' "${CARGO_TARGET_DIR-<unset>}"
  printf 'UV_CACHE_DIR=%s\\n' "${UV_CACHE_DIR-<unset>}"
  printf 'PIP_TARGET=%s\\n' "${PIP_TARGET-<unset>}"
  printf 'PIP_PREFIX=%s\\n' "${PIP_PREFIX-<unset>}"
  printf 'PIP_ROOT=%s\\n' "${PIP_ROOT-<unset>}"
  printf 'PYTHONHOME=%s\\n' "${PYTHONHOME-<unset>}"
  printf 'VIRTUAL_ENV=%s\\n' "${VIRTUAL_ENV-<unset>}"
  printf 'UV_PROJECT_ENVIRONMENT=%s\\n' "${UV_PROJECT_ENVIRONMENT-<unset>}"
  printf 'BASH_ENV=%s\\n' "${BASH_ENV-<unset>}"
  printf 'ENV=%s\\n' "${ENV-<unset>}"
  printf 'CDPATH=%s\\n' "${CDPATH-<unset>}"
  printf 'PATH=%s\\n' "$PATH"
  printf 'LC_ALL=%s\\n' "${LC_ALL-<unset>}"
  printf 'HTTPS_PROXY=%s\\n' "${HTTPS_PROXY-<unset>}"
  printf 'CURL_CA_BUNDLE=%s\\n' "${CURL_CA_BUNDLE-<unset>}"
  printf 'SSH_AUTH_SOCK=%s\\n' "${SSH_AUTH_SOCK-<unset>}"
  printf 'AMH_TEST_PRESERVED=%s\\n' "${AMH_TEST_PRESERVED-<unset>}"
} > "$HOME/install-env.txt"

case "${FAKE_INSTALL_MODE:-success}" in
  exit42)
    exit 42
    ;;
  wait_resistant)
    trap '' TERM
    bash -c 'trap "" TERM; exec -a quickstart-resistant-descendant sleep 60' &
    child_pid=$!
    phase_pgid=$(ps -o pgid= -p "$$" | tr -d ' ')
    child_pgid=$(ps -o pgid= -p "$child_pid" | tr -d ' ')
    {
      printf 'phase_pid=%s\\n' "$$"
      printf 'phase_pgid=%s\\n' "$phase_pgid"
      printf 'child_pid=%s\\n' "$child_pid"
      printf 'child_pgid=%s\\n' "$child_pgid"
    } > "${FAKE_PROCESS_STATE_FILE:?}"
    : > "${FAKE_SIGNAL_READY_FILE:?}"
    while :; do wait "$child_pid" || true; done
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

    real_git = shutil.which("git")
    real_mktemp = shutil.which("mktemp")
    real_chmod = shutil.which("chmod")
    assert real_git is not None
    assert real_mktemp is not None
    assert real_chmod is not None
    tool_bin = tmp_path / "tool-bin"
    _write_executable(
        tool_bin / "git",
        """#!/usr/bin/env bash
set -u

{
  printf 'HOME=%s\\n' "${HOME-<unset>}"
  printf 'TMPDIR=%s\\n' "${TMPDIR-<unset>}"
  printf 'GIT_DIR=%s\\n' "${GIT_DIR-<unset>}"
  printf 'GIT_WORK_TREE=%s\\n' "${GIT_WORK_TREE-<unset>}"
  printf 'GIT_COMMON_DIR=%s\\n' "${GIT_COMMON_DIR-<unset>}"
  printf 'GIT_OBJECT_DIRECTORY=%s\\n' "${GIT_OBJECT_DIRECTORY-<unset>}"
  printf 'GIT_ALTERNATE_OBJECT_DIRECTORIES=%s\\n' "${GIT_ALTERNATE_OBJECT_DIRECTORIES-<unset>}"
  printf 'GIT_INDEX_FILE=%s\\n' "${GIT_INDEX_FILE-<unset>}"
  printf 'GIT_TRACE=%s\\n' "${GIT_TRACE-<unset>}"
  printf 'GIT_TRACE2=%s\\n' "${GIT_TRACE2-<unset>}"
  printf 'GIT_TRACE2_EVENT=%s\\n' "${GIT_TRACE2_EVENT-<unset>}"
  printf 'GIT_TRACE2_PERF=%s\\n' "${GIT_TRACE2_PERF-<unset>}"
  printf 'GIT_TRACE_PERFORMANCE=%s\\n' "${GIT_TRACE_PERFORMANCE-<unset>}"
  printf 'GIT_TRACE_PACKET=%s\\n' "${GIT_TRACE_PACKET-<unset>}"
  printf 'GIT_TRACE_PACK_ACCESS=%s\\n' "${GIT_TRACE_PACK_ACCESS-<unset>}"
  printf 'GIT_TRACE_SETUP=%s\\n' "${GIT_TRACE_SETUP-<unset>}"
  printf 'GIT_TRACE_CURL=%s\\n' "${GIT_TRACE_CURL-<unset>}"
  printf 'GIT_TRACE_SHALLOW=%s\\n' "${GIT_TRACE_SHALLOW-<unset>}"
  printf 'GIT_TRACE_FSMONITOR=%s\\n' "${GIT_TRACE_FSMONITOR-<unset>}"
  printf 'GIT_TRACE_REFS=%s\\n' "${GIT_TRACE_REFS-<unset>}"
  printf 'GIT_TRACE_PACKFILE=%s\\n' "${GIT_TRACE_PACKFILE-<unset>}"
  printf 'PATH=%s\\n' "$PATH"
  printf 'LC_ALL=%s\\n' "${LC_ALL-<unset>}"
  printf 'HTTPS_PROXY=%s\\n' "${HTTPS_PROXY-<unset>}"
  printf 'CURL_CA_BUNDLE=%s\\n' "${CURL_CA_BUNDLE-<unset>}"
  printf 'SSH_AUTH_SOCK=%s\\n' "${SSH_AUTH_SOCK-<unset>}"
} > "${FAKE_GIT_ENV_RECORD:?}"

if [ -n "${GIT_OBJECT_DIRECTORY-}" ]; then
  mkdir -p "$GIT_OBJECT_DIRECTORY"
  printf 'git objects escaped\\n' > "$GIT_OBJECT_DIRECTORY/quickstart-pollution.txt"
fi

if [ "${FAKE_CLONE_FAIL:-0}" = 1 ] && [ "${1:-}" = clone ]; then
  line=1
  while [ "$line" -le 100 ]; do
    printf 'clone-log-%03d\\n' "$line"
    line=$((line + 1))
  done
  exit 44
fi

unset GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_OBJECT_DIRECTORY
unset GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_INDEX_FILE
unset GIT_TRACE GIT_TRACE2 GIT_TRACE2_EVENT GIT_TRACE2_PERF
unset GIT_TRACE_PERFORMANCE GIT_TRACE_PACKET GIT_TRACE_PACK_ACCESS
unset GIT_TRACE_SETUP GIT_TRACE_CURL GIT_TRACE_SHALLOW GIT_TRACE_FSMONITOR
unset GIT_TRACE_REFS GIT_TRACE_PACKFILE
exec "${AMH_TEST_REAL_GIT:?}" "$@"
""",
    )
    _write_executable(
        tool_bin / "mktemp",
        """#!/usr/bin/env bash
set -u
if [ "${FAKE_MKTEMP_FAIL:-0}" = 1 ]; then
  mkdir -p "${FAKE_MKTEMP_PARTIAL_ROOT:?}"
  printf '%s\\n' "$FAKE_MKTEMP_PARTIAL_ROOT"
  exit 71
fi
exec "${AMH_TEST_REAL_MKTEMP:?}" "$@"
""",
    )
    _write_executable(
        tool_bin / "chmod",
        """#!/usr/bin/env bash
set -u
if [ "${FAKE_BOOTSTRAP_CHMOD_FAIL:-0}" = 1 ]; then
  for arg in "$@"; do
    case "$arg" in
      */xdg-runtime) exit 72 ;;
    esac
  done
fi
exec "${AMH_TEST_REAL_CHMOD:?}" "$@"
""",
    )

    inherited = tmp_path / "inherited-env"
    git_env_record = tmp_path / "git-clone-env.txt"
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
            "PIP_LOG": str(host_pip_target / "pip.log"),
            "PIP_REPORT": str(host_pip_target / "pip-report.json"),
            "PIP_BUILD_TRACKER": str(host_pip_target / "pip-build-tracker"),
            "PIP_DOWNLOAD_CACHE": str(host_pip_target / "pip-download-cache"),
            "PIP_SRC": str(host_pip_target / "pip-src"),
            "PYTHONUSERBASE": str(inherited / "python-user-base"),
            "PYTHONPYCACHEPREFIX": str(inherited / "python-pycache"),
            "CARGO_HOME": str(inherited / "cargo"),
            "RUSTUP_HOME": str(inherited / "rustup"),
            "CARGO_TARGET_DIR": str(inherited / "cargo-target"),
            "UV_CACHE_DIR": str(inherited / "uv-cache"),
            "XDG_CONFIG_HOME": str(inherited / "xdg-config"),
            "XDG_CACHE_HOME": str(inherited / "xdg-cache"),
            "XDG_DATA_HOME": str(inherited / "xdg-data"),
            "XDG_STATE_HOME": str(inherited / "xdg-state"),
            "TMPDIR": str(outer_tmp),
            "TMP": str(outer_tmp),
            "TEMP": str(outer_tmp),
            "TEMPDIR": str(outer_tmp),
            "BASH_ENV": str(inherited / "missing-bash-env"),
            "ENV": str(inherited / "missing-sh-env"),
            "CDPATH": str(inherited / "cdpath"),
            "GIT_DIR": str(host_pip_target / "git-dir"),
            "GIT_WORK_TREE": str(host_pip_target / "git-work-tree"),
            "GIT_COMMON_DIR": str(host_pip_target / "git-common-dir"),
            "GIT_OBJECT_DIRECTORY": str(host_pip_target / "git-objects"),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(host_pip_target / "git-alternates"),
            "GIT_INDEX_FILE": str(host_pip_target / "git-index"),
            "GIT_TRACE": str(host_pip_target / "git-trace.log"),
            "GIT_TRACE2": str(host_pip_target / "git-trace2.log"),
            "GIT_TRACE2_EVENT": str(host_pip_target / "git-trace2-event.log"),
            "GIT_TRACE2_PERF": str(host_pip_target / "git-trace2-perf.log"),
            "GIT_TRACE_PERFORMANCE": str(host_pip_target / "git-trace-performance.log"),
            "GIT_TRACE_PACKET": str(host_pip_target / "git-trace-packet.log"),
            "GIT_TRACE_PACK_ACCESS": str(host_pip_target / "git-trace-pack-access.log"),
            "GIT_TRACE_SETUP": str(host_pip_target / "git-trace-setup.log"),
            "GIT_TRACE_CURL": str(host_pip_target / "git-trace-curl.log"),
            "GIT_TRACE_SHALLOW": str(host_pip_target / "git-trace-shallow.log"),
            "GIT_TRACE_FSMONITOR": str(host_pip_target / "git-trace-fsmonitor.log"),
            "GIT_TRACE_REFS": str(host_pip_target / "git-trace-refs.log"),
            "GIT_TRACE_PACKFILE": str(host_pip_target / "git-trace-packfile.log"),
            "LC_ALL": "C",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "CURL_CA_BUNDLE": "fixture-ca-preserved",
            "SSH_AUTH_SOCK": str(inherited / "ssh-agent.sock"),
            "AMH_TEST_PRESERVED": "preserved",
            "AMH_TEST_REAL_GIT": real_git,
            "AMH_TEST_REAL_MKTEMP": real_mktemp,
            "AMH_TEST_REAL_CHMOD": real_chmod,
            "FAKE_GIT_ENV_RECORD": str(git_env_record),
            "AMH_QUICKSTART_TARGET_SECONDS": "120",
        }
    )
    env["PATH"] = f"{tool_bin}{os.pathsep}{env['PATH']}"
    host_roots = (host_home, host_bin, host_pip_target)
    return QuickstartFixture(
        repo=repo,
        script=repo / "benchmarks" / "quickstart-60s.sh",
        env=env,
        outer_tmp=outer_tmp,
        host_roots=host_roots,
        host_manifests=tuple(_tree_manifest(root) for root in host_roots),
    )


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    pgid: int
    state: str
    command: str


def _process_rows() -> list[tuple[int, int, int, str, str]]:
    result = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,pgid=,stat=,command="],
        capture_output=True,
        text=True,
        check=True,
    )
    rows: list[tuple[int, int, int, str, str]] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 4)
        if len(parts) < 4:
            continue
        command = parts[4] if len(parts) == 5 else ""
        rows.append((int(parts[0]), int(parts[1]), int(parts[2]), parts[3], command))
    return rows


def _process_identity(pid: int) -> ProcessIdentity | None:
    for row_pid, _ppid, pgid, state, command in _process_rows():
        if row_pid == pid:
            return ProcessIdentity(pid=row_pid, pgid=pgid, state=state, command=command)
    return None


def _same_process_is_running(identity: ProcessIdentity) -> bool:
    current = _process_identity(identity.pid)
    return bool(
        current
        and not current.state.startswith("Z")
        and current.pgid == identity.pgid
        and current.command == identity.command
    )


def _identity_still_matches(identity: ProcessIdentity) -> bool:
    current = _process_identity(identity.pid)
    return bool(
        current
        and current.pgid == identity.pgid
        and current.command == identity.command
    )


def _live_processes_in_group(pgid: int) -> list[ProcessIdentity]:
    return [
        ProcessIdentity(pid=pid, pgid=row_pgid, state=state, command=command)
        for pid, _ppid, row_pgid, state, command in _process_rows()
        if row_pgid == pgid and not state.startswith("Z")
    ]


def _descendant_process_groups(root_pid: int) -> set[int]:
    rows = _process_rows()
    children_by_parent: dict[int, list[tuple[int, int]]] = {}
    for pid, ppid, pgid, _state, _command in rows:
        children_by_parent.setdefault(ppid, []).append((pid, pgid))

    groups: set[int] = set()
    pending = [root_pid]
    seen: set[int] = set()
    while pending:
        parent = pending.pop()
        if parent in seen:
            continue
        seen.add(parent)
        for child_pid, child_pgid in children_by_parent.get(parent, []):
            groups.add(child_pgid)
            pending.append(child_pid)
    return groups


def _kill_process_group(pgid: int) -> None:
    if pgid <= 0 or pgid == os.getpgrp():
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _cleanup_benchmark_process(
    process: subprocess.Popen[str], known_phase_processes: tuple[ProcessIdentity, ...] = ()
) -> None:
    groups = {
        identity.pgid
        for identity in known_phase_processes
        if _identity_still_matches(identity)
    }
    if process.poll() is None:
        groups.update(_descendant_process_groups(process.pid))
    for pgid in groups:
        _kill_process_group(pgid)
    if process.poll() is None:
        _kill_process_group(process.pid)
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate(timeout=5)


def _run_quickstart(
    fixture: QuickstartFixture, *args: str, env_updates: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = fixture.env.copy()
    if env_updates:
        env.update(env_updates)
    process = subprocess.Popen(
        [str(fixture.script), *args],
        cwd=fixture.repo,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        _cleanup_benchmark_process(process)
        raise
    return subprocess.CompletedProcess(
        process.args, process.returncode, stdout=stdout, stderr=stderr
    )


def _bench_root(output: str) -> Path:
    match = re.search(r"^Tmp:\s+(.+)$", output, flags=re.MULTILINE)
    assert match, f"quickstart did not print its benchmark root:\n{output}"
    return Path(match.group(1).strip())


def _remove_kept_benchmark_root(bench_root: Path, allowed_parent: Path) -> None:
    resolved_root = bench_root.resolve()
    resolved_parent = allowed_parent.resolve()
    assert resolved_root != resolved_parent
    assert resolved_root.is_relative_to(resolved_parent)
    assert resolved_root.name.startswith("amh-bench-")
    shutil.rmtree(resolved_root, ignore_errors=True)


def _read_env(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines())


def test_default_success_preserves_host_trees_and_removes_benchmark_root(
    quickstart_fixture: QuickstartFixture,
) -> None:
    result = _run_quickstart(quickstart_fixture)
    output = result.stdout + result.stderr
    bench_root = _bench_root(output)

    assert result.returncode == 0, output
    assert "✅ PASS" in output
    assert bench_root.resolve().is_relative_to(quickstart_fixture.outer_tmp.resolve())
    assert not bench_root.exists()
    quickstart_fixture.assert_host_unchanged()


def test_mktemp_failure_is_fail_closed_and_cleans_partial_root(
    quickstart_fixture: QuickstartFixture,
    tmp_path: Path,
) -> None:
    partial_root = tmp_path / "partial-benchmark-root"
    result = _run_quickstart(
        quickstart_fixture,
        env_updates={
            "FAKE_MKTEMP_FAIL": "1",
            "FAKE_MKTEMP_PARTIAL_ROOT": str(partial_root),
        },
    )
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "failed to create benchmark root" in output
    assert "✅ PASS" not in output
    assert not partial_root.exists()
    quickstart_fixture.assert_host_unchanged()


def test_bootstrap_failure_is_fail_closed_and_cleans_benchmark_root(
    quickstart_fixture: QuickstartFixture,
) -> None:
    result = _run_quickstart(
        quickstart_fixture, env_updates={"FAKE_BOOTSTRAP_CHMOD_FAIL": "1"}
    )
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "failed to initialize benchmark root" in output
    assert "✅ PASS" not in output
    assert list(quickstart_fixture.outer_tmp.iterdir()) == []
    quickstart_fixture.assert_host_unchanged()


def test_known_git_and_pip_write_overrides_cannot_escape_benchmark_root(
    quickstart_fixture: QuickstartFixture,
) -> None:
    result = _run_quickstart(quickstart_fixture)
    output = result.stdout + result.stderr
    bench_root = _bench_root(output)
    escaped_paths = [
        Path(quickstart_fixture.env["GIT_OBJECT_DIRECTORY"])
        / "quickstart-pollution.txt",
        Path(quickstart_fixture.env["PIP_LOG"]),
    ]

    assert result.returncode == 0, output
    assert not bench_root.exists()
    assert [str(path) for path in escaped_paths if path.exists()] == []
    quickstart_fixture.assert_host_unchanged()


def test_keep_contains_every_managed_output_inside_printed_benchmark_root(
    quickstart_fixture: QuickstartFixture,
) -> None:
    result = _run_quickstart(quickstart_fixture, "--keep")
    output = result.stdout + result.stderr
    bench_root = _bench_root(output)

    try:
        assert result.returncode == 0, output
        assert bench_root.is_dir()
        for relative in (
            "home",
            "brain",
            "cache/pip",
            "cache/uv",
            "tmp",
            "xdg-config",
            "xdg-data",
            "xdg-state",
            "xdg-runtime",
            "pyuserbase",
            "pycache",
            "pip-src",
            "cargo",
            "cargo-target",
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
            "TMP": str(bench_root / "tmp"),
            "TEMP": str(bench_root / "tmp"),
            "TEMPDIR": str(bench_root / "tmp"),
            "XDG_CONFIG_HOME": str(bench_root / "xdg-config"),
            "XDG_CACHE_HOME": str(bench_root / "cache"),
            "XDG_DATA_HOME": str(bench_root / "xdg-data"),
            "XDG_STATE_HOME": str(bench_root / "xdg-state"),
            "XDG_RUNTIME_DIR": str(bench_root / "xdg-runtime"),
            "PIP_CONFIG_FILE": "/dev/null",
            "PIP_CACHE_DIR": str(bench_root / "cache" / "pip"),
            "PIP_LOG": "<unset>",
            "PIP_REPORT": "<unset>",
            "PIP_BUILD_TRACKER": "<unset>",
            "PIP_DOWNLOAD_CACHE": "<unset>",
            "PIP_SRC": str(bench_root / "pip-src"),
            "PYTHONUSERBASE": str(bench_root / "pyuserbase"),
            "PYTHONPYCACHEPREFIX": str(bench_root / "pycache"),
            "CARGO_HOME": str(bench_root / "cargo"),
            "RUSTUP_HOME": str(bench_root / "rustup"),
            "CARGO_TARGET_DIR": str(bench_root / "cargo-target"),
            "UV_CACHE_DIR": str(bench_root / "cache" / "uv"),
            "PIP_TARGET": "<unset>",
            "PIP_PREFIX": "<unset>",
            "PIP_ROOT": "<unset>",
            "PYTHONHOME": "<unset>",
            "VIRTUAL_ENV": "<unset>",
            "UV_PROJECT_ENVIRONMENT": "<unset>",
            "BASH_ENV": "<unset>",
            "ENV": "<unset>",
            "CDPATH": "<unset>",
            "PATH": quickstart_fixture.env["PATH"],
            "LC_ALL": "C",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "CURL_CA_BUNDLE": "fixture-ca-preserved",
            "SSH_AUTH_SOCK": str(quickstart_fixture.env["SSH_AUTH_SOCK"]),
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
    finally:
        _remove_kept_benchmark_root(bench_root, quickstart_fixture.outer_tmp)


def test_keep_shim_targets_real_clone_venv_inside_benchmark_root(
    quickstart_fixture: QuickstartFixture,
) -> None:
    result = _run_quickstart(quickstart_fixture, "--keep")
    output = result.stdout + result.stderr
    bench_root = _bench_root(output)

    try:
        assert result.returncode == 0, output
        shim = bench_root / "home" / ".local" / "bin" / "memory"
        match = re.search(
            r'^exec "([^"]+)" "\$@"$',
            shim.read_text(encoding="utf-8"),
            flags=re.MULTILINE,
        )
        assert match, "fixture memory shim must exec the clone venv memory binary"
        target = Path(match.group(1))
        assert target == bench_root / "agent-memory-hub" / ".venv" / "bin" / "memory"
        assert shim.is_relative_to(bench_root)
        assert target.is_relative_to(bench_root)
        assert target.is_file()

        shim_result = subprocess.run(
            [str(shim), "probe"], capture_output=True, text=True, timeout=5, check=False
        )
        assert shim_result.returncode == 0
        assert shim_result.stdout == "fixture-memory-ok:probe\n"
        quickstart_fixture.assert_host_unchanged()
    finally:
        _remove_kept_benchmark_root(bench_root, quickstart_fixture.outer_tmp)


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


def test_clone_failure_is_bounded_cleans_up_and_uses_sanitized_environment(
    quickstart_fixture: QuickstartFixture,
) -> None:
    result = _run_quickstart(
        quickstart_fixture, env_updates={"FAKE_CLONE_FAIL": "1"}
    )
    output = result.stdout + result.stderr
    bench_root = _bench_root(output)
    clone_env = _read_env(Path(quickstart_fixture.env["FAKE_GIT_ENV_RECORD"]))
    clone_log_lines = [line for line in output.splitlines() if line.startswith("clone-log-")]

    assert result.returncode == 1, output
    assert "clone failed" in output
    assert "✅ PASS" not in output
    assert len(clone_log_lines) == 80
    assert clone_log_lines[0] == "clone-log-021"
    assert clone_log_lines[-1] == "clone-log-100"
    assert not bench_root.exists()
    assert {key: clone_env[key] for key in GIT_WRITE_OVERRIDE_KEYS} == {
        key: "<unset>" for key in GIT_WRITE_OVERRIDE_KEYS
    }
    assert clone_env["HOME"] == str(bench_root / "home")
    assert clone_env["TMPDIR"] == str(bench_root / "tmp")
    assert clone_env["PATH"] == quickstart_fixture.env["PATH"]
    assert clone_env["LC_ALL"] == "C"
    assert clone_env["HTTPS_PROXY"] == "http://127.0.0.1:9"
    assert clone_env["CURL_CA_BUNDLE"] == "fixture-ca-preserved"
    assert clone_env["SSH_AUTH_SOCK"] == quickstart_fixture.env["SSH_AUTH_SOCK"]
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
    process_state_file = tmp_path / f"install-processes-{sent_signal.name}"
    env = quickstart_fixture.env.copy()
    env.update(
        {
            "FAKE_INSTALL_MODE": "wait_resistant",
            "FAKE_SIGNAL_READY_FILE": str(ready_file),
            "FAKE_PROCESS_STATE_FILE": str(process_state_file),
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
    phase_identity: ProcessIdentity | None = None
    child_identity: ProcessIdentity | None = None
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not ready_file.exists():
            assert process.poll() is None, "quickstart exited before fake install became ready"
            time.sleep(0.05)
        assert ready_file.exists(), "fake install never became ready for signal"
        process_state = _read_env(process_state_file)
        phase_pid = int(process_state["phase_pid"])
        phase_pgid = int(process_state["phase_pgid"])
        child_pid = int(process_state["child_pid"])
        child_pgid = int(process_state["child_pgid"])

        phase_identity = _process_identity(phase_pid)
        child_identity = _process_identity(child_pid)
        assert phase_identity is not None
        assert child_identity is not None
        assert phase_identity.pgid == phase_pgid
        assert child_identity.pgid == child_pgid == phase_pgid
        assert "install.sh" in phase_identity.command
        assert "quickstart-resistant-descendant" in child_identity.command

        process.send_signal(sent_signal)
        output, _ = process.communicate(timeout=10)

        child_deadline = time.monotonic() + 3
        while time.monotonic() < child_deadline and _live_processes_in_group(phase_pgid):
            time.sleep(0.05)
        assert not _same_process_is_running(child_identity)
        assert not _live_processes_in_group(phase_pgid), "isolated phase group is still running"
    finally:
        known_phase_processes = [
            identity for identity in (phase_identity, child_identity) if identity is not None
        ]
        if process_state_file.exists():
            saved_state = _read_env(process_state_file)
            for pid_key, pgid_key, command_marker in (
                ("phase_pid", "phase_pgid", "install.sh"),
                ("child_pid", "child_pgid", "quickstart-resistant-descendant"),
            ):
                current = _process_identity(int(saved_state[pid_key]))
                if (
                    current is not None
                    and current.pgid == int(saved_state[pgid_key])
                    and command_marker in current.command
                ):
                    known_phase_processes.append(current)
        _cleanup_benchmark_process(process, tuple(known_phase_processes))

    bench_root = _bench_root(output)
    assert process.returncode == expected_returncode, output
    assert "✅ PASS" not in output
    assert not bench_root.exists()
    quickstart_fixture.assert_host_unchanged()
