# Agent 原生探索 / 记忆与 AMH 边界矩阵

> 初版日期：2026-07-10  
> 目标读者：AMH 维护者、adapter 实现者、后续接手治理项的 Agent。  
> 范围：先厘清概念边界和冲突处理规则；不把所有 Agent 的产品细节一次性写成最终版。

## 结论先行

Agent UI 里展示的 `Explored`、`Search`、`List pages` 这类轨迹，本质上是**当前任务的代码探索过程**，不是长期记忆，也不是 AMH 召回。它可以帮助用户判断 Agent 怎么找代码，但不能直接当作跨 Agent 事实源。

各 Agent 自带的 memories / rules / project instructions 是**各自运行时的本地上下文机制**。它们可以影响本 Agent 的行为，但默认不跨 Agent、不可统一审计，也不一定有 AMH 这种 evidence / MemoryItem / index / context_pack 分层。

AMH 的定位是**共享第二大脑**：把可复用结论写成 `MemoryItem`，把原始来源保留在 evidence 层，把召回和注入作为可解释、可治理的派生过程。Agent 原生能力应该被 AMH 适配和利用，而不是和 AMH 抢“事实源”地位。

## 三类东西不要混

| 类别 | 典型表现 | 谁拥有 | 是否长期 | 是否跨 Agent | 是否可审计 | AMH 处理方式 |
|---|---|---|---:|---:|---:|---|
| 运行探索轨迹 | Codex `Explored` / `Search` / `List pages`，Claude/Cursor/Qoder 的 tool trace | 当前 Agent runtime / UI | 通常否 | 否 | 弱，取决于产品日志 | 只作为当前会话证据；需要复用时提炼成 episode / artifact |
| 原生指令层 | `AGENTS.md`、`CLAUDE.md`、Cursor Rules、Qoder settings hooks | Agent 或 repo | 是 | 取决于 Agent 是否读取 | 中 | 可由 AMH profile 生成或引用；不是 MemoryItem 本身 |
| 原生记忆层 | Codex local memories、Claude Code auto memory、Cursor Memories 等 | 单个 Agent 产品 | 是 | 通常否 | 弱到中 | 当作候选上下文；重要结论需经 AMH 写入漏斗固化 |
| AMH evidence | transcript、hook payload、runtime event、resource / extraction | AMH | 是 | 是 | 强 | 不默认进 prompt；为 MemoryItem 和治理提供证据 |
| AMH MemoryItem | fact / decision / episode / artifact / signal / handoff | AMH | 是 | 是 | 强 | 跨 Agent 共享事实源；通过 WriteService / governance 管控 |
| AMH context_pack | `<agent_brain>` 压缩视图、detail_uri、retrieve hint | AMH 派生层 | 派生 | 是 | 强 | 注入当前 Agent；可按 detail_uri 回读证据 |

## Agent 维度初版矩阵

| Agent / 平台 | 原生探索轨迹 | 原生指令 / 规则 | 原生记忆 | Hook / 扩展点 | 与 AMH 的正确关系 | 冲突风险 |
|---|---|---|---|---|---|---|
| Codex | `Explored`、Search/List 等 UI 轨迹，反映当前任务如何查代码 | `AGENTS.md`，Codex 启动时按全局、项目、目录层级读取，近目录覆盖远目录 | Local Codex memories，默认存于 Codex home 下的 `memories/`，可通过 `/memories` 控制当前任务是否使用/生成 | Codex hooks，可在 `UserPromptSubmit`、`Stop`、tool 相关事件等生命周期点执行脚本 | AMH 通过 `AGENTS.md` 注入纪律，通过 `UserPromptSubmit` hook 自动召回，通过 MCP/CLI 支持主动检索 | 中：Codex native memories 可能和 AMH item 过期状态冲突；以当前用户消息、当前仓库证据和 AMH validity 为准 |
| Claude Code | tool use / transcript / session trace | `CLAUDE.md`、`.claude/rules/`；Claude 文档明确 CLAUDE.md 与 auto memory 都是 context，不是强制配置 | Auto memory，由 Claude 根据纠正和偏好写入；`/memory` 可查看和管理 | Claude Code hooks 覆盖 session、turn、tool call 等 cadence | AMH 通过 CLAUDE.md/AGENTS awareness、hooks、MCP 接入；Claude auto memory 只能作为候选输入，不能替代 AMH truth | 中高：CLAUDE.md/auto memory 与 AMH injected context 可能重复或冲突；强制阻断应交给 hook，不交给记忆文本 |
| Cursor | Agent / Composer 的工具轨迹和编辑轨迹 | Cursor Rules、Project / Team / User Rules、AGENTS.md 支持 | Cursor Memories 产品细节需继续按官方文档核实；社区常把 memory bank 做成 rules / docs 工作流 | MCP、Rules、Skills；hook 能力需按版本另核 | AMH 首选通过 Rules / AGENTS awareness + MCP 接入；Memory Bank 类文件可被 AMH harvest 或写入 MemoryItem | 中：rules / memory-bank 容易和 AMH 重复维护同一事实 |
| Qoder / QoderWork | IDE / JetBrains plugin 中 Agent 执行轨迹 | settings / workspace awareness；产品强调 context engineering | Qoder 自身记忆能力需按当前版本核实；已有外部方案通过 hooks 接长期记忆 | Qoder Hooks 支持 `UserPromptSubmit`、`PreToolUse`、`PostToolUse`、`PostToolUseFailure`、`Stop`，可阻断部分事件 | AMH 适合走 hooks + MCP：prompt 前注入、Stop 写 evidence、tool 前做 guard | 中：hook 注入与 Qoder 自身上下文工程可能重复，需要 runtime ledger 证明谁注入了什么 |
| Wukong / Hermes 等内部 Agent | 取决于本地实现和 UI | 取决于 provider prompt、侧边栏上下文、adapter config | 取决于产品实现 | 通常通过 provider tool、MCP、文件 sidecar 或自研 hook | AMH 只声明已验证 adapter 层级：awareness、tool、automatic hook、fallback 分开证明 | 中：内部 Agent 容易把 provider prompt、workspace artifact、AMH memory 混成一层；需要 adapter doctor 和 runtime evidence |
| GitHub Copilot / IDE Agent | IDE 工具轨迹、chat history | repo instructions、workspace settings、rules | 产品侧记忆需另核 | MCP / IDE extension / repo instructions | AMH 可先作为 MCP/tool 或 derived profile；不要假设 Copilot 读取 AMH item | 中：IDE 上下文和 AMH context_pack 可能同时提供相似约束 |

## 冲突处理优先级

当 Agent 原生记忆、项目规则、AMH 记忆和当前现场证据冲突时，按下面顺序处理：

1. **当前用户消息优先**：用户本轮明确纠偏时，立即覆盖旧记忆。
2. **当前仓库 / 工具证据优先**：`git status`、源码、测试、日志、真实 API 返回比任何 memory 更权威。
3. **当前 repo 指令优先于旧记忆**：当前生效的 `AGENTS.md`、`CLAUDE.md`、Rules 是本仓库行为合同。
4. **AMH MemoryItem 优先于 Agent native memory**：AMH item 有 type、validity、refs、evidence、治理状态；native memory 多为本 Agent 本地提示。
5. **Agent native memory 是 hint，不是事实源**：可触发检索或提醒，但高风险判断要回查源码 / docs / AMH evidence。
6. **Explored / tool trace 不是长期结论**：除非提炼成 episode / artifact，否则不写入 AMH items。
7. **不确定时写 signal，不写 fact**：发现冲突但没解决时，写 `signal` 标明影响和期望操作；不要把猜测固化成事实。

## AMH 集成原则

| 原则 | 说明 | 反例 |
|---|---|---|
| 分层接入 | awareness、tool、automatic hook、fallback 四层分开证明 | 只配了 MCP 就宣称“自动记忆已接入” |
| Evidence 先行 | 原始 transcript / hook payload / runtime event 进 evidence，不直接当 MemoryItem | 把整段聊天直接写成 fact |
| 结论才跨 Agent | 只有经过提炼的 fact / decision / episode / artifact / signal / handoff 才进入共享脑池 | 把 Codex `Explored` 列表原样共享给 Claude |
| 注入可回放 | `<agent_brain>` 应包含 item id、keywords、detail_uri 或 hook ledger | 只说“我记得以前做过” |
| 原生记忆降级为候选 | Codex/Claude/Cursor/Qoder 自己记住的内容可以触发 search，但不能绕过 AMH governance | native memory 说“总是跳过测试”就直接跳过 |
| 冲突要显式化 | 发现 native memory 和 AMH item 冲突时，写 feedback / signal / superseding item | 静默按其中一边执行 |

## 需要持续补全的产品事实

| 待核对象 | 要核什么 | 推荐来源 | 状态 |
|---|---|---|---|
| Codex `Explored` | UI 展示是否有持久化、是否可导出为 evidence | Codex manual / 本机 runtime | 待补实验 |
| Codex Memories | task-level 控制、文件结构、和 `features.memories` 的精确交互 | OpenAI Codex Memories 文档 + 本机 `.codex/memories` | 初步确认 |
| Claude Code auto memory | 存储目录、加载上限、subagent memory 边界 | Claude Code memory docs + 本机 Claude 配置 | 初步确认 |
| Cursor Memories | 官方当前能力、作用域、和 Rules 的关系 | Cursor docs / product UI | 待核 |
| Qoder/QoderWork memory | 自带 memory 是否产品化、hooks 注入格式、和 QoderWork plugin 的关系 | Qoder docs / 本机配置 / AMH adapter doctor | 待核 |
| Wukong/Hermes | provider tools、workspace artifact、sidecar 和 AMH 的真实边界 | 内部 repo / runtime evidence | 待核 |

## 建议的后续工作

1. 已加 docs truth contract，锁住“Explored 不是 memory”“native memory 不是 AMH truth”这两个口径。
2. 已在 adapter capability / doctor JSON / Web Admin Agent 管理页接入首版 `memory_boundary`：`amh_role`、`native_memory_role`、`native_memory_state`、`native_memory_observed`、`explored_trace_role`、`last_injection`、`priority_order`、`evidence_layers`、`conflict_policy`。其中 `last_injection` 来自 runtime injection cohort ledger，只暴露 cohort/session/cwd/item_count/token 这类机械摘要。`native_memory_observed` 在 capability 中保守为 false；在 doctor JSON 中只有成功的 native memory bridge 诊断会置 true。
3. 所有 adapter doctor report 已传入 brain_dir，因此 `last_injection` 可按当前 brain 的 runtime ledger 回填到 doctor JSON；Qoder/Wukong 这类已有 native memory bridge 诊断的 adapter，会在诊断成功时把 `native_memory_observed` 置 true。
4. 下一步继续细化 Web Admin action result：把 doctor 返回的 native bridge 观测结果、project rules、runtime observed、last injection 分层展示。
5. 给 `/remember` 或 WriteService 增加提示：从 Agent native memory / explored trace 归档时，必须选择 MemoryItem type，并写来源边界。
6. 后续可以把本文拆成两张图：一张“Agent 原生上下文层”，一张“AMH 共享事实层”，中间用 hooks / MCP / profiles / evidence 连接。

## 当前来源

- OpenAI Codex AGENTS.md 文档：Codex 会在工作前读取 `AGENTS.md`，按全局、项目、目录层级合并，近目录覆盖远目录。
- OpenAI Codex Memories 文档：Codex local memories 需启用，存于 Codex home，后台从符合条件的任务生成，并可用 `/memories` 做 task-level 控制。
- OpenAI Codex Hooks 文档：hooks 是 Codex 生命周期扩展点，可用于日志、prompt scan、生成持久记忆、turn 停止时校验等。
- Claude Code Memory 文档：`CLAUDE.md` 与 auto memory 都在会话开始加载；它们是 context，不是强制配置；强制阻断应使用 hook。
- Claude Code Hooks 文档：hooks 会在 session、turn、tool call 等 cadence 触发。
- Cursor Rules 文档：Rules 提供持久指令，支持 Project / Team / User Rules 和 AGENTS.md。
- Qoder Hooks 文档：Qoder IDE / JetBrains plugin hooks 支持关键 Agent 执行点，且相对 prompt instructions 更确定。
- AMH 本仓库 README、adapter boundary 图和已写入 memory items：AMH 把 awareness、tool、automatic hook、fallback 分层证明，MemoryItem 与 evidence 分层治理。
