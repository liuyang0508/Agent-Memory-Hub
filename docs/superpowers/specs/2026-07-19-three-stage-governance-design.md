# Agent Memory Hub 三阶段治理设计

> 日期：2026-07-19
>
> 状态：Approved for planning
>
> 适用分支基线：`codex/dual-route-recall` / `a5f15cd`
>
> 治理顺序：可靠性、安全与发布门禁 → 召回质量增强 → 多 Agent 产品化

## 1. 背景

双路召回、Recall Admission、Gateway、ContextFirewall 与 consolidated hook preflight
已经完成代码和发布证据闭环。关键词不再由 Agent 先主观切成 3–5 个词；自动 hook
把原始 prompt 交给确定性 QuerySignal、语义路线和关键词路线共同处理。显式
`--project` 仍是调用者授权的硬过滤边界。

下一阶段不能继续把全部问题归为“召回算法问题”。当前代码基线仍存在五类经过实时
核验的治理债务：

1. Qoder transcript JSONL 行若解码为字符串，diagnose 路径会对字符串调用 `.get()`；
2. MemoryData 进程退出码为 0、但没有生成结果文件时，runner 仍可能判为 passed；
3. WebSocket 与 SSE 把 JWT 放进 URL query，默认代理或 ASGI access log 可能记录令牌；
4. `HubIndex` 有 `close()`，但 `WriteService`、SDK components 与部分 CLI 调用缺少统一、
   可组合的资源生命周期合同；
5. Docker 使用未定义的 `all` extra，Web 启动依赖与镜像安装合同不一致；GitHub `main`
   未启用 branch protection，类型检查仍允许失败，核心门禁与外部镜像发布未分层。

因此后续治理采用严格顺序。前一阶段没有通过退出门禁时，不得把后一阶段能力放入
默认产品路径。

## 2. 目标与非目标

### 2.1 目标

- 让“通过测试”“通过 benchmark”“adapter 已验证”“发布成功”都能追溯到充分证据；
- 消除会造成崩溃、假绿、凭据泄露和长进程资源增长的已知风险；
- 把召回评测从单一 R@K 扩展为 retrieval、answerability、temporal、abstention 四类；
- 把真实错召回、漏召回和防火墙拒绝转成可重放、可分层的回归语料；
- 统一多 Agent 的安装、配置、诊断、运行观察、注入验证、修复与升级合同；
- 保持本地优先、Markdown 事实源、派生索引、Gateway 单一注入出口的现有架构边界。

### 2.2 非目标

- 不在阶段一重写整个检索栈或 adapter 架构；
- 不把 E5、cross-encoder、LLM query rewrite 直接设为默认能力；
- 不用跨项目 shadow candidate 绕过显式 `--project` 硬过滤；
- 不把原始 transcript 当作长期知识条目自动注入；
- 不以“存在 adapter 文件”替代真机 runtime evidence；
- 不为了追求全绿而删除安全边界、放宽 Gateway 或缩小真实验证范围。

## 3. 总体治理架构

### 3.1 四类事实源

| 事实 | 权威来源 | 派生视图 |
|---|---|---|
| 长期知识 | `items/*.md` | SQLite/FTS/vector、Wiki、图谱 |
| 原始会话证据 | `sources/conversations/` | harvest draft、evidence span |
| 运行行为 | `runtime/*.jsonl` 与 adapter verification record | doctor 汇总、Web 面板 |
| 发布质量 | committed fixture、测试输出、CI run、release artifact | readiness 文档、dashboard |

任何派生视图都不能反向覆盖事实源。运行证据和发布证据必须带时间、代码 provenance、
配置和样本边界，过期证据不能继续支撑 verified 声明。

### 3.2 三种失败策略

1. **Fail closed**：认证、租户隔离、敏感信息、benchmark 完整性、发布 provenance；
2. **Graceful degradation**：可选 embedding、外部模型、非关键 enrichment；
3. **Explicitly blocked**：缺数据集、缺凭据、缺真机 runtime evidence，不得伪装成 passed。

### 3.3 阶段门禁

每阶段必须同时具备：

- 代码证据：实现和兼容边界；
- 测试证据：失败样本先红、修复后绿，覆盖异常路径；
- 运行证据：真实命令、真实产物、真实资源或延迟指标；
- 发布证据：升级说明、回滚方式、CI/GitHub 状态。

## 4. 阶段一：可靠性、安全与发布门禁

### 4.1 输入和 adapter 诊断健壮性

所有 transcript、配置和 runtime JSON 都按不可信输入处理：

- JSON 解码后先验证容器类型，再访问 `.get()`、`.items()` 或嵌套字段；
- 单行损坏、JSON scalar、超大行、未知事件类型只降低该条证据可信度，不使 doctor 崩溃；
- transcript 内部时间缺失时才回退文件 mtime；无效内部时间不得污染排序；
- Qoder 与 QoderWork 共享解析合同时复用同一 defensive reader；
- 增加 string、number、null、list、malformed JSON、BOM/CRLF 的参数化回归。

### 4.2 外部 benchmark 必须 fail closed

MemoryData runner 的 passed 判定必须同时满足：

- 子进程退出码为 0；
- 本次 run 使用隔离 artifact 目录或唯一 run id，不能读取旧产物；
- 至少发现一个符合预期 schema 的结果 artifact；
- 结果行数达到本次计划的 expected count，或明确记录合法的 closed-set empty reason；
- 没有 failed、error、missing、timeout 行；
- 结果中的 family、run level、agent config 与执行计划一致；
- run record 最后原子写入，未完成运行不能留下可被 dashboard 当成 passed 的半成品。

smoke 与 full 必须分开。检索 R@K、answer generation 和 judge 不得共享一个笼统的
“benchmark passed”标签。

### 4.3 实时通道认证与日志安全

Web API 继续支持 Authorization Bearer。浏览器 WebSocket/SSE 改为以下合同：

- 同源 Web Admin 优先使用 `HttpOnly + Secure + SameSite=Lax` 会话 cookie；
- 非 cookie 客户端先用 Bearer 换取短时、单用途 connection ticket；
- 长期 JWT 不再放入 WebSocket/SSE URL；
- 旧 query token 仅允许显式兼容开关和短迁移窗口，默认关闭；
- access log、异常日志、前端调试信息和连接关闭 reason 均不得包含 token；
- ticket 绑定 tenant、role、过期时间和用途，验证后立即作废。

安全回归需要证明：URL、日志、异常、repr、metrics 中都不存在测试令牌 sentinel。

### 4.4 资源生命周期合同

资源所有权采用“创建者负责关闭”：

- `HubIndex.close()` 幂等，并实现同步 context manager；
- `WriteService.close()` 只关闭其拥有的 index，不关闭调用者注入的共享资源；
- `ClientComponents.close()` 与 SDK `Client.close()` 释放缓存资源；
- CLI、MCP、benchmark、Web lifespan 对各自创建的资源使用 `try/finally` 或 context manager；
- 不依赖 CPython GC 或 sqlite connection destructor 作为正常关闭机制；
- 增加重复 write/search/doctor/SDK client 循环的 FD 稳态回归，并验证异常路径同样归零。

### 4.5 Docker 与服务启动合同

- 定义真实的 `all` extra，或在镜像中显式安装 `.[web,embeddings]`；不得用 core-only
  fallback 启动依赖 FastAPI/uvicorn 的默认服务；
- 安装失败必须使 image build 失败；
- healthcheck 请求真实 HTTP health endpoint，并区分进程存活、存储可写、索引可用；
- 提供 no-model/core-only 独立镜像或启动 profile，而不是隐式缺依赖；
- Docker smoke 至少覆盖 build、serve、health、一次受认证 API 调用和持久卷重启。

### 4.6 CI、分支保护与发布任务分层

核心 required checks：

1. lint；
2. type check；
3. Python 3.11/3.12 unit + conformance；
4. hook tests；
5. security-focused tests；
6. Docker smoke；
7. benchmark integrity smoke。

治理要求：

- 清理 mypy 基线后移除 `continue-on-error`；
- 为 `main` 启用 branch protection，要求 PR、required checks 和非过期 review；
- Gitee mirror、npm publish、官网部署属于 distribution checks，不作为普通 PR 核心质量门禁；
- tag release 必须同时要求核心门禁和对应 distribution preflight；
- 外部 secret 缺失显示 blocked/skipped-with-reason，不得显示代码测试失败或静默 success；
- release artifact 保存 checksum、版本、commit SHA、Python/平台和 adapter hook provenance。

### 4.7 阶段一退出门禁

- Qoder/QoderWork 对所有 JSON scalar 和损坏行不崩溃；
- MemoryData 空产物、旧产物、少行、失败行、schema 错误均判失败；
- WebSocket/SSE URL 与日志不含长期 JWT；
- 资源压力测试证明循环操作后 FD 回到允许的稳定窗口；
- Docker 从空缓存构建并通过真实服务 smoke；
- 本地全量测试和 GitHub required checks 全绿；
- `main` branch protection 已生效；
- readiness 文档列出升级、兼容和回滚命令。

## 5. 阶段二：召回质量增强

### 5.1 指标分层

| 层 | 回答的问题 | 核心指标 |
|---|---|---|
| Retrieval | 是否找到了正确证据 | R@K、MRR、FP/FN |
| Admission | 是否应该进入候选 | accepted/rejected reason、误拒率 |
| Answerability | 证据是否足以回答 | supported/partial/insufficient |
| Temporal | 是否使用了当前有效事实 | stale/superseded/contradiction accuracy |
| Abstention | 不知道时是否拒答 | abstention precision/recall |
| Injection | 最终是否安全注入 | firewall include/exclude、token cost |

禁止用 Retrieval R@K 代表端到端回答正确率。

### 5.2 可重放真实语料

- calibration、heldout、production replay 三套数据严格分离；
- 将 recall gap、firewall rejection、empty recall、wrong project、unused/contradicted
  outcome 聚合为去敏 cohort；
- 每个 case 保存预期 item、禁止 item、项目边界、时序状态、admission 与 injection 预期；
- 真实长中文、中英混合、代码、日志、命令型、多语言、多模态分别统计；
- corpus 只能追加或显式版本化，不能为了让当前算法过关而覆盖历史失败样本。

### 5.3 项目边界治理

- 自动 hook 不要求 Agent 先猜 project；
- 显式 `--project` 继续是硬过滤；
- 可增加 cross-project shadow route，只记录候选和原因，不直接注入；
- shadow 命中可产生“可能项目不匹配”诊断，但不得自动扩大授权范围；
- 项目 alias、repo remote、cwd、worktree 和 memory item project 形成可解释的 scope evidence。

### 5.4 时序、冲突与答案充分性

- supersedes 链、validity window、source time、event time 分开处理；
- temporal resolver 输出选择原因，而不是只降低旧条目分数；
- 冲突证据无法消解时触发 abstention 或同时展示边界；
- answerability 只评估 Gateway 允许的候选，不读取被 ContextFirewall 排除的内容；
- multi-session 与 knowledge update 建立独立回归，不混入普通事实查询平均分。

### 5.5 反馈闭环和可解释性

- 成功注入记录 query signal、route、item id、admission reason、Gateway reason 与 pack 视图；
- 失败路径记录封闭枚举和 hash，不存原始敏感 prompt；
- 用户采用、未采用、纠正、矛盾反馈更新 gain，但最终 access count 只记录 Gateway 注入项；
- 提供按 adapter、项目、语言、case type 的日报/周报；
- 每次默认算法变化必须生成旧/新差异报告和新增回归 case。

### 5.6 模型增强准入

E5、cross-encoder、LLM query rewrite 或新 reranker 只有同时满足以下条件才可默认启用：

- heldout 与 production replay 都有显著收益；
- FP、安全负样本和跨项目污染不恶化；
- 冷启动、p95/max、内存和离线可用性在预算内；
- 模型不可用时确定性路径行为一致；
- 两轮 fresh brain 正式验证通过。

### 5.7 阶段二退出门禁

- 指标分层和数据集分层均有机器可读报告；
- 真实 replay cohort 无未解释回归；
- temporal、multi-session、abstention 达到规格中预先冻结的阈值；
- 41-case 安全夹具和关键词/QuerySignal corpus 继续全绿；
- 两轮 fresh brain 性能验证覆盖 p50、p95、max、error、timeout、fallback；
- 所有增强仍经过 Recall Admission、Gateway 与 ContextFirewall。

## 6. 阶段三：多 Agent 产品化

### 6.1 版本化 capability manifest

每个 adapter 声明：

- adapter id、版本、支持平台和客户端版本范围；
- hook events、payload schema、output protocol；
- MCP/CLI/awareness channel 能力；
- install、verify、doctor、repair、upgrade、uninstall 命令；
- runtime evidence 类型和有效期；
- feature flag、降级方式与回滚方式。

状态统一拆为：

1. `implemented`；
2. `installed`；
3. `configured`；
4. `doctor_passed`；
5. `runtime_observed`；
6. `context_injected`。

对外 `verified` 至少需要 4–6 全部满足且证据未过期。

### 6.2 生命周期合同

- install 幂等，只修改 hub-owned 配置块；
- verify 检查静态文件、配置顺序、协议和 hook hash；
- doctor 区分 error、warn、optional、stale evidence；
- repair 只修复明确归属 AMH 的漂移，不覆盖用户自定义 hook；
- upgrade 先检查包版本，再刷新 hook/adapter，并保留可回滚备份；
- uninstall 只移除 hub-owned 内容；
- 所有命令支持机器可读 JSON 和稳定 reason code。

### 6.3 试点和推广顺序

1. Codex + Qoder：覆盖不同 hook/runtime evidence 形态；
2. Claude Code + QoderWork：验证同族差异和共享 awareness；
3. Wukong + OpenClaw：验证外部产品和 CLI registry；
4. 其余 adapter：按 capability manifest 和用户需求推进。

每一批先 shadow/diagnostic，再 canary，最后默认启用。单 adapter 故障不得影响 core
memory CLI/MCP 或其他 adapter。

### 6.4 安装升级和证据产品化

- 新安装、旧版本升级、hook repair、卸载各有独立 E2E；
- hook provenance 保存 package version、commit、hash、installed_at；
- Web/CLI 能展示当前状态、证据时间、缺失项和精确修复命令；
- 文档中的 adapter 数量和状态由 manifest/verification record 生成，避免手工漂移；
- 发布采用 cohort 和 kill switch，出现召回/安全回归可单 adapter 回滚。

### 6.5 阶段三退出门禁

- Codex/Qoder 完成全生命周期真机验证；
- 第二批 adapter 通过同一合同，证明协议不是为单客户端特制；
- 状态文案全部能追溯到 manifest、doctor 与 runtime evidence；
- 新装、升级、修复、卸载在支持平台重复运行通过；
- package、GitHub Release、安装器、文档和 hook provenance 一致；
- 任一 adapter 关闭或失败时 core CLI/MCP 仍可用。

## 7. 可观测性与治理报表

三阶段共用以下低敏指标：

- hook：p50/p95/p99/max、timeout、fallback、protocol error；
- recall：route、admission、gap、FP/FN、project mismatch、empty recall；
- Gateway：included/excluded reason、pack tokens、selected view；
- lifecycle：open resource、FD delta、pending/review depth、stale/superseded count；
- adapter：installed/configured/doctor/runtime/injected、evidence age；
- release：commit、fixture hash、test run、artifact checksum、upgrade cohort。

原始 prompt、JWT、API key、私有 item 正文不得进入公开报告或普通 telemetry。

## 8. 发布与回滚

- 每阶段单独发布，不把三阶段积累成一次不可回滚的大版本；
- feature flag 必须定义默认值、owner、到期条件和删除条件；
- 数据/schema 改动先向后兼容读，再并行写，最后才迁移默认值；
- adapter/hook 升级保留上一版本备份与 provenance；
- benchmark、doctor 或安全门禁失败时停止 promotion，不自动回滚用户数据；
- 回滚只切代码、feature flag、adapter 配置和派生索引，Markdown 事实源不做破坏性回退。

## 9. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 阶段一范围膨胀成大重构 | 只处理已证实风险和必要合同，结构优化另建 backlog |
| 严格 benchmark 导致历史报告大量变红 | 保留历史 artifact，但重新标注 evidence tier，不伪造 passed |
| cookie/ticket 改造破坏旧客户端 | 短迁移开关、明确 deprecation、双路径 E2E |
| 资源 close 改造误关共享 index | 显式 ownership 标记和注入资源测试 |
| recall 指标优化过拟合 | calibration/heldout/replay 分离，冻结安全负样本 |
| adapter 状态大面积降级 | 真实降级为事实，不用文案掩盖；提供精确 repair 路径 |

## 10. 完成定义

“三个阶段完成”不是文档完成或测试数量增加，而是：

1. 阶段一所有退出门禁有当前 commit 的代码、测试、运行和 GitHub 证据；
2. 阶段二的分层质量指标、真实回放和 fresh brain 性能均达到冻结阈值；
3. 阶段三至少两批不同类型 adapter 通过统一生命周期合同和真机证据；
4. 全量回归、发布物、升级路径、回滚路径和文档彼此一致；
5. 最终 completion audit 对每条显式要求逐项给出权威证据，不用“未发现问题”代替证明。
