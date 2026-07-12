from __future__ import annotations

import hashlib
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
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
    install_script: Path
    search_script: Path
    git_wrapper: Path
    mktemp_wrapper: Path
    mkdir_wrapper: Path
    ps_wrapper: Path
    rm_wrapper: Path
    git_env_record: Path
    attack_sentinels: tuple[Path, ...]
    host_roots: tuple[Path, Path, Path]
    host_manifests: tuple[TreeManifest, ...]

    def assert_host_unchanged(self) -> None:
        assert tuple(_tree_manifest(root) for root in self.host_roots) == self.host_manifests

    @staticmethod
    def _set_assignments(path: Path, assignments: dict[str, str | int]) -> None:
        content = path.read_text(encoding="utf-8")
        for key, value in assignments.items():
            replacement = f"{key}={shlex.quote(str(value))}"
            content, count = re.subn(
                rf"^{re.escape(key)}=.*$",
                lambda _match: replacement,
                content,
                count=1,
                flags=re.MULTILINE,
            )
            assert count == 1, f"missing fixture assignment {key} in {path}"
        path.write_text(content, encoding="utf-8")

    def _commit_repo_config(self, message: str) -> None:
        subprocess.run(
            ["git", "add", "install.sh", "agent_runtime_kit/tools/search-memory.sh"],
            cwd=self.repo,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )

    def configure_install(
        self,
        mode: str,
        *,
        ready_file: Path | None = None,
        process_state_file: Path | None = None,
    ) -> None:
        self._set_assignments(
            self.install_script,
            {
                "INSTALL_MODE": mode,
                "SIGNAL_READY_FILE": ready_file or "/dev/null",
                "PROCESS_STATE_FILE": process_state_file or "/dev/null",
            },
        )
        self._commit_repo_config(f"fixture install mode {mode}")

    def configure_search_exit(self, exit_code: int) -> None:
        self._set_assignments(self.search_script, {"SEARCH_EXIT": exit_code})
        self._commit_repo_config(f"fixture search exit {exit_code}")

    def configure_clone_failure(self) -> None:
        self._set_assignments(self.git_wrapper, {"CLONE_FAIL": 1})

    def configure_mktemp_failure(self, output_path: Path) -> None:
        self._set_assignments(
            self.mktemp_wrapper,
            {"MKTEMP_MODE": "fail_existing", "MKTEMP_OUTPUT": output_path},
        )

    def configure_mktemp_symlink_success(self, output_path: str) -> None:
        self._set_assignments(
            self.mktemp_wrapper,
            {"MKTEMP_MODE": "success_existing", "MKTEMP_OUTPUT": output_path},
        )

    def configure_bootstrap_failure(self) -> None:
        self._set_assignments(self.mkdir_wrapper, {"BOOTSTRAP_MKDIR_FAIL": 1})

    def configure_pgid_failure(self, mode: str) -> None:
        self._set_assignments(self.ps_wrapper, {"PS_MODE": mode})

    def configure_runtime_ps_mode(self, mode_file: Path) -> None:
        self._set_assignments(self.ps_wrapper, {"PS_MODE_FILE": mode_file})

    def configure_cleanup_rm_failure(self, mode: str) -> None:
        self._set_assignments(self.rm_wrapper, {"RM_MODE": mode})

    def configure_startup_signal_handoff(
        self,
        *,
        first_signal_file: Path,
        phase_state_file: Path,
        startup_ready_file: Path,
        handoff_ready_file: Path,
        handoff_release_file: Path,
    ) -> None:
        content = self.script.read_text(encoding="utf-8")

        signal_assignment = next(
            (
                assignment
                for assignment in ('    PENDING_SIGNAL_CODE="$1"\n', '    FIRST_SIGNAL_CODE="$1"\n')
                if assignment in content
            ),
            None,
        )
        assert signal_assignment is not None, "missing first-signal assignment anchor"
        content = content.replace(
            signal_assignment,
            signal_assignment
            + f"    printf '%s\\n' \"$1\" > {shlex.quote(str(first_signal_file))}\n",
            1,
        )

        active_pid_anchor = "  ACTIVE_PID=$!\n"
        assert content.count(active_pid_anchor) == 1
        startup_gate = f"""{active_pid_anchor}  fixture_startup_attempt=0
  fixture_startup_state=""
  while [ "$fixture_startup_attempt" -lt 100 ]; do
    fixture_startup_state=$(process_state_for_pid "$ACTIVE_PID")
    case "$fixture_startup_state" in T*) break ;; esac
    sleep 0.01
    fixture_startup_attempt=$((fixture_startup_attempt + 1))
  done
  fixture_startup_pgid=$(process_group_for_pid "$ACTIVE_PID")
  {{
    printf 'phase_pid=%s\\n' "$ACTIVE_PID"
    printf 'phase_pgid=%s\\n' "$fixture_startup_pgid"
  }} > {shlex.quote(str(phase_state_file))}
  : > {shlex.quote(str(startup_ready_file))}
  while [ ! -s {shlex.quote(str(first_signal_file))} ]; do sleep 0.01; done
"""
        content = content.replace(active_pid_anchor, startup_gate, 1)

        handoff_gate = f"""  : > {shlex.quote(str(handoff_ready_file))}
  while [ ! -e {shlex.quote(str(handoff_release_file))} ]; do sleep 0.01; done
"""
        phase_start_anchor = '  PHASE_STARTING=0\n  if [ "$FIRST_SIGNAL_CODE" -ne 0 ]; then\n'
        if phase_start_anchor in content:
            handoff_index = content.rfind(phase_start_anchor)
            content = content[:handoff_index] + handoff_gate + content[handoff_index:]
        else:
            legacy_anchor = (
                "  trap 'handle_signal 143' TERM\n  pending_code=\"$PENDING_SIGNAL_CODE\"\n"
            )
            assert content.count(legacy_anchor) == 1, "missing legacy signal handoff anchor"
            content = content.replace(
                legacy_anchor,
                "  trap 'handle_signal 143' TERM\n"
                + handoff_gate
                + '  pending_code="$PENDING_SIGNAL_CODE"\n',
                1,
            )

        self.script.write_text(content, encoding="utf-8")


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
INSTALL_MODE=success
SIGNAL_READY_FILE=/dev/null
PROCESS_STATE_FILE=/dev/null
PROBE_PYTHON=/dev/null

"$PROBE_PYTHON" -c 'pass'
if [ -n "${MEMORY_PYTHON-}" ]; then "$MEMORY_PYTHON" -c 'pass'; fi
if [ -n "${AGENT_MEMORY_HUB_PYTHON-}" ]; then "$AGENT_MEMORY_HUB_PYTHON" -c 'pass'; fi

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
  printf 'LANG=%s\\n' "${LANG-<unset>}"
  printf 'LC_ALL=%s\\n' "${LC_ALL-<unset>}"
  printf 'LC_CTYPE=%s\\n' "${LC_CTYPE-<unset>}"
  printf 'HTTP_PROXY=%s\\n' "${HTTP_PROXY-<unset>}"
  printf 'HTTPS_PROXY=%s\\n' "${HTTPS_PROXY-<unset>}"
  printf 'ALL_PROXY=%s\\n' "${ALL_PROXY-<unset>}"
  printf 'NO_PROXY=%s\\n' "${NO_PROXY-<unset>}"
  printf 'http_proxy=%s\\n' "${http_proxy-<unset>}"
  printf 'https_proxy=%s\\n' "${https_proxy-<unset>}"
  printf 'all_proxy=%s\\n' "${all_proxy-<unset>}"
  printf 'no_proxy=%s\\n' "${no_proxy-<unset>}"
  printf 'SSL_CERT_FILE=%s\\n' "${SSL_CERT_FILE-<unset>}"
  printf 'SSL_CERT_DIR=%s\\n' "${SSL_CERT_DIR-<unset>}"
  printf 'REQUESTS_CA_BUNDLE=%s\\n' "${REQUESTS_CA_BUNDLE-<unset>}"
  printf 'CURL_CA_BUNDLE=%s\\n' "${CURL_CA_BUNDLE-<unset>}"
  printf 'PIP_CERT=%s\\n' "${PIP_CERT-<unset>}"
  printf 'GIT_SSL_CAINFO=%s\\n' "${GIT_SSL_CAINFO-<unset>}"
  printf 'SSH_AUTH_SOCK=%s\\n' "${SSH_AUTH_SOCK-<unset>}"
  printf 'AMH_TEST_PRESERVED=%s\\n' "${AMH_TEST_PRESERVED-<unset>}"
  printf 'GIT_CONFIG_GLOBAL=%s\\n' "${GIT_CONFIG_GLOBAL-<unset>}"
  printf 'PYTHONPATH=%s\\n' "${PYTHONPATH-<unset>}"
  printf 'PYTHONSTARTUP=%s\\n' "${PYTHONSTARTUP-<unset>}"
  printf 'PYTHONINSPECT=%s\\n' "${PYTHONINSPECT-<unset>}"
  printf 'MEMORY_PYTHON=%s\\n' "${MEMORY_PYTHON-<unset>}"
  printf 'AGENT_MEMORY_HUB_PYTHON=%s\\n' "${AGENT_MEMORY_HUB_PYTHON-<unset>}"
} > "$HOME/install-env.txt"

case "$INSTALL_MODE" in
  exit42)
    line=1
    while [ "$line" -le 100 ]; do
      printf 'install-log-%03d\\n' "$line"
      line=$((line + 1))
    done
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
    } > "$PROCESS_STATE_FILE"
    : > "$SIGNAL_READY_FILE"
    while :; do wait "$child_pid" || true; done
    ;;
esac
""",
    )
    _write_executable(
        repo / "agent_runtime_kit" / "tools" / "search-memory.sh",
        """#!/usr/bin/env bash
set -u
SEARCH_EXIT=0
PROBE_PYTHON=/dev/null
"$PROBE_PYTHON" -c 'pass'
if [ -n "${MEMORY_PYTHON-}" ]; then "$MEMORY_PYTHON" -c 'pass'; fi
if [ -n "${AGENT_MEMORY_HUB_PYTHON-}" ]; then "$AGENT_MEMORY_HUB_PYTHON" -c 'pass'; fi
{
  printf 'HOME=%s\\n' "$HOME"
  printf 'BRAIN_DIR=%s\\n' "${BRAIN_DIR-<unset>}"
  printf 'AGENT_MEMORY_HUB_BIN=%s\\n' "${AGENT_MEMORY_HUB_BIN-<unset>}"
  printf 'AGENT_MEMORY_HUB_HOME=%s\\n' "${AGENT_MEMORY_HUB_HOME-<unset>}"
  printf 'TMPDIR=%s\\n' "${TMPDIR-<unset>}"
  printf 'PIP_TARGET=%s\\n' "${PIP_TARGET-<unset>}"
  printf 'GIT_CONFIG_GLOBAL=%s\\n' "${GIT_CONFIG_GLOBAL-<unset>}"
  printf 'PYTHONPATH=%s\\n' "${PYTHONPATH-<unset>}"
  printf 'PYTHONSTARTUP=%s\\n' "${PYTHONSTARTUP-<unset>}"
  printf 'PYTHONINSPECT=%s\\n' "${PYTHONINSPECT-<unset>}"
  printf 'MEMORY_PYTHON=%s\\n' "${MEMORY_PYTHON-<unset>}"
  printf 'AGENT_MEMORY_HUB_PYTHON=%s\\n' "${AGENT_MEMORY_HUB_PYTHON-<unset>}"
} > "$HOME/search-env.txt"
printf 'search-line-1\\nsearch-line-2\\nsearch-line-3\\nsearch-line-4\\n'
if [ "$SEARCH_EXIT" -ne 0 ]; then
  line=1
  while [ "$line" -le 100 ]; do
    printf 'search-log-%03d\\n' "$line"
    line=$((line + 1))
  done
fi
exit "$SEARCH_EXIT"
""",
    )
    QuickstartFixture._set_assignments(repo / "install.sh", {"PROBE_PYTHON": sys.executable})
    QuickstartFixture._set_assignments(
        repo / "agent_runtime_kit" / "tools" / "search-memory.sh",
        {"PROBE_PYTHON": sys.executable},
    )

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "quickstart@example.test"], cwd=repo, check=True)
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
    real_mkdir = shutil.which("mkdir")
    real_ps = shutil.which("ps")
    real_rm = shutil.which("rm")
    assert real_git is not None
    assert real_mktemp is not None
    assert real_mkdir is not None
    assert real_ps is not None
    assert real_rm is not None
    tool_bin = tmp_path / "tool-bin"
    _write_executable(
        tool_bin / "git",
        """#!/usr/bin/env bash
set -u
REAL_GIT=/dev/null
GIT_ENV_RECORD=/dev/null
CLONE_FAIL=0
PROBE_PYTHON=/dev/null

"$PROBE_PYTHON" -c 'pass'
if [ -n "${MEMORY_PYTHON-}" ]; then "$MEMORY_PYTHON" -c 'pass'; fi
if [ -n "${AGENT_MEMORY_HUB_PYTHON-}" ]; then "$AGENT_MEMORY_HUB_PYTHON" -c 'pass'; fi

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
  printf 'GIT_CONFIG_GLOBAL=%s\\n' "${GIT_CONFIG_GLOBAL-<unset>}"
  printf 'PYTHONPATH=%s\\n' "${PYTHONPATH-<unset>}"
  printf 'PYTHONSTARTUP=%s\\n' "${PYTHONSTARTUP-<unset>}"
  printf 'PYTHONINSPECT=%s\\n' "${PYTHONINSPECT-<unset>}"
  printf 'MEMORY_PYTHON=%s\\n' "${MEMORY_PYTHON-<unset>}"
  printf 'AGENT_MEMORY_HUB_PYTHON=%s\\n' "${AGENT_MEMORY_HUB_PYTHON-<unset>}"
} > "$GIT_ENV_RECORD"

if [ -n "${GIT_OBJECT_DIRECTORY-}" ]; then
  mkdir -p "$GIT_OBJECT_DIRECTORY"
  printf 'git objects escaped\\n' > "$GIT_OBJECT_DIRECTORY/quickstart-pollution.txt"
fi

if [ "$CLONE_FAIL" -eq 1 ] && [ "${1:-}" = clone ]; then
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
exec "$REAL_GIT" "$@"
""",
    )
    _write_executable(
        tool_bin / "mktemp",
        """#!/usr/bin/env bash
set -u
REAL_MKTEMP=/dev/null
MKTEMP_MODE=success
MKTEMP_OUTPUT=/dev/null
case "$MKTEMP_MODE" in
  fail_existing)
    printf '%s\\n' "$MKTEMP_OUTPUT"
    exit 71
    ;;
  success_existing)
    printf '%s\\n' "$MKTEMP_OUTPUT"
    exit 0
    ;;
esac
exec "$REAL_MKTEMP" "$@"
""",
    )
    _write_executable(
        tool_bin / "mkdir",
        """#!/usr/bin/env bash
set -u
REAL_MKDIR=/dev/null
BOOTSTRAP_MKDIR_FAIL=0
if [ "$BOOTSTRAP_MKDIR_FAIL" -eq 1 ]; then
  for arg in "$@"; do
    case "$arg" in
      */amh-bench-*/home) exit 72 ;;
    esac
  done
fi
exec "$REAL_MKDIR" "$@"
""",
    )
    _write_executable(
        tool_bin / "ps",
        """#!/usr/bin/env bash
set -u
REAL_PS=/dev/null
PS_MODE=normal
PS_MODE_FILE=/dev/null
if [ "$PS_MODE_FILE" != /dev/null ] && [ -f "$PS_MODE_FILE" ]; then
  IFS= read -r PS_MODE < "$PS_MODE_FILE" || true
fi
if [ "$PS_MODE" = fail_process_queries ] \
  && [ "$#" -eq 4 ] \
  && [ "$1" = -o ] \
  && { [ "$2" = pgid= ] || [ "$2" = stat= ]; } \
  && [ "$3" = -p ]; then
  exit 74
fi
if [ "$PS_MODE" = hide_pgid ] \
  && [ "$#" -eq 4 ] \
  && [ "$1" = -o ] \
  && [ "$2" = pgid= ] \
  && [ "$3" = -p ]; then
  exit 0
fi
if [ "$PS_MODE" = report_benchmark_pgid ] \
  && [ "$#" -eq 4 ] \
  && [ "$1" = -o ] \
  && [ "$2" = pgid= ] \
  && [ "$3" = -p ]; then
  exec "$REAL_PS" -o pgid= -p "$PPID"
fi
exec "$REAL_PS" "$@"
""",
    )
    _write_executable(
        tool_bin / "rm",
        """#!/usr/bin/env bash
set -u
REAL_RM=/dev/null
RM_MODE=normal
if [ "$RM_MODE" = fail_cleanup ]; then
  for arg in "$@"; do
    case "$arg" in */amh-bench-*) exit 75 ;; esac
  done
fi
if [ "$RM_MODE" = signal_cleanup ]; then
  for arg in "$@"; do
    case "$arg" in
      */amh-bench-*)
        kill -INT "$PPID"
        sleep 0.02
        exit 75
        ;;
    esac
  done
fi
exec "$REAL_RM" "$@"
""",
    )

    inherited = tmp_path / "inherited-env"
    git_env_record = tmp_path / "git-clone-env.txt"
    QuickstartFixture._set_assignments(
        tool_bin / "git",
        {
            "REAL_GIT": real_git,
            "GIT_ENV_RECORD": git_env_record,
            "PROBE_PYTHON": sys.executable,
        },
    )
    QuickstartFixture._set_assignments(tool_bin / "mktemp", {"REAL_MKTEMP": real_mktemp})
    QuickstartFixture._set_assignments(tool_bin / "mkdir", {"REAL_MKDIR": real_mkdir})
    QuickstartFixture._set_assignments(tool_bin / "ps", {"REAL_PS": real_ps})
    QuickstartFixture._set_assignments(tool_bin / "rm", {"REAL_RM": real_rm})

    hook_sentinel = host_pip_target / "git-hook-ran.txt"
    hook_dir = tmp_path / "host-git-hooks"
    _write_executable(
        hook_dir / "post-checkout",
        f"#!/bin/sh\nprintf 'hook escaped\\n' > {shlex.quote(str(hook_sentinel))}\n",
    )
    git_config = tmp_path / "host-gitconfig"
    git_config.write_text(f"[core]\n\thooksPath = {hook_dir}\n", encoding="utf-8")

    sitecustomize_sentinel = host_pip_target / "sitecustomize-ran.txt"
    malicious_pythonpath = tmp_path / "host-pythonpath"
    malicious_pythonpath.mkdir()
    (malicious_pythonpath / "sitecustomize.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(sitecustomize_sentinel)!r}).write_text('sitecustomize escaped\\n')\n",
        encoding="utf-8",
    )
    memory_python_sentinel = host_pip_target / "memory-python-ran.txt"
    agent_python_sentinel = host_pip_target / "agent-python-ran.txt"
    memory_python = tmp_path / "host-memory-python"
    agent_python = tmp_path / "host-agent-python"
    _write_executable(
        memory_python,
        f"#!/bin/sh\nprintf 'memory python escaped\\n' > {shlex.quote(str(memory_python_sentinel))}\n",
    )
    _write_executable(
        agent_python,
        f"#!/bin/sh\nprintf 'agent python escaped\\n' > {shlex.quote(str(agent_python_sentinel))}\n",
    )

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
            "GIT_CONFIG_GLOBAL": str(git_config),
            "PYTHONPATH": str(malicious_pythonpath),
            "PYTHONSTARTUP": str(tmp_path / "host-python-startup.py"),
            "PYTHONINSPECT": "1",
            "MEMORY_PYTHON": str(memory_python),
            "AGENT_MEMORY_HUB_PYTHON": str(agent_python),
            "LANG": "C",
            "LC_ALL": "C",
            "LC_CTYPE": "C",
            "HTTP_PROXY": "http://127.0.0.1:8",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "ALL_PROXY": "socks5://127.0.0.1:7",
            "NO_PROXY": "localhost,127.0.0.1",
            "http_proxy": "http://127.0.0.1:18",
            "https_proxy": "http://127.0.0.1:19",
            "all_proxy": "socks5://127.0.0.1:17",
            "no_proxy": "localhost,127.0.0.1",
            "SSL_CERT_FILE": "fixture-ssl-cert-file",
            "SSL_CERT_DIR": "fixture-ssl-cert-dir",
            "REQUESTS_CA_BUNDLE": "fixture-requests-ca",
            "CURL_CA_BUNDLE": "fixture-ca-preserved",
            "PIP_CERT": "fixture-pip-cert",
            "GIT_SSL_CAINFO": "fixture-git-ca",
            "SSH_AUTH_SOCK": str(inherited / "ssh-agent.sock"),
            "AMH_TEST_PRESERVED": "preserved",
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
        install_script=repo / "install.sh",
        search_script=repo / "agent_runtime_kit" / "tools" / "search-memory.sh",
        git_wrapper=tool_bin / "git",
        mktemp_wrapper=tool_bin / "mktemp",
        mkdir_wrapper=tool_bin / "mkdir",
        ps_wrapper=tool_bin / "ps",
        rm_wrapper=tool_bin / "rm",
        git_env_record=git_env_record,
        attack_sentinels=(
            hook_sentinel,
            sitecustomize_sentinel,
            memory_python_sentinel,
            agent_python_sentinel,
        ),
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
    return bool(current and current.pgid == identity.pgid and current.command == identity.command)


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
        identity.pgid for identity in known_phase_processes if _identity_still_matches(identity)
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


def _run_quickstart(fixture: QuickstartFixture, *args: str) -> subprocess.CompletedProcess[str]:
    env = fixture.env.copy()
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


def _ownership_token(output: str) -> str:
    match = re.search(r"^Ownership:\s+(amh-quickstart:[0-9]+:[0-9]+)$", output, re.MULTILINE)
    assert match, f"quickstart did not print its ownership token:\n{output}"
    return match.group(1)


def _assert_owned_benchmark_root(
    bench_root: Path, allowed_parent: Path, expected_token: str
) -> None:
    assert bench_root.is_dir()
    assert not bench_root.is_symlink()
    resolved_root = bench_root.resolve()
    resolved_parent = allowed_parent.resolve()
    assert resolved_root != resolved_parent
    assert resolved_root.parent == resolved_parent
    assert resolved_root.name.startswith("amh-bench-")
    marker = resolved_root / ".amh-quickstart-owned"
    assert marker.is_file()
    assert not marker.is_symlink()
    assert marker.read_text(encoding="utf-8") == f"{expected_token}\n"


def _remove_kept_benchmark_root(
    bench_root: Path, allowed_parent: Path, expected_token: str
) -> None:
    try:
        _assert_owned_benchmark_root(bench_root, allowed_parent, expected_token)
    except (AssertionError, OSError, UnicodeError):
        return
    shutil.rmtree(bench_root.resolve())


def _read_env(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines())


def _recover_phase_identities(process_state_file: Path) -> tuple[ProcessIdentity, ...]:
    """Best-effort cleanup evidence; a partial state file must never hide a test failure."""
    try:
        saved_state = _read_env(process_state_file)
    except (OSError, UnicodeError, ValueError):
        return ()

    identities: list[ProcessIdentity] = []
    for pid_key, pgid_key, command_marker in (
        ("phase_pid", "phase_pgid", "install.sh"),
        ("child_pid", "child_pgid", "quickstart-resistant-descendant"),
    ):
        try:
            pid = int(saved_state[pid_key])
            expected_pgid = int(saved_state[pgid_key])
        except (KeyError, ValueError):
            continue
        current = _process_identity(pid)
        if (
            current is not None
            and current.pgid == expected_pgid
            and command_marker in current.command
        ):
            identities.append(current)
    return tuple(identities)


def test_keep_cleanup_refuses_nested_parent_and_wrong_run_token(tmp_path: Path) -> None:
    allowed_parent = tmp_path / "allowed"
    allowed_parent.mkdir()

    nested_root = allowed_parent / "nested" / "amh-bench-nested"
    nested_root.mkdir(parents=True)
    nested_sentinel = nested_root / "sentinel.txt"
    nested_sentinel.write_text("keep\n", encoding="utf-8")
    (nested_root / ".amh-quickstart-owned").write_text("amh-quickstart:123:456\n", encoding="utf-8")
    _remove_kept_benchmark_root(nested_root, allowed_parent, "amh-quickstart:123:456")
    assert nested_sentinel.is_file()

    wrong_token_root = allowed_parent / "amh-bench-wrong-token"
    wrong_token_root.mkdir()
    wrong_token_sentinel = wrong_token_root / "sentinel.txt"
    wrong_token_sentinel.write_text("keep\n", encoding="utf-8")
    (wrong_token_root / ".amh-quickstart-owned").write_text(
        "amh-quickstart:123:456\n", encoding="utf-8"
    )
    _remove_kept_benchmark_root(wrong_token_root, allowed_parent, "amh-quickstart:999:999")
    assert wrong_token_sentinel.is_file()

    whitespace_root = allowed_parent / "amh-bench-whitespace"
    whitespace_root.mkdir()
    whitespace_sentinel = whitespace_root / "sentinel.txt"
    whitespace_sentinel.write_text("keep\n", encoding="utf-8")
    (whitespace_root / ".amh-quickstart-owned").write_text(
        " amh-quickstart:123:456\n", encoding="utf-8"
    )
    _remove_kept_benchmark_root(whitespace_root, allowed_parent, "amh-quickstart:123:456")
    assert whitespace_sentinel.is_file()


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


def test_mktemp_failure_stdout_cannot_delete_existing_sentinel_root(
    quickstart_fixture: QuickstartFixture,
) -> None:
    sentinel_root = quickstart_fixture.outer_tmp / "amh-bench-existing-sentinel"
    sentinel_root.mkdir()
    sentinel = sentinel_root / "do-not-delete.txt"
    sentinel.write_text("host sentinel\n", encoding="utf-8")
    before = _tree_manifest(sentinel_root)
    quickstart_fixture.configure_mktemp_failure(sentinel_root)

    result = _run_quickstart(quickstart_fixture)
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "failed to create benchmark root" in output
    assert "✅ PASS" not in output
    assert sentinel_root.is_dir()
    assert _tree_manifest(sentinel_root) == before
    quickstart_fixture.assert_host_unchanged()


def test_successful_mktemp_symlink_with_trailing_slash_cannot_escape_root(
    quickstart_fixture: QuickstartFixture,
    tmp_path: Path,
) -> None:
    external_root = tmp_path / "external-sentinel-root"
    external_root.mkdir()
    sentinel = external_root / "do-not-delete.txt"
    sentinel.write_text("external sentinel\n", encoding="utf-8")
    sentinel.chmod(0o640)
    before = _tree_manifest(external_root)
    symlink_root = quickstart_fixture.outer_tmp / "amh-bench-symlink"
    symlink_root.symlink_to(external_root, target_is_directory=True)
    quickstart_fixture.configure_mktemp_symlink_success(f"{symlink_root}/")

    try:
        result = _run_quickstart(quickstart_fixture)
        output = result.stdout + result.stderr

        assert result.returncode == 1, output
        assert "failed to create benchmark root" in output
        assert "✅ PASS" not in output
        assert symlink_root.is_symlink()
        assert external_root.is_dir()
        assert sentinel.is_file()
        assert stat.S_IMODE(sentinel.stat().st_mode) == 0o640
        assert sentinel.read_text(encoding="utf-8") == "external sentinel\n"
        assert _tree_manifest(external_root) == before
        quickstart_fixture.assert_host_unchanged()
    finally:
        if symlink_root.is_symlink():
            symlink_root.unlink()
        shutil.rmtree(external_root, ignore_errors=True)


def test_bootstrap_failure_is_fail_closed_and_cleans_benchmark_root(
    quickstart_fixture: QuickstartFixture,
) -> None:
    quickstart_fixture.configure_bootstrap_failure()
    result = _run_quickstart(quickstart_fixture)
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "failed to initialize benchmark root" in output
    assert "✅ PASS" not in output
    assert list(quickstart_fixture.outer_tmp.iterdir()) == []
    quickstart_fixture.assert_host_unchanged()


@pytest.mark.parametrize("rm_mode", ["fail_cleanup", "signal_cleanup"])
def test_cleanup_rm_failure_warns_and_retains_owned_root(
    quickstart_fixture: QuickstartFixture,
    rm_mode: str,
) -> None:
    quickstart_fixture.configure_cleanup_rm_failure(rm_mode)
    result = _run_quickstart(quickstart_fixture)
    output = result.stdout + result.stderr
    bench_root = _bench_root(output)
    ownership_token = _ownership_token(output)

    try:
        assert result.returncode == 0, output
        assert f"warning: failed to remove owned benchmark root: {bench_root}" in output
        _assert_owned_benchmark_root(bench_root, quickstart_fixture.outer_tmp, ownership_token)
        quickstart_fixture.assert_host_unchanged()
    finally:
        _remove_kept_benchmark_root(bench_root, quickstart_fixture.outer_tmp, ownership_token)


@pytest.mark.parametrize("ps_mode", ["hide_pgid", "report_benchmark_pgid"])
def test_invalid_phase_pgid_fails_closed_before_phase_command_executes(
    quickstart_fixture: QuickstartFixture,
    ps_mode: str,
) -> None:
    quickstart_fixture.configure_pgid_failure(ps_mode)
    result = _run_quickstart(quickstart_fixture)
    output = result.stdout + result.stderr
    bench_root = _bench_root(output)

    assert result.returncode == 1, output
    assert "failed to establish isolated process group" in output
    assert "✅ PASS" not in output
    assert not quickstart_fixture.git_env_record.exists()
    assert not bench_root.exists()
    quickstart_fixture.assert_host_unchanged()


def test_explicit_allowlist_blocks_host_execution_injection(
    quickstart_fixture: QuickstartFixture,
) -> None:
    result = _run_quickstart(quickstart_fixture, "--keep")
    output = result.stdout + result.stderr
    bench_root = _bench_root(output)
    ownership_token = _ownership_token(output)

    try:
        assert result.returncode == 0, output
        _assert_owned_benchmark_root(bench_root, quickstart_fixture.outer_tmp, ownership_token)
        escaped = [str(path) for path in quickstart_fixture.attack_sentinels if path.exists()]
        assert escaped == []
        forbidden_keys = (
            "GIT_CONFIG_GLOBAL",
            "PYTHONPATH",
            "PYTHONSTARTUP",
            "PYTHONINSPECT",
            "MEMORY_PYTHON",
            "AGENT_MEMORY_HUB_PYTHON",
        )
        phase_envs = (
            _read_env(quickstart_fixture.git_env_record),
            _read_env(bench_root / "home" / "install-env.txt"),
            _read_env(bench_root / "home" / "search-env.txt"),
        )
        for phase_env in phase_envs:
            assert {key: phase_env[key] for key in forbidden_keys} == {
                key: "<unset>" for key in forbidden_keys
            }
        quickstart_fixture.assert_host_unchanged()
    finally:
        _remove_kept_benchmark_root(bench_root, quickstart_fixture.outer_tmp, ownership_token)


def test_known_git_and_pip_write_overrides_cannot_escape_benchmark_root(
    quickstart_fixture: QuickstartFixture,
) -> None:
    result = _run_quickstart(quickstart_fixture)
    output = result.stdout + result.stderr
    bench_root = _bench_root(output)
    escaped_paths = [
        Path(quickstart_fixture.env["GIT_OBJECT_DIRECTORY"]) / "quickstart-pollution.txt",
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
    ownership_token = _ownership_token(output)

    try:
        assert result.returncode == 0, output
        _assert_owned_benchmark_root(bench_root, quickstart_fixture.outer_tmp, ownership_token)
        for relative in (
            "home",
            "brain",
            "cache/pip",
            "tmp",
            "xdg-config",
            "xdg-data",
            "xdg-state",
            "pyuserbase",
            "pycache",
            "cargo",
            "cargo-target",
            "rustup",
            "uv-cache",
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
            "XDG_RUNTIME_DIR": "<unset>",
            "PIP_CONFIG_FILE": "/dev/null",
            "PIP_CACHE_DIR": str(bench_root / "cache" / "pip"),
            "PIP_LOG": "<unset>",
            "PIP_REPORT": "<unset>",
            "PIP_BUILD_TRACKER": "<unset>",
            "PIP_DOWNLOAD_CACHE": "<unset>",
            "PIP_SRC": "<unset>",
            "PYTHONUSERBASE": str(bench_root / "pyuserbase"),
            "PYTHONPYCACHEPREFIX": str(bench_root / "pycache"),
            "CARGO_HOME": str(bench_root / "cargo"),
            "RUSTUP_HOME": str(bench_root / "rustup"),
            "CARGO_TARGET_DIR": str(bench_root / "cargo-target"),
            "UV_CACHE_DIR": str(bench_root / "uv-cache"),
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
            "LANG": "C",
            "LC_ALL": "C",
            "LC_CTYPE": "C",
            "HTTP_PROXY": "http://127.0.0.1:8",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "ALL_PROXY": "socks5://127.0.0.1:7",
            "NO_PROXY": "localhost,127.0.0.1",
            "http_proxy": "http://127.0.0.1:18",
            "https_proxy": "http://127.0.0.1:19",
            "all_proxy": "socks5://127.0.0.1:17",
            "no_proxy": "localhost,127.0.0.1",
            "SSL_CERT_FILE": "fixture-ssl-cert-file",
            "SSL_CERT_DIR": "fixture-ssl-cert-dir",
            "REQUESTS_CA_BUNDLE": "fixture-requests-ca",
            "CURL_CA_BUNDLE": "fixture-ca-preserved",
            "PIP_CERT": "fixture-pip-cert",
            "GIT_SSL_CAINFO": "fixture-git-ca",
            "SSH_AUTH_SOCK": str(quickstart_fixture.env["SSH_AUTH_SOCK"]),
            "AMH_TEST_PRESERVED": "<unset>",
            "GIT_CONFIG_GLOBAL": "<unset>",
            "PYTHONPATH": "<unset>",
            "PYTHONSTARTUP": "<unset>",
            "PYTHONINSPECT": "<unset>",
            "MEMORY_PYTHON": "<unset>",
            "AGENT_MEMORY_HUB_PYTHON": "<unset>",
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
                "GIT_CONFIG_GLOBAL",
                "PYTHONPATH",
                "PYTHONSTARTUP",
                "PYTHONINSPECT",
                "MEMORY_PYTHON",
                "AGENT_MEMORY_HUB_PYTHON",
            )
        }
        shim = bench_root / "home" / ".local" / "bin" / "memory"
        assert shim.is_file()
        assert shim.is_relative_to(bench_root)
        assert (bench_root / "home" / ".claude" / "commands" / "remember.md").is_file()
        assert "search-line-4" not in output
        quickstart_fixture.assert_host_unchanged()
    finally:
        _remove_kept_benchmark_root(bench_root, quickstart_fixture.outer_tmp, ownership_token)


def test_keep_clone_failure_retains_owned_root_and_complete_clone_log(
    quickstart_fixture: QuickstartFixture,
) -> None:
    quickstart_fixture.configure_clone_failure()
    result = _run_quickstart(quickstart_fixture, "--keep")
    output = result.stdout + result.stderr
    bench_root = _bench_root(output)
    ownership_token = _ownership_token(output)

    try:
        assert result.returncode == 1, output
        assert "clone failed" in output
        _assert_owned_benchmark_root(bench_root, quickstart_fixture.outer_tmp, ownership_token)
        clone_log = bench_root / "clone.log"
        assert clone_log.is_file()
        clone_lines = clone_log.read_text(encoding="utf-8").splitlines()
        assert clone_lines == [f"clone-log-{line:03d}" for line in range(1, 101)]
        output_clone_lines = [line for line in output.splitlines() if line.startswith("clone-log-")]
        assert output_clone_lines == clone_lines[-80:]
        quickstart_fixture.assert_host_unchanged()
    finally:
        _remove_kept_benchmark_root(bench_root, quickstart_fixture.outer_tmp, ownership_token)


def test_keep_install_failure_retains_owned_root_and_complete_install_log(
    quickstart_fixture: QuickstartFixture,
) -> None:
    quickstart_fixture.configure_install("exit42")
    result = _run_quickstart(quickstart_fixture, "--keep")
    output = result.stdout + result.stderr
    bench_root = _bench_root(output)
    ownership_token = _ownership_token(output)

    try:
        assert result.returncode == 1, output
        assert "install.sh failed" in output
        _assert_owned_benchmark_root(bench_root, quickstart_fixture.outer_tmp, ownership_token)
        install_log = bench_root / "install.log"
        assert install_log.is_file()
        install_lines = install_log.read_text(encoding="utf-8").splitlines()
        assert install_lines == [f"install-log-{line:03d}" for line in range(1, 101)]
        output_install_lines = [
            line for line in output.splitlines() if line.startswith("install-log-")
        ]
        assert output_install_lines == install_lines[-80:]
        quickstart_fixture.assert_host_unchanged()
    finally:
        _remove_kept_benchmark_root(bench_root, quickstart_fixture.outer_tmp, ownership_token)


def test_keep_search_failure_retains_owned_root_and_complete_search_log(
    quickstart_fixture: QuickstartFixture,
) -> None:
    quickstart_fixture.configure_search_exit(43)
    result = _run_quickstart(quickstart_fixture, "--keep")
    output = result.stdout + result.stderr
    bench_root = _bench_root(output)
    ownership_token = _ownership_token(output)

    try:
        assert result.returncode == 1, output
        assert "first search failed" in output
        _assert_owned_benchmark_root(bench_root, quickstart_fixture.outer_tmp, ownership_token)
        search_log = bench_root / "search.log"
        assert search_log.is_file()
        search_lines = search_log.read_text(encoding="utf-8").splitlines()
        assert search_lines == [
            "search-line-1",
            "search-line-2",
            "search-line-3",
            "search-line-4",
            *(f"search-log-{line:03d}" for line in range(1, 101)),
        ]
        output_search_lines = [
            line for line in output.splitlines() if line.startswith("search-log-")
        ]
        assert output_search_lines == search_lines[-80:]
        quickstart_fixture.assert_host_unchanged()
    finally:
        _remove_kept_benchmark_root(bench_root, quickstart_fixture.outer_tmp, ownership_token)


def test_keep_shim_targets_real_clone_venv_inside_benchmark_root(
    quickstart_fixture: QuickstartFixture,
) -> None:
    result = _run_quickstart(quickstart_fixture, "--keep")
    output = result.stdout + result.stderr
    bench_root = _bench_root(output)
    ownership_token = _ownership_token(output)

    try:
        assert result.returncode == 0, output
        _assert_owned_benchmark_root(bench_root, quickstart_fixture.outer_tmp, ownership_token)
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
        _remove_kept_benchmark_root(bench_root, quickstart_fixture.outer_tmp, ownership_token)


def test_install_failure_is_nonzero_cleans_up_and_preserves_host(
    quickstart_fixture: QuickstartFixture,
) -> None:
    quickstart_fixture.configure_install("exit42")
    result = _run_quickstart(quickstart_fixture)
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
    quickstart_fixture.configure_clone_failure()
    result = _run_quickstart(quickstart_fixture)
    output = result.stdout + result.stderr
    bench_root = _bench_root(output)
    clone_env = _read_env(quickstart_fixture.git_env_record)
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
    quickstart_fixture.configure_search_exit(43)
    result = _run_quickstart(quickstart_fixture)
    output = result.stdout + result.stderr
    bench_root = _bench_root(output)

    assert result.returncode == 1, output
    assert "first search failed" in output
    assert "✅ PASS" not in output
    assert not bench_root.exists()
    quickstart_fixture.assert_host_unchanged()


@pytest.mark.parametrize(
    ("first_signal", "opposite_signal", "expected_returncode"),
    [
        (signal.SIGINT, signal.SIGTERM, 130),
        (signal.SIGTERM, signal.SIGINT, 143),
    ],
)
def test_startup_cross_signal_preserves_first_code_without_starting_phase(
    quickstart_fixture: QuickstartFixture,
    tmp_path: Path,
    first_signal: signal.Signals,
    opposite_signal: signal.Signals,
    expected_returncode: int,
) -> None:
    case_name = f"{first_signal.name}-{opposite_signal.name}"
    first_signal_file = tmp_path / f"startup-first-{case_name}"
    phase_state_file = tmp_path / f"startup-phase-{case_name}"
    startup_ready_file = tmp_path / f"startup-ready-{case_name}"
    handoff_ready_file = tmp_path / f"handoff-ready-{case_name}"
    handoff_release_file = tmp_path / f"handoff-release-{case_name}"
    quickstart_fixture.configure_startup_signal_handoff(
        first_signal_file=first_signal_file,
        phase_state_file=phase_state_file,
        startup_ready_file=startup_ready_file,
        handoff_ready_file=handoff_ready_file,
        handoff_release_file=handoff_release_file,
    )
    process = subprocess.Popen(
        [str(quickstart_fixture.script)],
        cwd=quickstart_fixture.repo,
        env=quickstart_fixture.env.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    output = ""
    phase_identity: ProcessIdentity | None = None
    phase_pgid = 0
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not startup_ready_file.exists():
            assert process.poll() is None, "quickstart exited before startup gate"
            time.sleep(0.01)
        assert startup_ready_file.exists(), "quickstart never reached startup gate"

        phase_state = _read_env(phase_state_file)
        phase_pid = int(phase_state["phase_pid"])
        phase_pgid = int(phase_state["phase_pgid"])
        phase_identity = _process_identity(phase_pid)
        assert phase_identity is not None
        assert phase_identity.pgid == phase_pgid
        assert phase_identity.state.startswith("T"), phase_identity

        process.send_signal(first_signal)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not first_signal_file.exists():
            assert process.poll() is None, "quickstart exited before recording first signal"
            time.sleep(0.01)
        assert first_signal_file.read_text(encoding="utf-8").strip() == str(expected_returncode)

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not handoff_ready_file.exists():
            assert process.poll() is None, "quickstart exited before signal handoff gate"
            time.sleep(0.01)
        assert handoff_ready_file.exists(), "quickstart never reached signal handoff gate"
        assert not quickstart_fixture.git_env_record.exists()

        burst_started = time.monotonic()
        released = False
        while time.monotonic() - burst_started < 0.12 and process.poll() is None:
            try:
                process.send_signal(opposite_signal)
            except ProcessLookupError:
                break
            if not released and time.monotonic() - burst_started >= 0.04:
                handoff_release_file.touch()
                released = True
            time.sleep(0.005)
        if not released:
            handoff_release_file.touch()
        output, _ = process.communicate(timeout=10)

        group_deadline = time.monotonic() + 3
        while time.monotonic() < group_deadline and _live_processes_in_group(phase_pgid):
            time.sleep(0.05)
        assert not _live_processes_in_group(phase_pgid)
    finally:
        handoff_release_file.touch(exist_ok=True)
        known = (phase_identity,) if phase_identity is not None else ()
        _cleanup_benchmark_process(process, known)

    bench_root = _bench_root(output)
    assert process.returncode == expected_returncode, output
    assert "✅ PASS" not in output
    assert not quickstart_fixture.git_env_record.exists()
    assert not bench_root.exists()
    quickstart_fixture.assert_host_unchanged()


@pytest.mark.parametrize(
    ("sent_signal", "expected_returncode"),
    [(signal.SIGINT, 130), (signal.SIGTERM, 143)],
)
def test_signal_teardown_uses_anchored_group_when_runtime_ps_fails(
    quickstart_fixture: QuickstartFixture,
    tmp_path: Path,
    sent_signal: signal.Signals,
    expected_returncode: int,
) -> None:
    ready_file = tmp_path / f"runtime-ps-ready-{sent_signal.name}"
    process_state_file = tmp_path / f"runtime-ps-processes-{sent_signal.name}"
    ps_mode_file = tmp_path / f"runtime-ps-mode-{sent_signal.name}"
    ps_mode_file.write_text("normal\n", encoding="utf-8")
    quickstart_fixture.configure_install(
        "wait_resistant",
        ready_file=ready_file,
        process_state_file=process_state_file,
    )
    quickstart_fixture.configure_runtime_ps_mode(ps_mode_file)
    process = subprocess.Popen(
        [str(quickstart_fixture.script)],
        cwd=quickstart_fixture.repo,
        env=quickstart_fixture.env.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    output = ""
    phase_identity: ProcessIdentity | None = None
    child_identity: ProcessIdentity | None = None
    phase_pgid = 0
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not ready_file.exists():
            assert process.poll() is None, "quickstart exited before resistant phase was ready"
            time.sleep(0.05)
        assert ready_file.exists(), "resistant phase never became ready"
        process_state = _read_env(process_state_file)
        phase_pid = int(process_state["phase_pid"])
        phase_pgid = int(process_state["phase_pgid"])
        child_pid = int(process_state["child_pid"])
        phase_identity = _process_identity(phase_pid)
        child_identity = _process_identity(child_pid)
        assert phase_identity is not None
        assert child_identity is not None
        assert phase_identity.pgid == phase_pgid
        assert child_identity.pgid == phase_pgid

        ps_mode_file.write_text("fail_process_queries\n", encoding="utf-8")
        process.send_signal(sent_signal)
        output, _ = process.communicate(timeout=10)

        group_deadline = time.monotonic() + 3
        while time.monotonic() < group_deadline and _live_processes_in_group(phase_pgid):
            time.sleep(0.05)
        assert not _same_process_is_running(phase_identity)
        assert not _same_process_is_running(child_identity)
        assert not _live_processes_in_group(phase_pgid)
    finally:
        known_phase_processes = [
            identity for identity in (phase_identity, child_identity) if identity is not None
        ]
        known_phase_processes.extend(_recover_phase_identities(process_state_file))
        _cleanup_benchmark_process(process, tuple(known_phase_processes))

    bench_root = _bench_root(output)
    assert process.returncode == expected_returncode, output
    assert "✅ PASS" not in output
    assert not bench_root.exists()
    quickstart_fixture.assert_host_unchanged()


@pytest.mark.parametrize(
    ("signal_sequence", "expected_returncode"),
    [
        ((signal.SIGINT,), 130),
        ((signal.SIGTERM,), 143),
        ((signal.SIGINT, signal.SIGTERM), 130),
        ((signal.SIGTERM, signal.SIGINT), 143),
    ],
)
def test_signal_sequence_cleans_only_benchmark_root_and_preserves_host(
    quickstart_fixture: QuickstartFixture,
    tmp_path: Path,
    signal_sequence: tuple[signal.Signals, ...],
    expected_returncode: int,
) -> None:
    sequence_name = "-".join(item.name for item in signal_sequence)
    ready_file = tmp_path / f"install-ready-{sequence_name}"
    process_state_file = tmp_path / f"install-processes-{sequence_name}"
    quickstart_fixture.configure_install(
        "wait_resistant",
        ready_file=ready_file,
        process_state_file=process_state_file,
    )
    env = quickstart_fixture.env.copy()
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

        process.send_signal(signal_sequence[0])
        if len(signal_sequence) == 2:
            time.sleep(0.1)
            process.send_signal(signal_sequence[1])
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
        known_phase_processes.extend(_recover_phase_identities(process_state_file))
        _cleanup_benchmark_process(process, tuple(known_phase_processes))

    bench_root = _bench_root(output)
    assert process.returncode == expected_returncode, output
    assert "✅ PASS" not in output
    assert not bench_root.exists()
    quickstart_fixture.assert_host_unchanged()
