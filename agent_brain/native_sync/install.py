"""Reversible native-only Hook installation and host capability discovery."""

from __future__ import annotations

from typing import Any

import json
import plistlib
import platform
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


ORG_MCP_NAME = "amh-org"
ORG_MCP_MODULE = "agent_brain.native_sync.org_mcp"


def org_mcp_server(
    state: Path, *, policy_path: str | None = None, product: str = ""
) -> dict[str, Any]:
    """Point at the installed client; credentials remain in the runtime profile."""
    args = ["--state", str(state.resolve())]
    if policy_path is not None:
        args.extend(["--policy", policy_path])
    if product:
        args.extend(["--product", product])
    argv = runtime_argv(ORG_MCP_MODULE, *args)
    return {"command": argv[0], "args": argv[1:]}


def _check_mcp_owner(current: Any, desired: dict[str, Any], receipt: dict[str, Any]) -> None:
    if current is not None and current != desired and current != receipt.get("after"):
        raise ValueError(
            "amh-org MCP entry already exists or was edited; review it before installing"
        )


def _codex_mcp(text: str, server: dict[str, Any], previous: dict[str, Any]) -> str:
    from collections.abc import MutableMapping
    import tomlkit

    document = tomlkit.parse(text)
    if "mcp_servers" not in document:
        document["mcp_servers"] = tomlkit.table()
    servers = document["mcp_servers"]
    if not isinstance(servers, MutableMapping):
        raise ValueError("mcp_servers must be a TOML table")
    _check_mcp_owner(servers.get(ORG_MCP_NAME), server, previous)
    servers[ORG_MCP_NAME] = server
    return tomlkit.dumps(document)


def _save_mcp_receipt(
    saved: dict[str, Any], path: Path, before: Any, after: Any, format: str
) -> None:
    entries = saved.setdefault("mcp", {})
    entries.setdefault(str(path), {"before": before, "format": format})
    entries[str(path)]["after"] = after


def _restore_mcp(path: Path, entry: dict[str, Any]) -> bool:
    """Restore only our server when unrelated configuration changed after install."""
    if not path.exists():
        return True
    if path.is_symlink():
        return False
    try:
        if entry["format"] == "toml":
            import tomlkit

            document = tomlkit.parse(path.read_text())
            servers = document.get("mcp_servers", {})
        else:
            document = json.loads(path.read_text())
            if not isinstance(document, dict):
                return False
            servers = document.get("mcpServers", {})
        from collections.abc import MutableMapping

        if not isinstance(servers, MutableMapping):
            return False
        current = servers.get(ORG_MCP_NAME)
        if current == entry.get("before"):
            return True  # Already restored (including a full-file receipt restore).
        if current != entry["after"]:
            return False  # The member edited this entry; never replace it.
        if entry.get("before") is None:
            del servers[ORG_MCP_NAME]
        else:
            servers[ORG_MCP_NAME] = entry["before"]
        if entry["format"] == "toml":
            atomic_text(path, tomlkit.dumps(document))
        else:
            atomic_json(path, document)
        return True
    except (ValueError, TypeError, OSError):
        return False


def install_hooks(home: Path, state: Path, *, products: list[str]) -> list[str]:
    """Prevalidate client settings, then install owned callbacks/read flags with reversible receipts."""
    home, state = Path(home).resolve(), Path(state).resolve()
    from .profiles import recall_allowed

    if not recall_allowed(state):
        raise ValueError("recall-disabled profile cannot install product integrations")
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    receipt = state / "install-receipt.json"
    saved = json.loads(receipt.read_text()) if receipt.exists() else {"json": {}, "text": {}}
    report = []
    claude_mcp = org_mcp_server(state, product="claude-code")
    codex_mcp = org_mcp_server(state, product="codex-cli")
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
        previous = saved.get("json", {}).get(str(path), {}).get("after", {}).get("hooks", {})
        owned = [
            h for entries in previous.values() for entry in entries for h in entry.get("hooks", [])
        ]
        for entries in data.get("hooks", {}).values():
            for entry in entries:
                for handler in entry.get("hooks", []):
                    if _managed(handler.get("command", "")) and handler not in owned:
                        raise ValueError(
                            "another profile or edited native Hook exists; detach it explicitly: "
                            + str(path)
                        )
    claude_config = home / ".claude.json"
    if "claude-code" in products:
        if claude_config.is_symlink():
            raise ValueError("refusing to replace symlink: " + str(claude_config))
        data = json.loads(claude_config.read_text()) if claude_config.exists() else {}
        if not isinstance(data, dict) or not isinstance(data.get("mcpServers", {}), dict):
            raise ValueError("invalid user MCP configuration: " + str(claude_config))
        _check_mcp_owner(
            data.get("mcpServers", {}).get(ORG_MCP_NAME),
            claude_mcp,
            saved.get("mcp", {}).get(str(claude_config), {}),
        )
    config_path = home / ".codex/config.toml"
    if any(p in products for p in ("codex-cli", "codex-desktop", "chatgpt-desktop")):
        from agent_brain.agent_integrations.codex_config import remove_mcp_block

        if config_path.is_symlink():
            raise ValueError("refusing to replace symlink: " + str(config_path))
        _codex_mcp(
            native_reading_config(
                remove_mcp_block(config_path.read_text() if config_path.exists() else "")
            ),
            codex_mcp,
            saved.get("mcp", {}).get(str(config_path), {}),
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
    if "claude-code" in products:
        original = json.loads(claude_config.read_text()) if claude_config.exists() else {}
        data = json.loads(json.dumps(original))
        servers = data.setdefault("mcpServers", {})
        _save_mcp_receipt(saved, claude_config, servers.get(ORG_MCP_NAME), claude_mcp, "json")
        servers[ORG_MCP_NAME] = claude_mcp
        saved["json"].setdefault(
            str(claude_config), {"before": original, "existed": claude_config.exists()}
        )
        saved["json"][str(claude_config)]["after"] = data
        atomic_json(receipt, saved)
        atomic_json(claude_config, data)
        report.append(str(claude_config))
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
        import tomllib

        _save_mcp_receipt(
            saved,
            codex_config,
            tomllib.loads(original).get("mcp_servers", {}).get(ORG_MCP_NAME),
            codex_mcp,
            "toml",
        )
        changed = _codex_mcp(
            native_reading_config(remove_mcp_block(original)),
            codex_mcp,
            saved.get("mcp", {}).get(str(codex_config), {}),
        )
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
        if path.is_symlink():
            conflicts.append(raw)
            continue
        try:
            data = json.loads(path.read_text())
        except (ValueError, OSError):
            conflicts.append(raw)
            continue
        if data != entry["after"]:
            conflicts.append(raw)
            continue  # Never discard edits made after install.
        if entry["existed"]:
            atomic_json(path, entry["before"])
        else:
            path.unlink()
    for raw, entry in saved["text"].items():
        path = Path(raw)
        if not path.is_symlink() and path.exists() and path.read_text() == entry["after"]:
            if entry.get("existed", True):
                atomic_text(path, entry["before"])
            else:
                path.unlink()
        else:
            conflicts.append(raw)
    for raw, entry in saved.get("mcp", {}).items():
        if not _restore_mcp(Path(raw), entry) and raw not in conflicts:
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
    recall_state: Path | None = None,
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
    from agent_brain._version import __version__

    if recall_state is not None:
        if runtime_state is None or not recall_state.is_absolute():
            raise ValueError("recall parent requires an absolute host runtime binding")
        from .profiles import organization_pin

        parent_config = json.loads((recall_state / "config.json").read_text())
        atomic_json(
            runtime_state / "recall-parent.json",
            {"state": str(recall_state.resolve()), "pin": organization_pin(parent_config)},
        )
    host_mcp = runtime_state is not None and platform.system() == "Darwin"
    manifest: dict[str, Any] = {
        "name": "amh-native-org",
        "version": __version__,
        "description": (
            "组织原生记忆同步与只读检索工具（需完成本机配对）"
            if host_mcp
            else "组织原生记忆 Hook；只读检索工具需绑定 macOS 主机后导出"
        ),
        "author": {"name": "Agent Memory Hub"},
    }
    if host_mcp:
        assert runtime_state is not None
        manifest["mcpServers"] = {
            ORG_MCP_NAME: org_mcp_server(
                runtime_state,
                policy_path="${CLAUDE_PLUGIN_ROOT}/runtime/connection-policy.json",
                product="cowork",
            )
        }
    atomic_json(root / ".claude-plugin/plugin.json", manifest)
    runtime = root / "runtime/agent_brain/native_sync"
    runtime.mkdir(parents=True, exist_ok=True)
    atomic_text(runtime.parent / "__init__.py", "")
    for name in [
        "__init__.py",
        "common.py",
        "contracts.py",
        "evidence.py",
        "facets.py",
        "storage.py",
        "transport.py",
        "projection.py",
        "profiles.py",
        "source.py",
        "source_policy.py",
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
        )
        + (
            "\n只读组织工具已声明：search_org_memory、read_org_memory、related_org_memory。首次配对完成前工具返回 not_connected；不代表已经连接或通过验收。\n"
            if host_mcp
            else "\n当前包未声明只读组织检索 MCP。请在 macOS 主机安装 AMH 客户端并显式绑定持久运行目录后重新导出。\n"
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


def launch_agent(state: Path, home: Path, *, install: bool = True, background: bool = False) -> Path:
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
        **({"LimitLoadToSessionType": "Background"} if background else {}),
        "StandardOutPath": str(state / "worker.log"),
        "StandardErrorPath": str(state / "worker-error.log"),
    }
    atomic_text(path, plistlib.dumps(data).decode())
    return path


def detach_products(state: Path, *, apply: bool = False) -> dict[str, Any]:
    """Remove only receipt-owned native callbacks/MCP; keep all unrelated values.

    Preview is the default. Every destination is prevalidated before mutation.
    Changed owned entries are conflicts, never permission to overwrite. Native
    reading flags, legacy integrations and discipline prose are not restored.
    This deliberately does not stop the profile's synchronization service.
    """
    import copy
    import tomlkit

    state = Path(state).resolve()
    receipt = state / "install-receipt.json"
    if not receipt.exists():
        return {"changed": [], "conflicts": [], "applied": False}
    saved = json.loads(receipt.read_text())
    changes: dict[Path, tuple[str, str]] = {}
    conflicts: list[str] = []
    for raw, entry in saved.get("json", {}).items():
        path = Path(raw)
        if not path.exists():
            continue
        try:
            if path.is_symlink():
                raise ValueError("symlink")
            original = path.read_text()
            data = json.loads(original)
            desired = copy.deepcopy(data)
            for event, groups in entry["after"].get("hooks", {}).items():
                owned = [
                    handler
                    for group in groups
                    for handler in group.get("hooks", [])
                    if _managed(handler.get("command", ""))
                ]
                if not owned or event not in desired.get("hooks", {}):
                    continue
                current_groups = desired.get("hooks", {}).get(event, [])
                kept = []
                for group in current_groups:
                    handlers = []
                    for handler in group.get("hooks", []):
                        if handler in owned:
                            continue
                        if _managed(handler.get("command", "")):
                            raise ValueError("owned hook edited or replaced")
                        handlers.append(handler)
                    if handlers:
                        kept.append({**group, "hooks": handlers})
                desired["hooks"][event] = kept
            if desired != data:
                changes[path] = (original, json.dumps(desired, ensure_ascii=False, indent=2) + "\n")
        except (OSError, ValueError, TypeError, KeyError):
            conflicts.append(raw)
    for raw, entry in saved.get("mcp", {}).items():
        path = Path(raw)
        if not path.exists():
            continue
        try:
            if path.is_symlink():
                raise ValueError("symlink")
            original = path.read_text()
            content = changes.get(path, (original, original))[1]
            document = tomlkit.parse(content) if entry["format"] == "toml" else json.loads(content)
            servers = document.get("mcp_servers" if entry["format"] == "toml" else "mcpServers", {})
            current = servers.get(ORG_MCP_NAME)
            if current is None:
                continue
            if current != entry["after"]:
                raise ValueError("owned MCP entry edited or replaced")
            del servers[ORG_MCP_NAME]
            changed = (
                tomlkit.dumps(document)
                if entry["format"] == "toml"
                else json.dumps(document, ensure_ascii=False, indent=2) + "\n"
            )
            changes[path] = (original, changed)
        except (OSError, ValueError, TypeError, KeyError):
            conflicts.append(raw)
    conflicts = sorted(set(conflicts))
    result = {
        "changed": [str(p) for p in changes],
        "conflicts": conflicts,
        "applied": False,
    }
    if conflicts or not apply:
        return result
    # A concurrent product edit between preview and apply must not be discarded.
    for path, (original, _) in changes.items():
        if path.is_symlink() or path.read_text() != original:
            conflicts.append(str(path))
    if conflicts:
        return result
    for path, (_, changed) in changes.items():
        atomic_text(path, changed)
    result["applied"] = True
    return result
