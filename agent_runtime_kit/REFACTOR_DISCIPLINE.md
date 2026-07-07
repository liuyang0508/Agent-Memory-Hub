# Refactor Discipline（重构纪律）

> 给任何 LLM Agent（Claude Code、Codex、Qoder、Wukong 等）看的"如何做行为保持重构"指南。
> 跟 `HONEST_REVISION_PLAYBOOK.md` 配对：本手册说"如何做"，那份说"做错了如何修订"。

## 为什么需要这条

2026-05-31 有一次教科书级 behavior-preserving 重构：
- cli.py 2080→cli/ 包 44 命令（commit `3b57e5e`，747 ✅）
- app.py 1742→133 + 7 APIRouter（commit `b5c852a`，761 ✅，8/8 adversarial verify）
- 同日踩坑 episode：AST 切割漏 `__main__` 块，靠 dogfooding 发现（`mem-20260531-145740`）

24 小时后一个 worktree 用"consolidation"名义**反向操作**了上述全部 + 顺带删 6 个 core 模块。
commit message 自称"1050 lines preservation"，实际 diff 是 `-15536/+4798`。
被 `docs/audit/2026-06-01-consolidation-worktree-capability-loss-audit.md` §9 定性为 regression。

本纪律存在的目的：**让下一次"consolidation / simplification / cleanup"必须满足 5-31 那套流程，或显式 justify 为什么跳过**。

---

## 5 道必经关卡

任何 worktree/branch 如果要做 "重构 / 拆分 / 合并 / 瘦身 / 简化 / cleanup"，必须**按序**通过以下 5 关：

### 关卡 1: Surface-Lock（行为锁）

在动任何代码之前，写一个测试（或测试集）锁住当前对外行为表面。

| 层级 | 怎么锁 |
|------|--------|
| CLI | 列出所有命令名 + 参数签名 → 断言集 |
| MCP | 列出所有 tool name + schema → 快照比对 |
| Web | 列出所有 route path + method + response model → 快照比对 |
| Python API | 列出所有 public 函数签名 → 断言集 |
| Shell shim | `python -m pkg`、`memory <cmd>` 等真实调用路径 → 逐个断言 |

**失败标准**：如果你不能写出 surface-lock 测试，说明你还不够理解当前行为，不够格重构。

### 关卡 2: Mechanical Cutting（机械切割）

代码搬迁必须用**确定性脚本**（AST 切割、sed、自动化工具），不能手写目标文件。

理由：
- AST 切割按 def 行 → end_lineno 逐字节提取，零转写错误
- 手写 1653 行 cli.py == 重新实现，不是重构
- 机械切割的 diff 可以 verify（输入 = 原文件，输出 = 分片文件，`cat 分片 | diff 原文` 应该只剩 import/boilerplate 差异）

**特别注意**（来自踩坑 episode `mem-20260531-145740`）：
- AST 切割器只提取 `def/import/assign` 会**丢失**顶层的 `if __name__=="__main__"`、`@app.middleware` 装饰器、模块级 `Expr` 节点
- 拆完后**审计原文件所有顶层 AST 节点类型**，不只函数

### 关卡 3: 全调用路径验证

不只 `import` 测试。按**真实调用路径**逐个验证：

```bash
# 必须覆盖的入口（示例）
python -m agent_brain.interfaces.cli write --type fact --title test ...
memory write --type fact --title test ...          # console_script
agent_runtime_kit/tools/write-memory.sh ...                    # shell shim → python -m
curl http://localhost:PORT/api/items               # web route
```

来自 5-31 教训：717 测试全绿但 `python -m` 路径炸了，因为测试只走 `from pkg import app`。

### 关卡 4: Adversarial Verification（对抗验证）

对于影响 >20 个函数或 >500 行的重构，跑 multi-agent workflow 或至少 3 个独立 reviewer：

- 每个 agent 负责证伪一个维度（路由完整性 / 模型签名 / 中间件 / 状态共享 / import 链）
- 结果必须是 `N/N preserved, 0 broken, 0 uncertain` 才算通过
- 5-31 web 拆分用了 8 维度 workflow（390K token / 212s），最终 8/8 preserved

**省力替代**（<20 函数的小重构）：至少做一次 `git diff --stat` + 手动确认删除的每个函数在新位置有对应。

### 关卡 5: Commit Honesty（提交诚实）

commit message 必须如实描述：

| 必须包含 | 示例 |
|----------|------|
| 行数变化 | `cli.py 2080→cli/ 5 files (same 44 commands)` |
| 测试结果 | `747 passed / 2 skipped` |
| 已知风险 | `web/app.py split deferred to separate PR (high risk)` |
| 删除的东西 | 如果删了任何 public API，必须列出 |

**反例**：`"preservation snapshot of 1050 lines of WIP"` 描述一个 `-15536/+4798` 的 diff — 这是 14× 失真。

---

## 红线（触发即 abort）

以下任何一条成立，重构分支**不得 merge**：

1. **Surface-lock 测试数量减少** — 你不能通过删测试来让测试通过
2. **Public API 减少且没有 migration path** — 删命令/删 route/删 MCP tool 不叫重构，叫 breaking change
3. **反向操作最近 7 天内已 merge 的 behavior-preserving 重构** — 这叫 revert，需要 revert 的理由和流程，不能伪装成 refactor
4. **Commit message 的行数变化与 `git diff --stat` 偏差 >2×** — 说明 commit message 在撒谎

---

## Justify-Skip 流程

如果你认为某一关卡不适用，必须在 commit message 或 PR description 里写：

```
REFACTOR_DISCIPLINE skip: 关卡 N — [理由]
```

理由必须是具体的技术原因，不能是"太复杂了"或"时间不够"。

合法的 skip 理由示例：
- "关卡 4 skip — 只移动 3 个函数，手动 diff 确认完整性足够"
- "关卡 2 skip — 新增纯新代码（不是搬迁），无原文件可做机械切割"

不合法的理由：
- "这只是 cleanup" — cleanup 也能引入 regression
- "我会手动检查" — 5-31 已证明 717 测试绿也漏了真回归

---

## 适用范围

本纪律适用于**名义上不改变外部行为**的代码变动。以下不受本纪律约束：

- 新增功能（feature branch）— 没有"行为保持"的约束
- 有意的 breaking change — 走 deprecation / migration 流程，不伪装成 refactor
- 文档/配置/CI 改动 — 不影响运行时行为

---

## 参考

- 教科书重构：`mem-20260531-145507`（harvester + cli/mcp split, 747 ✅）
- 教科书重构：`mem-20260531-175519`（web route split, 761 ✅, 8/8 adversarial）
- 踩坑实录：`mem-20260531-145740`（`__main__` 丢失, dogfooding 救场）
- 反面教材：`docs/audit/2026-06-01-consolidation-worktree-capability-loss-audit.md` §9
- 修订手册：`agent_runtime_kit/HONEST_REVISION_PLAYBOOK.md`
