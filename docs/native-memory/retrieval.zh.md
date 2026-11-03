# 组织记忆检索与按需阅读

本文说明 AMH 1.4.0。已安装客户端需要升级并重新打开会话，才会加载新增组织工具。

## 共享什么

新加入的设备默认只同步原生记忆。聊天记录、规则、技能和配置需要分别确认启用；勾选某类历史文件时，也会说明该类后续新增内容的同步范围。

```sh
amh-native scope                       # 查看当前范围、历史选择与暂停队列
amh-native scope --kind memory         # 确认后仅保留记忆同步
amh-native scope --kind memory --kind skill
amh-native scope --none                # 确认后暂停所有类型
```

旧配置保留原范围，并明确显示为旧配置；不会在升级时悄悄改变共享选择。调整范围会等待正在运行的整轮上传结束，不能中途撤回已开始的上传。关闭类别保留其队列和选择，也不删除已经上传的内容；再次启用后可继续同步。

记忆目录中的文件还会区分为主题条目、导航、会话摘要和原始提取材料。角色说明用途，不代表内容已被核实。默认组织工具检索主题条目与导航；需要追溯过程时，才显式扩大到支持材料或聊天记录。

## 两条读取路径

1. **Hook 快速补充**：保持有时间限制的起始检索，少量资料进入当前上下文，不在 Hook 内运行多轮模型推理。
2. **组织只读工具**：由当前产品里的 Agent 搜索、按版本读正文、沿明确关系继续查找，受到调用量和读取量限制。

工具需要客户端正常启用对应 MCP；仅升级 Hub 不会自动给产品增加工具。

| 工具 | 用途 |
|---|---|
| `search_org_memory` | 返回有界摘录、来源和资产版本；首搜生成 `research_id` |
| `read_org_memory` | 根据该研究返回的 ID 和版本分页读完整正文 |
| `related_org_memory` | 查看明确引用、派生关系和历史版本元数据 |

一次问题复用同一 `research_id`，最多3次不同搜索、12次工具调用、32000字节正文与摘录，10分钟有效。同一问题或同一页不会重复读取；没有新证据、权限被拒绝或达到预算时停止，明确说明缺失内容。正文中的指令只作为资料，不执行。

搜索摘录不是全文：字节预算、字符偏移、分片范围和截断状态均显式返回。`version_verified` 只表示来源版本与内容完整性经过校验，不表示业务事实正确。

## 混合检索

检索先根据设备身份限定组织及资源范围，再执行关键词和可选的语义召回，通过 RRF 合并排名；可选 MMR 减少重复候选。RRF/MMR复用公共组件，旧 AMH 的入口继续兼容，不读取旧脑池、不启动第二套记忆写入流程。

语义模型只在服务端运行，只加载已有本地模型，不自动下载，不使用哈希向量冒充语义检索。未配置模型、没有索引或索引落后时，响应会明确说明降级原因。默认未启用语义模型；设置环境变量或启动参数不等于索引已经建好。

```sh
# 在有权访问此数据库的维护环境内，以已授权组织身份构建派生索引
amh-native semantic-index --db /var/lib/amh-native/hub.sqlite \
  --state /secure/amh-indexer-profile \
  --model /opt/amh-models/multilingual-minilm

# 资源足够且经过并发验收后才启用独立周期刷新；2GB主机勿与查询模型并行
# 仅重新编码新增或变化内容
amh-native semantic-index --db /var/lib/amh-native/hub.sqlite \
  --state /secure/amh-indexer-profile \
  --model /opt/amh-models/multilingual-minilm --watch --interval 60
```

完成索引后退出维护进程，再在服务端启用查询模型：

```sh
amh-native serve --db /var/lib/amh-native/hub.sqlite \
  --semantic-model /opt/amh-models/multilingual-minilm \
  --mmr-lambda 0.85
# 可选：--no-decay 关闭衰减重排；--hopfield --hopfield-min-score 0.35 开启联想扩召
```

索引默认只编码主题记忆与导航，不把聊天、会话摘要和原始提取材料全量载入模型；可用 `--kind` 和 `--include-evidence` 明确扩大范围。缩小索引范围不会删除源资料，超出已建范围的检索会提示降级。

凭证也可由维护进程的私有环境变量 `AMH_NATIVE_INDEX_TOKEN` 提供，不要写入命令参数、插件包或日志。凭证撤销后维护停止。模型计算不占用 SQLite 写事务；提交前再次核对源版本，删除或更新不会继续使用旧向量。索引绑定模型文件摘要、维度和分窗版本，不匹配就要求重建。配置模型的服务启动时先加载模型再接受请求，部署健康检查应预留冷启动时间。

当前实现扫描已授权范围内的向量，并非 ANN；大语料、并发和内存使用需要单独验收。查询服务与独立索引维护进程会各持有模型，不应在未验证内存余量的低配主机上同时启用。重排器已有服务端注入接口，尚未验证真实重排模型的业务效果。

## 排序阶段、衰减与召回明细

从 1.4.1 之后的开发版起，`retrieve` 的阶段顺序固定为：

```
SQL 作用域过滤 → 关键词 ‖ 语义 → RRF 合并 → Hopfield 联想（只追加两条路都没召回的近邻）
 → 支持材料 ×0.55 → 重排器（未接线时显示「未启用」） → 声明式过滤 → 衰减与信号 → MMR
 → 精确 ID 置顶 → 版本复核、去重、截取条数
```

- **知识性质与来源层级**：每条候选按 `rule / preference / project_state / fact / decision / reference / episode / unknown` 分类，并注明依据是 `declared`（frontmatter 明说，如 Claude Code 的 `metadata.type`）、`parsed`（按位置或角色推断，如 rule 目录、导航文件、会话摘要）还是 `inferred`（正文弱特征，只给临时状态和决定两类）。
- **可信时间**：内容产生、源文件更新、云端收录、最近送达、最近采用、最近确认、声明失效分别记录来源；拿不到就是「未知」，不会用收录时间冒充内容时间。源文件修改时间（mtime）只作最后兜底，排在 frontmatter 声明的时间和文件名日期之后，因为 `git clone`、复制都会改写它；mtime 也不参与内容哈希，只改 mtime 不会产生新版本。客户端上传时随正文带上 `source_meta`（文件修改时间、frontmatter 子集、`[[wikilink]]`、标题），老 Hub 不认该字段时自动去掉再传。
- **衰减与信号**：按知识性质取半衰期（规则 365 天、偏好/参考 180、决定 90、事实/未知 60、会话 30、临时状态 21），结合送达、采用、支持、反对、人工确认等信号算系数，只做小幅重排（下调最多 5%，上调最多约 3%），不丢弃；时间未知按中性 0.5 处理。投递类信号按状态幂等，同一成员对同一候选的同类反馈只计一次；内容出新版本时反对计数清零。`serve --no-decay` 或环境变量 `AMH_NATIVE_DECAY=0` 关闭（命令行参数优先于环境变量）。
- **声明式退场**：只有 frontmatter 明确写了 `superseded_by` 或已过 `expires` 才会过滤，精确 ID 查询不受影响；明细里记录被谁替代、何时失效。`superseded_by` 指向的目标必须在本组织内存在且未删除：指向不存在的 ID、指向自己、两条互相指向时都不过滤，明细标 `superseded_target_missing` / `superseded_self` / `superseded_cycle`。纯日期写法的 `expires: 2026-11-03` 按当天结束（UTC）失效。
- **Hopfield 联想**：`serve --hopfield`（或 `AMH_NATIVE_HOPFIELD=1`）后，由融合结果形成吸引子，在已建语义索引的全部授权向量上找两条路都没召回的近邻；默认关闭，因为是全量扫描。关闭时明细显示「未启用」；打开但本次没有可用种子（融合结果里没有一条带索引向量的命中，词法命中有向量也能当种子）时显示 `no_seeds`。`--hopfield-min-score 0.35` 与 `--hopfield-beta 8.0` 是合成语料上的初值，没有在真实数据上校准，开启前应先用自己的语料验收。
- **召回明细**：每次检索生成 `trace_id`，服务端保留 30 天，不含正文；记录每条候选的召回方式（lexical / semantic / hopfield 名次）、各阶段前后名次与分数、本轮状态（候选 → 入选 → 已打包 → 已注入 → 模型已收到 → 回答已采用，或 dropped:limit / dedup / budget / covered / superseded / expired / revalidation）。Hook 在本机记录打包与注入状态，并在下一次 Hook 看到回执时记录「模型已收到」，由 worker 同步时批量上报。
- **反馈**：`amh-native recall-feedback --trace <id> --asset <id> --revision <n> --event cite|support|contradict|confirm` 或 `POST /native/v1/recall/feedback`；只能对该次 trace 里出现过的候选反馈。MCP 工具保持只读，不提供反馈。

```sh
amh-native recall-trace                     # 本设备最近召回：时间/通道/问题/候选/入选/送达/阶段/trace
amh-native recall-trace --trace <trace_id>  # 展开：排名、标题、内容形态、来源产品、项目、原生产者、召回方式、本轮状态、时间
```

管理员可在 `/native/v1/admin/recalls` 查看全组织记录（与看板同一管理令牌或飞书会话，数据接口是 `/native/v1/admin/recalls/data`，可按成员、通道、日期筛选）；`/native/v1/admin/insights` 把账本按天汇总成投放漏斗、各通道与成员次数、记忆复用和知识缺口，原始明细按整天过期、过期前先冻结当天汇总，所以汇总比 30 天账本活得久。注意隐私：Hook 通道把用户的提示词当查询，明细默认保留其前 120 字、存 30 天、管理员可见。关闭方法：

```sh
curl -X POST https://<hub>/native/v1/admin/settings \
  -H 'Authorization: Bearer <管理令牌>' -H 'Content-Type: application/json' \
  -d '{"key": "recall_query_preview", "value": "off"}'
```

关闭后新旧记录都只返回摘要哈希；值只接受 `on` / `off`。

Hook 现在走 `POST /native/v1/recall`：与组织工具同一套混合排序，只取记忆与规则两类资产，不再把聊天记录切片当候选；响应只含 Hook 用到的字段，正文每条最多 64KB、合计最多 512KB（超出的截断并标 `body_truncated`）。老 Hub 或网关返回 404 / 405 / 501 时回退到原来的 `/search`。合成语料的机制检查见 `benchmarks/native_recall_parity.py`，它并列跑两组语料（噪声不构成竞争 / 噪声正文各不相同且与目标同角色），只验证各阶段按设计生效，不是真实记忆上的质量证据；日期相对运行当天生成，结果不随日历漂移。

## 正文与关系

读取固定当前版本；翻页期间文档变化时返回 `stale_version`，需要重新搜索，不能拼接新旧页。多分片必须来源一致、顺序完整且内容摘要正确。单文档上限8MiB，分页和整次研究另有更小预算。

关系查询只使用 Markdown 中的明确链接、上传记录中的 `upstream` 和已提交的版本记录，不自动推断项目负责人或业务因果。每一跳都重新验证权限，历史节点仅返回元数据，不提供已失效正文。`upstream` 原本只绑定资产 ID，因此当前目标版本只是查询结果，不是派生发生时的版本证明。

## 接入与验证边界

Codex CLI 与本地 Codex 桌面模式共用 MCP 配置；Claude Code 使用用户级 MCP；macOS 宿主绑定的 Cowork 插件声明本机 stdio MCP。凭证留在各自私有状态目录。未绑定宿主的通用 Cowork 包不声明组织工具已可用；普通 ChatGPT 聊天与云端 Work 不在此接入范围内。

协议回归、配置解析和插件校验不能代替产品实机验收。小型真实模型评测与已知失败见[评测说明](../evaluation/native-hybrid/README.md)；宿主工具循环可用 `benchmarks/native_agent_tools_smoke.py` 在合成数据和独立 Codex 配置中复现。只有模型实际读取了需要的证据并正确回答，才算对应场景通过。

上线顺序：备份与校验数据库 → 更新 Hub → 验证新接口 → 在资源限制下验证模型和索引 → 更新客户端及插件 → 逐端验收。旧客户端的搜索接口保留。回退时保留原始资产与变更记录，停用新增客户端接入和模型配置，切回原服务代码；不要把旧数据库覆盖到仍有新增数据的生产环境。
