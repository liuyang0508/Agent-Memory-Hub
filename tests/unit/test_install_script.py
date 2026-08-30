"""Smoke tests for the one-click installer and uninstall path."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "install.sh"
INSTALL_PS1 = REPO_ROOT / "install.ps1"
NPM_PACKAGE = REPO_ROOT / "package.json"
NPM_BIN = REPO_ROOT / "packaging/npm/bin/agent-memory-hub.js"
NPM_POSTINSTALL = REPO_ROOT / "packaging/npm/scripts/postinstall.js"
HOMEBREW_CASK = REPO_ROOT / "Casks/agent-memory-hub.rb"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell") or shutil.which("powershell.exe")


def _run_install(args: list[str], tmp_path: Path, *, script: Path = INSTALL_SH) -> subprocess.CompletedProcess[str]:
    env = {
        "HOME": str(tmp_path / "home"),
        "BRAIN_DIR": str(tmp_path / "brain"),
        "AGENT_MEMORY_HUB_HOME": str(tmp_path / "checkout"),
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
        "PYTHONUSERBASE": str(tmp_path / "pyuserbase"),
        "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
    }
    (tmp_path / "home").mkdir(exist_ok=True)
    return subprocess.run(
        ["sh", str(script), *args],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        check=False,
    )


def _run_windows_uninstall(
    args: list[str],
    tmp_path: Path,
    *,
    brain_dir: Path | None = None,
    script: Path = INSTALL_PS1,
    checkout_dir: Path = REPO_ROOT,
) -> subprocess.CompletedProcess[str]:
    if not POWERSHELL:
        raise RuntimeError("PowerShell is unavailable")
    env = os.environ.copy()
    env.update(
        {
            "AMH_USER_HOME": str(tmp_path / "home"),
            "BRAIN_DIR": str(brain_dir or (tmp_path / "brain")),
            "AGENT_MEMORY_HUB_HOME": str(checkout_dir),
        }
    )
    return subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Uninstall",
            "-KeepPackage",
            *args,
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        check=False,
    )


def _run_windows_install(
    tmp_path: Path,
    *,
    fail_adapter: str = "",
) -> tuple[subprocess.CompletedProcess[str], Path]:
    if not POWERSHELL:
        raise RuntimeError("PowerShell is unavailable")
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir(exist_ok=True)
    adapter_log = tmp_path / "adapter-actions.log"
    (fake_bin / "python.cmd").write_text(
        "@echo off\r\n"
        "if \"%1\"==\"-c\" exit /b 0\r\n"
        "if \"%1\"==\"-m\" if \"%2\"==\"pip\" exit /b 0\r\n"
        "if not \"%1\"==\"-m\" exit /b 1\r\n"
        "if not \"%2\"==\"agent_brain.interfaces.cli\" exit /b 1\r\n"
        "if \"%3\"==\"sync-pending\" echo %3 %4 %5>>\"%AMH_TEST_SYNC_LOG%\"\r\n"
        "if \"%3\"==\"sync-pending\" exit /b 0\r\n"
        "if not \"%3\"==\"adapter\" exit /b 0\r\n"
        "echo %3 %4 %5>>\"%AMH_TEST_ADAPTER_LOG%\"\r\n"
        "if \"%5\"==\"%AMH_TEST_FAIL_ADAPTER%\" exit /b 7\r\n"
        "exit /b 0\r\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(
        {
            "AMH_USER_HOME": str(tmp_path / "home"),
            "BRAIN_DIR": str(tmp_path / "brain"),
            "AGENT_MEMORY_HUB_HOME": str(REPO_ROOT),
            "AMH_INSTALL_ADAPTERS": "codex claude_code",
            "AMH_TEST_ADAPTER_LOG": str(adapter_log),
            "AMH_TEST_SYNC_LOG": str(tmp_path / "sync-actions.log"),
            "AMH_TEST_FAIL_ADAPTER": fail_adapter,
            "AGENT_MEMORY_HUB_PYTHON": str(fake_bin / "python.cmd"),
            "AGENT_MEMORY_HUB_APP_PYTHON": str(fake_bin / "python.cmd"),
            "AMH_SKIP_PATH_UPDATE": "1",
            "PATH": str(fake_bin) + os.pathsep + env.get("PATH", ""),
        }
    )
    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(INSTALL_PS1),
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        check=False,
    )
    return result, adapter_log


def test_install_script_syntax_is_portable_shell():
    result = subprocess.run(
        ["sh", "-n", str(INSTALL_SH)],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stdout


def test_install_script_local_verify_only_checks_required_assets(tmp_path: Path):
    result = _run_install(["--verify-only"], tmp_path)

    assert result.returncode == 0, result.stdout
    assert "Agent Memory Hub local install verification" in result.stdout
    assert "installer_self_check=ok" in result.stdout
    assert "agent_runtime_kit/hooks/inject-context.sh" in result.stdout
    assert "agent_runtime_kit/tools/write-memory.sh" in result.stdout


def test_install_script_remote_verify_only_checks_clone_preconditions(tmp_path: Path):
    copied = tmp_path / "remote-install.sh"
    shutil.copy2(INSTALL_SH, copied)
    result = _run_install(["--verify-only"], tmp_path, script=copied)

    assert result.returncode == 0, result.stdout
    assert "Agent Memory Hub remote install verification" in result.stdout
    assert "target:" in result.stdout


def test_release_installers_are_published_from_github_release_assets():
    script = INSTALL_SH.read_text(encoding="utf-8")
    powershell = INSTALL_PS1.read_text(encoding="utf-8")
    workflow = (REPO_ROOT / ".github/workflows/release-installers.yml").read_text(
        encoding="utf-8"
    )

    for text in (script, powershell, workflow):
        assert "aihub0508" not in text
        assert "<owner>/agent-memory-hub" not in text

    assert "https://github.com/liuyang0508/agent-memory-hub.git" in script
    assert "__AMH_GITHUB_REPOSITORY__" in script
    assert "__AMH_GITHUB_REF_NAME__" in script
    assert 'AMH_REF:-${AMH_BRANCH:-$RELEASE_REF}' in script
    assert "checkout --detach FETCH_HEAD" in script

    assert "https://github.com/liuyang0508/agent-memory-hub.git" in powershell
    assert "__AMH_GITHUB_REPOSITORY__" in powershell
    assert "__AMH_GITHUB_REF_NAME__" in powershell

    assert "softprops/action-gh-release@v2" in workflow
    assert "dist/install.sh" in workflow
    assert "dist/install.ps1" in workflow
    assert "dist/checksums.txt" in workflow
    assert "${{ github.repository }}" in workflow
    assert "${{ github.ref_name }}" in workflow


def test_npm_package_is_an_installer_channel():
    package = json.loads(NPM_PACKAGE.read_text(encoding="utf-8"))
    version_ns: dict[str, str] = {}
    exec((REPO_ROOT / "agent_brain/_version.py").read_text(encoding="utf-8"), version_ns)

    assert package["name"] == "agent-memory-hub"
    assert package["version"] == version_ns["__version__"]
    assert package["bin"] == {"agent-memory-hub": "packaging/npm/bin/agent-memory-hub.js"}
    assert package["scripts"]["postinstall"] == "node packaging/npm/scripts/postinstall.js"
    assert "install.sh" in package["files"]
    assert "install.ps1" in package["files"]
    assert "packaging/npm/bin/" in package["files"]
    assert "packaging/npm/scripts/" in package["files"]

    bin_script = NPM_BIN.read_text(encoding="utf-8")
    postinstall = NPM_POSTINSTALL.read_text(encoding="utf-8")

    assert "install.sh / install.ps1" in bin_script
    assert "AGENT_MEMORY_HUB_NPM_SKIP_INSTALL" in bin_script
    assert "AGENT_MEMORY_HUB_NPM_SKIP_INSTALL" in postinstall
    assert "AMH_RELEASE_REF" in postinstall
    assert "v${packageJson.version}" in postinstall
    assert "AGENT_MEMORY_HUB_NPM_INSTALL_ARGS" in postinstall


def test_homebrew_cask_delegates_to_release_installer():
    cask = HOMEBREW_CASK.read_text(encoding="utf-8")

    assert 'cask "agent-memory-hub"' in cask
    assert "releases/latest/download/install.sh" in cask
    assert 'verified: "github.com/liuyang0508/agent-memory-hub/"' in cask
    assert 'executable: "/bin/sh"' in cask
    assert '"#{staged_path}/install.sh"' in cask
    assert '"--uninstall"' in cask
    assert "raw.githubusercontent.com" not in cask


def test_npm_publish_workflow_uses_npm_token_and_public_package():
    workflow = (REPO_ROOT / ".github/workflows/publish-npm.yml").read_text(
        encoding="utf-8"
    )

    assert "actions/setup-node@v4" in workflow
    assert 'registry-url: "https://registry.npmjs.org"' in workflow
    assert "npm pack --dry-run" in workflow
    assert "npm publish --access public" in workflow
    assert "NODE_AUTH_TOKEN: ${{ secrets.NPM_TOKEN }}" in workflow


def test_install_script_local_dry_run_reports_minimal_action(tmp_path: Path):
    result = _run_install(["--dry-run", "--minimal"], tmp_path)

    assert result.returncode == 0, result.stdout
    assert "Agent Memory Hub local install dry run" in result.stdout
    assert "action:  install" in result.stdout
    assert "minimal: true" in result.stdout


def test_install_script_uses_project_venv_and_managed_cli_shim():
    script = INSTALL_SH.read_text(encoding="utf-8")

    assert 'APP_VENV="$CODE_DIR/.venv"' in script
    assert 'MEMORY_BIN="$APP_VENV/bin/memory"' in script
    assert 'MEMORY_SHIM="$USER_BIN/memory"' in script
    assert '"$MEMORY_BIN" reindex' in script
    assert 'if "$MEMORY_BIN" reindex' in script
    assert "索引构建失败" in script
    assert '"$MEMORY_BIN" reindex 2>&1 || true' not in script
    assert 'python3 -m pip install -e "$CODE_DIR" --user' not in script
    assert 'command -v memory >/dev/null' not in script


def test_install_script_wires_all_supported_adapters_through_single_entrypoint():
    script = INSTALL_SH.read_text(encoding="utf-8")

    assert 'AMH_INSTALL_ADAPTERS="${AMH_INSTALL_ADAPTERS:-' in script
    for adapter in [
        "codex",
        "claude_code",
        "wukong",
        "cursor",
        "cline",
        "continue_dev",
        "hermes_agent",
        "qoder",
        "qoder_work",
        "aider",
        "github_copilot",
        "aone_copilot",
        "openhuman",
        "opensquilla",
        "openclaw",
    ]:
        assert adapter in script

    assert '"$MEMORY_BIN" adapter install "$adapter"' in script
    assert '"$MEMORY_BIN" adapter uninstall "$adapter"' in script
    assert "optional_adapter_not_configured" in script
    assert "adapter_install_partial_failures" not in script
    assert "adapter_uninstall_partial_failures" in script


def test_install_script_completion_copy_is_adapter_failure_aware():
    script = INSTALL_SH.read_text(encoding="utf-8")

    assert "ADAPTER_STATUS_LABEL=" in script
    assert "ADAPTER_MODULE_COPY=" in script
    assert "安装完成；部分可选 Agent 未配置" in script
    assert "已配置 adapter:" in script
    assert "可选未配置 adapter:" in script
    assert 'ADAPTER_CONFIGURED_COPY=" 无"' in script
    assert "已配置 adapter:${ADAPTER_CONFIGURED_COPY}；可选未配置 adapter:${ADAPTER_INSTALL_FAILED}" in script
    assert "安装完成，但部分 Agent Adapter 配置失败" not in script
    assert "失败:$ADAPTER_INSTALL_FAILED" not in script
    assert "Agent Adapter: $ADAPTER_MODULE_COPY" in script
    assert "全量 Agent Adapter: $AMH_INSTALL_ADAPTERS" not in script


def test_install_uninstall_removes_only_hub_owned_config_and_keeps_user_data(tmp_path: Path):
    home = tmp_path / "home"
    claude_dir = home / ".claude"
    commands_dir = claude_dir / "commands"
    claude_dir.mkdir(parents=True)
    commands_dir.mkdir()
    brain_dir = tmp_path / "brain"
    brain_dir.mkdir()
    (brain_dir / "keep.txt").write_text("do not remove", encoding="utf-8")
    (commands_dir / "remember.md").write_text("hub command", encoding="utf-8")
    settings = claude_dir / "settings.json"
    hub_hook = f"{REPO_ROOT}/agent_runtime_kit/hooks/inject-context.sh"
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {"matcher": "", "hooks": [{"type": "command", "command": hub_hook}]},
                        {"matcher": "", "hooks": [{"type": "command", "command": "/usr/bin/true"}]},
                    ],
                    "Stop": [
                        {
                            "matcher": "",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"{REPO_ROOT}/agent_runtime_kit/hooks/session-end-signal.sh",
                                }
                            ],
                        }
                    ],
                },
                "mcpServers": {
                    "agent-memory-hub": {
                        "command": f"{REPO_ROOT}/agent_runtime_kit/mcp/server.sh"
                    },
                    "other-server": {"command": "/usr/bin/true"},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = _run_install(["--uninstall"], tmp_path)

    assert result.returncode == 0, result.stdout
    assert "卸载完成" in result.stdout
    assert "removed_hooks=2" in result.stdout
    assert "removed_mcp=1" in result.stdout
    assert (brain_dir / "keep.txt").exists()
    assert not (commands_dir / "remember.md").exists()
    updated = json.loads(settings.read_text(encoding="utf-8"))
    assert updated["mcpServers"] == {"other-server": {"command": "/usr/bin/true"}}
    assert updated["hooks"]["UserPromptSubmit"] == [
        {"matcher": "", "hooks": [{"type": "command", "command": "/usr/bin/true"}]}
    ]
    assert updated["hooks"]["Stop"] == []


def test_windows_uninstall_removes_owned_codex_config_and_is_idempotent(tmp_path: Path):
    if not POWERSHELL:
        return

    home = tmp_path / "home"
    codex_dir = home / ".codex"
    claude_commands = home / ".claude" / "commands"
    codex_dir.mkdir(parents=True)
    claude_commands.mkdir(parents=True)
    brain_dir = tmp_path / "brain"
    brain_dir.mkdir()
    (brain_dir / "keep.txt").write_text("keep", encoding="utf-8")

    agents = codex_dir / "AGENTS.md"
    agents.write_text(
        "user-before\n\n"
        "<!-- BEGIN agent-memory-hub -->\nmanaged\n<!-- END agent-memory-hub -->\n\n"
        "user-after\n",
        encoding="utf-8",
    )

    hooks_json = codex_dir / "hooks.json"
    hub_hook = str(REPO_ROOT / "agent_runtime_kit/hooks/inject-context.sh")
    hooks_json.write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {
                            "hooks": [
                                {"type": "command", "command": hub_hook},
                                {"type": "command", "command": "echo user-hook"},
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    hub_state_key = f"{hooks_json}:user_prompt_submit:0:0"
    user_state_key = f"{hooks_json}:user_prompt_submit:0:1"
    config = codex_dir / "config.toml"
    config.write_text(
        "model = \"keep-me\"\n\n"
        "[hooks.state]\n\n"
        f"[hooks.state.'{hub_state_key}']\ntrusted_hash = \"sha256:hub\"\n\n"
        f"[hooks.state.'{user_state_key}']\ntrusted_hash = \"sha256:user\"\n\n"
        "[mcp_servers.agent-memory-hub]\ncommand = \"amh\"\n\n"
        "[mcp_servers.other]\ncommand = \"keep\"\n",
        encoding="utf-8",
    )
    remember = claude_commands / "remember.md"
    remember.write_text("use agent-memory-hub write-memory.sh", encoding="utf-8")

    result = _run_windows_uninstall([], tmp_path)

    assert result.returncode == 0, result.stdout
    assert "Uninstall complete." in result.stdout
    assert (brain_dir / "keep.txt").exists()
    agents_text = agents.read_text(encoding="utf-8")
    assert "BEGIN agent-memory-hub" not in agents_text
    assert "user-before" in agents_text
    assert "user-after" in agents_text
    hooks = json.loads(hooks_json.read_text(encoding="utf-8"))
    assert hooks["hooks"]["UserPromptSubmit"] == [
        {"hooks": [{"type": "command", "command": "echo user-hook"}]}
    ]
    config_text = config.read_text(encoding="utf-8")
    assert "mcp_servers.agent-memory-hub" not in config_text
    assert hub_state_key not in config_text
    assert user_state_key in config_text
    assert "mcp_servers.other" in config_text
    assert not remember.exists()

    before_second_run = {
        path: path.read_text(encoding="utf-8")
        for path in (agents, hooks_json, config)
    }
    second = _run_windows_uninstall([], tmp_path)
    assert second.returncode == 0, second.stdout
    assert before_second_run == {
        path: path.read_text(encoding="utf-8")
        for path in (agents, hooks_json, config)
    }


def test_windows_uninstall_purge_data_requires_explicit_switch(tmp_path: Path):
    if not POWERSHELL:
        return

    (tmp_path / "home").mkdir()
    brain_dir = tmp_path / "brain"
    brain_dir.mkdir()
    (brain_dir / "memory.md").write_text("private memory", encoding="utf-8")

    kept = _run_windows_uninstall([], tmp_path)
    assert kept.returncode == 0, kept.stdout
    assert brain_dir.exists()

    purged = _run_windows_uninstall(["-PurgeData"], tmp_path)
    assert purged.returncode == 0, purged.stdout
    assert not brain_dir.exists()


def test_windows_uninstall_refuses_to_purge_user_home(tmp_path: Path):
    if not POWERSHELL:
        return

    home = tmp_path / "home"
    home.mkdir()
    marker = home / "keep.txt"
    marker.write_text("must survive", encoding="utf-8")

    result = _run_windows_uninstall(["-PurgeData"], tmp_path, brain_dir=home)

    assert result.returncode == 1, result.stdout
    assert "Refusing unsafe recursive deletion target" in result.stdout
    assert marker.exists()


def test_windows_uninstall_removes_only_validated_checkout(tmp_path: Path):
    if not POWERSHELL:
        return

    home = tmp_path / "home"
    home.mkdir()
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    shutil.copy2(INSTALL_PS1, checkout / "install.ps1")
    (checkout / "pyproject.toml").write_text("[project]\nname='agent-memory-hub'\n")
    (checkout / "agent_brain").mkdir()

    result = _run_windows_uninstall(
        ["-RemoveCheckout"],
        tmp_path,
        script=checkout / "install.ps1",
        checkout_dir=checkout,
    )

    assert result.returncode == 0, result.stdout
    assert not checkout.exists()
    assert home.exists()


def test_windows_uninstall_refuses_to_remove_user_home_as_checkout(tmp_path: Path):
    if not POWERSHELL:
        return

    home = tmp_path / "home"
    home.mkdir()
    marker = home / "keep.txt"
    marker.write_text("must survive", encoding="utf-8")

    result = _run_windows_uninstall(
        ["-RemoveCheckout"],
        tmp_path,
        checkout_dir=home,
    )

    assert result.returncode == 1, result.stdout
    assert "Refusing unsafe or non-AMH checkout deletion target" in result.stdout
    assert marker.exists()


def test_windows_uninstall_then_reinstall_reconfigures_core_adapters(tmp_path: Path):
    if not POWERSHELL:
        return

    codex_dir = tmp_path / "home" / ".codex"
    codex_dir.mkdir(parents=True)
    (codex_dir / "config.toml").write_text(
        '[mcp_servers.agent-memory-hub]\ncommand = "bash"\n',
        encoding="utf-8",
    )
    brain_item = tmp_path / "brain" / "items" / "remember.md"
    brain_item.parent.mkdir(parents=True)
    brain_item.write_text("keep this memory", encoding="utf-8")

    removed = _run_windows_uninstall([], tmp_path)
    assert removed.returncode == 0, removed.stdout
    assert brain_item.exists()
    assert not (codex_dir / "config.toml").exists()

    first, adapter_log = _run_windows_install(tmp_path)
    assert first.returncode == 0, first.stdout
    assert "Agent Memory Hub installed." in first.stdout
    assert adapter_log.read_text(encoding="utf-8").splitlines() == [
        "adapter install codex",
        "adapter install claude_code",
    ]

    second, adapter_log = _run_windows_install(tmp_path)
    assert second.returncode == 0, second.stdout
    assert adapter_log.read_text(encoding="utf-8").splitlines() == [
        "adapter install codex",
        "adapter install claude_code",
        "adapter install codex",
        "adapter install claude_code",
    ]


def test_windows_reinstall_fails_if_present_core_adapter_is_not_restored(tmp_path: Path):
    if not POWERSHELL:
        return

    (tmp_path / "home" / ".codex").mkdir(parents=True)

    result, adapter_log = _run_windows_install(tmp_path, fail_adapter="codex")

    assert result.returncode == 1, result.stdout
    assert "Required local adapters failed to reinstall: codex" in result.stdout
    assert adapter_log.read_text(encoding="utf-8").splitlines() == [
        "adapter install codex",
        "adapter install claude_code",
    ]


def test_windows_install_replays_pending_and_installs_memory_shim(tmp_path: Path):
    if not POWERSHELL:
        return

    pending = tmp_path / "brain" / "pending"
    pending.mkdir(parents=True)
    (pending / "pending-record.jsonl").write_text("{}\n", encoding="utf-8")

    result, _adapter_log = _run_windows_install(tmp_path)

    assert result.returncode == 0, result.stdout
    assert "Replaying safe pending memories..." in result.stdout
    sync_log = tmp_path / "sync-actions.log"
    assert sync_log.read_text(encoding="utf-8").splitlines() == [
        "sync-pending --apply --safe-only",
    ]
    shim = tmp_path / "home" / ".local" / "bin" / "memory.cmd"
    assert shim.exists()
    assert "agent_brain.interfaces.cli" in shim.read_text(encoding="utf-8")


def test_python_resolver_checks_windows_project_venv():
    resolver = (
        REPO_ROOT / "agent_runtime_kit" / "tools" / "_resolve-python.sh"
    ).read_text(encoding="utf-8")

    assert '"$_PROJECT_ROOT/.venv/Scripts/python.exe"' in resolver
    assert (
        '_REQUIRED_IMPORTS="${AGENT_MEMORY_HUB_PYTHON_IMPORTS:-agent_brain.interfaces.cli}"'
        in resolver
    )
