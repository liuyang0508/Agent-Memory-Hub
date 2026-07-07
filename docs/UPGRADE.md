# v0.5 → v1 升级指南

v1 把 hub 核心从 bash 重构为 Python，但保留所有现有入口的向后兼容形态。

## 升级步骤

1. 拉取最新代码：
   ```bash
   cd ~/path/to/agent-memory-hub
   git pull
   ```

2. 安装 Python 包（要求 Python 3.11+）：
   ```bash
   pip install -e .
   ```
   如需本地语义向量检索，安装 embeddings extra：
   ```bash
   pip install -e ".[embeddings]"
   ```

3. 重建索引（v1 新增 SQLite 影子索引；md 文件不动）：
   ```bash
   memory reindex
   ```
   安装 embeddings extra 后，首次语义向量检索会下载 sentence-transformers 模型。

4. 验证：
   ```bash
   memory list-recent --n 5
   ```
   应该看到你最近的 5 条 items。

## 行为变化

- `agent_runtime_kit/tools/write-memory.sh` 现在是 thin wrapper，调用 `memory write`。所有 v0.5 flag 仍然支持。
- `agent_runtime_kit/mcp/server.py` 现在是 thin shim，调用 `agent_brain.interfaces.mcp.server.run()`。MCP client 配置不需要改。
- 新增 SQLite index `~/.agent-memory-hub/index.db`。可随时删除 + `memory reindex` 重建，md 文件是 source of truth。

## 数据兼容性

v0.5 写的所有 items 在 v1 schema 下**全部能加载**，无需迁移。

## 问题

如果 `memory reindex` 失败、CLI 找不到 / MCP 连不上，提 issue 并附上 `memory version` 的输出。
