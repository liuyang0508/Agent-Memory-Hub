# Qoder / QoderWork 配置收敛治理设计

日期：2026-07-19（Asia/Shanghai）

状态：已批准并进入实现

## 1. 背景与实时证据

阶段三报告仍把 Qoder 和 QoderWork 标记为 blocked。2026-07-19 对真实用户配置复查后，确认阻塞不只是“缺少新会话证据”，而是已发生配置漂移：

- `~/.qoder/settings.json` 中存在 4 份 AMH `UserPromptSubmit` Hook；
- `~/.qoderwork/settings.json` 中存在 3 份 AMH `UserPromptSubmit` Hook；
- 重复项同时引用稳定主仓、旧 feature worktree、已删除的 `amh-bench-*` 临时目录和其他 Python 环境；
- Qoder 与 QoderWork MCP 配置仍引用旧 `dual-route-recall` worktree；
- `memory adapter doctor qoder` 和 `memory adapter doctor qoder_work` 均返回 error；
- `memory adapter verify qoder` 返回 `DOCTOR_FAILED`，QoderWork 返回 `CONTEXT_MISSING`。

现有共享函数 `prune_duplicate_hub_hook_handlers()` 已被 Codex 和 Claude Code 使用，但 Qoder 与 QoderWork 的 installer 只更新当前 checkout 的命令，不会删除其他 checkout 的同名 AMH handler。doctor 也只证明“存在预期 Hook”，不能证明“配置已经收敛为唯一权威 Hook”。

本治理先关闭这个已经在真实环境发生的 blocker，再把相同合同推广到其他 Adapter；不把一次手工修配置冒充产品能力。

## 2. 目标

本轮完成后：

1. Qoder 与 QoderWork 每个受支持事件恰好保留一份当前稳定主仓的 AMH Hook；
2. 所有同名 AMH Hook 的临时目录、旧 worktree、旧 checkout 重复项自动删除；
3. 用户自有或第三方 Hook 的内容与相对顺序保持不变；
4. `UserPromptSubmit` 的唯一 AMH Hook 位于首位，并输出对应客户端要求的 JSON additional context；
5. MCP command、args、`BRAIN_DIR`、`PYTHONPATH` 收敛到当前稳定运行时；
6. doctor 能区分“存在 Hook”和“唯一、有效、顺序正确的 Hook”；
7. repair/install 幂等，第二次执行不再产生配置变化；
8. required `adapter-governance` 直接验证上述合同；
9. 默认安装不再把临时 worktree 当成长生命周期配置的权威运行时；
10. 发布后修复真实本机配置，并用真实 Hook 与客户端会话证据复核。

## 3. 非目标

- 本轮不重构全部 16 个 Adapter；
- 不改变召回算法、Gateway、corpus 或 Hook 输出协议；
- 不删除未知第三方 Hook，不按路径模糊删除普通用户脚本；
- 不把旧 worktree 保留为运行时 fallback；
- 不因缺少真实客户端会话而伪造 `verified`；
- 不在 Hook 内联网、自更新或静默改写配置；
- GitHub Actions Node 运行时升级另建治理轮次，不与本 blocker 混提。

## 4. 方案比较与决策

### 方案 A：复用共享去重原语，定向收敛 Qoder 家族（采用）

Qoder 与 QoderWork installer 接入现有跨 checkout AMH Hook 识别和去重逻辑，同时补强诊断与 required CI。

优点：直接关闭真实 blocker，复用已经在 Codex/Claude 验证过的所有权边界，改动范围可审计。缺点：其他 Adapter 的统一抽象留到下一轮。

### 方案 B：重写全部 Hook Adapter 的统一 reconciler

一次把 Codex、Claude、Qoder、QoderWork 迁移到新的配置事务层。

优点：长期抽象更统一。缺点：扩大回归面，延迟真实 Qoder 修复，也会把已稳定的 Codex/Claude 路径重新置于风险中。

### 方案 C：只执行本机 `memory adapter repair`

优点：短期最快。缺点：当前 installer 仍会残留重复项，无法阻止其他用户复现，也没有 CI 证明，因此不采用。

## 5. 权威配置合同

### 5.1 Hook 所有权识别

AMH 只管理同时满足以下条件的 handler：

- command token 指向 `/agent_runtime_kit/hooks/<script>` 或兼容的 `/brain/hooks/<script>`；
- `<script>` 是当前 Adapter 对该事件声明的 Hook script；
- 识别基于 shell token，而不是任意子串匹配。

未知脚本、用户 guard、其他产品 Hook 和结构异常但不属于 AMH 的条目必须保留。

### 5.2 每事件唯一性

对 Qoder/QoderWork 的 `UserPromptSubmit` 与 `Stop` 分别执行：

1. 找到当前 checkout 的目标 handler；
2. 如果不存在，创建一份 canonical handler；
3. 把 canonical handler 的 command 更新为完整期望命令；
4. 删除其他 checkout 中相同脚本名的 AMH handler；
5. 删除后为空的 AMH-only entry；
6. 保留 mixed entry 中的第三方 handler；
7. `UserPromptSubmit` canonical entry 移至列表首位；
8. `Stop` 不改变第三方 entry 的相对顺序。

收敛后，每个事件中对应脚本的 AMH handler 数量必须严格等于 1。

### 5.3 命令完整性

canonical command 必须与 Adapter 生成器的期望值完全一致，包括：

- 固定安全 PATH；
- `AGENT_MEMORY_HUB_ADAPTER=qoder|qoder_work`；
- `MEMORY_PYTHON=<稳定运行时>`；
- `UserPromptSubmit` 的 `AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT=json`；
- 当前稳定主仓内的正确 Hook 脚本路径；
- 不包含 Qoder fish 不兼容的 POSIX PATH expansion。

### 5.4 MCP 合同

install/repair 继续以原子 JSON 替换方式更新 AMH server，严格校验：

- `command` 等于稳定 AMH Python；
- `args` 等于 `-m agent_brain.interfaces.mcp.server`；
- `env.BRAIN_DIR` 等于真实 brain；
- `env.PYTHONPATH` 等于稳定主仓；
- server enabled，Qoder extension cache 未 disabled；
- Qoder User、SharedClientCache、extension/local 三份配置一致；
- 不覆盖其他 MCP server。

### 5.5 长期运行时根目录权威

Qoder 配置由 GUI 在未来会话中长期消费，因此不能无条件使用“当前执行代码所在 checkout”。运行时根目录按以下优先级确定：

1. 构造 Adapter 时显式传入的 `repo_dir`：作为测试或嵌入调用的显式依赖注入；
2. 未显式传入时，读取 AMH 管理的 `memory` CLI shim，只有当它符合受管脚本格式、目标存在于 `<root>/.venv/bin/memory`，且 `<root>` 同时包含 `pyproject.toml` 和 AMH Hook 目录时，才把 `<root>` 作为长期权威；
3. shim 不存在的首次安装环境，回退到当前模块 checkout；
4. shim 存在但无法解析、目标不存在或根目录校验失败时 fail closed，要求先运行 `memory doctor --fix` 或正式 installer，不把可疑 checkout 写入 GUI 配置。

该规则只决定 Qoder/QoderWork 本轮写入的长期命令路径，不改变 Python import 解析或其他 Adapter。路径判定不依赖分支名、目录名猜测或 GitHub 网络状态。

## 6. 组件与数据流

### 6.1 Installer / Repair

`QoderAdapter.install()` 与 `QoderWorkAdapter.install()` 对每个事件执行同一收敛序列：

```text
解析并校验长期运行时根目录
  → 读取 JSON
  → 定位或创建 canonical handler
  → 更新 canonical command
  → prune 同名 AMH duplicate
  → 调整 UserPromptSubmit 首位
  → 比较变更
  → 原子写回
```

`memory adapter repair` 复用 install 路径，因此不另建第二套修复逻辑。

### 6.2 Doctor

扩展共享 Qoder-compatible 诊断，使每个事件输出以下事实：

- canonical handler 数量；
- 同名 AMH handler 总数；
- canonical command 是否精确匹配；
- UserPromptSubmit 是否首位；
- script 是否存在且可执行。
- 长期运行时根目录是否来自有效显式注入、受管 shim 或合法首次安装 fallback。

任一事件出现 0 份、超过 1 份、错误脚本、命令漂移或顺序错误时均为 error。诊断只报告 AMH 相关路径和计数，不回显第三方完整命令或配置中的潜在秘密。

### 6.3 Verify

生命周期 verify 继续以 doctor 为前置门禁：

- 配置未收敛：`DOCTOR_FAILED`；
- 配置收敛但没有 fresh 客户端 context：保持 `CONTEXT_MISSING`；
- 只有 doctor、runtime、context 和客户端有效性证据都新鲜时才允许 `verified`。

这保持“事务成功不等于客户端有效”的现有真实性边界。

## 7. 错误处理与安全边界

- malformed JSON：拒绝覆盖，返回可操作错误；
- `hooks` / `mcpServers` 不是对象：拒绝覆盖；
- 已存在但失效或非受管的 CLI shim：拒绝重写长期 Qoder 配置；
- 原子写失败：原文件保持不变；
- mixed entry：只移除明确归属 AMH 的 duplicate handler；
- 无法识别所有权：保留并由 doctor 报告，不猜测删除；
- 目标 Python 或脚本不存在：doctor error，repair 不把无效目标写成 PASS；
- 配置收敛不写入 raw prompt、transcript、token 或秘密。

## 8. 测试设计

新增独立的 Qoder 配置收敛合同测试，覆盖 Qoder 与 QoderWork：

1. current + temp benchmark + old worktree 三类重复项收敛为 1；
2. 只有 stale handler 时创建 current canonical 并删除 stale；
3. mixed entry 中第三方 handler 原样保留；
4. 独立第三方 entry 的内容和相对顺序保持；
5. UserPromptSubmit canonical entry 位于首位；
6. Stop 不无故重排第三方 entry；
7. command 的 adapter、Python、JSON output 和脚本路径全部正确；
8. MCP 从旧 worktree 收敛到当前稳定路径；
9. install/repair 连续执行两次，第二次字节级不变；
10. doctor 对重复、缺失、错误顺序、错误 command 分别 fail closed；
11. malformed JSON 不被覆盖；
12. uninstall 只移除 AMH-owned handlers；
13. 默认从 feature worktree 执行、但受管 shim 指向稳定主仓时，最终命令仍指向稳定主仓；
14. shim 不存在时允许首次安装 fallback，shim 存在但损坏时 fail closed；
15. Qoder 与 QoderWork 使用相同合同但各自保持客户端路径和 adapter 名称。

现有全量 unit、system、conformance、Hook shell、ruff、mypy 和 report checks 仍需通过。

## 9. CI 治理

在 required `adapter-governance` job 中显式加入新的配置收敛测试文件，并加强 CI 合同测试，要求：

- Qoder convergence 测试不可被移出 required job；
- 不允许 `continue-on-error`；
- 不允许测试写入真实 HOME；
- 测试 fixture 必须使用隔离 HOME 和显式 repo/brain；
- committed adapter governance report 仍由生成器校验，不能手写假绿。

## 10. 发布与真实迁移

1. 在隔离 worktree 完成实现和测试；
2. 全量验证通过后快进合入本地 `main` 并直推 GitHub；
3. 确认远端 required checks 全绿；
4. 只从稳定主仓 `main` 执行：

```bash
memory adapter repair qoder
memory adapter repair qoder_work
```

5. 复核真实配置：每事件 1 份 AMH handler，`amh-bench-*` 和旧 worktree 引用为 0；
6. 运行 doctor、verify 和真实 Hook 协议探针；
7. 重启或刷新 Qoder/QoderWork，分别提交可召回已知项目记忆的 prompt；
8. 只有 transcript/context evidence 真实到达后才更新阶段三 verified 状态。

若客户端不能在当前自动化环境中完成交互，代码、配置和 Hook 可判定完成，但 Qoder/QoderWork 的客户端 effectiveness 必须继续显示 blocker，并明确给出唯一人工复核动作，不能降级门禁。

## 11. 回滚

- 代码可回退本轮提交；
- 已删除的 stale duplicate 不恢复，因为它们引用无效或非权威 checkout；
- 用户第三方 Hook 未被修改，无需恢复；
- 若 canonical 主仓不可用，先修复稳定安装或重新安装 Adapter，不回退到临时 worktree；
- MCP 回滚不得重新引入已删除 checkout。

## 12. 完成定义

以下全部满足，本轮配置收敛治理才完成：

1. 两个 Adapter 的重复/stale/mixed/ordering/idempotence 单测全部通过；
2. doctor 对重复和命令漂移 fail closed；
3. required `adapter-governance` 显式执行收敛合同；
4. 本地全量门禁通过；
5. 真实 Qoder Hook 从 4 份降为 1 份，QoderWork 从 3 份降为 1 份；
6. 真实配置中临时目录、旧 worktree 和错误 Python 引用为 0；
7. 从非稳定 checkout 调用默认 Adapter 时仍选择受管 shim 的稳定运行时根目录；
8. Qoder 三份 MCP 配置与 QoderWork MCP 均指向稳定主仓；
9. 第三方 Hook 前后内容一致；
10. GitHub `main` 与本地 SHA 一致，required checks 全绿；
11. 客户端 effectiveness 有真实证据，或继续以明确 blocker 呈现而不假绿；
12. 产出物和迁移结果写入 AMH artifact memory，供后续 Agent 承接。
