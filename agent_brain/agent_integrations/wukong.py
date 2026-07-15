"""Wukong adapter.

Primary integration is Wukong/RewindDesktop's own MCP runtime:
``wukong-cli mcp add/list/start/tools`` writes to the *current login scope*
under ``~/.real/users/user-*/.mcp/mcpServerConfig.json``.  This matters because
RewindDesktop's ``McpConfig::config_path()`` resolves through
``get_agent_work_root()``, which is user-scoped after login.

``~/.wukong/brain_context.md`` is still maintained as a compatibility sidecar
for older/static prompt-injection surfaces, but current Wukong runtime
verification must go through MCP, not the sidecar file.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import AdapterBase, AdapterConfig
from .awareness import (
    diagnose_awareness_block,
    install_awareness_block,
    render_awareness_block,
    uninstall_awareness_block,
)
from .diagnostics import (
    AdapterDiagnosticCheck,
    AdapterDiagnosticReport,
    diagnose_mcp_json_server,
    overall_status,
)
from .python_runtime import amh_python_executable
from .registry import register_adapter

CONTEXT_FILE = Path.home() / ".wukong" / "brain_context.md"
DEFAULT_MCP_CONFIG_PATH = Path.home() / ".real" / ".mcp" / "mcpServerConfig.json"
MCP_CONFIG_PATH = DEFAULT_MCP_CONFIG_PATH
REAL_USERS_DIR = Path.home() / ".real" / "users"
WUKONG_SERVER_USERS_DIR = (
    Path.home()
    / "Library"
    / "Application Support"
    / "dingtalk-rewind-server"
    / "users"
)
WUKONG_SERVER_LOGS_DIR = (
    Path.home()
    / "Library"
    / "Application Support"
    / "dingtalk-rewind-server"
    / "logs"
)
SERVER_NAME = "agent-memory-hub"
DISPLAY_NAME = "Agent Memory Hub"
MCP_ARGS = ["-m", "agent_brain.interfaces.mcp.server"]
WUKONG_BOOTSTRAP_SKILL_ID = "agent-memory-hub-shared-memory"
WUKONG_BOOTSTRAP_SKILL_NAME = "agent-memory-hub-shared-memory"
WUKONG_BOOTSTRAP_SKILL_DIR = "agent-memory-hub-shared-memory"
WUKONG_BOOTSTRAP_MARKER = "Agent Memory Hub Wukong Bootstrap Skill"
WUKONG_NATIVE_MEMORY_MARKER = "Agent Memory Hub Wukong Native Memory Bridge"
WUKONG_NATIVE_MEMORY_PATH = "agent-memory-hub/awareness.md"
WUKONG_NATIVE_MEMORY_CHUNK_ID = f"{WUKONG_NATIVE_MEMORY_PATH}#0"
WUKONG_NATIVE_BRAIN_MEMORY_ID = "agent-memory-hub-shared-memory"
WUKONG_NATIVE_BRAIN_MEMORY_KEY = "agent-memory-hub shared memory redirect"
WUKONG_CLI_CANDIDATES = (
    Path("/Applications/DingTalkWuKong.app/Contents/MacOS/wukong-cli"),
)
SHORT_PROMPT_MAX_CHARS = 32
MESSAGE_LEN_RE = re.compile(r"\bmessage_len=(\d{1,4})\b")
PROMPT_FIELD_RES = (
    re.compile(r"\bmessage_preview=([^\s]+)"),
    re.compile(r'\blabel=Some\("([^"]{1,256})"\)'),
    re.compile(r"\btitle=([^\s]+)"),
    re.compile(r'"(?:title|message_preview|prompt)"\s*:\s*"([^"]{1,256})"'),
)
WUKONG_AMH_TOOL_MARKERS = (
    "search_memory",
    "brief_memory",
    "read_memory",
    "write_memory",
)
WUKONG_AMH_CONTEXT_MARKERS = (
    "<agent_brain>",
    WUKONG_BOOTSTRAP_MARKER,
    "Agent Memory Hub Awareness Channel",
    "agent-memory-hub shared memory",
)
WUKONG_AMH_CALL_MARKERS = (
    "tool_call",
    "tool_calls",
    "call_tool",
    "callTool",
    "function_call",
    "tool_name",
)
BEGIN = "<!-- BEGIN agent-memory-hub -->"
END = "<!-- END agent-memory-hub -->"
CLI_TIMEOUT_SECONDS = 20
EVIDENCE_READ_LIMIT_BYTES = 16 * 1024 * 1024
SESSION_ID_RE = re.compile(
    r"(?:session_id=Some\(\"|session_id=|sessionId[\"=:\s]+)([0-9a-fA-F-]{36})"
)


def _block_end(content: str, start: int) -> int:
    """Index just past the END sentinel for the hub block that begins at
    ``start``.  If END is missing (a truncated / corrupted block) treat the
    rest of the file as the block so install / uninstall recover instead of
    crashing on ``str.index(END)``."""
    end_idx = content.find(END, start)
    if end_idx == -1:
        return len(content)
    return end_idx + len(END)


class WukongAdapter(AdapterBase):
    """Real-install adapter for Wukong via scoped MCP + context sidecar."""

    def __init__(self, brain_dir: Path, repo_dir: Path | None = None):
        super().__init__(brain_dir)
        self.repo_dir = repo_dir or Path(__file__).resolve().parents[2]

    def get_config(self) -> AdapterConfig:
        return AdapterConfig(
            agent_name="wukong",
            config_dir=Path.home() / ".wukong",
            hook_type="file",
            inject_method="system_prompt",
            supports_hooks=True,
            supports_mcp=True,
        )

    def install(self) -> str:
        context_changed, context_msg = self._install_context()
        mcp_changed, mcp_msg = self._install_mcp()
        workspace_changed, workspace_msg = self._install_workspace_awareness()
        skill_changed, skill_msg = self._install_bootstrap_skill()
        native_changed, native_msg = self._install_native_memory_bridge()
        if (
            not context_changed
            and not mcp_changed
            and not workspace_changed
            and not skill_changed
            and not native_changed
        ):
            return (
                "wukong adapter: already installed (up-to-date) in "
                f"{CONTEXT_FILE}, Wukong MCP runtime, Wukong workspace awareness, "
                "Wukong bootstrap skill, and Wukong native memory bridge"
            )
        return " | ".join([context_msg, mcp_msg, workspace_msg, skill_msg, native_msg])

    def uninstall(self) -> str:
        context_msg = self._uninstall_context()
        mcp_msg = self._uninstall_mcp()
        workspace_msg = self._uninstall_workspace_awareness()
        skill_msg = self._uninstall_bootstrap_skill()
        native_msg = self._uninstall_native_memory_bridge()
        return " | ".join([context_msg, mcp_msg, workspace_msg, skill_msg, native_msg])

    def inject_context(self, query: str) -> str:
        return (
            f"# Wukong context injection via brain_context.md and MCP server '{SERVER_NAME}'\n"
            f"# Search: {amh_python_executable(self.repo_dir)} -m agent_brain search '{query}'\n"
            f"# Data: {self.brain_dir}\n"
        )

    def get_install_instructions(self) -> str:
        return (
            "## Wukong Adapter\n\n"
            "Registers the Agent Memory Hub MCP server through Wukong's own\n"
            "`wukong-cli mcp add/list/start/tools` runtime when available. This\n"
            "targets the current login scope under `~/.real/users/user-*/.mcp/`,\n"
            "matching RewindDesktop's `McpConfig::config_path()` +\n"
            "`get_agent_work_root()` contract. If the CLI is unavailable, the\n"
            f"adapter falls back to the legacy shared config `{MCP_CONFIG_PATH}`.\n\n"
            f"It also writes a compatibility brain-context block into `{CONTEXT_FILE}`.\n\n"
            "Run programmatically:\n\n"
            "    from agent_brain.agent_integrations.wukong import WukongAdapter\n"
            "    WukongAdapter(brain_dir=Path.home() / '.agent-memory-hub').install()\n\n"
            "Idempotent — re-running is a no-op if content unchanged.\n"
            "To remove: call `.uninstall()`."
        )

    def diagnose(self) -> AdapterDiagnosticReport:
        checks = [self._diagnose_context(), self._diagnose_mcp()]
        if self._should_manage_real_user_scope():
            checks.extend([
                self._diagnose_user_scoped_mcp_configs(),
                self._diagnose_workspace_awareness(),
                self._diagnose_bootstrap_skill(),
                self._diagnose_native_memory_bridge(),
                self._diagnose_client_effectiveness(),
            ])
        return AdapterDiagnosticReport(
            adapter="wukong",
            overall_status=overall_status(checks),
            checks=checks,
            brain_dir=self.brain_dir,
        )

    def _install_context(self) -> tuple[bool, str]:
        CONTEXT_FILE.parent.mkdir(parents=True, exist_ok=True)
        block = self._build_block()

        if CONTEXT_FILE.exists():
            content = CONTEXT_FILE.read_text(encoding="utf-8")
            if BEGIN in content:
                old_start = content.index(BEGIN)
                old_end = _block_end(content, old_start)
                old_block = content[old_start:old_end]
                if old_block == block:
                    return False, f"wukong adapter: brain context already installed in {CONTEXT_FILE}"
                content = content[:old_start] + block + content[old_end:]
                self._atomic_write_context(content)
                return True, f"wukong adapter: updated block in {CONTEXT_FILE}"
        else:
            content = ""

        if content and not content.endswith("\n"):
            content += "\n"
        content += block + "\n"
        self._atomic_write_context(content)
        return True, f"wukong adapter: installed brain context in {CONTEXT_FILE}"

    def _uninstall_context(self) -> str:
        if not CONTEXT_FILE.exists():
            return f"wukong adapter: {CONTEXT_FILE} does not exist, nothing to remove"
        content = CONTEXT_FILE.read_text(encoding="utf-8")
        if BEGIN not in content:
            return "wukong adapter: no hub block found, nothing to remove"

        start = content.index(BEGIN)
        end = _block_end(content, start)
        before = content[:start].rstrip("\n")
        after = content[end:].lstrip("\n")
        cleaned = before + ("\n" if before and after else "") + after
        self._atomic_write_context(cleaned)
        return f"wukong adapter: removed brain context from {CONTEXT_FILE}"

    def _install_mcp(self) -> tuple[bool, str]:
        user_changed = False
        user_msg: str | None = None
        if _should_use_wukong_cli():
            if self._should_manage_real_user_scope():
                user_changed, user_msg = self._install_user_scoped_mcp_configs()
            result = self._install_mcp_via_cli()
            if result is not None:
                return result[0] or user_changed, " | ".join(
                    msg for msg in (user_msg, result[1]) if msg
                )
        MCP_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        config = _read_json(MCP_CONFIG_PATH)
        servers = config.setdefault("mcpServers", {})
        if not isinstance(servers, dict):
            raise RuntimeError(
                f"refuse to overwrite {MCP_CONFIG_PATH}: mcpServers must be an object"
            )

        desired = _mcp_server_config(self.brain_dir)
        if servers.get(SERVER_NAME) == desired:
            return user_changed, " | ".join(
                msg
                for msg in (
                    user_msg,
                    f"wukong adapter: MCP server already registered in {MCP_CONFIG_PATH}",
                )
                if msg
            )

        servers[SERVER_NAME] = desired
        _atomic_write_json(MCP_CONFIG_PATH, config)
        return True, " | ".join(
            msg
            for msg in (
                user_msg,
                f"wukong adapter: registered MCP server in {MCP_CONFIG_PATH}",
            )
            if msg
        )

    def _uninstall_mcp(self) -> str:
        cli_msg = self._uninstall_mcp_via_cli() if _should_use_wukong_cli() else None
        user_msg = self._uninstall_user_scoped_mcp_configs() if self._should_manage_real_user_scope() else None
        if not MCP_CONFIG_PATH.exists():
            file_msg = f"wukong adapter: {MCP_CONFIG_PATH} does not exist, nothing to remove"
            return " | ".join(msg for msg in (cli_msg, user_msg, file_msg) if msg)
        config = _read_json(MCP_CONFIG_PATH)
        servers = config.get("mcpServers", {})
        if not isinstance(servers, dict) or SERVER_NAME not in servers:
            file_msg = "wukong adapter: no hub MCP server entry, nothing to remove"
            return " | ".join(msg for msg in (cli_msg, user_msg, file_msg) if msg)

        del servers[SERVER_NAME]
        _atomic_write_json(MCP_CONFIG_PATH, config)
        file_msg = f"wukong adapter: removed MCP server from {MCP_CONFIG_PATH}"
        return " | ".join(msg for msg in (cli_msg, user_msg, file_msg) if msg)

    def _install_mcp_via_cli(self) -> tuple[bool, str] | None:
        servers = _cli_list_servers()
        if servers is None:
            return None

        desired = _cli_add_payload(self.brain_dir)
        candidates = [server for server in servers if _is_hub_server(server)]
        matching_candidates = [server for server in candidates if _server_matches(server, self.brain_dir)]
        if matching_candidates:
            server = _preferred_hub_server(matching_candidates)
            server_id = str(server.get("id") or "")
            removed = _remove_duplicate_hub_servers(matching_candidates, keep_id=server_id)
            suffix = (
                f"; removed duplicate scoped Wukong MCP server(s): {', '.join(removed)}"
                if removed
                else ""
            )
            if server.get("isActive") is not True or server.get("status") != "connected":
                _cli_json("mcp", "start", {"id": server_id})
                return True, f"wukong adapter: started scoped Wukong MCP server {server_id}{suffix}"
            return (
                bool(removed),
                f"wukong adapter: scoped Wukong MCP server already registered as {server_id}{suffix}",
            )

        for server in candidates:
            server_id = str(server.get("id") or "")
            if not server_id:
                continue
            update_payload = dict(desired)
            update_payload["id"] = server_id
            updated = _cli_json("mcp", "update", update_payload)
            if updated is not None:
                _cli_json("mcp", "start", {"id": server_id})
                return True, f"wukong adapter: updated scoped Wukong MCP server {server_id}"

        added = _cli_json("mcp", "add", desired)
        if added is None:
            return None
        server_id = str(added.get("serverId") or added.get("server_id") or "")
        if server_id:
            _cli_json("mcp", "start", {"id": server_id})
            return True, f"wukong adapter: registered scoped Wukong MCP server {server_id}"
        return True, "wukong adapter: registered scoped Wukong MCP server"

    def _uninstall_mcp_via_cli(self) -> str | None:
        servers = _cli_list_servers()
        if servers is None:
            return None
        removed: list[str] = []
        for server in servers:
            if not _is_hub_server(server):
                continue
            server_id = str(server.get("id") or "")
            if not server_id:
                continue
            if _cli_json("mcp", "remove", {"id": server_id}) is not None:
                removed.append(server_id)
        if removed:
            return f"wukong adapter: removed scoped Wukong MCP server(s): {', '.join(removed)}"
        return "wukong adapter: no scoped Wukong MCP server entry, nothing to remove"

    def _build_block(self) -> str:
        lines = [
            BEGIN,
            "# Agent Memory Hub — Brain Pool Context",
            "",
            f"Brain directory: `{self.brain_dir}`",
            f"Search: `{amh_python_executable(self.repo_dir)} -m agent_brain search <query>`",
            "",
            "This agent has access to a shared brain pool.",
            "Use `search` to find relevant memories before starting work.",
            END,
        ]
        return "\n".join(lines)

    def _awareness_block(self) -> str:
        return render_awareness_block(
            agent_name="Wukong",
            brain_dir=self.brain_dir,
            tool_channel=(
                "Wukong workspace AGENTS.md/MEMORY.md awareness plus scoped "
                "Wukong MCP server; use CLI search as fallback if MCP is hidden"
            ),
            mcp_tools_available=True,
            extra_guidance=(
                "Wukong may read workspace MEMORY.md/AGENTS.md before using MCP; these files are the static awareness fallback.",
                "A one-word or short project/name prompt means first search AMH, not greet the user.",
                "Qoder native memory or Wukong local memory is not the AMH shared brain unless it points back to agent-memory-hub.",
            ),
        )

    def _install_workspace_awareness(self) -> tuple[bool, str]:
        if not self._should_manage_real_user_scope():
            return False, "wukong adapter: workspace awareness skipped outside Wukong user scope"
        paths = self._workspace_awareness_paths()
        if not paths:
            return False, f"wukong adapter: no Wukong workspace awareness paths under {REAL_USERS_DIR}"
        changed = 0
        block = self._awareness_block()
        for path in paths:
            if install_awareness_block(path, block, placement="prepend"):
                changed += 1
        return (
            changed > 0,
            f"wukong adapter: workspace awareness {'installed' if changed else 'already present'} in {changed}/{len(paths)} file(s)",
        )

    def _uninstall_workspace_awareness(self) -> str:
        if not self._should_manage_real_user_scope():
            return "wukong adapter: workspace awareness skipped outside Wukong user scope"
        paths = self._workspace_awareness_paths()
        removed = 0
        for path in paths:
            if uninstall_awareness_block(path):
                removed += 1
        if not paths:
            return f"wukong adapter: no Wukong workspace awareness paths under {REAL_USERS_DIR}"
        return f"wukong adapter: workspace awareness removed from {removed}/{len(paths)} file(s)"

    def _install_bootstrap_skill(self) -> tuple[bool, str]:
        if not self._should_manage_real_user_scope():
            return False, "wukong adapter: bootstrap skill skipped outside Wukong user scope"
        roots = self._real_user_roots()
        if not roots:
            return False, f"wukong adapter: no Wukong user roots under {REAL_USERS_DIR}"

        changed = 0
        db_synced = 0
        content = self._bootstrap_skill_content()
        for user_root in roots:
            skill_dir = user_root / ".skills" / WUKONG_BOOTSTRAP_SKILL_DIR
            skill_file = skill_dir / "SKILL.md"
            skill_dir.mkdir(parents=True, exist_ok=True)
            if not skill_file.exists() or skill_file.read_text(encoding="utf-8") != content:
                skill_file.write_text(content, encoding="utf-8")
                changed += 1
            db_path = user_root / ".skills" / "skills.db"
            if db_path.exists() and self._upsert_bootstrap_skill_db(db_path, skill_dir):
                db_synced += 1
                changed += 1
        return (
            changed > 0,
            "wukong adapter: bootstrap skill "
            f"{'installed' if changed else 'already present'} in {len(roots)} user scope(s), "
            f"skills.db synced in {db_synced}/{len(roots)} scope(s)",
        )

    def _uninstall_bootstrap_skill(self) -> str:
        if not self._should_manage_real_user_scope():
            return "wukong adapter: bootstrap skill skipped outside Wukong user scope"
        roots = self._real_user_roots()
        removed_files = 0
        removed_db = 0
        for user_root in roots:
            db_path = user_root / ".skills" / "skills.db"
            if db_path.exists() and self._delete_bootstrap_skill_db(db_path):
                removed_db += 1
            skill_dir = user_root / ".skills" / WUKONG_BOOTSTRAP_SKILL_DIR
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue
            try:
                text = skill_file.read_text(encoding="utf-8")
            except OSError:
                continue
            if WUKONG_BOOTSTRAP_MARKER not in text:
                continue
            shutil.rmtree(skill_dir)
            removed_files += 1
        if not roots:
            return f"wukong adapter: no Wukong user roots under {REAL_USERS_DIR}"
        return (
            "wukong adapter: bootstrap skill removed from "
            f"{removed_files}/{len(roots)} directory scope(s), "
            f"skills.db cleaned in {removed_db}/{len(roots)} scope(s)"
        )

    def _install_native_memory_bridge(self) -> tuple[bool, str]:
        if not self._should_manage_real_user_scope():
            return False, "wukong adapter: native memory bridge skipped outside Wukong user scope"
        text = self._native_memory_bridge_content()
        changed = 0
        total = 0
        errors: list[str] = []
        for db_path in self._native_memory_db_paths():
            total += 1
            try:
                if _sync_wukong_memory_index_bridge(db_path, text):
                    changed += 1
            except sqlite3.Error as exc:
                errors.append(f"{db_path}: {exc}")
        for db_path in self._native_brain_db_paths():
            total += 1
            try:
                if _sync_wukong_brain_memory_bridge(db_path, text):
                    changed += 1
            except sqlite3.Error as exc:
                errors.append(f"{db_path}: {exc}")
        if errors:
            return (
                changed > 0,
                "wukong adapter: native memory bridge partially synced "
                f"in {changed}/{total} database(s); errors: {'; '.join(errors)}",
            )
        if total == 0:
            return (
                False,
                f"wukong adapter: native memory bridge skipped; no Wukong memory DB under {WUKONG_SERVER_USERS_DIR}",
            )
        return (
            changed > 0,
            f"wukong adapter: native memory bridge {'synced' if changed else 'already synced'} in {changed}/{total} database(s)",
        )

    def _uninstall_native_memory_bridge(self) -> str:
        if not self._should_manage_real_user_scope():
            return "wukong adapter: native memory bridge skipped outside Wukong user scope"
        removed = 0
        total = 0
        for db_path in self._native_memory_db_paths():
            total += 1
            try:
                if _delete_wukong_memory_index_bridge(db_path):
                    removed += 1
            except sqlite3.Error:
                continue
        for db_path in self._native_brain_db_paths():
            total += 1
            try:
                if _delete_wukong_brain_memory_bridge(db_path):
                    removed += 1
            except sqlite3.Error:
                continue
        if total == 0:
            return f"wukong adapter: no Wukong native memory DB under {WUKONG_SERVER_USERS_DIR}"
        return f"wukong adapter: native memory bridge removed from {removed}/{total} database(s)"

    def _diagnose_native_memory_bridge(self) -> AdapterDiagnosticCheck:
        memory_dbs = self._native_memory_db_paths()
        brain_dbs = self._native_brain_db_paths()
        if not memory_dbs and not brain_dbs:
            return AdapterDiagnosticCheck(
                name="Wukong native memory bridge",
                status="warn",
                detail=f"no Wukong memory DB discovered under {WUKONG_SERVER_USERS_DIR}",
                fix="open Wukong once, then run: memory adapter install wukong",
            )
        issues: list[str] = []
        checked = 0
        for db_path in memory_dbs:
            checked += 1
            try:
                if not _wukong_memory_index_has_bridge(db_path):
                    issues.append(f"missing AMH native memory bridge in {db_path}")
            except sqlite3.Error as exc:
                issues.append(f"cannot inspect {db_path}: {exc}")
        for db_path in brain_dbs:
            checked += 1
            try:
                if not _wukong_brain_memory_has_bridge(db_path):
                    issues.append(f"missing AMH native brain redirect in {db_path}")
            except sqlite3.Error as exc:
                issues.append(f"cannot inspect {db_path}: {exc}")
        if issues:
            return AdapterDiagnosticCheck(
                name="Wukong native memory bridge",
                status="error",
                detail="; ".join(issues),
                fix="run: memory adapter install wukong",
            )
        return AdapterDiagnosticCheck(
            name="Wukong native memory bridge",
            status="ok",
            detail=f"AMH native memory bridge present in {checked} Wukong database(s)",
        )

    def _native_memory_db_paths(self) -> list[Path]:
        if not WUKONG_SERVER_USERS_DIR.exists():
            return []
        return sorted(
            path
            for path in WUKONG_SERVER_USERS_DIR.glob("user-*/storage/memory/memory.sqlite")
            if path.is_file()
        )

    def _native_brain_db_paths(self) -> list[Path]:
        if not WUKONG_SERVER_USERS_DIR.exists():
            return []
        return sorted(
            path
            for path in WUKONG_SERVER_USERS_DIR.glob("user-*/memory/brain.db")
            if path.is_file()
        )

    def _native_memory_bridge_content(self) -> str:
        return "\n".join(
            [
                f"# {WUKONG_NATIVE_MEMORY_MARKER}",
                "",
                f"Brain directory: `{self.brain_dir}`",
                f"AMH repo: `{self.repo_dir}`",
                "",
                "这是写入 Wukong 本地 memory.sqlite / brain.db 的检索桥，不是用户业务记忆。",
                "当用户只输入一个词、项目名、人名或短 prompt 时，不要直接问候；先查 Agent Memory Hub。",
                "",
                "优先通过 Wukong MCP runtime 调用 `agent-memory-hub`：",
                "- `brief_memory`：先拿有界全貌。",
                "- `search_memory`：按用户问题、项目名、历史上下文检索。",
                "- `read_memory`：只读取真正需要的 1-3 条详情。",
                "- `write_memory`：遇到决策、事实、信号、产物、可复用经验时写入共享大脑。",
                "- 普通检索使用 auto 的 locator/overview 候选；显式 detail 只用于有意、少量的诊断读取。",
                "",
                "如果 MCP runtime 没暴露，则用 CLI 兜底：",
                f"`PYTHONPATH={self.repo_dir} BRAIN_DIR={self.brain_dir} {amh_python_executable(self.repo_dir)} -m agent_brain.interfaces.cli search \"<用户问题>\" --top-k 5 --context-firewall --verbosity auto --explain`",
                "",
                "关键词：AMH agent-memory-hub Agent Memory Hub 共享记忆 共享大脑 历史上下文 跨智能体 项目记忆 短prompt brief_memory search_memory read_memory write_memory mcp_runtime。",
            ]
        )

    def _upsert_bootstrap_skill_db(self, db_path: Path, skill_dir: Path) -> bool:
        with sqlite3.connect(db_path) as conn:
            if not _sqlite_has_table(conn, "skills") or not _sqlite_has_table(conn, "skills_fts"):
                return False
            now_ms = int(time.time() * 1000)
            existing = conn.execute(
                "select created_at, description, central_path, enabled, search_keywords "
                "from skills where id = ?",
                (WUKONG_BOOTSTRAP_SKILL_ID,),
            ).fetchone()
            description = self._bootstrap_skill_description()
            keywords = self._bootstrap_skill_keywords()
            created_at = int(existing[0]) if existing else now_ms
            next_row = (
                WUKONG_BOOTSTRAP_SKILL_ID,
                WUKONG_BOOTSTRAP_SKILL_NAME,
                description,
                "local",
                "agent-memory-hub",
                None,
                None,
                None,
                "local",
                WUKONG_BOOTSTRAP_SKILL_ID,
                str(skill_dir),
                1,
                created_at,
                now_ms,
                now_ms,
                "ok",
                ".md",
                keywords,
            )
            changed = (
                existing is None
                or existing[1] != description
                or existing[2] != str(skill_dir)
                or int(existing[3]) != 1
                or existing[4] != keywords
            )
            conn.execute(
                """
                insert into skills (
                    id, name, description, source_type, source_ref, remote_skill_id,
                    remote_version, remote_source, sync_status, directory_identity,
                    central_path, enabled, created_at, updated_at, last_sync_at,
                    status, extension, search_keywords
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(id) do update set
                    name = excluded.name,
                    description = excluded.description,
                    source_type = excluded.source_type,
                    source_ref = excluded.source_ref,
                    remote_skill_id = excluded.remote_skill_id,
                    remote_version = excluded.remote_version,
                    remote_source = excluded.remote_source,
                    sync_status = excluded.sync_status,
                    directory_identity = excluded.directory_identity,
                    central_path = excluded.central_path,
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at,
                    last_sync_at = excluded.last_sync_at,
                    status = excluded.status,
                    extension = excluded.extension,
                    search_keywords = excluded.search_keywords
                """,
                next_row,
            )
            conn.execute(
                "delete from skills_fts where skill_id = ?",
                (WUKONG_BOOTSTRAP_SKILL_ID,),
            )
            conn.execute(
                "insert into skills_fts (skill_id, name, description, keywords) values (?, ?, ?, ?)",
                (
                    WUKONG_BOOTSTRAP_SKILL_ID,
                    WUKONG_BOOTSTRAP_SKILL_NAME,
                    description,
                    keywords,
                ),
            )
            return changed

    def _delete_bootstrap_skill_db(self, db_path: Path) -> bool:
        with sqlite3.connect(db_path) as conn:
            if not _sqlite_has_table(conn, "skills") or not _sqlite_has_table(conn, "skills_fts"):
                return False
            before = conn.execute(
                "select count(*) from skills where id = ?",
                (WUKONG_BOOTSTRAP_SKILL_ID,),
            ).fetchone()[0]
            conn.execute(
                "delete from skills_fts where skill_id = ?",
                (WUKONG_BOOTSTRAP_SKILL_ID,),
            )
            conn.execute(
                "delete from skills where id = ?",
                (WUKONG_BOOTSTRAP_SKILL_ID,),
            )
            return bool(before)

    def _bootstrap_skill_description(self) -> str:
        return (
            "Agent Memory Hub shared memory bootstrap: for short project/name prompts, "
            "resume, handoff, planning, debugging, or cross-agent context, first use "
            "mcp_runtime to call agent-memory-hub brief_memory/search_memory/read_memory; "
            "fallback to AMH CLI when MCP is hidden."
        )

    def _bootstrap_skill_keywords(self) -> str:
        return (
            "AMH agent-memory-hub Agent Memory Hub 共享记忆 共享大脑 跨智能体 "
            "项目名 短prompt 历史上下文 继续之前工作 交接 brief_memory "
            "search_memory read_memory write_memory mcp_runtime"
        )

    def _bootstrap_skill_content(self) -> str:
        return "\n".join(
            [
                f"# {WUKONG_BOOTSTRAP_SKILL_NAME}",
                "",
                f"{WUKONG_BOOTSTRAP_MARKER}。",
                "",
                "## 何时使用",
                "",
                "- 用户只输入一个词、项目名、人名或短 prompt。",
                "- 用户说继续、承接、上次、历史上下文、交接、规划、排查、调试。",
                "- 当前任务可能依赖 Claude Code、Codex、Qoder、Wukong 等其他 Agent 产生过的记忆。",
                "",
                "## 必须先做",
                "",
                "不要直接问候，也不要只读 Wukong 本地会话记忆。先通过 Wukong 的 `mcp_runtime` 使用 AMH：",
                "",
                "1. `list_servers`，找到 `agent-memory-hub` 或 `Agent Memory Hub`。",
                "2. `list_tools`，确认 `brief_memory`、`search_memory`、`read_memory` 可用。",
                "3. 对短 prompt 先调用 `brief_memory` 或 `search_memory`，再按需 `read_memory` 读取 1-3 条详情。",
                "4. 普通检索使用 auto 的 locator/overview 候选；显式 detail 只用于有意、少量的诊断读取。",
                "",
                "如果当前界面没有暴露 MCP runtime，就用 CLI 兜底：",
                "",
                "```bash",
                f"BRAIN_DIR={self.brain_dir} PYTHONPATH={self.repo_dir} {amh_python_executable(self.repo_dir)} -m agent_brain.interfaces.cli search \"<用户问题或项目名>\" --top-k 5 --format text --context-firewall --verbosity auto --explain",
                "```",
                "",
                "## 回答边界",
                "",
                "- AMH 召回的是 memory candidates，不是当前聊天 transcript。",
                "- 当前用户消息和实时文件证据优先。",
                "- 如果 AMH 没召回到可靠记忆，要明确说没有找到共享记忆证据，再继续基于当前仓库查证。",
                "",
            ]
        )

    def _workspace_awareness_paths(self) -> list[Path]:
        paths: list[Path] = []
        for user_root in self._real_user_roots():
            workspace = user_root / "workspace"
            if not workspace.exists() or not workspace.is_dir():
                continue
            paths.extend([workspace / "AGENTS.md", workspace / "MEMORY.md"])
            projects_dir = workspace / "projects"
            if not projects_dir.exists() or not projects_dir.is_dir():
                continue
            for project_root in projects_dir.iterdir():
                if not project_root.is_dir():
                    continue
                paths.extend([project_root / "AGENTS.md", project_root / "MEMORY.md"])
        paths = sorted({path for path in paths})
        return paths

    def _install_user_scoped_mcp_configs(self) -> tuple[bool, str]:
        paths = self._user_mcp_config_paths()
        if not paths:
            return False, f"wukong adapter: no user-scoped MCP config paths under {REAL_USERS_DIR}"
        desired = _mcp_server_config(self.brain_dir)
        changed = 0
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            config = _read_json(path)
            servers = config.setdefault("mcpServers", {})
            if not isinstance(servers, dict):
                raise RuntimeError(f"refuse to overwrite {path}: mcpServers must be an object")
            existing = servers.get(SERVER_NAME)
            if isinstance(existing, dict) and _server_matches(existing, self.brain_dir):
                continue
            servers[SERVER_NAME] = desired
            _atomic_write_json(path, config)
            changed += 1
        return (
            changed > 0,
            f"wukong adapter: user-scoped MCP config {'synced' if changed else 'already synced'} in {changed}/{len(paths)} file(s)",
        )

    def _uninstall_user_scoped_mcp_configs(self) -> str:
        paths = self._user_mcp_config_paths()
        removed = 0
        for path in paths:
            if not path.exists():
                continue
            config = _read_json(path)
            servers = config.get("mcpServers", {})
            if not isinstance(servers, dict) or SERVER_NAME not in servers:
                continue
            del servers[SERVER_NAME]
            _atomic_write_json(path, config)
            removed += 1
        if not paths:
            return f"wukong adapter: no user-scoped MCP config paths under {REAL_USERS_DIR}"
        return f"wukong adapter: removed user-scoped MCP config from {removed}/{len(paths)} file(s)"

    def _user_mcp_config_paths(self) -> list[Path]:
        return [user_root / ".mcp" / "mcpServerConfig.json" for user_root in self._real_user_roots()]

    def _real_user_roots(self) -> list[Path]:
        if not REAL_USERS_DIR.exists():
            return []
        roots = [
            path
            for path in REAL_USERS_DIR.iterdir()
            if path.is_dir() and path.name.startswith("user-")
        ]
        roots.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        return roots

    def _should_manage_real_user_scope(self) -> bool:
        return MCP_CONFIG_PATH == DEFAULT_MCP_CONFIG_PATH

    def _atomic_write_context(self, content: str) -> None:
        """Write CONTEXT_FILE atomically (temp file + os.replace) so a crash
        mid-write can never leave a half-written context file."""
        tmp = CONTEXT_FILE.with_suffix(CONTEXT_FILE.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(CONTEXT_FILE)

    def _atomic_write(self, content: str) -> None:
        """Backward-compatible alias for robustness tests and older callers."""
        self._atomic_write_context(content)

    def _diagnose_context(self) -> AdapterDiagnosticCheck:
        if not CONTEXT_FILE.exists():
            return AdapterDiagnosticCheck(
                name="Wukong brain context",
                status="error",
                detail=f"missing: {CONTEXT_FILE}",
                fix="run: memory adapter install wukong",
            )

        content = CONTEXT_FILE.read_text(encoding="utf-8")
        if BEGIN not in content or END not in content:
            return AdapterDiagnosticCheck(
                name="Wukong brain context",
                status="error",
                detail=f"hub sentinel block missing or incomplete: {CONTEXT_FILE}",
                fix="run: memory adapter install wukong",
            )

        block = content[content.index(BEGIN):_block_end(content, content.index(BEGIN))]
        if str(self.brain_dir) not in block:
            return AdapterDiagnosticCheck(
                name="Wukong brain context",
                status="error",
                detail="hub block points to a different brain directory",
                fix="run: memory adapter install wukong",
            )

        return AdapterDiagnosticCheck(
            name="Wukong brain context",
            status="ok",
            detail=f"hub block present in {CONTEXT_FILE}",
        )

    def _diagnose_user_scoped_mcp_configs(self) -> AdapterDiagnosticCheck:
        paths = self._user_mcp_config_paths()
        if not paths:
            return AdapterDiagnosticCheck(
                name="Wukong user-scoped MCP config",
                status="warn",
                detail=f"no Wukong user-scoped MCP config paths discovered under {REAL_USERS_DIR}",
                fix="log into Wukong once, then run: memory adapter install wukong",
            )
        missing: list[str] = []
        mismatched: list[str] = []
        for path in paths:
            if not path.exists():
                missing.append(str(path))
                continue
            config = _read_json(path)
            servers = config.get("mcpServers", {})
            if not isinstance(servers, dict) or SERVER_NAME not in servers:
                missing.append(str(path))
                continue
            server = servers.get(SERVER_NAME)
            if not isinstance(server, dict) or not _server_matches(server, self.brain_dir):
                mismatched.append(str(path))
        if missing:
            return AdapterDiagnosticCheck(
                name="Wukong user-scoped MCP config",
                status="error",
                detail=f"missing AMH server in user-scoped MCP config: {', '.join(missing)}",
                fix="run: memory adapter install wukong",
            )
        if mismatched:
            return AdapterDiagnosticCheck(
                name="Wukong user-scoped MCP config",
                status="error",
                detail=f"AMH server mismatch in user-scoped MCP config: {', '.join(mismatched)}",
                fix="run: memory adapter install wukong",
            )
        return AdapterDiagnosticCheck(
            name="Wukong user-scoped MCP config",
            status="ok",
            detail=f"AMH server present in {len(paths)} user-scoped MCP config file(s)",
        )

    def _diagnose_workspace_awareness(self) -> AdapterDiagnosticCheck:
        paths = self._workspace_awareness_paths()
        if not paths:
            return AdapterDiagnosticCheck(
                name="Wukong workspace awareness",
                status="warn",
                detail=f"no Wukong workspace AGENTS.md/MEMORY.md paths discovered under {REAL_USERS_DIR}",
                fix="log into Wukong once, then run: memory adapter install wukong",
            )
        issues: list[str] = []
        for path in paths:
            check = diagnose_awareness_block(
                check_name="Wukong workspace awareness",
                path=path,
                brain_dir=self.brain_dir,
                install_command="memory adapter install wukong",
                require_first=True,
            )
            if check.status != "ok":
                issues.append(check.detail)
        if issues:
            return AdapterDiagnosticCheck(
                name="Wukong workspace awareness",
                status="error",
                detail=f"AMH awareness issue(s) in Wukong workspace file(s): {'; '.join(issues)}",
                fix="run: memory adapter install wukong",
            )
        return AdapterDiagnosticCheck(
            name="Wukong workspace awareness",
            status="ok",
            detail=f"AMH awareness present in {len(paths)} Wukong workspace file(s)",
        )

    def _diagnose_bootstrap_skill(self) -> AdapterDiagnosticCheck:
        roots = self._real_user_roots()
        if not roots:
            return AdapterDiagnosticCheck(
                name="Wukong bootstrap skill",
                status="warn",
                detail=f"no Wukong user roots discovered under {REAL_USERS_DIR}",
                fix="log into Wukong once, then run: memory adapter install wukong",
            )
        missing: list[str] = []
        for user_root in roots:
            skill_file = user_root / ".skills" / WUKONG_BOOTSTRAP_SKILL_DIR / "SKILL.md"
            db_path = user_root / ".skills" / "skills.db"
            if not skill_file.exists():
                missing.append(str(skill_file))
                continue
            if not db_path.exists():
                missing.append(str(db_path))
                continue
            try:
                text = skill_file.read_text(encoding="utf-8")
            except OSError:
                missing.append(str(skill_file))
                continue
            if WUKONG_BOOTSTRAP_MARKER not in text:
                missing.append(str(skill_file))
                continue
            with sqlite3.connect(db_path) as conn:
                if not _sqlite_has_table(conn, "skills"):
                    missing.append(str(db_path))
                    continue
                row = conn.execute(
                    "select enabled, central_path from skills where id = ?",
                    (WUKONG_BOOTSTRAP_SKILL_ID,),
                ).fetchone()
            if row is None or int(row[0]) != 1 or row[1] != str(skill_file.parent):
                missing.append(str(db_path))
        if missing:
            return AdapterDiagnosticCheck(
                name="Wukong bootstrap skill",
                status="error",
                detail=f"missing AMH bootstrap skill in Wukong skill store: {', '.join(missing)}",
                fix="run: memory adapter install wukong",
            )
        return AdapterDiagnosticCheck(
            name="Wukong bootstrap skill",
            status="ok",
            detail=f"AMH bootstrap skill present in {len(roots)} Wukong user scope(s)",
        )

    def _diagnose_mcp(self) -> AdapterDiagnosticCheck:
        if _should_use_wukong_cli():
            cli_check = self._diagnose_mcp_via_cli()
            if cli_check is not None:
                return cli_check
        return diagnose_mcp_json_server(
            check_name="Wukong MCP server",
            config_path=MCP_CONFIG_PATH,
            server_name=SERVER_NAME,
            expected_command=amh_python_executable(self.repo_dir),
            expected_args=MCP_ARGS,
            expected_env={"BRAIN_DIR": str(self.brain_dir)},
            install_command="memory adapter install wukong",
        )

    def _diagnose_mcp_via_cli(self) -> AdapterDiagnosticCheck | None:
        servers = _cli_list_servers()
        if servers is None:
            return None
        candidates = [server for server in servers if _is_hub_server(server)]
        if not candidates:
            return AdapterDiagnosticCheck(
                name="Wukong MCP server",
                status="error",
                detail="missing scoped Wukong MCP server in current login scope",
                fix="run: memory adapter install wukong",
            )

        matching = [server for server in candidates if _server_matches(server, self.brain_dir)]
        if not matching:
            return AdapterDiagnosticCheck(
                name="Wukong MCP server",
                status="error",
                detail="scoped Wukong MCP server exists but command/args/env do not match current AMH install",
                fix="run: memory adapter install wukong",
            )

        server = matching[0]
        server_id = str(server.get("id") or "")
        status = str(server.get("status") or "unknown")
        if server.get("isActive") is not True:
            return AdapterDiagnosticCheck(
                name="Wukong MCP server",
                status="error",
                detail=f"scoped Wukong MCP server {server_id} is inactive",
                fix=f"run: wukong-cli mcp start --json '{{\"id\":\"{server_id}\"}}'",
            )
        if status != "connected":
            return AdapterDiagnosticCheck(
                name="Wukong MCP server",
                status="warn",
                detail=f"scoped Wukong MCP server {server_id} status is {status}",
                fix=f"run: wukong-cli mcp start --json '{{\"id\":\"{server_id}\"}}'",
            )

        tools = _cli_json("mcp", "tools", {"id": server_id})
        tool_names = (
            {
                str(tool.get("name"))
                for tool in tools.get("tools", [])
                if isinstance(tool, dict)
            }
            if isinstance(tools, dict)
            else set()
        )
        if {"search_memory", "write_memory", "list_recent"}.issubset(tool_names):
            return AdapterDiagnosticCheck(
                name="Wukong MCP server",
                status="ok",
                detail=f"scoped Wukong MCP server {server_id} connected with AMH tools",
            )

        return AdapterDiagnosticCheck(
            name="Wukong MCP server",
            status="warn",
            detail=f"scoped Wukong MCP server {server_id} connected but AMH tools were not discovered",
            fix="run: memory adapter install wukong",
        )

    def _diagnose_client_effectiveness(self) -> AdapterDiagnosticCheck:
        evidence_files = self._client_evidence_files()
        if not evidence_files:
            return AdapterDiagnosticCheck(
                name="Wukong client AMH effectiveness",
                status="warn",
                detail=(
                    "no Wukong client logs or LLM proxy evidence discovered to prove "
                    "AMH reached the model"
                ),
                fix="start a Wukong session with a known AMH-backed prompt, then re-run doctor",
            )

        runtime_only: list[Path] = []
        for path in evidence_files[:40]:
            kind, session_id = self._classify_client_evidence(path)
            if kind == "amh":
                return AdapterDiagnosticCheck(
                    name="Wukong client AMH effectiveness",
                    status="ok",
                    detail=f"recent Wukong evidence shows AMH usage: {path}",
                )
            if kind == "short-prompt":
                suffix = f" session_id={session_id}" if session_id else ""
                return AdapterDiagnosticCheck(
                    name="Wukong client AMH effectiveness",
                    status="warn",
                    detail=(
                        "latest Wukong short-prompt session did not show AMH usage"
                        f"{suffix}: {path}"
                    ),
                    fix=(
                        "open a fresh Wukong task, ask a known AMH-backed prompt, "
                        "verify it calls agent-memory-hub via mcp_runtime/brief_memory/search_memory, "
                        "then re-run: memory adapter doctor wukong --format json"
                    ),
                )
            if kind == "runtime-only":
                runtime_only.append(path)

        if runtime_only:
            return AdapterDiagnosticCheck(
                name="Wukong client AMH effectiveness",
                status="warn",
                detail=(
                    "Wukong exposes MCP runtime/server evidence, but no AMH context "
                    f"or tool-call evidence was observed: {runtime_only[0]}"
                ),
                fix="verify Wukong injects the AMH bootstrap skill or calls mcp_runtime for agent-memory-hub",
            )
        return AdapterDiagnosticCheck(
            name="Wukong client AMH effectiveness",
            status="warn",
            detail="Wukong evidence files exist, but none prove AMH reached or was used by the model",
            fix="start a Wukong session with a known AMH-backed prompt, then re-run doctor",
        )

    def _client_evidence_files(self) -> list[Path]:
        paths: list[Path] = []
        if WUKONG_SERVER_LOGS_DIR.exists():
            for pattern in ("app/*.log", "frontend/*.log"):
                paths.extend(
                    path
                    for path in WUKONG_SERVER_LOGS_DIR.glob(pattern)
                    if path.is_file()
                )
        if WUKONG_SERVER_USERS_DIR.exists():
            for user_root in WUKONG_SERVER_USERS_DIR.glob("user-*"):
                proxy_root = user_root / "storage" / "llm_proxy"
                request_log = proxy_root / "requests.jsonl"
                if request_log.is_file():
                    paths.append(request_log)
                raw_root = proxy_root / "raw"
                if raw_root.exists():
                    paths.extend(path for path in raw_root.glob("*.json") if path.is_file())
        unique = sorted({path for path in paths}, key=lambda path: _path_mtime(path), reverse=True)
        return unique

    def _classify_client_evidence(self, path: Path) -> tuple[str, str | None]:
        text = _read_evidence_text(path)
        if not text:
            return "unknown", None
        session_id = self._latest_short_prompt_session_id(text)
        if session_id:
            if _text_has_amh_usage(text, session_id=session_id):
                return "amh", session_id
            return "short-prompt", session_id
        if _text_has_wukong_short_prompt(text):
            if _text_has_amh_usage(text):
                return "amh", None
            return "short-prompt", None
        if _text_has_amh_usage(text):
            return "amh", None
        if "mcp_runtime injected" in text or SERVER_NAME in text or DISPLAY_NAME in text:
            return "runtime-only", None
        return "unknown", None

    def _latest_short_prompt_session_id(self, text: str) -> str | None:
        for line in reversed(text.splitlines()):
            if not _text_has_wukong_short_prompt(line):
                continue
            match = SESSION_ID_RE.search(line)
            if match:
                return match.group(1)
        return None


def _mcp_server_config(brain_dir: Path) -> dict[str, object]:
    return {
        "isActive": True,
        "name": DISPLAY_NAME,
        "type": "stdio",
        "disabledTools": [],
        "env": {"BRAIN_DIR": str(brain_dir)},
        "description": "Shared Agent Memory Hub MCP server",
        "timeout": 60,
        "isBuiltin": False,
        "isRemovable": True,
        "source": "user",
        "command": amh_python_executable(),
        "args": MCP_ARGS,
    }


def _cli_add_payload(brain_dir: Path) -> dict[str, object]:
    return {
        "name": DISPLAY_NAME,
        "type": "stdio",
        "command": amh_python_executable(),
        "args": MCP_ARGS,
        "env": {"BRAIN_DIR": str(brain_dir)},
        "description": "Shared Agent Memory Hub MCP server",
    }


def _find_wukong_cli() -> Path | None:
    path = shutil.which("wukong-cli")
    if path:
        return Path(path)
    for candidate in WUKONG_CLI_CANDIDATES:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _should_use_wukong_cli() -> bool:
    # Unit tests monkeypatch MCP_CONFIG_PATH to a temp file. In that case never
    # touch a real running Wukong app via CLI.
    return MCP_CONFIG_PATH == DEFAULT_MCP_CONFIG_PATH and _find_wukong_cli() is not None


def _cli_json(namespace: str, action: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    cli = _find_wukong_cli()
    if cli is None:
        return None
    try:
        completed = subprocess.run(
            [str(cli), namespace, action, "--json", json.dumps(payload, ensure_ascii=False)],
            capture_output=True,
            text=True,
            timeout=CLI_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    if completed.returncode != 0:
        return None
    try:
        parsed = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _cli_list_servers() -> list[dict[str, Any]] | None:
    payload = _cli_json("mcp", "list", {})
    if payload is None:
        return None
    servers = payload.get("servers")
    if not isinstance(servers, list):
        return None
    return [server for server in servers if isinstance(server, dict)]


def _is_hub_server(server: dict[str, Any]) -> bool:
    return (
        server.get("id") == SERVER_NAME
        or server.get("name") == DISPLAY_NAME
        or server.get("description") == "Shared Agent Memory Hub MCP server"
    )


def _preferred_hub_server(servers: list[dict[str, Any]]) -> dict[str, Any]:
    for server in servers:
        if server.get("id") == SERVER_NAME:
            return server
    for server in servers:
        if server.get("isActive") is True and server.get("status") == "connected":
            return server
    return servers[0]


def _remove_duplicate_hub_servers(
    servers: list[dict[str, Any]],
    *,
    keep_id: str,
) -> list[str]:
    removed: list[str] = []
    for server in servers:
        server_id = str(server.get("id") or "")
        if not server_id or server_id == keep_id:
            continue
        if _cli_json("mcp", "remove", {"id": server_id}) is not None:
            removed.append(server_id)
    return removed


def _server_matches(server: dict[str, Any], brain_dir: Path) -> bool:
    env = server.get("env") if isinstance(server.get("env"), dict) else {}
    return (
        server.get("type") == "stdio"
        and server.get("command") == amh_python_executable()
        and server.get("args") == MCP_ARGS
        and env.get("BRAIN_DIR") == str(brain_dir)
    )


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"refuse to overwrite malformed {path} - fix it by hand first: {exc}"
        ) from exc
    if not isinstance(loaded, dict):
        raise RuntimeError(f"refuse to overwrite {path}: JSON root must be an object")
    return loaded


def _atomic_write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _sqlite_has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "select 1 from sqlite_master where name = ? and type in ('table', 'view')",
        (name,),
    ).fetchone()
    return row is not None


def _sync_wukong_memory_index_bridge(db_path: Path, text: str) -> bool:
    with sqlite3.connect(db_path, timeout=5) as conn:
        if not all(
            _sqlite_has_table(conn, table)
            for table in ("memory_chunks", "chunks_fts")
        ):
            return False
        now = int(time.time())
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        existing = conn.execute(
            "select text, hash from memory_chunks where id = ?",
            (WUKONG_NATIVE_MEMORY_CHUNK_ID,),
        ).fetchone()
        changed = existing is None or existing[0] != text or existing[1] != content_hash
        if _sqlite_has_table(conn, "memory_files"):
            conn.execute(
                """
                insert into memory_files (path, source, hash, mtime, size)
                values (?, ?, ?, ?, ?)
                on conflict(path) do update set
                    source = excluded.source,
                    hash = excluded.hash,
                    mtime = excluded.mtime,
                    size = excluded.size
                """,
                (
                    WUKONG_NATIVE_MEMORY_PATH,
                    "agent-memory-hub",
                    content_hash,
                    now,
                    len(text.encode("utf-8")),
                ),
            )
        conn.execute(
            "delete from chunks_fts where id = ?",
            (WUKONG_NATIVE_MEMORY_CHUNK_ID,),
        )
        conn.execute(
            """
            insert into memory_chunks (
                id, path, source, start_line, end_line, hash, model, text, embedding, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(id) do update set
                path = excluded.path,
                source = excluded.source,
                start_line = excluded.start_line,
                end_line = excluded.end_line,
                hash = excluded.hash,
                model = excluded.model,
                text = excluded.text,
                embedding = excluded.embedding,
                updated_at = excluded.updated_at
            """,
            (
                WUKONG_NATIVE_MEMORY_CHUNK_ID,
                WUKONG_NATIVE_MEMORY_PATH,
                "agent-memory-hub",
                1,
                max(1, len(text.splitlines())),
                content_hash,
                "",
                text,
                "[]",
                now,
            ),
        )
        conn.execute(
            "insert into chunks_fts (id, text) values (?, ?)",
            (WUKONG_NATIVE_MEMORY_CHUNK_ID, _wukong_fts_text(text)),
        )
        if _sqlite_has_table(conn, "memory_meta"):
            conn.execute(
                "insert into memory_meta (key, value) values ('last_sync_at', ?) "
                "on conflict(key) do update set value = excluded.value",
                (str(now),),
            )
        return changed


def _delete_wukong_memory_index_bridge(db_path: Path) -> bool:
    with sqlite3.connect(db_path, timeout=5) as conn:
        if not _sqlite_has_table(conn, "memory_chunks"):
            return False
        existed = conn.execute(
            "select 1 from memory_chunks where id = ?",
            (WUKONG_NATIVE_MEMORY_CHUNK_ID,),
        ).fetchone()
        if _sqlite_has_table(conn, "chunks_fts"):
            conn.execute(
                "delete from chunks_fts where id = ?",
                (WUKONG_NATIVE_MEMORY_CHUNK_ID,),
            )
        conn.execute(
            "delete from memory_chunks where id = ?",
            (WUKONG_NATIVE_MEMORY_CHUNK_ID,),
        )
        if _sqlite_has_table(conn, "memory_files"):
            conn.execute(
                "delete from memory_files where path = ?",
                (WUKONG_NATIVE_MEMORY_PATH,),
            )
        return existed is not None


def _wukong_memory_index_has_bridge(db_path: Path) -> bool:
    with sqlite3.connect(db_path, timeout=5) as conn:
        if not all(
            _sqlite_has_table(conn, table)
            for table in ("memory_chunks", "chunks_fts")
        ):
            return False
        row = conn.execute(
            "select text from memory_chunks where id = ? and source = 'agent-memory-hub'",
            (WUKONG_NATIVE_MEMORY_CHUNK_ID,),
        ).fetchone()
        if row is None or WUKONG_NATIVE_MEMORY_MARKER not in row[0]:
            return False
        fts_row = conn.execute(
            "select 1 from chunks_fts where id = ? and chunks_fts match ?",
            (WUKONG_NATIVE_MEMORY_CHUNK_ID, '"brief_memory"'),
        ).fetchone()
        return fts_row is not None


def _sync_wukong_brain_memory_bridge(db_path: Path, text: str) -> bool:
    with sqlite3.connect(db_path, timeout=5) as conn:
        if not _sqlite_has_table(conn, "memories"):
            return False
        now = datetime.now(timezone.utc).isoformat()
        existing = conn.execute(
            "select content from memories where id = ?",
            (WUKONG_NATIVE_BRAIN_MEMORY_ID,),
        ).fetchone()
        changed = existing is None or existing[0] != text
        conn.execute(
            "delete from memories where id = ? or key = ?",
            (WUKONG_NATIVE_BRAIN_MEMORY_ID, WUKONG_NATIVE_BRAIN_MEMORY_KEY),
        )
        conn.execute(
            """
            insert into memories (
                id, key, content, category, source, role,
                conversation_id, external_msg_id, created_at, updated_at, embedding
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                WUKONG_NATIVE_BRAIN_MEMORY_ID,
                WUKONG_NATIVE_BRAIN_MEMORY_KEY,
                text,
                "system",
                "agent-memory-hub",
                "system",
                None,
                None,
                now,
                now,
                None,
            ),
        )
        return changed


def _delete_wukong_brain_memory_bridge(db_path: Path) -> bool:
    with sqlite3.connect(db_path, timeout=5) as conn:
        if not _sqlite_has_table(conn, "memories"):
            return False
        existed = conn.execute(
            "select 1 from memories where id = ?",
            (WUKONG_NATIVE_BRAIN_MEMORY_ID,),
        ).fetchone()
        conn.execute(
            "delete from memories where id = ? or key = ?",
            (WUKONG_NATIVE_BRAIN_MEMORY_ID, WUKONG_NATIVE_BRAIN_MEMORY_KEY),
        )
        return existed is not None


def _wukong_brain_memory_has_bridge(db_path: Path) -> bool:
    with sqlite3.connect(db_path, timeout=5) as conn:
        if not _sqlite_has_table(conn, "memories"):
            return False
        row = conn.execute(
            "select content from memories where id = ? and source = 'agent-memory-hub'",
            (WUKONG_NATIVE_BRAIN_MEMORY_ID,),
        ).fetchone()
        if row is None or WUKONG_NATIVE_MEMORY_MARKER not in row[0]:
            return False
        if not _sqlite_has_table(conn, "memories_fts"):
            return True
        fts_row = conn.execute(
            "select 1 from memories_fts where memories_fts match ? limit 1",
            ('"brief_memory"',),
        ).fetchone()
        return fts_row is not None


def _wukong_fts_text(text: str) -> str:
    keyword_bag = (
        " AMH agent memory hub agent-memory-hub Agent Memory Hub "
        "共享 记忆 共享记忆 共享 大脑 共享大脑 历史 上下文 历史上下文 "
        "跨 智能体 跨智能体 项目 项目记忆 短 prompt 短prompt "
        "brief_memory search_memory read_memory write_memory mcp_runtime"
    )
    return f"{text}\n\n{keyword_bag}"


def _path_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _read_evidence_text(path: Path) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > EVIDENCE_READ_LIMIT_BYTES:
                handle.seek(-EVIDENCE_READ_LIMIT_BYTES, 2)
            return handle.read().decode("utf-8", errors="ignore")
    except OSError:
        return ""


def _text_has_wukong_short_prompt(text: str) -> bool:
    lines = text.splitlines() or [text]
    return any(_line_has_wukong_short_prompt(line) for line in lines)


def _line_has_wukong_short_prompt(line: str) -> bool:
    length_match = MESSAGE_LEN_RE.search(line)
    candidates = _prompt_field_candidates(line)
    if length_match is None:
        return bool(candidates) and any(_is_short_prompt_value(candidate) for candidate in candidates)
    declared_length = int(length_match.group(1))
    if declared_length <= 0 or declared_length > SHORT_PROMPT_MAX_CHARS:
        return False
    if not candidates:
        return True
    return any(_is_short_prompt_value(candidate, declared_length=declared_length) for candidate in candidates)


def _prompt_field_candidates(line: str) -> list[str]:
    candidates: list[str] = []
    for pattern in PROMPT_FIELD_RES:
        for match in pattern.finditer(line):
            value = match.group(1).strip()
            if value:
                candidates.append(value)
    return candidates


def _is_short_prompt_value(value: str, *, declared_length: int | None = None) -> bool:
    compact = value.strip()
    if not compact:
        return False
    if len(compact) > SHORT_PROMPT_MAX_CHARS:
        return False
    if declared_length is None:
        return True
    return declared_length <= SHORT_PROMPT_MAX_CHARS


def _text_has_amh_usage(text: str, *, session_id: str | None = None) -> bool:
    if session_id is not None:
        scoped_text = "\n".join(line for line in text.splitlines() if session_id in line)
        if not scoped_text:
            return False
        text = scoped_text

    if any(marker in text for marker in WUKONG_AMH_CONTEXT_MARKERS):
        return True
    has_server = SERVER_NAME in text or DISPLAY_NAME in text
    has_tool = any(marker in text for marker in WUKONG_AMH_TOOL_MARKERS)
    has_call = any(marker in text for marker in WUKONG_AMH_CALL_MARKERS)
    return has_server and has_tool and has_call


register_adapter("wukong", WukongAdapter)
