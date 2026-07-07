# Hooks 装配指南

Agent Memory Hub 默认安装 7 个 AMH-owned hooks。它们不是“越多越自动写记忆”，而是分成主动上下文闭环和低噪声生命周期证据两类。

| Hook script | Event | 行为 |
|---|---|---|
| `inject-discipline.sh` | `SessionStart` | 注入记忆纪律；Codex 默认只注入短提示，完整纪律由 `AGENTS.md` 承载 |
| `inject-context.sh` | `UserPromptSubmit` | 按用户 prompt 检索相关 memory candidates 并注入上下文；同时把当前用户 prompt 写成 `sources/conversations` 的 `live-prompt` 防丢证据 |
| `session-end-signal.sh` | `Stop` | 如果 payload 有 `transcript_path`，导入完整 transcript 作为权威原始对话；同时每个 session 去重写一条 session-active signal |
| `lifecycle-event.sh` | `PreCompact` | 记录 runtime event，并写一条 compact-boundary signal |
| `lifecycle-event.sh` | `PostCompact` | 只记录 runtime event |
| `lifecycle-event.sh` | `SubagentStart` | 只记录 runtime event |
| `lifecycle-event.sh` | `SubagentStop` | 只记录 runtime event |

AMH 不接管 `PreToolUse`、`PostToolUse`、`PermissionRequest`。这些 hook 高频、敏感且更适合作为安全 guard/audit，而不是默认 memory 层。

## 推荐安装

从仓库根目录运行：

```bash
python -m agent_brain.interfaces.cli adapter install codex
python -m agent_brain.interfaces.cli adapter install claude_code
```

安装器会：

- 幂等更新 AMH-owned hook entry，不覆盖其他工具的 hook。
- 给 hook command 注入保底 `PATH`，避免 hook runner 环境过瘦时 `#!/usr/bin/env bash` 返回 127。
- 迁移旧 `brain/hooks/*` 路径到 `agent_runtime_kit/hooks/*`。
- `inject-context.sh` 在 `search-memory.sh` 外包一层内部超时：优先使用 `gtimeout`/`timeout`，缺失时用 Python `subprocess.run(..., timeout=...)` fallback，确保 `UserPromptSubmit` 慢召回 fail-open 返回 `{}`。

macOS 上可选安装 coreutils 作为 shell timeout 快路径：

```bash
brew install coreutils
```

这只是环境止血项；真实保障仍是 hook 内建 Python fallback，不要求用户机器必须安装 coreutils。

## 验证

```bash
python -m agent_brain.interfaces.cli adapter doctor codex --format json
python -m agent_brain.interfaces.cli adapter doctor claude_code --format json
```

预期：

- `overall_status: ok`
- hooks detail 包含 `SessionStart, UserPromptSubmit, Stop, PreCompact, PostCompact, SubagentStart, SubagentStop`

也可以直接检查配置：

```bash
jq '.hooks | {SessionStart,UserPromptSubmit,Stop,PreCompact,PostCompact,SubagentStart,SubagentStop}' ~/.codex/hooks.json
jq '.hooks | {SessionStart,UserPromptSubmit,Stop,PreCompact,PostCompact,SubagentStart,SubagentStop}' ~/.claude/settings.json
```

## 单脚本 smoke

```bash
HUB=/path/to/agent-memory-hub

echo '{"session_id":"test","cwd":"/tmp","hook_event_name":"SessionStart","source":"startup"}' | \
  "$HUB/agent_runtime_kit/hooks/inject-discipline.sh" | python3 -m json.tool | head -20

echo '{"session_id":"test-stop","transcript_path":"/tmp/foo","cwd":"/tmp","hook_event_name":"Stop"}' | \
  "$HUB/agent_runtime_kit/hooks/session-end-signal.sh"
echo "stop exit: $?"

echo '{"session_id":"test-compact","transcript_path":"/tmp/foo","cwd":"/tmp","hook_event_name":"PreCompact"}' | \
  "$HUB/agent_runtime_kit/hooks/lifecycle-event.sh"
echo "precompact exit: $?"

echo '{"session_id":"test-subagent","cwd":"/tmp","hook_event_name":"SubagentStart"}' | \
  "$HUB/agent_runtime_kit/hooks/lifecycle-event.sh"
echo "subagent exit: $?"
```

## 卸载

```bash
python -m agent_brain.interfaces.cli adapter uninstall codex
python -m agent_brain.interfaces.cli adapter uninstall claude_code
```

卸载只移除 AMH-owned entries，保留用户或其他插件自己的 hook。

## 故障排查

| 症状 | 排查 |
|---|---|
| 新会话仍提示 hook 需要信任 | Codex 新会话按 `t` trust all；这是 hook trust 状态，不是 AMH 失败 |
| `hook exited with code 127` | 确认配置里的 AMH command 是否以保底 `PATH=/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin...` 开头；旧会话需重启才会读取新配置 |
| `UserPromptSubmit hook timed out after 10s` | 先跑 `python -m agent_brain.interfaces.cli adapter doctor codex --format json` 看 `Codex hook timeout tooling`；缺 `gtimeout/timeout` 时应显示 Python fallback。若仍超时，检查 `~/.agent-memory-hub/runtime/hook-latency.jsonl` 里的 `search_memory timeout` 记录 |
| adapter doctor 缺少 lifecycle hooks | 重新运行 `python -m agent_brain.interfaces.cli adapter install <adapter>` |
| `PreCompact` signal 太多 | `lifecycle-event.sh` 按 session 去重；如仍异常，检查 hook payload 的 `session_id` 是否稳定 |
