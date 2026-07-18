# 阶段三多 Agent 产品化治理就绪报告

状态：**PASS**  
证据时间：`2026-07-19T05:30:00+00:00`  
基线提交：`eab685aec5d6bbf8c54004448e6b547e7bcceeff`  
实现摘要：`sha256:0a736c563e28e3d12169191b2ced4231111dc6fc918b66602bef1d2c94a7092d`

## 结论

- manifest：16 个，schema `amh-adapter-manifest/v1`；
- 生命周期结果：`amh-adapter-lifecycle-result/v1`；
- 发布控制：`amh-adapter-release-controls/v1`，顺序为 shadow → canary → default，disabled 为单 adapter kill switch；
- 隐私扫描：pass，违规字段 0；
- core isolation：CLI、MCP、禁用 hook 空协议均通过。

## 两批合同证据

| 批次 | Adapter | 同合同结果 |
|---:|---|---|
| 1 | codex, qoder | PASS |
| 2 | claude_code, qoder_work | PASS |

## Manifest 矩阵

| Adapter | 平台 | Channel | Hook output protocol |
|---|---|---|---|
| `aider` | darwin, linux, windows | awareness, cli | none |
| `aone_copilot` | darwin, linux, windows | awareness, cli | none |
| `claude_code` | darwin, linux, windows | awareness, cli, hook, mcp | claude-hook-json/v1 |
| `cline` | darwin, linux, windows | cli, mcp | none |
| `codex` | darwin, linux, windows | awareness, cli, hook, mcp | codex-hook-json/v1 |
| `continue_dev` | darwin, linux, windows | cli, mcp | none |
| `cursor` | darwin, linux, windows | cli, hook, mcp | none |
| `github_copilot` | darwin, linux, windows | awareness, cli | none |
| `hermes_agent` | darwin, linux, windows | cli, mcp | none |
| `mulerun` | darwin, linux, windows | cli | none |
| `openclaw` | darwin, linux, windows | cli, mcp | none |
| `openhuman` | darwin, linux, windows | awareness, cli | none |
| `opensquilla` | darwin, linux, windows | cli, mcp | none |
| `qoder` | darwin, linux, windows | awareness, cli, hook, mcp | qoder-hook-json/v1 |
| `qoder_work` | darwin, linux, windows | awareness, cli, hook, mcp | qoder-hook-json/v1 |
| `wukong` | darwin, linux, windows | awareness, cli, hook, mcp | none |

## 真实性边界

本报告证明 manifest、生命周期事务、TTL、provenance、发布控制和隔离合同已经机器化。单机 `verified` 仍必须同时满足 configured、doctor、fresh runtime、fresh context injection 与 fresh verification；缺少真实客户端证据时保持 blocker，不由本报告静态晋升。
