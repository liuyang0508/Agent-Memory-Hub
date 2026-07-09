"""QoderWork adapter.

QoderWork uses the same public hooks schema as Qoder, but keeps an independent
workspace/team config path and adapter identity so runtime evidence and stop
signals are attributed to ``qoder_work``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import AdapterBase, AdapterConfig
from .awareness import (
    diagnose_awareness_block,
    install_awareness_block,
    render_awareness_block,
    uninstall_awareness_block,
)
from .codex_config import (
    atomic_write_json as _atomic_write_json,
    command_references_path as _command_references_path,
    hook_belongs_to as _hook_belongs_to,
    hook_script_present as _hook_script_present,
    read_json_config as _read_json_config,
    update_hook_command as _update_hook_command,
)
from .diagnostics import (
    AdapterDiagnosticCheck,
    AdapterDiagnosticReport,
    diagnose_layered_context_pack_evidence,
    diagnose_mcp_json_server,
    diagnose_runtime_evidence,
    overall_status,
)
from .hook_config import (
    adapter_hook_command as _adapter_hook_command,
    hook_script_aliases as _hook_script_aliases,
    POSIX_PATH_EXPANSION,
)
from .python_runtime import amh_python_executable
from .qoder_diagnostics import diagnose_hook_scripts, diagnose_settings_hooks
from .registry import register_adapter


def _path_from_env(env_name: str, default: Path) -> Path:
    override = os.environ.get(env_name)
    if not override:
        return default
    return Path(override).expanduser()


def _workspace_awareness_disabled() -> bool:
    value = os.environ.get("AGENT_MEMORY_HUB_DISABLE_WORKSPACE_AWARENESS", "")
    return value.lower() in {"1", "true", "yes", "on"}


SETTINGS_PATH = Path.home() / ".qoderwork" / "settings.json"
MCP_CONFIG_PATH = Path.home() / ".qoderwork" / "mcp.json"
AWARENESS_PATH = Path.home() / ".qoderwork" / "awareness" / "main" / "AGENTS.md"
QODERWORK_PROJECTS_DIR = _path_from_env(
    "AGENT_MEMORY_HUB_QODERWORK_PROJECTS_DIR",
    Path.home() / ".qoderwork" / "projects",
)
QODERWORK_SKILLS_DIR = _path_from_env(
    "AGENT_MEMORY_HUB_QODERWORK_SKILLS_DIR",
    Path.home() / ".qoderwork" / "skills",
)
SERVER_NAME = "agent-memory-hub"
BOOTSTRAP_SKILL_DIR = "agent-memory-hub-shared-memory"
BOOTSTRAP_SKILL_MARKER = "Agent Memory Hub QoderWork Bootstrap Skill"


class QoderWorkAdapter(AdapterBase):
    """Adapter for QoderWork's Qoder-compatible hooks settings."""

    HOOK_EVENTS = ("UserPromptSubmit", "Stop")
    HOOK_SCRIPTS = {
        "UserPromptSubmit": "inject-context.sh",
        "Stop": "session-end-signal.sh",
    }

    def __init__(self, brain_dir: Path, repo_dir: Path | None = None):
        super().__init__(brain_dir)
        self.repo_dir = repo_dir or Path(__file__).resolve().parents[2]
        self.hooks_dir = self.repo_dir / "agent_runtime_kit" / "hooks"

    def get_config(self) -> AdapterConfig:
        return AdapterConfig(
            agent_name="qoder_work",
            config_dir=Path.home() / ".qoderwork",
            hook_type="file",
            inject_method="rules_file",
            supports_hooks=True,
            supports_mcp=True,
        )

    def install(self) -> str:
        self._validate_inputs()
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        settings = _read_json_config(SETTINGS_PATH)
        hooks = settings.setdefault("hooks", {})

        changed_events: list[str] = []
        for event in self.HOOK_EVENTS:
            script = self.hooks_dir / self.HOOK_SCRIPTS[event]
            entries = hooks.setdefault(event, [])
            expected_command = self._hook_command(event, script)
            if _hook_script_present(entries, str(script)):
                changed = _update_hook_command(
                    entries,
                    script_path=str(script),
                    expected_command=expected_command,
                )
                if event == "UserPromptSubmit":
                    changed = self._move_hook_entry_first(entries, script) or changed
                if changed:
                    changed_events.append(event)
                continue
            entry = {
                "matcher": "",
                "hooks": [{"type": "command", "command": expected_command}],
            }
            if event == "UserPromptSubmit":
                entries.insert(0, entry)
            else:
                entries.append(entry)
            changed_events.append(event)

        awareness_changed = install_awareness_block(AWARENESS_PATH, self._awareness_block())
        workspace_awareness = self._install_workspace_awareness()
        bootstrap_skill = self._install_bootstrap_skill()
        mcp_msg = self._install_mcp()
        if changed_events:
            _atomic_write_json(SETTINGS_PATH, settings)
            return (
                f"qoder_work adapter: installed {len(changed_events)} hook(s) "
                f"({', '.join(changed_events)}) into {SETTINGS_PATH}; "
                f"awareness channel {'installed' if awareness_changed else 'already present'} in {AWARENESS_PATH}; "
                f"{workspace_awareness}; "
                f"{bootstrap_skill}; "
                f"{mcp_msg}"
            )
        if awareness_changed:
            return (
                f"qoder_work adapter: installed awareness channel in {AWARENESS_PATH}; "
                f"{workspace_awareness}; {bootstrap_skill}; {mcp_msg}"
            )
        return (
            f"qoder_work adapter: already installed at {SETTINGS_PATH}; "
            f"{workspace_awareness}; {bootstrap_skill}; {mcp_msg}"
        )

    def uninstall(self) -> str:
        awareness_removed = uninstall_awareness_block(AWARENESS_PATH)
        workspace_awareness = self._uninstall_workspace_awareness()
        bootstrap_skill = self._uninstall_bootstrap_skill()
        mcp_msg = self._uninstall_mcp()
        if not SETTINGS_PATH.exists():
            if awareness_removed:
                return (
                    f"qoder_work adapter: removed awareness channel from {AWARENESS_PATH}; "
                    f"{workspace_awareness}; {bootstrap_skill}; {mcp_msg}"
                )
            return (
                f"qoder_work adapter: {SETTINGS_PATH} does not exist; "
                f"{workspace_awareness}; {bootstrap_skill}; {mcp_msg}"
            )
        settings = _read_json_config(SETTINGS_PATH)
        hooks = settings.get("hooks")
        if not isinstance(hooks, dict):
            if awareness_removed:
                return (
                    f"qoder_work adapter: removed awareness channel from {AWARENESS_PATH}; "
                    f"{workspace_awareness}; {bootstrap_skill}; {mcp_msg}"
                )
            return (
                f"qoder_work adapter: no hooks section; "
                f"{workspace_awareness}; {bootstrap_skill}; {mcp_msg}"
            )

        removed = 0
        for event in self.HOOK_EVENTS:
            entries = hooks.get(event, [])
            kept = [entry for entry in entries if not _hook_belongs_to(entry, str(self.hooks_dir))]
            removed += len(entries) - len(kept)
            hooks[event] = kept

        _atomic_write_json(SETTINGS_PATH, settings)
        return (
            f"qoder_work adapter: removed {removed} hub-owned hook entr"
            f"{'y' if removed == 1 else 'ies'}; "
            f"awareness channel {'removed' if awareness_removed else 'not present'}; "
            f"{workspace_awareness}; "
            f"{bootstrap_skill}; "
            f"{mcp_msg}"
        )

    def inject_context(self, query: str) -> str:
        return (
            f"# QoderWork brain-pool context hook: {self.hooks_dir / 'inject-context.sh'}\n"
            f"# Data: {self.brain_dir}\n"
            f"# Query for reference: {query}\n"
        )

    def get_install_instructions(self) -> str:
        return (
            "## QoderWork Adapter\n\n"
            f"Installs hub-owned hooks into `{SETTINGS_PATH}`:\n"
            f"- UserPromptSubmit -> `{self.hooks_dir / 'inject-context.sh'}`\n"
            f"- Stop -> `{self.hooks_dir / 'session-end-signal.sh'}`\n"
            f"- Awareness Channel -> `{AWARENESS_PATH}`\n\n"
            "QoderWork can also read workspace `AGENTS.md` and local skills, so install "
            f"syncs awareness into recent workspace roots from `{QODERWORK_PROJECTS_DIR}` and writes "
            f"`{QODERWORK_SKILLS_DIR / BOOTSTRAP_SKILL_DIR / 'SKILL.md'}` as a short-prompt bootstrap.\n\n"
            f"It also registers the AMH MCP server in `{MCP_CONFIG_PATH}` as `{SERVER_NAME}`.\n\n"
            "This adapter uses the Qoder-compatible hooks schema, but writes an "
            "independent workspace settings file and runtime adapter identity.\n"
        )

    def diagnose(self) -> AdapterDiagnosticReport:
        checks = [
            self._diagnose_settings_hooks(),
            self._diagnose_prompt_hook_mode(),
            self._diagnose_hook_scripts(),
            diagnose_awareness_block(
                check_name="QoderWork awareness channel",
                path=AWARENESS_PATH,
                brain_dir=self.brain_dir,
                install_command="memory adapter install qoder_work",
            ),
            self._diagnose_workspace_awareness(),
            self._diagnose_bootstrap_skill(),
            diagnose_mcp_json_server(
                check_name="QoderWork MCP server",
                config_path=MCP_CONFIG_PATH,
                server_name=SERVER_NAME,
                expected_command=amh_python_executable(self.repo_dir),
                expected_args=["-m", "agent_brain.interfaces.mcp.server"],
                expected_env=self._mcp_env(),
                install_command="memory adapter install qoder_work",
            ),
            diagnose_runtime_evidence(
                brain_dir=self.brain_dir,
                adapter="qoder_work",
                check_name="QoderWork runtime evidence",
            ),
            diagnose_layered_context_pack_evidence(
                brain_dir=self.brain_dir,
                adapter="qoder_work",
                check_name="QoderWork layered context pack evidence",
            ),
        ]
        return AdapterDiagnosticReport(
            adapter="qoder_work",
            overall_status=overall_status(checks),
            checks=checks,
            brain_dir=self.brain_dir,
        )

    def _validate_inputs(self) -> None:
        for script in self.HOOK_SCRIPTS.values():
            path = self.hooks_dir / script
            if not path.exists():
                raise FileNotFoundError(
                    f"hook script missing: {path} - is the agent-memory-hub repo intact?"
                )

    def _diagnose_settings_hooks(self) -> AdapterDiagnosticCheck:
        return diagnose_settings_hooks(
            settings_path=SETTINGS_PATH,
            hooks_dir=self.hooks_dir,
            hook_events=self.HOOK_EVENTS,
            hook_scripts=self.HOOK_SCRIPTS,
            adapter_label="QoderWork",
            install_command="run: memory adapter install qoder_work",
        )

    def _diagnose_hook_scripts(self) -> AdapterDiagnosticCheck:
        return diagnose_hook_scripts(
            hooks_dir=self.hooks_dir,
            hook_scripts=self.HOOK_SCRIPTS,
            adapter_label="QoderWork",
        )

    def _diagnose_prompt_hook_mode(self) -> AdapterDiagnosticCheck:
        check_name = "QoderWork prompt hook injection mode"
        try:
            settings = _read_json_config(SETTINGS_PATH)
        except RuntimeError as exc:
            return AdapterDiagnosticCheck(
                name=check_name,
                status="error",
                detail=str(exc),
                fix="repair JSON by hand, then run: memory adapter install qoder_work",
            )
        hooks = settings.get("hooks")
        if not isinstance(hooks, dict):
            return AdapterDiagnosticCheck(
                name=check_name,
                status="error",
                detail="missing top-level hooks object",
                fix="run: memory adapter install qoder_work",
            )
        entries = hooks.get("UserPromptSubmit", [])
        if not entries:
            return AdapterDiagnosticCheck(
                name=check_name,
                status="error",
                detail="missing UserPromptSubmit hooks",
                fix="run: memory adapter install qoder_work",
            )
        first_hooks = entries[0].get("hooks", []) if isinstance(entries[0], dict) else []
        first_command = str(first_hooks[0].get("command") if first_hooks else "")
        prompt_script = self.hooks_dir / self.HOOK_SCRIPTS["UserPromptSubmit"]
        if not _command_references_path(first_command, str(prompt_script)):
            return AdapterDiagnosticCheck(
                name=check_name,
                status="error",
                detail="AMH inject-context hook is not first; QoderWork may ignore later hook context",
                fix="run: memory adapter install qoder_work",
            )
        if "AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT=json" not in first_command:
            return AdapterDiagnosticCheck(
                name=check_name,
                status="error",
                detail="QoderWork prompt hook must emit JSON hookSpecificOutput.additionalContext context",
                fix="run: memory adapter install qoder_work",
            )
        if POSIX_PATH_EXPANSION in first_command:
            return AdapterDiagnosticCheck(
                name=check_name,
                status="error",
                detail=(
                    "QoderWork runs hooks through fish-compatible Qoder runtime; "
                    "current hook command contains POSIX-only PATH expansion"
                ),
                fix="run: memory adapter install qoder_work",
            )
        return AdapterDiagnosticCheck(
            name=check_name,
            status="ok",
            detail="AMH prompt hook is first and emits QoderWork JSON additionalContext",
        )

    def _hook_command(self, event: str, script: Path) -> str:
        if event == "UserPromptSubmit":
            return _adapter_hook_command(
                "qoder_work",
                script,
                extra_env={
                    "MEMORY_PYTHON": amh_python_executable(self.repo_dir),
                    "AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT": "json",
                },
                path_strategy="fixed",
            )
        return _adapter_hook_command(
            "qoder_work",
            script,
            extra_env={"MEMORY_PYTHON": amh_python_executable(self.repo_dir)},
            path_strategy="fixed",
        )

    def _move_hook_entry_first(self, entries: list, script: Path) -> bool:
        script_paths = _hook_script_aliases(str(script))
        for index, entry in enumerate(entries):
            hooks = entry.get("hooks", [])
            if not any(
                any(_command_references_path(hook.get("command", ""), path) for path in script_paths)
                for hook in hooks
            ):
                continue
            if index == 0:
                return False
            entries.insert(0, entries.pop(index))
            return True
        return False

    def _awareness_block(self) -> str:
        return render_awareness_block(
            agent_name="QoderWork",
            brain_dir=self.brain_dir,
            tool_channel="QoderWork hooks plus AMH custom MCP server from `~/.qoderwork/mcp.json`; `memory` CLI remains the fallback",
            mcp_tools_available=True,
            extra_guidance=(
                "QoderWork reads its static awareness fallback from QoderWork awareness/main AGENTS.md.",
                "The hooks are automatic injection/recording; the MCP config is the proactive tool layer.",
                "Verified status requires transcript-level AMH context evidence such as an <agent_brain> candidate block reaching the session.",
            ),
        )

    def _workspace_awareness_block(self) -> str:
        return render_awareness_block(
            agent_name="Qoder / QoderWork",
            brain_dir=self.brain_dir,
            tool_channel=(
                "Qoder-family workspace AGENTS.md plus the available AMH MCP server; "
                "`memory` CLI remains the fallback when the UI does not expose MCP tools"
            ),
            mcp_tools_available=True,
            extra_guidance=(
                "Qoder and QoderWork may share this workspace file; use whichever AMH MCP tools the current client exposes.",
                "Treat one-word or short project/name prompts as context requests, not greetings.",
                "If no <agent_brain> block appears, proactively call AMH MCP tools such as search_memory / brief_memory before non-trivial work.",
            ),
        )

    def _install_workspace_awareness(self) -> str:
        if _workspace_awareness_disabled():
            return "workspace awareness skipped: disabled by AGENT_MEMORY_HUB_DISABLE_WORKSPACE_AWARENESS"
        paths = self._workspace_awareness_paths()
        changed = 0
        for path in paths:
            if install_awareness_block(path, self._workspace_awareness_block()):
                changed += 1
        if not paths:
            return "workspace awareness skipped: no QoderWork workspace roots discovered"
        return f"workspace awareness {'installed' if changed else 'already present'} in {changed}/{len(paths)} root(s)"

    def _uninstall_workspace_awareness(self) -> str:
        if _workspace_awareness_disabled():
            return "workspace awareness skipped: disabled by AGENT_MEMORY_HUB_DISABLE_WORKSPACE_AWARENESS"
        paths = self._workspace_awareness_paths()
        removed = 0
        for path in paths:
            if uninstall_awareness_block(path):
                removed += 1
        if not paths:
            return "workspace awareness skipped: no QoderWork workspace roots discovered"
        return f"workspace awareness removed from {removed}/{len(paths)} root(s)"

    def _install_bootstrap_skill(self) -> str:
        skill_path = self._bootstrap_skill_path()
        content = self._bootstrap_skill_content()
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        if skill_path.exists() and skill_path.read_text(encoding="utf-8") == content:
            return f"bootstrap skill already present in {skill_path}"
        skill_path.write_text(content, encoding="utf-8")
        return f"bootstrap skill installed in {skill_path}"

    def _uninstall_bootstrap_skill(self) -> str:
        skill_dir = QODERWORK_SKILLS_DIR / BOOTSTRAP_SKILL_DIR
        skill_path = skill_dir / "SKILL.md"
        if not skill_path.exists():
            return "bootstrap skill not present"
        text = skill_path.read_text(encoding="utf-8")
        if BOOTSTRAP_SKILL_MARKER not in text:
            return f"bootstrap skill left untouched: unmanaged file at {skill_path}"
        skill_path.unlink()
        try:
            skill_dir.rmdir()
        except OSError:
            pass
        return f"bootstrap skill removed from {skill_path}"

    def _diagnose_workspace_awareness(self) -> AdapterDiagnosticCheck:
        if _workspace_awareness_disabled():
            return AdapterDiagnosticCheck(
                name="QoderWork workspace awareness",
                status="warn",
                detail="workspace awareness disabled by AGENT_MEMORY_HUB_DISABLE_WORKSPACE_AWARENESS",
                fix="unset AGENT_MEMORY_HUB_DISABLE_WORKSPACE_AWARENESS, then run: memory adapter install qoder_work",
            )
        paths = self._workspace_awareness_paths()
        if not paths:
            return AdapterDiagnosticCheck(
                name="QoderWork workspace awareness",
                status="warn",
                detail="no recent QoderWork workspace roots discovered from projects",
                fix="start a QoderWork session once, then run: memory adapter install qoder_work",
            )
        missing: list[str] = []
        for path in paths:
            if not path.exists():
                missing.append(str(path))
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                missing.append(str(path))
                continue
            if "Agent Memory Hub Awareness Channel" not in content:
                missing.append(str(path))
        if missing:
            return AdapterDiagnosticCheck(
                name="QoderWork workspace awareness",
                status="error",
                detail=f"missing awareness block in workspace AGENTS.md: {', '.join(missing)}",
                fix="run: memory adapter install qoder_work",
            )
        return AdapterDiagnosticCheck(
            name="QoderWork workspace awareness",
            status="ok",
            detail=f"hub awareness block present in {len(paths)} workspace root(s)",
        )

    def _diagnose_bootstrap_skill(self) -> AdapterDiagnosticCheck:
        path = self._bootstrap_skill_path()
        if not path.exists():
            return AdapterDiagnosticCheck(
                name="QoderWork bootstrap skill",
                status="error",
                detail=f"missing bootstrap skill: {path}",
                fix="run: memory adapter install qoder_work",
            )
        text = path.read_text(encoding="utf-8")
        required = (
            BOOTSTRAP_SKILL_MARKER,
            "mcp__agent-memory-hub__brief_memory",
            "mcp__agent-memory-hub__search_memory",
            "短 prompt",
        )
        missing = [item for item in required if item not in text]
        if missing:
            return AdapterDiagnosticCheck(
                name="QoderWork bootstrap skill",
                status="error",
                detail=f"bootstrap skill missing required text: {', '.join(missing)}",
                fix="run: memory adapter install qoder_work",
            )
        return AdapterDiagnosticCheck(
            name="QoderWork bootstrap skill",
            status="ok",
            detail=f"bootstrap skill present in {path}",
        )

    def _workspace_awareness_paths(self) -> list[Path]:
        paths: list[Path] = []
        seen: set[Path] = set()
        for root in self._discover_workspace_roots():
            path = root / "AGENTS.md"
            if path == AWARENESS_PATH or path in seen:
                continue
            seen.add(path)
            paths.append(path)
        return paths

    def _discover_workspace_roots(self, *, limit: int = 12) -> list[Path]:
        if not QODERWORK_PROJECTS_DIR.exists():
            return []
        candidates = [
            path
            for path in QODERWORK_PROJECTS_DIR.rglob("*")
            if path.is_file() and (path.suffix == ".jsonl" or path.name.endswith("-session.json"))
        ]
        candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        roots: list[Path] = []
        seen: set[Path] = set()
        for candidate in candidates:
            root = self._cwd_from_project_file(candidate)
            if root is None or root in seen:
                continue
            seen.add(root)
            roots.append(root)
            if len(roots) >= limit:
                break
        return roots

    def _cwd_from_project_file(self, path: Path) -> Path | None:
        if path.suffix == ".jsonl":
            return self._cwd_from_jsonl(path)
        return self._cwd_from_session_json(path)

    def _cwd_from_jsonl(self, path: Path) -> Path | None:
        try:
            with path.open("r", encoding="utf-8-sig") as handle:
                for _ in range(5):
                    line = handle.readline()
                    if not line:
                        return None
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    root = self._coerce_existing_dir(payload.get("cwd"))
                    if root is not None:
                        return root
        except OSError:
            return None
        return None

    def _cwd_from_session_json(self, path: Path) -> Path | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        return self._coerce_existing_dir(payload.get("working_dir") or payload.get("cwd"))

    def _coerce_existing_dir(self, value: object) -> Path | None:
        if not isinstance(value, str) or not value:
            return None
        path = Path(value).expanduser()
        if path.exists() and path.is_dir():
            resolved = path.resolve()
            if resolved == Path(resolved.anchor):
                return None
            return resolved
        return None

    def _bootstrap_skill_path(self) -> Path:
        return QODERWORK_SKILLS_DIR / BOOTSTRAP_SKILL_DIR / "SKILL.md"

    def _bootstrap_skill_content(self) -> str:
        return "\n".join(
            [
                f"# {BOOTSTRAP_SKILL_MARKER}",
                "",
                "## 触发场景",
                "",
                "当用户输入一个词、项目名、人名、短 prompt、继续之前工作、跨智能体交接、调试、规划或非平凡任务时，先把它当成上下文请求。",
                "这类上下文必须来自 AMH 召回或当前仓库证据，而不是写死在 bootstrap skill 里。",
                "",
                "## 必须先做",
                "",
                "优先通过 QoderWork MCP 工具读取 AMH 共享记忆：",
                "- `mcp__agent-memory-hub__brief_memory`：先拿有界全貌；",
                "- `mcp__agent-memory-hub__search_memory`：按用户原词和项目关键词检索；",
                "- `mcp__agent-memory-hub__read_memory`：只读取真正需要的 1-3 条详情。",
                "",
                "如果界面只暴露通用 MCP 调用，请按 QoderWork 提示使用 `qw_mcp_list`、`qw_mcp_get`、`qw_mcp_call` 调用上述工具。",
                "",
                "## 回答纪律",
                "",
                "- 不要只根据 QoderWork 原生记忆、用户个人信息或一句问候回答。",
                "- 不要把 AMH 召回结果说成当前聊天历史；它们是 memory candidates。",
                "- 当前用户指令和实时文件证据优先于旧记忆。",
                "- 产生可复用事实、决策、信号、产物或交接信息时，调用 `mcp__agent-memory-hub__write_memory` 写回。",
                "",
                "## CLI 兜底",
                "",
                "如果 MCP 工具不可用，使用 CLI：",
                "",
                "```bash",
                f"BRAIN_DIR={self.brain_dir} PYTHONPATH={self.repo_dir} {amh_python_executable(self.repo_dir)} -m agent_brain.interfaces.cli search \"<用户问题或项目名>\" --top-k 5 --format text --context-firewall --verbosity auto --explain",
                "```",
                "",
            ]
        )

    def _install_mcp(self) -> str:
        MCP_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        config = _read_json_config(MCP_CONFIG_PATH)
        servers = config.setdefault("mcpServers", {})
        if not isinstance(servers, dict):
            raise RuntimeError(f"refuse to overwrite {MCP_CONFIG_PATH}: mcpServers must be an object")

        desired = self._mcp_server_config()
        if servers.get(SERVER_NAME) == desired:
            return f"MCP server already registered in {MCP_CONFIG_PATH}"
        servers[SERVER_NAME] = desired
        _atomic_write_json(MCP_CONFIG_PATH, config)
        return f"registered MCP server in {MCP_CONFIG_PATH}"

    def _uninstall_mcp(self) -> str:
        if not MCP_CONFIG_PATH.exists():
            return f"{MCP_CONFIG_PATH} does not exist, no MCP server to remove"
        config = _read_json_config(MCP_CONFIG_PATH)
        servers = config.get("mcpServers", {})
        if not isinstance(servers, dict) or SERVER_NAME not in servers:
            return "no hub MCP server entry to remove"
        del servers[SERVER_NAME]
        _atomic_write_json(MCP_CONFIG_PATH, config)
        return f"removed MCP server from {MCP_CONFIG_PATH}"

    def _mcp_env(self) -> dict[str, str]:
        return {
            "BRAIN_DIR": str(self.brain_dir),
            "PYTHONPATH": str(self.repo_dir),
        }

    def _mcp_server_config(self) -> dict[str, object]:
        return {
            "command": amh_python_executable(self.repo_dir),
            "args": ["-m", "agent_brain.interfaces.mcp.server"],
            "env": self._mcp_env(),
            "enabled": True,
        }


register_adapter("qoder_work", QoderWorkAdapter)
