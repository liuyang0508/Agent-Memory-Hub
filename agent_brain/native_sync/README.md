# 组织记忆：存储与同步

适用于 AMH 1.4.1。使用方法和验证边界见[检索与按需阅读](../../docs/native-memory/retrieval.zh.md)；旧版行为见 [1.3.3 文档](https://github.com/liuyang0508/Agent-Memory-Hub/blob/v1.3.3/agent_brain/native_sync/README.md)。

组织记忆分为三层：产品维护原生记忆，AMH 客户端记录同步状态，云端 Hub 保存共享内容。上传的是文件内容及来源信息，不是产品数据库或整个目录的镜像。

| 层次 | 存储 | 用途 |
|---|---|---|
| 产品原生记忆 | Markdown；Codex 另有内部 SQLite | 由产品生成、维护和读取 |
| 本机 AMH | `state.sqlite` | 记录共享选择、文件版本、待上传内容及召回状态 |
| 云端 Hub | `hub.sqlite` | 保存组织内容、来源、版本、全文检索索引及可选的派生语义索引 |

新加入设备默认仅同步原生记忆；聊天、规则、技能和配置需明确启用。旧配置保留原范围并提示审查，可通过 `amh-native scope` 查看和调整。

## 产品原生记忆

以下为 macOS 默认路径示意，`~` 表示用户主目录；实际位置可能随版本和配置变化。

```text
~/.codex/                              # Codex CLI 与本地 Codex 桌面模式
├── memories/                          # AMH 读取的记忆文件
│   ├── memory_summary.md
│   ├── MEMORY.md
│   ├── raw_memories.md
│   └── rollout_summaries/
├── memories*.sqlite                   # 产品内部记忆处理数据库
├── state_*.sqlite                     # 产品会话、项目等状态
└── sessions/                          # 会话记录，与记忆分开处理

~/.claude/projects/<项目>/              # Claude Code
└── memory/
    ├── MEMORY.md
    └── <主题>.md

~/Library/Application Support/<Claude 或 Claude-3p>/
└── local-agent-mode-sessions/<运行目录>/ # Cowork，布局依运行环境而定
    ├── memory/memory/MEMORY.md
    └── spaces/<空间 ID>/memory/
        ├── MEMORY.md
        └── <主题>.md
```

- **Codex CLI / ChatGPT 本地 Codex 模式**：共用本机 Codex 存储，AMH 按来源路径去重。`memories*.sqlite` 可包含记忆提取结果、处理任务和汇总进度；AMH 不读取或上传这些内部数据库。
- **Claude Code**：AMH 读取项目 `memory/` 中的 Markdown，不依赖产品内部数据库。
- **Cowork**：插件以运行环境提供的 `CLAUDE_COWORK_MEMORY_PATH_OVERRIDE` 为准发现记忆目录，不固定套用示例路径。

这里的 ChatGPT 接入指**桌面应用中的本地 Codex 模式**，不代表普通 ChatGPT 聊天或云端 Work 已接通。

## 本机 AMH

安装后的默认布局如下；Cowork 运行时目录可通过配置调整。

```text
~/.amh-native/venv/                    # 程序运行环境
~/.amh-native-sync/
├── config.json                        # Hub 地址、设备身份及配置
├── state.sqlite                       # 主客户端同步数据库
├── install-receipt.json                # 安装修改与恢复记录
├── status.json                        # 最近同步状态
├── hook-metrics.jsonl                 # Hook 指标
├── cowork-plugin/                     # 生成的插件
├── cowork-connection/                 # 配对文件，独立于插件包
└── cowork-runtime/
    ├── config.json
    └── state.sqlite                   # Cowork 独立同步状态
```

主客户端服务已接入的 Codex 和 Claude Code；Cowork 使用独立运行时身份和状态。

| `state.sqlite` 主要表 | 内容 |
|---|---|
| `sources`、`snapshots` | 来源目录、共享选择、文件哈希和资产关联 |
| `source_parts`、`outbox` | 本机内容分块、待上传正文及版本 |
| `lineage_records`、`recall_sources` | 组织内容的来源与版本元数据 |
| `coverage`、`native_proofs`、`emitted` | 会话读取、投递和去重记录 |
| `replica` | 显式开启 `--projection` 后保存的组织正文副本 |
| `meta` | 同步游标和运行状态 |

默认不保存其他成员的正文副本，但自己的内容分块和待上传正文会保存在本机。召回内容仍可能进入产品自己的会话记录。

## 云端 Hub

默认部署使用独立 SQLite 数据库，不复制各产品的内部 DB。

```text
/opt/amh-native/
├── current -> releases/<版本>/        # 当前服务程序
└── releases/
/var/lib/amh-native/
├── hub.sqlite                         # 组织数据库
└── deployed-revision                  # 部署版本记录
```

| `hub.sqlite` 主要表 | 内容 |
|---|---|
| `assets` | 资产分块当前版本：组织、资产 ID、版本、来源设备及 JSON 正文 |
| `changes` | 已提交的变更记录 |
| `search_index` | FTS5 全文检索索引；旧搜索接口继续兼容 |
| `native_semantic_meta`、`native_semantic_vectors` | 显式构建后保存模型描述和当前版本的派生向量，可重建；不替代源资料 |
| `members`、`devices` | 成员和设备身份 |
| `invites`、`runtime_invites` | 加入与配对状态 |
| `feishu_*`、`admins`、`settings` | 登录、管理及服务配置 |

各产品内容在同一 Hub 中按组织、成员、设备、产品和来源区分。SQLite 使用 WAL 模式，运行期间可能出现 `-wal`、`-shm` 辅助文件。

## 上传与召回的时机

```text
产品写入记忆 → 后台扫描变化 → 本机待上传队列 → HTTPS → Hub
用户提交问题 → Hook 查询 Hub → 去重、选取片段 → 当前会话上下文
                 └→ 同时唤醒后台检查变化
```

- 安装前已有内容需勾选共享；未选择的旧文件不会因为后续修改而自动上传。
- 后台默认每轮完成约 10 秒后再检查，Hook 可提前唤醒；只同步允许共享的变化，不在每次提问时重传全库。
- **上传和查询不互相等待。** 本地更新为 V2、云端仍为 V1 的短暂窗口内，查询可能取到旧版本；下一轮成功同步后收敛。
- 默认不会用云端旧内容覆盖原生记忆。上传失败保留队列重试；两次扫描之间的每个中间修改版本不保证留存。
- 默认按问题从云端召回，不自动把整批组织记忆写入各产品。`--projection` 可显式启用本地组织资源，但不会关闭 Hook 查询。

安装步骤见[用户手册](https://github.com/liuyang0508/Agent-Memory-Hub/blob/v1.4.1/docs/native-memory/user-guide.zh.md)，技术边界见[架构说明](https://github.com/liuyang0508/Agent-Memory-Hub/blob/v1.4.0/docs/native-memory/architecture.zh.md)，故障处理见[运维说明](https://github.com/liuyang0508/Agent-Memory-Hub/blob/v1.4.0/docs/native-memory/operations.zh.md)。

组织只读工具支持搜索、固定版本正文分页和明确关系查询；循环由宿主 Agent 执行，不放进 Hook。未配置真实模型或未构建索引时明确降级，不能将代码支持等同于语义召回已启用。
