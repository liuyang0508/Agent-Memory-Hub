"""Qoder adapter.

Qoder's documented hooks config uses ``~/.qoder/settings.json`` with a
Claude-Code-like ``hooks`` object. Current public hooks docs list
``UserPromptSubmit`` and ``Stop`` as supported events; ``SessionStart`` is not
installed here because the docs only mark it as upcoming.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

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
from .qoder_diagnostics import (
    diagnose_hook_scripts,
    diagnose_settings_hooks,
)
from .registry import register_adapter


def _path_from_env(env_name: str, default: Path) -> Path:
    override = os.environ.get(env_name)
    if not override:
        return default
    return Path(override).expanduser()


def _workspace_awareness_disabled() -> bool:
    value = os.environ.get("AGENT_MEMORY_HUB_DISABLE_WORKSPACE_AWARENESS", "")
    return value.lower() in {"1", "true", "yes", "on"}


SETTINGS_PATH = Path.home() / ".qoder" / "settings.json"
AWARENESS_PATH = Path.home() / ".qoder" / "AGENTS.md"
QODER_PROJECTS_DIR = _path_from_env(
    "AGENT_MEMORY_HUB_QODER_PROJECTS_DIR",
    Path.home() / ".qoder" / "projects",
)
QODER_MEMORIES_DIR = _path_from_env(
    "AGENT_MEMORY_HUB_QODER_MEMORIES_DIR",
    Path.home() / ".qoder" / "memories",
)
MCP_CONFIG_PATH = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Qoder"
    / "SharedClientCache"
    / "mcp.json"
)
MCP_EXTENSION_CONFIG_PATH = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Qoder"
    / "SharedClientCache"
    / "extension"
    / "local"
    / "mcp.json"
)
MCP_USER_CONFIG_PATH = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Qoder"
    / "User"
    / "mcp.json"
)
QODER_LOCAL_DB_PATH = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Qoder"
    / "SharedClientCache"
    / "cache"
    / "db"
    / "local.db"
)
SERVER_NAME = "agent-memory-hub"
NATIVE_BRIDGE_RELATIVE_PATH = (
    Path("global")
    / "user_communication"
    / "Agent_Memory_Hub_共享记忆入口.md"
)
NATIVE_BRIDGE_RELATIVE_PATHS = (
    NATIVE_BRIDGE_RELATIVE_PATH,
    Path("global") / "user_info" / "Agent_Memory_Hub_共享记忆入口.md",
)
NATIVE_BRIDGE_TITLE = "Agent Memory Hub 共享记忆入口"
NATIVE_BRIDGE_MARKER = "Qoder 原生 SearchMemory 不能替代 AMH"
NATIVE_PRIORITY_REDIRECT_RELATIVE_PATHS = (
    Path("global") / "user_info" / "用户个人信息.md",
)
NATIVE_PRIORITY_REDIRECT_TITLES = tuple(
    relative_path.stem for relative_path in NATIVE_PRIORITY_REDIRECT_RELATIVE_PATHS
)
NATIVE_PRIORITY_REDIRECT_KEYWORDS = (
    "AMH",
    "agent-memory-hub",
    "共享记忆",
    "共享大脑",
    "历史上下文",
    "SearchMemory",
    "继续之前工作",
    "项目记忆",
)
NATIVE_REDIRECT_BEGIN = "<!-- BEGIN agent-memory-hub-native-redirect -->"
NATIVE_REDIRECT_END = "<!-- END agent-memory-hub-native-redirect -->"


class QoderAdapter(AdapterBase):
    """Adapter for Qoder (qoder.ai IDE)."""

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
            agent_name="qoder",
            config_dir=Path.home() / ".qoder",
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
        native_bridge = self._install_native_memory_bridge()
        mcp_msg = self._install_mcp()
        if changed_events:
            _atomic_write_json(SETTINGS_PATH, settings)
            return (
                f"qoder adapter: installed {len(changed_events)} hook(s) "
                f"({', '.join(changed_events)}) into {SETTINGS_PATH}; "
                f"awareness channel {'installed' if awareness_changed else 'already present'} in {AWARENESS_PATH}; "
                f"{workspace_awareness}; "
                f"{native_bridge}; "
                f"{mcp_msg}"
            )
        if awareness_changed:
            return (
                f"qoder adapter: installed awareness channel in {AWARENESS_PATH}; "
                f"{workspace_awareness}; {native_bridge}; {mcp_msg}"
            )
        return (
            f"qoder adapter: already installed at {SETTINGS_PATH}; "
            f"{workspace_awareness}; {native_bridge}; {mcp_msg}"
        )

    def uninstall(self) -> str:
        awareness_removed = uninstall_awareness_block(AWARENESS_PATH)
        workspace_awareness = self._uninstall_workspace_awareness()
        native_bridge = self._uninstall_native_memory_bridge()
        mcp_msg = self._uninstall_mcp()
        if not SETTINGS_PATH.exists():
            if awareness_removed:
                return (
                    f"qoder adapter: removed awareness channel from {AWARENESS_PATH}; "
                    f"{workspace_awareness}; {native_bridge}; {mcp_msg}"
                )
            return (
                f"qoder adapter: {SETTINGS_PATH} does not exist; "
                f"{workspace_awareness}; {native_bridge}; {mcp_msg}"
            )
        settings = _read_json_config(SETTINGS_PATH)
        hooks = settings.get("hooks")
        if not isinstance(hooks, dict):
            if awareness_removed:
                return (
                    f"qoder adapter: removed awareness channel from {AWARENESS_PATH}; "
                    f"{workspace_awareness}; {native_bridge}; {mcp_msg}"
                )
            return (
                f"qoder adapter: no hooks section; "
                f"{workspace_awareness}; {native_bridge}; {mcp_msg}"
            )

        removed = 0
        for event in self.HOOK_EVENTS:
            entries = hooks.get(event, [])
            kept = [entry for entry in entries if not _hook_belongs_to(entry, str(self.hooks_dir))]
            removed += len(entries) - len(kept)
            hooks[event] = kept

        _atomic_write_json(SETTINGS_PATH, settings)
        return (
            f"qoder adapter: removed {removed} hub-owned hook entr"
            f"{'y' if removed == 1 else 'ies'}; "
            f"awareness channel {'removed' if awareness_removed else 'not present'}; "
            f"{workspace_awareness}; "
            f"{native_bridge}; "
            f"{mcp_msg}"
        )

    def inject_context(self, query: str) -> str:
        return (
            f"# Qoder brain-pool context hook: {self.hooks_dir / 'inject-context.sh'}\n"
            f"# Data: {self.brain_dir}\n"
            f"# Query for reference: {query}\n"
        )

    def get_install_instructions(self) -> str:
        return (
            "## Qoder Adapter\n\n"
            f"Installs hub-owned hooks into `{SETTINGS_PATH}`:\n"
            f"- UserPromptSubmit -> `{self.hooks_dir / 'inject-context.sh'}`\n"
            f"- Stop -> `{self.hooks_dir / 'session-end-signal.sh'}`\n"
            f"- Awareness Channel -> `{AWARENESS_PATH}`\n\n"
            "Qoder also reads `AGENTS.md` from the workspace root, so install syncs "
            f"the same Awareness Channel into recent workspace roots discovered from `{QODER_PROJECTS_DIR}`.\n\n"
            f"It also registers the AMH MCP server in Qoder's profile MCP config "
            f"`{self._user_mcp_config_path()}`, mirrors `{MCP_CONFIG_PATH}`, and keeps "
            f"Qoder's extension cache `{MCP_EXTENSION_CONFIG_PATH}` in sync.\n\n"
            "The adapter intentionally does not install SessionStart because current "
            "Qoder hooks docs mark that event as upcoming.\n"
        )

    def diagnose(self) -> AdapterDiagnosticReport:
        checks = [
            self._diagnose_settings_hooks(),
            self._diagnose_prompt_hook_mode(),
            self._diagnose_hook_scripts(),
            diagnose_awareness_block(
                check_name="Qoder awareness channel",
                path=AWARENESS_PATH,
                brain_dir=self.brain_dir,
                install_command="memory adapter install qoder",
            ),
            self._diagnose_workspace_awareness(),
            self._diagnose_native_memory_bridge(),
            diagnose_mcp_json_server(
                check_name="Qoder MCP user profile",
                config_path=self._user_mcp_config_path(),
                server_name=SERVER_NAME,
                expected_command=amh_python_executable(self.repo_dir),
                expected_args=["-m", "agent_brain.interfaces.mcp.server"],
                expected_env=self._mcp_env(),
                install_command="memory adapter install qoder",
            ),
            diagnose_mcp_json_server(
                check_name="Qoder MCP shared cache",
                config_path=MCP_CONFIG_PATH,
                server_name=SERVER_NAME,
                expected_command=amh_python_executable(self.repo_dir),
                expected_args=["-m", "agent_brain.interfaces.mcp.server"],
                expected_env=self._mcp_env(),
                install_command="memory adapter install qoder",
            ),
            self._diagnose_extension_mcp_server(),
            self._diagnose_client_effectiveness(),
            diagnose_runtime_evidence(
                brain_dir=self.brain_dir,
                adapter="qoder",
                check_name="Qoder runtime evidence",
            ),
            diagnose_layered_context_pack_evidence(
                brain_dir=self.brain_dir,
                adapter="qoder",
                check_name="Qoder layered context pack evidence",
            ),
        ]
        return AdapterDiagnosticReport(
            adapter="qoder",
            overall_status=overall_status(checks),
            checks=checks,
        )

    def _validate_inputs(self) -> None:
        for script in self.HOOK_SCRIPTS.values():
            path = self.hooks_dir / script
            if not path.exists():
                raise FileNotFoundError(
                    f"hook script missing: {path} — is the agent-memory-hub repo intact?"
                )

    def _diagnose_settings_hooks(self) -> AdapterDiagnosticCheck:
        return diagnose_settings_hooks(
            settings_path=SETTINGS_PATH,
            hooks_dir=self.hooks_dir,
            hook_events=self.HOOK_EVENTS,
            hook_scripts=self.HOOK_SCRIPTS,
        )

    def _diagnose_hook_scripts(self) -> AdapterDiagnosticCheck:
        return diagnose_hook_scripts(
            hooks_dir=self.hooks_dir,
            hook_scripts=self.HOOK_SCRIPTS,
        )

    def _diagnose_prompt_hook_mode(self) -> AdapterDiagnosticCheck:
        check_name = "Qoder prompt hook injection mode"
        try:
            settings = _read_json_config(SETTINGS_PATH)
        except RuntimeError as exc:
            return AdapterDiagnosticCheck(
                name=check_name,
                status="error",
                detail=str(exc),
                fix="repair JSON by hand, then run: memory adapter install qoder",
            )
        hooks = settings.get("hooks")
        if not isinstance(hooks, dict):
            return AdapterDiagnosticCheck(
                name=check_name,
                status="error",
                detail="missing top-level hooks object",
                fix="run: memory adapter install qoder",
            )
        entries = hooks.get("UserPromptSubmit", [])
        if not entries:
            return AdapterDiagnosticCheck(
                name=check_name,
                status="error",
                detail="missing UserPromptSubmit hooks",
                fix="run: memory adapter install qoder",
            )
        first_hooks = entries[0].get("hooks", []) if isinstance(entries[0], dict) else []
        first_command = str(first_hooks[0].get("command") if first_hooks else "")
        prompt_script = self.hooks_dir / self.HOOK_SCRIPTS["UserPromptSubmit"]
        if not _command_references_path(first_command, str(prompt_script)):
            return AdapterDiagnosticCheck(
                name=check_name,
                status="error",
                detail="AMH inject-context hook is not first; Qoder may ignore later hook context",
                fix="run: memory adapter install qoder",
            )
        if "AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT=json" not in first_command:
            return AdapterDiagnosticCheck(
                name=check_name,
                status="error",
                detail="Qoder prompt hook must emit JSON hookSpecificOutput.additionalContext context",
                fix="run: memory adapter install qoder",
            )
        if POSIX_PATH_EXPANSION in first_command:
            return AdapterDiagnosticCheck(
                name=check_name,
                status="error",
                detail="Qoder runs hooks through fish; current hook command contains POSIX-only PATH expansion",
                fix="run: memory adapter install qoder",
            )
        return AdapterDiagnosticCheck(
            name=check_name,
            status="ok",
            detail="AMH prompt hook is first and emits Qoder JSON additionalContext",
        )

    def _hook_command(self, event: str, script: Path) -> str:
        if event == "UserPromptSubmit":
            return _adapter_hook_command(
                "qoder",
                script,
                extra_env={
                    "MEMORY_PYTHON": amh_python_executable(self.repo_dir),
                    "AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT": "json",
                },
                path_strategy="fixed",
            )
        return _adapter_hook_command(
            "qoder",
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
            agent_name="Qoder",
            brain_dir=self.brain_dir,
            tool_channel=(
                "Qoder hooks plus AMH MCP server from Qoder User/SharedClientCache mcp.json; "
                "`memory` CLI remains the fallback when the UI does not expose MCP tools"
            ),
            mcp_tools_available=True,
            extra_guidance=(
                "Qoder native memory/search tools are not AMH unless they are explicitly backed by agent-memory-hub.",
                "Hook execution alone is not enough: if no <agent_brain> block appears, proactively call AMH MCP tools such as search_memory / brief_memory before non-trivial work.",
                "Verified status requires transcript-level evidence that AMH context or AMH MCP tool results reached the Qoder session.",
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

    def _install_mcp(self) -> str:
        user_msg = self._install_user_mcp_config()
        main_msg = self._install_main_mcp_config()
        extension_msg = self._install_extension_mcp_config()
        return f"{user_msg}; {main_msg}; {extension_msg}"

    def _install_workspace_awareness(self) -> str:
        if _workspace_awareness_disabled():
            return "workspace awareness skipped: disabled by AGENT_MEMORY_HUB_DISABLE_WORKSPACE_AWARENESS"
        paths = self._workspace_awareness_paths()
        changed = 0
        for path in paths:
            if install_awareness_block(path, self._workspace_awareness_block()):
                changed += 1
        if not paths:
            return "workspace awareness skipped: no Qoder workspace roots discovered"
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
            return "workspace awareness skipped: no Qoder workspace roots discovered"
        return f"workspace awareness removed from {removed}/{len(paths)} root(s)"

    def _install_native_memory_bridge(self) -> str:
        """Install a Qoder-native memory that redirects native recall to AMH.

        Qoder currently has its own ``SearchMemory`` tool.  In practice the
        model may use that first.  A small native bridge makes that behavior
        useful: if Qoder native memory is hit, the content instructs the model
        to continue with AMH MCP/CLI recall instead of treating native memory as
        the shared brain.
        """
        profiles = self._native_memory_profiles()
        if not profiles:
            return "native memory bridge skipped: no Qoder memory profile discovered"
        changed = 0
        for profile in profiles:
            keep_paths = {profile / relative_path for relative_path in NATIVE_BRIDGE_RELATIVE_PATHS}
            changed += self._remove_stale_native_memory_bridges(profile, keep=keep_paths)
            content = self._native_memory_bridge_content()
            for path in keep_paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.exists() and path.read_text(encoding="utf-8") == content:
                    continue
                path.write_text(content, encoding="utf-8")
                changed += 1
            changed += self._install_native_priority_redirects(profile)
        database_changed = self._install_native_database_priority_redirects()
        total = len(profiles) * len(NATIVE_BRIDGE_RELATIVE_PATHS)
        return (
            f"native memory bridge {'updated' if changed else 'already present'}: "
            f"{total} bridge file(s) plus priority redirect(s) across {len(profiles)} profile(s); "
            f"database priority redirect row(s) updated: {database_changed}"
        )

    def _uninstall_native_memory_bridge(self) -> str:
        profiles = self._native_memory_profiles()
        removed = 0
        for profile in profiles:
            removed += self._remove_stale_native_memory_bridges(profile, keep=None)
            removed += self._uninstall_native_priority_redirects(profile)
            for relative_path in NATIVE_BRIDGE_RELATIVE_PATHS:
                path = profile / relative_path
                if path.exists() and self._is_managed_native_memory_bridge(path):
                    path.unlink()
                    removed += 1
        if not profiles:
            return "native memory bridge skipped: no Qoder memory profile discovered"
        return f"native memory bridge removed: {removed} bridge file(s) across {len(profiles)} profile(s)"

    def _install_native_priority_redirects(self, profile: Path) -> int:
        changed = 0
        for relative_path in NATIVE_PRIORITY_REDIRECT_RELATIVE_PATHS:
            path = profile / relative_path
            if not path.exists() or not path.is_file():
                continue
            original = path.read_text(encoding="utf-8")
            updated = self._replace_native_redirect_block(
                original,
                self._native_priority_redirect_block(),
            )
            if updated == original:
                continue
            path.write_text(updated, encoding="utf-8")
            changed += 1
        return changed

    def _uninstall_native_priority_redirects(self, profile: Path) -> int:
        removed = 0
        for relative_path in NATIVE_PRIORITY_REDIRECT_RELATIVE_PATHS:
            path = profile / relative_path
            if not path.exists() or not path.is_file():
                continue
            original = path.read_text(encoding="utf-8")
            updated = self._remove_native_redirect_block(original)
            if updated == original:
                continue
            path.write_text(updated, encoding="utf-8")
            removed += 1
        return removed

    def _replace_native_redirect_block(self, content: str, block: str) -> str:
        cleaned = self._remove_native_redirect_block(content).rstrip()
        if cleaned:
            return f"{block}\n\n{cleaned}\n"
        return f"{block}\n"

    def _install_native_database_priority_redirects(self) -> int:
        if not QODER_LOCAL_DB_PATH.exists():
            return 0
        try:
            connection = sqlite3.connect(QODER_LOCAL_DB_PATH, timeout=2.0)
        except sqlite3.Error:
            return 0
        changed = 0
        try:
            columns = {row[1] for row in connection.execute("pragma table_info(agent_memory)")}
            if not {"id", "title", "content"}.issubset(columns):
                return 0
            placeholders = ", ".join("?" for _ in NATIVE_PRIORITY_REDIRECT_TITLES)
            select_columns = ["id", "title", "content"]
            if "keywords" in columns:
                select_columns.append("keywords")
            rows = connection.execute(
                f"""
                select {", ".join(select_columns)}
                from agent_memory
                where title in ({placeholders})
                """,
                NATIVE_PRIORITY_REDIRECT_TITLES,
            ).fetchall()
            now = int(time.time())
            for row in rows:
                row_id = row[0]
                content = row[2]
                if not isinstance(content, str):
                    continue
                updated_content = self._replace_native_redirect_block(
                    content,
                    self._native_priority_redirect_block(),
                )
                updates = ["content = ?"]
                params: list[object] = [updated_content]
                has_change = updated_content != content
                if "keywords" in columns:
                    keywords_index = select_columns.index("keywords")
                    original_keywords = row[keywords_index]
                    merged_keywords = self._merge_native_redirect_keywords(original_keywords)
                    if merged_keywords != str(original_keywords or ""):
                        updates.append("keywords = ?")
                        params.append(merged_keywords)
                        has_change = True
                if not has_change:
                    continue
                if "gmt_modified" in columns:
                    updates.append("gmt_modified = ?")
                    params.append(now)
                if "content_updated_at" in columns:
                    updates.append("content_updated_at = ?")
                    params.append(now)
                params.append(row_id)
                connection.execute(
                    f"update agent_memory set {', '.join(updates)} where id = ?",
                    params,
                )
                changed += 1
            connection.commit()
        except sqlite3.Error:
            return 0
        finally:
            connection.close()
        return changed

    def _merge_native_redirect_keywords(self, keywords: object) -> str:
        existing = str(keywords or "")
        parts = [
            part.strip()
            for part in existing.replace("，", ",").split(",")
            if part.strip()
        ]
        seen = set(parts)
        for keyword in NATIVE_PRIORITY_REDIRECT_KEYWORDS:
            if keyword in seen:
                continue
            parts.append(keyword)
            seen.add(keyword)
        return ",".join(parts)

    def _remove_native_redirect_block(self, content: str) -> str:
        start = content.find(NATIVE_REDIRECT_BEGIN)
        if start == -1:
            return content
        end = content.find(NATIVE_REDIRECT_END, start)
        if end == -1:
            end = len(content)
        else:
            end += len(NATIVE_REDIRECT_END)
            if end < len(content) and content[end] == "\n":
                end += 1
        return (content[:start].rstrip() + "\n" + content[end:].lstrip()).strip() + "\n"

    def _remove_stale_native_memory_bridges(self, profile: Path, *, keep: set[Path] | None) -> int:
        removed = 0
        candidates: list[Path] = []
        for category in ("user_communication", "user_info"):
            bridge_dir = profile / "global" / category
            if not bridge_dir.exists():
                continue
            for pattern in (
                "Agent_Memory_Hub共享记忆入口*.md",
                "Agent_Memory_Hub_共享记忆入口*.md",
            ):
                candidates.extend(bridge_dir.glob(pattern))
        for path in sorted(set(candidates)):
            if keep is not None and path in keep:
                continue
            if not path.is_file() or not self._is_managed_native_memory_bridge(path):
                continue
            path.unlink()
            removed += 1
        return removed

    def _is_managed_native_memory_bridge(self, path: Path) -> bool:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return False
        return NATIVE_BRIDGE_TITLE in text and NATIVE_BRIDGE_MARKER in text

    def _native_memory_profiles(self) -> list[Path]:
        if not QODER_MEMORIES_DIR.exists():
            return []
        profiles = [
            path
            for path in QODER_MEMORIES_DIR.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        ]
        profiles.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        return profiles

    def _native_memory_bridge_content(self) -> str:
        return "\n".join(
            [
                "---",
                f'title: "{NATIVE_BRIDGE_TITLE}"',
                "usage_scenario:",
                '    - "短 prompt、项目名、历史上下文、跨 Agent 交接、继续之前工作时"',
                '    - "Qoder 原生 SearchMemory 命中但需要共享大脑事实源时"',
                "keywords:",
                '    - "AMH"',
                '    - "agent-memory-hub"',
                '    - "共享记忆"',
                '    - "共享大脑"',
                '    - "历史上下文"',
                '    - "用户个人信息"',
                '    - "用户名"',
                '    - "个人信息"',
                '    - "SearchMemory"',
                '    - "继续之前工作"',
                '    - "项目记忆"',
                "---",
                "",
                f"{NATIVE_BRIDGE_MARKER}。它只说明你需要上下文；下一步必须使用 Agent Memory Hub 共享记忆。",
                "",
                "优先调用 AMH MCP 工具：`brief_memory`、`search_memory`、`read_memory`、`write_memory`。",
                "如果当前 Qoder UI 没有暴露 AMH MCP 工具，就使用 CLI 兜底：",
                "",
                "```bash",
                f"BRAIN_DIR={self.brain_dir} PYTHONPATH={self.repo_dir} {amh_python_executable(self.repo_dir)} -m agent_brain.interfaces.cli search \"<用户问题或项目名>\" --top-k 5 --format text --context-firewall --verbosity auto --explain",
                "```",
                "",
                "对于一个词、项目名、人名或很短的 prompt，不要停在问候或 Qoder 原生用户信息；先检索 AMH，再基于召回结果回答。",
                "",
            ]
        )

    def _native_priority_redirect_block(self) -> str:
        return "\n".join(
            [
                NATIVE_REDIRECT_BEGIN,
                "",
                "## Agent Memory Hub 原生记忆纠偏",
                "",
                "原生用户信息只可用于称呼，不是项目历史、跨 Agent 交接或共享大脑事实源。",
                "当用户输入一个词、项目名、人名或短 prompt 时，必须继续检索 AMH；不要只根据本文件回答问候。",
                "",
                "优先调用 AMH MCP 工具：`brief_memory`、`search_memory`、`read_memory`。",
                "如果 Qoder 当前界面没有暴露 AMH MCP 工具，使用 CLI 兜底：",
                "",
                "```bash",
                f"BRAIN_DIR={self.brain_dir} PYTHONPATH={self.repo_dir} {amh_python_executable(self.repo_dir)} -m agent_brain.interfaces.cli search \"<用户问题或项目名>\" --top-k 5 --format text --context-firewall --verbosity auto --explain",
                "```",
                "",
                "使用 AMH 召回结果后，再回答你知道什么、证据来自哪里、是否仍需要读取详情。",
                "",
                NATIVE_REDIRECT_END,
            ]
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

    def _discover_workspace_roots(self, *, limit: int = 8) -> list[Path]:
        if not QODER_PROJECTS_DIR.exists():
            return []
        transcript_paths = sorted(
            QODER_PROJECTS_DIR.glob("*/transcript/*.jsonl"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        roots: list[Path] = []
        seen: set[Path] = set()
        for transcript_path in transcript_paths:
            root = self._cwd_from_transcript(transcript_path)
            if root is None or root in seen:
                continue
            seen.add(root)
            roots.append(root)
            if len(roots) >= limit:
                break
        return roots

    def _cwd_from_transcript(self, transcript_path: Path) -> Path | None:
        try:
            with transcript_path.open("r", encoding="utf-8-sig") as handle:
                for _ in range(5):
                    line = handle.readline()
                    if not line:
                        return None
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    cwd = payload.get("cwd")
                    if not isinstance(cwd, str) or not cwd:
                        continue
                    path = Path(cwd).expanduser()
                    if path.exists() and path.is_dir():
                        return path.resolve()
        except OSError:
            return None
        return None

    def _install_user_mcp_config(self) -> str:
        return self._install_standard_mcp_config(self._user_mcp_config_path())

    def _install_main_mcp_config(self) -> str:
        return self._install_standard_mcp_config(MCP_CONFIG_PATH)

    def _install_standard_mcp_config(self, path: Path) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        config = _read_json_config(path)
        servers = config.setdefault("mcpServers", {})
        if not isinstance(servers, dict):
            raise RuntimeError(f"refuse to overwrite {path}: mcpServers must be an object")

        desired = self._mcp_server_config()
        if servers.get(SERVER_NAME) == desired:
            return f"MCP server already registered in {path}"
        servers[SERVER_NAME] = desired
        _atomic_write_json(path, config)
        return f"registered MCP server in {path}"

    def _install_extension_mcp_config(self) -> str:
        MCP_EXTENSION_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        config = _read_json_config(MCP_EXTENSION_CONFIG_PATH)
        servers = config.setdefault("mcpServers", {})
        if not isinstance(servers, dict):
            raise RuntimeError(
                f"refuse to overwrite {MCP_EXTENSION_CONFIG_PATH}: mcpServers must be an object"
            )

        existing = servers.get(SERVER_NAME)
        if not isinstance(existing, dict):
            existing = {}
        desired = self._extension_mcp_server_config(existing)
        next_config = dict(config)
        next_servers = dict(servers)
        next_servers[SERVER_NAME] = desired
        next_config["mcpServers"] = next_servers
        next_config["userConfigMD5"] = self._main_mcp_md5()
        if config == next_config:
            return f"MCP extension cache already synced in {MCP_EXTENSION_CONFIG_PATH}"
        _atomic_write_json(MCP_EXTENSION_CONFIG_PATH, next_config)
        return f"synced MCP extension cache in {MCP_EXTENSION_CONFIG_PATH}"

    def _uninstall_mcp(self) -> str:
        messages = [
            self._remove_mcp_server(self._user_mcp_config_path(), md5_after_write=False),
            self._remove_mcp_server(MCP_CONFIG_PATH, md5_after_write=False),
            self._remove_mcp_server(MCP_EXTENSION_CONFIG_PATH, md5_after_write=True),
        ]
        return "; ".join(messages)

    def _remove_mcp_server(self, path: Path, *, md5_after_write: bool) -> str:
        if not path.exists():
            return f"{path} does not exist, no MCP server to remove"
        config = _read_json_config(path)
        servers = config.get("mcpServers", {})
        if not isinstance(servers, dict) or SERVER_NAME not in servers:
            return f"no hub MCP server entry to remove from {path}"
        del servers[SERVER_NAME]
        if md5_after_write:
            config["userConfigMD5"] = self._main_mcp_md5()
        _atomic_write_json(path, config)
        return f"removed MCP server from {path}"

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

    def _extension_mcp_server_config(self, existing: dict[str, object]) -> dict[str, object]:
        now_ms = int(time.time() * 1000)
        return {
            "identifier": existing.get("identifier") or str(uuid4()),
            "command": amh_python_executable(self.repo_dir),
            "args": ["-m", "agent_brain.interfaces.mcp.server"],
            "env": self._mcp_env(),
            "url": existing.get("url", ""),
            "disabled": False,
            "autoApprove": existing.get("autoApprove"),
            "source": existing.get("source", "user"),
            "description": existing.get("description") or "Agent Memory Hub shared memory MCP server",
            "version": existing.get("version") or now_ms,
            "createAt": existing.get("createAt") or now_ms,
            "from": existing.get("from", ""),
            "fromId": existing.get("fromId", ""),
            "timeout": existing.get("timeout") or 60,
        }

    def _main_mcp_md5(self) -> str:
        if not MCP_CONFIG_PATH.exists():
            return ""
        return hashlib.md5(MCP_CONFIG_PATH.read_bytes()).hexdigest()

    def _user_mcp_config_path(self) -> Path:
        """Return Qoder's VSCode-profile MCP config path.

        Modern Qoder builds expose a VSCode-like user data profile under
        ``Application Support/Qoder/User`` while older AMH integration wrote
        only SharedClientCache files.  When tests monkeypatch the shared-cache
        path, derive the sibling ``User/mcp.json`` path from that temporary
        root so installs never touch the real profile.
        """
        default_shared = (
            Path.home()
            / "Library"
            / "Application Support"
            / "Qoder"
            / "SharedClientCache"
            / "mcp.json"
        )
        if MCP_CONFIG_PATH == default_shared:
            return MCP_USER_CONFIG_PATH
        if MCP_CONFIG_PATH.name == "mcp.json" and MCP_CONFIG_PATH.parent.name == "SharedClientCache":
            return MCP_CONFIG_PATH.parent.parent / "User" / "mcp.json"
        return MCP_USER_CONFIG_PATH

    def _diagnose_extension_mcp_server(self) -> AdapterDiagnosticCheck:
        check = diagnose_mcp_json_server(
            check_name="Qoder MCP extension cache",
            config_path=MCP_EXTENSION_CONFIG_PATH,
            server_name=SERVER_NAME,
            expected_command=amh_python_executable(self.repo_dir),
            expected_args=["-m", "agent_brain.interfaces.mcp.server"],
            expected_env=self._mcp_env(),
            install_command="memory adapter install qoder",
        )
        if check.status != "ok":
            return check
        config = _read_json_config(MCP_EXTENSION_CONFIG_PATH)
        server = config["mcpServers"][SERVER_NAME]
        if server.get("disabled") is True:
            return AdapterDiagnosticCheck(
                name="Qoder MCP extension cache",
                status="error",
                detail=f"MCP server is disabled in {MCP_EXTENSION_CONFIG_PATH}",
                fix="run: memory adapter install qoder",
            )
        if config.get("userConfigMD5") != self._main_mcp_md5():
            return AdapterDiagnosticCheck(
                name="Qoder MCP extension cache",
                status="warn",
                detail="extension cache MD5 does not match Qoder user MCP config",
                fix="run: memory adapter install qoder",
            )
        return check

    def _diagnose_workspace_awareness(self) -> AdapterDiagnosticCheck:
        if _workspace_awareness_disabled():
            return AdapterDiagnosticCheck(
                name="Qoder workspace awareness",
                status="warn",
                detail="workspace awareness disabled by AGENT_MEMORY_HUB_DISABLE_WORKSPACE_AWARENESS",
                fix="unset AGENT_MEMORY_HUB_DISABLE_WORKSPACE_AWARENESS, then run: memory adapter install qoder",
            )
        paths = self._workspace_awareness_paths()
        if not paths:
            return AdapterDiagnosticCheck(
                name="Qoder workspace awareness",
                status="warn",
                detail="no recent Qoder workspace roots discovered from transcripts",
                fix="start a Qoder session once, then run: memory adapter install qoder",
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
                name="Qoder workspace awareness",
                status="error",
                detail=f"missing awareness block in workspace AGENTS.md: {', '.join(missing)}",
                fix="run: memory adapter install qoder",
            )
        return AdapterDiagnosticCheck(
            name="Qoder workspace awareness",
            status="ok",
            detail=f"hub awareness block present in {len(paths)} workspace root(s)",
        )

    def _diagnose_native_memory_bridge(self) -> AdapterDiagnosticCheck:
        profiles = self._native_memory_profiles()
        if not profiles:
            return AdapterDiagnosticCheck(
                name="Qoder native memory bridge",
                status="warn",
                detail=f"no Qoder native memory profiles discovered under {QODER_MEMORIES_DIR}",
                fix="create or sync one Qoder memory profile, then run: memory adapter install qoder",
            )
        missing: list[str] = []
        redirect_targets = 0
        for profile in profiles:
            for relative_path in NATIVE_BRIDGE_RELATIVE_PATHS:
                path = profile / relative_path
                if not path.exists() or not self._is_managed_native_memory_bridge(path):
                    missing.append(str(path))
            for relative_path in NATIVE_PRIORITY_REDIRECT_RELATIVE_PATHS:
                path = profile / relative_path
                if not path.exists():
                    continue
                redirect_targets += 1
                try:
                    text = path.read_text(encoding="utf-8")
                except OSError:
                    missing.append(str(path))
                    continue
                if (
                    NATIVE_REDIRECT_BEGIN not in text
                    or NATIVE_REDIRECT_END not in text
                    or not text.lstrip().startswith(NATIVE_REDIRECT_BEGIN)
                ):
                    missing.append(f"{path} (priority redirect is missing or not first)")
        missing.extend(self._native_database_priority_redirect_issues())
        if missing:
            return AdapterDiagnosticCheck(
                name="Qoder native memory bridge",
                status="error",
                detail=f"missing Qoder native AMH bridge or priority redirect: {', '.join(missing)}",
                fix="run: memory adapter install qoder",
            )
        if redirect_targets == 0:
            return AdapterDiagnosticCheck(
                name="Qoder native memory bridge",
                status="warn",
                detail=(
                    f"bridge files present in {len(profiles)} profile(s), but no native "
                    "用户个人信息.md target exists for priority redirect"
                ),
                fix="run Qoder memory sync once if native user_info memory is expected",
            )
        return AdapterDiagnosticCheck(
            name="Qoder native memory bridge",
            status="ok",
            detail=(
                f"bridge files and priority redirect present across {len(profiles)} "
                f"profile(s); priority redirect target(s): {redirect_targets}"
            ),
        )

    def _native_database_priority_redirect_issues(self) -> list[str]:
        if not QODER_LOCAL_DB_PATH.exists():
            return []
        try:
            connection = sqlite3.connect(f"file:{QODER_LOCAL_DB_PATH}?mode=ro", uri=True)
        except sqlite3.Error:
            return []
        issues: list[str] = []
        try:
            columns = {row[1] for row in connection.execute("pragma table_info(agent_memory)")}
            if not {"id", "title", "content"}.issubset(columns):
                return []
            placeholders = ", ".join("?" for _ in NATIVE_PRIORITY_REDIRECT_TITLES)
            rows = connection.execute(
                f"""
                select id, title, content
                from agent_memory
                where title in ({placeholders})
                """,
                NATIVE_PRIORITY_REDIRECT_TITLES,
            ).fetchall()
            for row_id, title, content in rows:
                if not isinstance(content, str):
                    issues.append(
                        f"{QODER_LOCAL_DB_PATH} agent_memory[{title}/{row_id}] "
                        "database priority redirect is missing"
                    )
                    continue
                if (
                    NATIVE_REDIRECT_BEGIN not in content
                    or NATIVE_REDIRECT_END not in content
                ):
                    issues.append(
                        f"{QODER_LOCAL_DB_PATH} agent_memory[{title}/{row_id}] "
                        "database priority redirect is missing"
                    )
                    continue
                if not content.lstrip().startswith(NATIVE_REDIRECT_BEGIN):
                    issues.append(
                        f"{QODER_LOCAL_DB_PATH} agent_memory[{title}/{row_id}] "
                        "database priority redirect is not first"
                    )
        except sqlite3.Error:
            return []
        finally:
            connection.close()
        return issues

    def _diagnose_client_effectiveness(self) -> AdapterDiagnosticCheck:
        transcripts = self._candidate_transcripts()
        if not transcripts:
            return AdapterDiagnosticCheck(
                name="Qoder client AMH effectiveness",
                status="warn",
                detail="no Qoder transcripts discovered to prove AMH context reached the model",
                fix="start a Qoder session and ask a known AMH-backed question, then re-run doctor",
            )
        bridge_refresh_mtime = self._latest_native_bridge_refresh_mtime()
        stale_transcript_paths: list[Path] = []
        for path in transcripts[:20]:
            evidence = self._classify_transcript_effectiveness(path)
            evidence_time = self._transcript_observed_time(path)
            if evidence == "amh":
                if (
                    bridge_refresh_mtime is not None
                    and evidence_time is not None
                    and evidence_time < bridge_refresh_mtime
                ):
                    stale_transcript_paths.append(path)
                    continue
                return AdapterDiagnosticCheck(
                    name="Qoder client AMH effectiveness",
                    status="ok",
                    detail=f"recent Qoder transcript shows AMH context/tool usage: {path}",
                )
            if evidence == "native-only":
                if (
                    bridge_refresh_mtime is not None
                    and evidence_time is not None
                    and evidence_time < bridge_refresh_mtime
                ):
                    stale_transcript_paths.append(path)
                    continue
                return AdapterDiagnosticCheck(
                    name="Qoder client AMH effectiveness",
                    status="warn",
                    detail=(
                        "latest AMH-hooked Qoder session used native SearchMemory "
                        f"without AMH MCP/context evidence: {path}"
                    ),
                    fix=(
                        "run: memory adapter install qoder, restart Qoder if needed, "
                        "then verify with an AMH-backed prompt"
                    ),
                )
        if stale_transcript_paths:
            return AdapterDiagnosticCheck(
                name="Qoder client AMH effectiveness",
                status="warn",
                detail=(
                    "latest Qoder transcript evidence predates the current Qoder native AMH bridge; "
                    f"start a fresh Qoder session to verify live AMH usage: {stale_transcript_paths[0]}"
                ),
                fix=(
                    "restart or refresh Qoder, ask an AMH-backed prompt such as a known project name, "
                    "then re-run: memory adapter doctor qoder --format json"
                ),
            )
        return AdapterDiagnosticCheck(
            name="Qoder client AMH effectiveness",
            status="warn",
            detail="Qoder transcripts found, but no AMH MCP/context evidence was observed",
            fix="verify Qoder exposes the agent-memory-hub MCP server or use the installed CLI fallback bridge",
        )

    def _latest_native_bridge_refresh_mtime(self) -> float | None:
        mtimes: list[float] = []
        for profile in self._native_memory_profiles():
            for relative_path in NATIVE_BRIDGE_RELATIVE_PATHS:
                path = profile / relative_path
                if path.exists() and self._is_managed_native_memory_bridge(path):
                    mtime = self._path_mtime(path)
                    if mtime is not None:
                        mtimes.append(mtime)
            for relative_path in NATIVE_PRIORITY_REDIRECT_RELATIVE_PATHS:
                path = profile / relative_path
                if not path.exists() or not path.is_file():
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except OSError:
                    continue
                if NATIVE_REDIRECT_BEGIN in text and NATIVE_REDIRECT_END in text:
                    mtime = self._path_mtime(path)
                    if mtime is not None:
                        mtimes.append(mtime)
        db_mtime = self._native_bridge_database_refresh_mtime()
        if db_mtime is not None:
            mtimes.append(db_mtime)
        return max(mtimes) if mtimes else None

    def _native_bridge_database_refresh_mtime(self) -> float | None:
        if not QODER_LOCAL_DB_PATH.exists():
            return None
        try:
            connection = sqlite3.connect(f"file:{QODER_LOCAL_DB_PATH}?mode=ro", uri=True)
        except sqlite3.Error:
            return None
        try:
            row = connection.execute(
                """
                select max(gmt_modified)
                from agent_memory
                where title = ?
                   or content like ?
                   or content like ?
                """,
                (
                    NATIVE_BRIDGE_TITLE,
                    f"%{NATIVE_BRIDGE_MARKER}%",
                    f"%{NATIVE_REDIRECT_BEGIN}%",
                ),
            ).fetchone()
        except sqlite3.Error:
            return None
        finally:
            connection.close()
        value = row[0] if row else None
        if value is None:
            return None
        try:
            timestamp = float(value)
        except (TypeError, ValueError):
            return None
        if timestamp > 9_999_999_999:
            timestamp /= 1000
        return timestamp

    def _path_mtime(self, path: Path) -> float | None:
        try:
            return path.stat().st_mtime
        except OSError:
            return None

    def _candidate_transcripts(self) -> list[Path]:
        if not QODER_PROJECTS_DIR.exists():
            return []
        paths = [
            path
            for path in QODER_PROJECTS_DIR.rglob("*")
            if path.is_file() and path.suffix.lower() in {".jsonl", ".json", ".log", ".txt"}
        ]
        paths.sort(key=lambda path: self._transcript_sort_key(path), reverse=True)
        return paths

    def _transcript_sort_key(self, path: Path) -> float:
        return self._transcript_observed_time(path) or self._path_mtime(path) or 0

    def _transcript_observed_time(self, path: Path) -> float | None:
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            return None
        latest: float | None = None
        for line in lines:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            timestamp = record.get("timestamp")
            if not isinstance(timestamp, str) or not timestamp:
                continue
            parsed = self._parse_timestamp(timestamp)
            if parsed is None:
                continue
            latest = parsed if latest is None else max(latest, parsed)
        return latest or self._path_mtime(path)

    def _parse_timestamp(self, value: str) -> float | None:
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        try:
            return datetime.fromisoformat(normalized).timestamp()
        except ValueError:
            return None

    def _classify_transcript_effectiveness(self, path: Path) -> str:
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            return "unknown"
        text = "\n".join(lines)
        has_amh_hook = "AGENT_MEMORY_HUB_ADAPTER=qoder" in text or "agent-memory-hub" in text
        if not has_amh_hook:
            return "unknown"
        if (
            "<agent_brain>" in text
            or "mcp_agent-memory-hub" in text
            or "mcp__agent-memory-hub" in text
            or "agent-memory-hub_search_memory" in text
            or '"name": "search_memory"' in text
            or '"name":"search_memory"' in text
            or self._transcript_has_amh_cli_search(lines)
        ):
            return "amh"
        for line in lines:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if self._record_uses_native_search_memory(record):
                return "native-only"
        return "unknown"

    def _transcript_has_amh_cli_search(self, lines: list[str]) -> bool:
        for line in lines:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if self._record_uses_amh_cli_search(record):
                return True
        return False

    def _record_uses_amh_cli_search(self, value: object) -> bool:
        if isinstance(value, list):
            return any(self._record_uses_amh_cli_search(item) for item in value)
        if not isinstance(value, dict):
            return False
        if value.get("type") == "tool_use":
            name = str(value.get("name") or "")
            command = value.get("input", {}).get("command") if isinstance(value.get("input"), dict) else None
            if (
                name in {"Bash", "Shell", "Terminal"}
                and isinstance(command, str)
                and "-m agent_brain.interfaces.cli search" in command
            ):
                return True
        return any(self._record_uses_amh_cli_search(child) for child in value.values())

    def _record_uses_native_search_memory(self, value: object) -> bool:
        if isinstance(value, list):
            return any(self._record_uses_native_search_memory(item) for item in value)
        if not isinstance(value, dict):
            return False
        if value.get("type") == "tool_use" and value.get("name") == "SearchMemory":
            return True
        return any(self._record_uses_native_search_memory(child) for child in value.values())

register_adapter("qoder", QoderAdapter)
