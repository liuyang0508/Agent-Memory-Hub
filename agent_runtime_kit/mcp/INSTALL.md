# MCP Server 装配指南（v0.3）

把 brain pool 暴露给任何支持 MCP 的客户端：Claude Code、Claude Desktop、Codex CLI、Qoder、Wukong。

## 前置

- Python 3.10+（macOS: `brew install python@3.12`）
- `mcp[cli]` 包（自动通过 venv 装）

## Step 1: 装 server（一次性）

```bash
HUB=/path/to/your/agent-memory-hub  # 改成你的路径

# 创建 venv 并装 mcp 包
python3.12 -m venv $HUB/agent_runtime_kit/mcp/.venv
$HUB/agent_runtime_kit/mcp/.venv/bin/pip install "mcp[cli]"

# 测一下能跑
echo "" | timeout 2 $HUB/agent_runtime_kit/mcp/server.sh 2>&1 | head -3
# 应看到 server 启动等 stdio 输入（超时正常）
```

## Step 2: 装到客户端

### 2.1 Claude Code (CC)

```bash
# 用 jq merge 到 ~/.claude/settings.json（保留现有 mcpServers 配置）
HUB=/path/to/agent-memory-hub  # 改成你的路径

jq --arg path "$HUB/agent_runtime_kit/mcp/server.sh" '
  .mcpServers //= {} |
  .mcpServers."agent-memory-hub" = {"command": $path}
' ~/.claude/settings.json > /tmp/s.json && mv /tmp/s.json ~/.claude/settings.json

# 验证
jq '.mcpServers' ~/.claude/settings.json
# 应看到：{"agent-memory-hub": {"command": "..."}}
```

重启 CC 后，CC 应该能看到这 4 个 MCP tool：
- `mcp__agent-memory-hub__search_memory`
- `mcp__agent-memory-hub__write_memory`
- `mcp__agent-memory-hub__list_recent`
- `mcp__agent-memory-hub__read_memory`

### 2.2 Claude Desktop (.app)

编辑 `~/Library/Application Support/Claude/claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "agent-memory-hub": {
      "command": "/path/to/agent-memory-hub/agent_runtime_kit/mcp/server.sh"
    }
  }
}
```

重启 Claude Desktop。

### 2.3 Codex CLI

按 Codex MCP 文档（https://platform.openai.com/docs/codex/mcp）配置。一般是：

```bash
codex mcp add agent-memory-hub /path/to/agent-memory-hub/agent_runtime_kit/mcp/server.sh
```

或编辑 `~/.codex/mcp.json`（具体路径以 Codex 当前版本为准）：

```json
{
  "agent-memory-hub": {
    "command": "/path/to/agent-memory-hub/agent_runtime_kit/mcp/server.sh"
  }
}
```

### 2.4 Qoder

按 Qoder extensions 文档配置 MCP server（具体 UI 路径请查 Qoder 当前版本）。command 同样填 `server.sh` 路径。

### 2.5 Wukong（钉钉 AI 助手）

Wukong 的 MCP 配置在企业管理后台或技能中心。把 `server.sh` 部署到 Wukong 能访问到的位置，然后在管理后台填配置。注意 Wukong 可能要求 SSE transport 而非 stdio——v0.4 会加 SSE 支持。

## Step 3: 端到端验证

任何装好 MCP 的客户端，问它：

```
"调用 search_memory 工具查 BOM 相关的 memory items"
```

客户端应该自己调起 `agent-memory-hub__search_memory` 工具，返回 brain pool 里 BOM 相关的 items。

## 4 个工具完整列表

| 工具 | 用途 | 关键参数 |
|---|---|---|
| `search_memory` | 关键词检索 + 多维过滤 | `query` (必), `type`, `project`, `tag`, `since_days`, `limit` |
| `write_memory` | 写一条 memory item | `type` (必), `title` (必), `summary` (必), `body` (必), `tags`, `project`, `sensitivity` |
| `list_recent` | 列最近 N 天的 items（无关键词）| `type`, `project`, `days`, `limit` |
| `read_memory` | 按 id 读完整内容 | `item_id` (必) |

## Schema 兼容性

- 所有工具的输入/输出都是 MCP 标准 JSON-RPC 2.0
- write_memory 的 type 必须是 `fact|episode|decision|artifact|signal|handoff`
- sensitivity 默认 `internal`，含密钥用 `secret`

## 跟 v0.2.x bash hook 的关系

| 接入路径 | 客户端 |
|---|---|
| Bash 直接调 `agent_runtime_kit/tools/*.sh`（v0.2 起）| 任何能跑 bash 的 Agent / 人 |
| CC hooks（v0.2.1 起）| 仅 CC |
| **MCP server（v0.3）** | **任何支持 MCP 的客户端：CC/Codex/Qoder/Wukong/Claude Desktop** |

三种路径**共用同一个数据后端** `~/.agent-memory-hub/items/`——任何路径写的，其他路径都能看到。

## 卸载 MCP

```bash
# 从 ~/.claude/settings.json 删
jq 'del(.mcpServers."agent-memory-hub")' ~/.claude/settings.json > /tmp/s.json && mv /tmp/s.json ~/.claude/settings.json

# 删 venv（节省磁盘空间，可选）
rm -rf $HUB/agent_runtime_kit/mcp/.venv
```

## 故障排查

| 症状 | 排查 |
|---|---|
| CC 看不到 MCP 工具 | `jq '.mcpServers' ~/.claude/settings.json`；重启 CC |
| `Error: install mcp first` | venv 没装 mcp 包：`$HUB/agent_runtime_kit/mcp/.venv/bin/pip install 'mcp[cli]'` |
| `python3.12 not found` | mac: `brew install python@3.12`；linux: `apt install python3.12 python3.12-venv` |
| 写入路径错（"item not found"）| 检查 env BRAIN_DIR；默认是 `~/.agent-memory-hub/` |
| Server 启动但客户端连不上 | 看客户端日志（CC: `~/.claude/logs/`）；用 `server.sh` 直接跑测一遍 |

## 已知限制（v0.3）

1. **仅 stdio transport**——SSE 留 v0.4
2. **同步阻塞调用**——大查询会让客户端等
3. **没有认证 / 沙箱**——server 信任所有调用方（本地用没问题，未来跨网络需加 auth）
4. **没有 GC**——session-end signal 累积要等 v0.4
