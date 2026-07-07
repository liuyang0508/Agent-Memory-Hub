# MCP 客户端配置示例（v0.3+）

替换 `__HUB__` 为你的 agent-memory-hub 项目代码路径，例如 `<path-to-agent-memory-hub>`。

## 客户端配置状态总览

| Client | Config 位置 | 格式 | 状态 |
|---|---|---|---|
| Claude Code | `~/.claude/settings.json` | JSON | `install-ready`; hook/MCP config and runtime evidence exist, but the latest verified gate did not pass |
| Codex CLI 0.130+ | `~/.codex/config.toml` | TOML | `verified` |
| Qoder | hooks settings; manual MCP config only | JSON | `install-ready` for hooks, MCP auto-config unverified |
| QoderWork | `~/.qoderwork/settings.json` hooks + `~/.qoderwork/awareness/main/AGENTS.md` + `~/.qoderwork/mcp.json` | JSON/Markdown | `verified`; current snapshot includes QoderWork GUI context-effective evidence |
| OpenHuman | `~/.openhuman/config.toml` agentmemory backend | TOML | `verified` |
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` | JSON | `docs-only` |
| Cursor | `~/.cursor/mcp.json` 或 `<project>/.cursor/mcp.json` | JSON | `verified` |
| Cline (VS Code) | VS Code settings.json | JSON | `verified` |
| Continue | `~/.continue/config.yaml` | YAML | `verified` |

> 状态遵循 README 的 truth contract：`verified` 表示对应适配器门禁已有 passed 记录；`install-ready` 表示安装路径和测试存在但当前门禁仍有阻塞；`docs-only` 表示只有配置文档；`wip` 表示当前只有 adapter stub 或规划。不要把 `wip` 当作已支持。

---

## Claude Code

`~/.claude/settings.json`：

```json
{
  "mcpServers": {
    "agent-memory-hub": {
      "command": "__HUB__/agent_runtime_kit/mcp/server.sh"
    }
  }
}
```

重启 CC 会话生效。验证：在 CC 里问 "调 mcp__agent-memory-hub__list_recent" 看是否真触发 MCP tool。

## Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "agent-memory-hub": {
      "command": "__HUB__/agent_runtime_kit/mcp/server.sh"
    }
  }
}
```

重启 Claude Desktop 应用。

## Codex CLI 0.130+

**推荐用 CLI 命令注册**（自动写入 config.toml，避免手抄出错）：

```bash
codex mcp add agent-memory-hub -- __HUB__/agent_runtime_kit/mcp/server.sh
codex mcp list                    # 验证 status=enabled
codex mcp get agent-memory-hub
```

或手动在 `~/.codex/config.toml` 末尾追加：

```toml
[mcp_servers.agent-memory-hub]
command = "__HUB__/agent_runtime_kit/mcp/server.sh"
```

可选 env：

```toml
[mcp_servers.agent-memory-hub]
command = "__HUB__/agent_runtime_kit/mcp/server.sh"
env = { BRAIN_DIR = "/path/to/custom/brain" }
```

**移除**：`codex mcp remove agent-memory-hub`

## Cursor

Cursor 1.0+ 支持 MCP。配置写到 `~/.cursor/mcp.json`（用户级，所有项目生效）或 `<project>/.cursor/mcp.json`（项目级）：

```json
{
  "mcpServers": {
    "agent-memory-hub": {
      "command": "__HUB__/agent_runtime_kit/mcp/server.sh"
    }
  }
}
```

重启 Cursor，在 Cursor Composer / Chat 里 MCP tool 应该自动可见。
官方文档：https://docs.cursor.com/context/model-context-protocol

## Cline（VS Code 插件）

通过 Cline 的 MCP server 配置 UI 或直接编辑 `cline_mcp_settings.json`：

```json
{
  "mcpServers": {
    "agent-memory-hub": {
      "command": "__HUB__/agent_runtime_kit/mcp/server.sh"
    }
  }
}
```

文件位置（macOS）：`~/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json`

重启 VS Code 或在 Cline 面板点 "Refresh MCP Servers"。

## Continue（VS Code / JetBrains 插件）

推荐使用 CLI 安装器写入 `~/.continue/config.yaml`：

```bash
memory adapter install continue_dev
memory adapter doctor continue_dev
```

手动配置时，使用 Continue 全局 `config.yaml` 的 `mcpServers` 列表：

```yaml
mcpServers:
  - name: agent-memory-hub
    command: __HUB__/agent_runtime_kit/mcp/server.sh
```

如果通过 Python 模块启动，也可以写成：

```yaml
mcpServers:
  - name: agent-memory-hub
    command: python
    args:
      - -m
      - agent_brain.interfaces.mcp.server
    env:
      BRAIN_DIR: /path/to/custom/brain
```

文档：https://docs.continue.dev/cli/configuration

## Goose（Block 开源 agent，也支持 MCP）

`~/.config/goose/config.yaml`：

```yaml
extensions:
  agent_brain:
    type: stdio
    cmd: __HUB__/agent_runtime_kit/mcp/server.sh
    enabled: true
```

文档：https://block.github.io/goose/docs/getting-started/using-extensions

---

## 用 BRAIN_DIR 环境变量指向自定义数据位置

如果你想让 brain pool 数据不在默认 `~/.agent-memory-hub/`，每个客户端配置都支持加 env：

```json
{
  "mcpServers": {
    "agent-memory-hub": {
      "command": "__HUB__/agent_runtime_kit/mcp/server.sh",
      "env": {
        "BRAIN_DIR": "/path/to/custom/brain"
      }
    }
  }
}
```

TOML（Codex）：

```toml
[mcp_servers.agent-memory-hub]
command = "__HUB__/agent_runtime_kit/mcp/server.sh"
env = { BRAIN_DIR = "/path/to/custom/brain" }
```

---

## 实测一个新客户端，请贡献 PR

如果你跑通了一个 `docs-only` 或 `wip` 状态的客户端：

1. 在 issue 里报告：用什么版本 / 怎么验证 / 有没有踩坑
2. 补安装路径、卸载路径和测试，再按 truth contract 调整状态
3. 顺手在 [README.md](../../README.md) 的 "Cross-agent integration" 表格也升级

详见 [CONTRIBUTING.md](../../CONTRIBUTING.md)。
