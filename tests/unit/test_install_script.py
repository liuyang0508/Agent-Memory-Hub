"""Smoke tests for the one-click installer and uninstall path."""

from __future__ import annotations

import json
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
