"""Reversible native-only Hook installation and host capability discovery."""

from __future__ import annotations

from typing import Any

import json
import plistlib
import re
import shlex
import shutil
from pathlib import Path
from .common import PRODUCTS, atomic_json, atomic_text, digest, legacy_memory_command, runtime_argv

EVENTS = ("SessionStart", "UserPromptSubmit", "PostToolUse", "Stop", "PostCompact")
OWNER = "agent_brain.native_sync.hooks"


def discover(home: Path) -> list[dict[str, Any]]:
    """Report observed product stores and limitations; shared storage does not prove the originating UI."""
    home = Path(home)
    result = []
    cc = list((home / ".claude/projects").glob("*/memory"))
    result.append(
        {
            "product": "claude-code",
            "name": PRODUCTS["claude-code"],
            "detected": bool(shutil.which("claude") or (home / ".claude").exists()),
            "roots": [str(p) for p in cc],
        }
    )
    result.append(
        {
            "product": "codex-cli",
            "name": PRODUCTS["codex-cli"],
            "detected": bool(shutil.which("codex") or (home / ".codex").exists()),
            "roots": [str(home / ".codex/memories")],
        }
    )
    # The ChatGPT desktop app manages Codex memory on this host ("Codex 记忆 →
    # 本地主机" in its settings), and that store is ~/.codex/memories. It is the
    # same directory Codex CLI and the Codex app use, so the three share one
    # store; rows are deduplicated by absolute path and labelled codex-shared.
    codex_store = home / ".codex/memories"
    if (Path("/Applications") / "ChatGPT.app").exists():
        result.append(
            {
                "product": "chatgpt-desktop",
                "name": PRODUCTS["chatgpt-desktop"],
                "detected": True,
                "roots": [str(codex_store)],
                "note": "记忆在「设置 → 个性化 → Codex 记忆」，与 Codex 共用同一份本地存储",
            }
        )
    if (Path("/Applications") / "Codex.app").exists():
        result.append(
            {
                "product": "codex-desktop",
                "name": PRODUCTS["codex-desktop"],
                "detected": True,
                "roots": [str(codex_store)],
                "note": "与 Codex CLI 共用同一份存储",
            }
        )
    # Cowork keeps workspaces under "Claude" or "Claude-3p" depending on the
    # build; checking only the first missed every memory on this machine.
    roots: list[str] = []
    for bundle in ("Claude", "Claude-3p"):
        base = home / "Library/Application Support" / bundle / "local-agent-mode-sessions"
        if base.is_dir():
            roots += [str(p.parent) for p in base.rglob("MEMORY.md")]
    result.append(
        {
            "product": "cowork",
            "name": PRODUCTS["cowork"],
            "detected": (Path("/Applications/Claude.app")).exists(),
            "roots": roots,
            "note": "plugin must run in actual Cowork environment; VM/cloud roots require binding",
        }
    )
    return result


def discover_assets(home: Path, product: str) -> list[dict[str, str]]:
    """Known source locations only; credentials and the AMH state directory are never sources."""
    candidates: list[tuple[Path, str]] = []
    if product == "claude-code":
        base = home / ".claude"
        candidates = [
            (base / "projects", "transcript"),
            (base / "rules", "rule"),
            (base / "CLAUDE.md", "rule"),
            (base / "skills", "skill"),
            (base / "settings.json", "config"),
        ]
    elif product in ("codex-cli", "codex-desktop", "chatgpt-desktop"):
        base = home / ".codex"
        candidates = [
            # The curated store. It was missing here, so Codex and the ChatGPT
            # desktop app were "detected" but contributed no memory rows at all.
            (base / "memories", "memory"),
            (base / "sessions", "transcript"),
            (base / "AGENTS.md", "rule"),
            (base / "skills", "skill"),
            (base / "config.toml", "config"),
        ]
    return [{"root": str(path), "kind": kind} for path, kind in candidates if path.exists()]


def _managed(command: Any) -> bool:
    return OWNER in str(command) or "amh-native-hook" in str(command)


def native_reading_config(text: str) -> str:
    """Enable reading while preserving TOML layout and explicit generation preferences."""
    from collections.abc import MutableMapping
    import tomlkit

    data = tomlkit.parse(text)
    for section, key in [("features", "memories"), ("memories", "use_memories")]:
        if section not in data:
            data[section] = tomlkit.table()
        target = data[section]
        if not isinstance(target, MutableMapping):
            raise ValueError(section + " must be a TOML table")
        target[key] = True
    return tomlkit.dumps(data)


def install_hooks(home: Path, state: Path, *, products: list[str]) -> list[str]:
    """Prevalidate client settings, then install owned callbacks/read flags with reversible receipts."""
    home, state = Path(home).resolve(), Path(state).resolve()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    receipt = state / "install-receipt.json"
    saved = json.loads(receipt.read_text()) if receipt.exists() else {"json": {}, "text": {}}
    report = []
    destinations = {}
    if "claude-code" in products:
        destinations[home / ".claude/settings.json"] = "claude-code"
    if any(p in products for p in ("codex-cli", "codex-desktop", "chatgpt-desktop")):
        destinations[home / ".codex/hooks.json"] = "codex-cli"
    # Validate every target before the first mutation, not halfway through a multi-client install.
    for path in destinations:
        if path.is_symlink():
            raise ValueError("refusing to replace symlink: " + str(path))
        data = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
            raise ValueError("invalid Hook configuration: " + str(path))
        for entries in data.get("hooks", {}).values():
            if not isinstance(entries, list) or any(
                not isinstance(entry, dict)
                or not isinstance(entry.get("hooks", []), list)
                or any(not isinstance(handler, dict) for handler in entry.get("hooks", []))
                for entry in entries
            ):
                raise ValueError("invalid Hook entries: " + str(path))
    config_path = home / ".codex/config.toml"
    if any(p in products for p in ("codex-cli", "codex-desktop", "chatgpt-desktop")):
        from agent_brain.agent_integrations.codex_config import remove_mcp_block

        if config_path.is_symlink():
            raise ValueError("refusing to replace symlink: " + str(config_path))
        native_reading_config(
            remove_mcp_block(config_path.read_text() if config_path.exists() else "")
        )
    for path, product in destinations.items():
        original = json.loads(path.read_text()) if path.exists() else {}
        if str(path) not in saved["json"]:
            saved["json"][str(path)] = {"before": original, "existed": path.exists()}
        data = json.loads(json.dumps(original))
        if product == "claude-code":
            data["autoMemoryEnabled"] = True
        hooks = data.setdefault("hooks", {})
        for event, entries in list(hooks.items()):
            kept = []
            for entry in entries:
                other = [
                    h
                    for h in entry.get("hooks", [])
                    if not _managed(h.get("command", ""))
                    and not legacy_memory_command(h.get("command", ""))
                ]
                if other:
                    kept.append({**entry, "hooks": other})
            hooks[event] = kept
        command = shlex.join(runtime_argv(OWNER, "--state", str(state), "--product", product))
        launcher = state / "amh-native-hook"
        atomic_text(launcher, "#!/bin/sh\nexec " + command + "\n")
        launcher.chmod(0o700)
        for event in EVENTS:
            handler = {
                "type": "command",
                "command": command,
                "timeout": 2 if event == "UserPromptSubmit" else 1,
            }
            hooks.setdefault(event, []).append({"hooks": [handler]})
        if isinstance(data.get("mcpServers"), dict):
            data["mcpServers"].pop("agent-memory-hub", None)
        saved["json"][str(path)]["after"] = data
        atomic_json(receipt, saved)  # Recovery record is durable before mutation.
        atomic_json(path, data)
        report.append(str(path))
    # Disable only recognized AMH-owned discipline text; retain all user prose.
    for path in [home / ".claude/CLAUDE.md", home / ".codex/AGENTS.md"]:
        if not path.exists():
            continue
        if (path.parent.name == ".claude" and "claude-code" not in products) or (
            path.parent.name == ".codex"
            and not any(p in products for p in ("codex-cli", "codex-desktop", "chatgpt-desktop"))
        ):
            continue
        original = path.read_text()
        changed = re.sub(
            r"<!-- BEGIN agent-memory-hub(?:-awareness)? -->.*?<!-- END agent-memory-hub(?:-awareness)? -->\n?",
            "",
            original,
            flags=re.S,
        )
        if changed != original:
            saved["text"].setdefault(str(path), {"before": original})
            saved["text"][str(path)]["after"] = changed
            atomic_json(receipt, saved)
            atomic_text(path, changed)
    codex_config = home / ".codex/config.toml"
    if any(p in products for p in ("codex-cli", "codex-desktop", "chatgpt-desktop")):
        from agent_brain.agent_integrations.codex_config import remove_mcp_block
        from agent_brain.agent_integrations.codex_hook_trust import (
            codex_command_hook_hash,
            codex_hook_state_key,
            trusted_hashes,
            upsert_trusted_hashes,
        )

        original = codex_config.read_text() if codex_config.exists() else ""
        changed = native_reading_config(remove_mcp_block(original))
        already_trusted = set(trusted_hashes(original).values())
        hooks_path = (home / ".codex/hooks.json").resolve()
        hooks_data = json.loads(hooks_path.read_text())["hooks"]
        updates = {}
        for event, groups in hooks_data.items():
            if event not in EVENTS:
                continue
            for group_index, group in enumerate(groups):
                for index, handler in enumerate(group.get("hooks", [])):
                    if handler.get("type") != "command":
                        continue
                    value = codex_command_hook_hash(
                        event, matcher=group.get("matcher"), hook=handler
                    )
                    if _managed(handler.get("command", "")) or value in already_trusted:
                        updates[codex_hook_state_key(hooks_path, event, group_index, index)] = value
        changed = upsert_trusted_hashes(changed, updates)
        if changed != original:
            saved["text"].setdefault(
                str(codex_config), {"before": original, "existed": codex_config.exists()}
            )
            saved["text"][str(codex_config)]["after"] = changed
            atomic_json(receipt, saved)
            atomic_text(codex_config, changed)
    atomic_json(receipt, saved)
    return report


def uninstall_hooks(home: Path, state: Path) -> list[str]:
    """Restore receipt-owned values only if untouched since installation; report later edits as conflicts."""
    receipt = Path(state) / "install-receipt.json"
    if not receipt.exists():
        return []
    saved = json.loads(receipt.read_text())
    conflicts = []
    for raw, entry in saved["json"].items():
        path = Path(raw)
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        if data != entry["after"]:
            conflicts.append(raw)
            continue  # Never discard edits made after install.
        if entry["existed"]:
            atomic_json(path, entry["before"])
        else:
            path.unlink()
    for raw, entry in saved["text"].items():
        path = Path(raw)
        if path.exists() and path.read_text() == entry["after"]:
            if entry.get("existed", True):
                atomic_text(path, entry["before"])
            else:
                path.unlink()
        else:
            conflicts.append(raw)
    if not conflicts:
        receipt.unlink()
    return conflicts


def export_cowork_plugin(
    destination: Path,
    *,
    policy: dict[str, str] | None = None,
    runtime_state: Path | None = None,
    connection_file: Path | None = None,
) -> Path:
    """Export stdlib Hooks; optional explicit host paths avoid ephemeral Cowork data.

    Only paths and public identity pins enter the archive, never ticket contents.
    Without host binding, the runtime continues using its supplied plugin data.
    """
    if policy is not None:
        from .transport import validate_url

        if set(policy) != {"hub", "member_id", "tenant_id"} or not all(policy.values()):
            raise ValueError("plugin policy must contain only public Hub/member/tenant pins")
        validate_url(policy["hub"])
    root = Path(destination)
    for path in (runtime_state, connection_file):
        if path is not None:
            if not path.is_absolute():
                raise ValueError("runtime binding paths must be absolute")
            if path.resolve() == root.resolve() or path.resolve().is_relative_to(root.resolve()):
                raise ValueError("runtime state and connection must remain outside the plugin")
    for directory in (
        root,
        root / ".claude-plugin",
        root / "hooks",
        root / "commands",
        root / "runtime",
        root / "runtime/agent_brain",
        root / "runtime/agent_brain/native_sync",
    ):
        if directory.is_symlink():
            raise ValueError("plugin output contains a symlink: " + str(directory))
    generated = [
        root / ".claude-plugin/plugin.json",
        root / "hooks/hooks.json",
        root / "runtime/entry.py",
        root / "runtime/connection-policy.json",
        root / "commands/doctor.md",
        root / "runtime/agent_brain/__init__.py",
    ]
    atomic_json(
        root / ".claude-plugin/plugin.json",
        {
            "name": "amh-native-org",
            "version": "0.1.0",
            "description": "组织原生记忆同步与召回",
            "author": {"name": "Agent Memory Hub"},
        },
    )
    runtime = root / "runtime/agent_brain/native_sync"
    runtime.mkdir(parents=True, exist_ok=True)
    atomic_text(runtime.parent / "__init__.py", "")
    for name in [
        "__init__.py",
        "common.py",
        "contracts.py",
        "evidence.py",
        "storage.py",
        "transport.py",
        "projection.py",
        "source.py",
        "materialize.py",
        "worker.py",
        "lineage.py",
        "discovery.py",
        "query_worker.py",
        "client.py",
        "cowork_runtime.py",
        "runtime_sources.py",
        "recall.py",
        "hooks.py",
    ]:
        atomic_text(runtime / name, (Path(__file__).parent / name).read_text())
        generated.append(runtime / name)
    atomic_text(
        root / "runtime/entry.py",
        "from agent_brain.native_sync.cowork_runtime import main\nmain()\n",
    )
    atomic_json(root / "runtime/connection-policy.json", policy or {})
    atomic_text(
        root / "commands/doctor.md",
        """---
description: 检查组织记忆运行环境，不读取记忆正文或凭证
---
在 Cowork 任务中运行下面的诊断命令，原样报告 JSON 结果。不要读取、展示或发送连接文件、邀请或设备凭证，不修改任何客户端配置。诊断只检查目录是否存在及 Hub 是否可达，不代表全部链路验收通过。

```sh
python3 "${CLAUDE_PLUGIN_ROOT}/runtime/entry.py" --policy "${CLAUDE_PLUGIN_ROOT}/runtime/connection-policy.json" --diagnose__STATE_ARGUMENT__
```
""".replace(
            "__STATE_ARGUMENT__",
            " --state " + shlex.quote(str(runtime_state)) if runtime_state is not None else "",
        ),
    )
    arguments = [
        "${CLAUDE_PLUGIN_ROOT}/runtime/entry.py",
        "--policy",
        "${CLAUDE_PLUGIN_ROOT}/runtime/connection-policy.json",
        "--product",
        "cowork",
        "--start-worker",
    ]
    if runtime_state is not None:
        arguments.extend(["--state", str(runtime_state)])
    if connection_file is not None:
        arguments.extend(["--connection-file", str(connection_file)])
    atomic_json(
        root / "hooks/hooks.json",
        {
            "hooks": {
                e: [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "python3",
                                "args": arguments,
                                "timeout": 2,
                            }
                        ]
                    }
                ]
                for e in EVENTS
            }
        },
    )
    import zipfile

    archive = Path(str(root) + ".zip")
    if archive.is_symlink():
        raise ValueError("plugin archive must not be a symlink")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in generated:
            bundle.write(path, str(path.relative_to(root)))
    return root


def launch_agent(state: Path, home: Path, *, install: bool = True) -> Path:
    """Write the macOS worker plist (or locate it); loading/unloading belongs to the CLI."""
    label = "com.amh.native." + digest(str(state.resolve()))[:12]
    path = home / "Library/LaunchAgents" / f"{label}.plist"
    if not install:
        return path
    root = Path(__file__).resolve().parents[2]
    data = {
        "Label": label,
        "ProgramArguments": runtime_argv(
            "agent_brain.native_sync.worker", "--state", str(state.resolve())
        ),
        "WorkingDirectory": str(root),
        "EnvironmentVariables": {"PYTHONPATH": str(root)},
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(state / "worker.log"),
        "StandardErrorPath": str(state / "worker-error.log"),
    }
    atomic_text(path, plistlib.dumps(data).decode())
    return path
