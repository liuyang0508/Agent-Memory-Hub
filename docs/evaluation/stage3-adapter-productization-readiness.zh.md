# 阶段三多 Agent 产品化治理就绪报告

状态：**PASS**
证据时间：`2026-07-19T05:45:00+08:00`
基线提交：`eab685aec5d6bbf8c54004448e6b547e7bcceeff`
实现摘要：`sha256:f59cfda32e27a166cc9aea40bc6a18af1761d8d854c6fc27fdd8cf5d77c29e96`

## 结论

- manifest：16 个，schema `amh-adapter-manifest/v1`；
- 生命周期结果：`amh-adapter-lifecycle-result/v1`；
- 发布控制：`amh-adapter-release-controls/v1`，顺序为 shadow → canary → default，disabled 为单 adapter kill switch；
- 隐私扫描：pass，违规字段 0；
- core isolation：CLI、MCP、禁用 hook 空协议均通过。

## Qoder 配置收敛边界

`amh-adapter-config-convergence/v1` 要求 qoder, qoder_work 的每个受支持事件恰有 1 个 AMH Hook，并通过 `managed-memory-shim` 选择稳定运行时。该合同由必需检查 `adapter-governance` 执行，证明配置所有权、Hook 基数和稳定运行时选择；真实客户端召回是否生效，仍须新鲜 transcript/context 证据，不能由静态门禁推断。

## 真机边界证据

平台：`darwin-arm64`；证据提交：`bf10a302305b06511953e6b0c31ebf6072008b0c`；hook：`sha256:a716eda7ad90a0a0a645d5e15171d3fdfd73d793269c74aed840119f2ba584bc`。

| Adapter | 批次 | install-verify | 最终判定 | blocker |
|---|---:|---|---|---|
| `codex` | 1 | passed / `OK` | verified | - |
| `qoder` | 1 | failed / `CONTEXT_MISSING` | blocked | context effectiveness not observed; support level remains install-ready |
| `claude_code` | 2 | passed / `OK` | verified | - |
| `qoder_work` | 2 | failed / `CONTEXT_MISSING` | blocked | context effectiveness not observed; runtime and context evidence stale |

四个 adapter 的 `repair`、`upgrade` 都以 schema `amh-adapter-lifecycle-result/v1` 返回 `passed / OK`。Qoder 与 QoderWork 的安装和修复可用，但 context effectiveness 不足或证据过期，因此保持 blocked，不以事务成功冒充 verified。

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
