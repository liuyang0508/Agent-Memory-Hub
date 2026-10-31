# 组织原生记忆端云协同（研发分支）

本模块保存产品原生内容，提供 Hooks 上传、组织云端检索和本地原生读取。它不写旧 AMH MemoryItem，不运行旧提炼、治理或召回管线。默认成员可检索本组织所有项目；项目只参与排序，显式指定时才过滤。

## 状态

代码与回归位于本研发分支，尚未发布到公共安装入口。Claude Code 与 Codex CLI 已在独立配置中双向验证：由真实模型显式写入原生记忆、Hook/后台同步、另一成员经 Hook 或原生文件读取；关闭两条消费路径的对照未取得验收值。这不等于所有原生后台提炼时序均已覆盖。

Codex 桌面 UI 已由人工触发并完成核心验收：Hook 读取 Claude Code 来源、关闭 Hook 后读取原生索引与正文，以及原生更新备注自动上传后被 CC/Codex CLI 读取，均有实际会话证据。当前宿主仅允许新增 `extensions/ad_hoc/notes/` 更新备注，规范正文与索引的归并由产品自身完成；AMH 不绕过该写入契约。

两台真实 Mac 的跨成员原生读取及同步后的缓存 Hook 已通过。该 SSH 隧道试验中的直接查询约 1.0–1.6 秒，首次无缓存的 Hook 超过预算时返回空结果，不能保证首次实时召回。

组织试点 HTTPS Hub 已部署至 `https://aihub0508.com`，固定功能版本为 `e685c89`；正常 TLS、服务就绪、未授权访问拒绝和原官网均已验证。[部署记录](https://github.com/liuyang0508/Agent-Memory-Hub/actions/runs/41080945986)。生产库未导入验收数据或个人历史；成员用自己的飞书账号完成接入。这不代表公共 `latest` 安装入口已经发布本模式。

Claude Desktop 4.4212 的 Cowork 已通过官方第三方网关模式进行隔离项目实机验收：真实 Hook 读取 Claude Code 来源，关闭 Hook 后原生读取 Codex 来源，以及 Cowork 原生写入后被 CC/Codex CLI 读取，均取得模型回答和运行记录证据。CLI 读取的关闭召回对照未取得验收值。正常重启应用后，新会话仍能召回内容，成员、设备及状态配置未改变。模型负责原生记忆生成，AMH 负责同步与召回；这些证据不代表所有产品版本、自动提炼时序或云任务环境均已覆盖。

工程职责与扩展约束见 [架构说明](architecture.zh.md)，故障处理与恢复边界见 [运维说明](operations.zh.md)。当前是单节点可恢复实现，尚无跨主机自动容灾。

## 安装与成员登记

组织管理员先在 Hub 主机上运行独立服务：

```bash
amh-native serve --db /var/lib/amh-native/hub.sqlite --host 127.0.0.1 --port 8787
```

**没有邀请码。** 成员加入的唯一凭据是所属企业的飞书账号：装完脚本 → 浏览器里飞书登录 → 勾选要共享的本机记忆 → 完成。Hub 用飞书返回的 `tenant_key` 确认归属，同名且未关联的成员会被复用，不会重复建档。为既有成员增加设备，同样是再登录一次，用 `members` 可查稳定 ID。

在包含本功能的代码版本上安装客户端：

```bash
sh install.sh --org-memory --hub https://memory.example.com
```

`--native` 是发布前的旧名字，仍然有效。

`--hub` 也支持有有效 TLS 配置的 IP 地址与端口。安装脚本下载地址和组织 Hub 地址彼此独立，不绑定 aihub0508.com。前期可在我们的实例上验证，后续组织自建。HTTP 仅允许本机 loopback 测试；服务端生产部署使用 HTTPS 反向代理。

安装器检测并逐项显示 Claude Code、Codex CLI、Claude Cowork、实际检测到的 Codex/ChatGPT 客户端（ChatGPT 客户端记忆保存在账号服务端，本机无可导入目录，会如实说明）。飞书登录后显示成员名与稳定 ID，再预览历史和确认配置。ID 自动生成，不要求手填。

新模块使用 `~/.amh-native-sync/`，独立安装环境 `.native-venv/`。已有旧 AMH 的托管 Hook、纪律区块和已识别旧 MCP 通道会按安装回执隔离；用户其他配置保留。Codex Hook 使用生成处理程序的正常信任 hash，不使用绕过信任的启动参数。确认安装后会启用 CC 自动记忆及 Codex 原生读取所需开关；已有 Codex `generate_memories` 选择保持不变，原生生成由产品管理并可能使用产品额度。未选历史文件中的后续改写也会继续保留本地，直到重新选择；本模块不自动判定语义上的新旧知识。

## 历史预览与来源绑定

安装器在本机展示来源、条目数、正文和最终选择。未选择的历史不会上传；相同内容重命名/复制以及已观察到的历史版本继续保留排除指纹。全程预览不需要独立 Web 平台，也不会为了预览先上传原文。

也可显式绑定单个文件或目录：

```bash
amh-native bind --product claude-code --kind memory --root ~/.claude/projects/PROJECT/memory --project orders
amh-native preview --source SOURCE_ID --details
```

`preview` 输出 `path` 和 `hash`。将选中的项目组成 JSON 对象 `{"file.md":"HASH"}`，保存为本地清单后执行：

```bash
amh-native select --source SOURCE_ID --manifest selected.json
amh-native sync
```

历史内容不能可靠证明原生产者时标注“未知”，不会自动冒认当前上传者。新源内容关联已认证成员。`producer_member_id` 与 `uploader_member_id` 分开；其他成员的副本不会变成当前人的知识原创证明。

Claude Code 新项目会通过 SessionStart / 成功的新建记忆事件登记来源；新发现目录中的既有文件仍按历史处理。启用后请新建会话，旧会话可能保留原有上下文。

来源类型包含 `memory`、`transcript`、`rule`、`skill`、`config`。安装器发现常见来源；特殊目录可以显式绑定。Codex CLI 与桌面共用存储时只采集一次；无法从文件证明具体入口的自动绑定标为 `codex-shared`，不伪造 CLI/桌面生产归属。项目未知时仍允许全组织检索。

## 上传、召回与原生读取

- Hook 只通知采集；独立 worker 负责持久队列、带抖动的失败退避与延迟生成补扫。断网先保存变化，恢复后补传；持续 Hook 唤醒不会绕过失败冷却。
- `UserPromptSubmit` 查询统一云端索引，使用成员/版本/上下文覆盖去重，返回有界 `additionalContext`。
- 召回网络请求放在可终止的子进程中，默认预算 650ms；超时使用最近成功授权下的本地缓存或空结果，不阻断正常任务。离线缓存授权窗口为15分钟，在线拒绝访问后清除可控副本。
- 本地组织资源位于目标记忆目录的 `amh-org/`，原生 MEMORY.md / memory_summary.md 添加小型导航区块。原生索引及正文保留生产者；更新前校验本地修改，不覆盖冲突。
- 相同输入不重写原生文件；不完整文档保留其上一完整版本，其他文档仍可更新/删除。
- 已确认的原生文件读取与 Hook 交付共用覆盖账本。没有可观测依据时不假装知道模型已加载。
- 确认消费后保存的相同正文成为原来源别名，不新增独立生产者。仅发现相同组织内容、无法确认来源时进入本地待归属状态，可在历史预览中查看，明确选择后以未知来源导入。语义改写不会交给 LLM 自动合并；不能承诺机械去重理解所有语义变化。
- 会话原文保留，本模块注入的资料在聊天检索字段中遮蔽，避免以接收者聊天身份再次作为独立证据命中。

查状态或调试：

```bash
amh-native status
amh-native search '订单回调验签失败'
amh-native worker                 # 未使用 LaunchAgent 时
amh-native pause
amh-native resume
```

暂停停止后台共享与 Hook 云召回；已经下载并可原生读取的资料不会因此自动销毁。已交付给成员的数据无法通过撤销做到“从记忆中抹除”；在线撤销只约束后续访问和受管理副本。

## 聊天、规则、技能和配置

文本、完整聊天和技能二进制附件按有界块传输，验证完整文档 hash 后重组。当前单文件上限64MiB，超限/无法读取会报告，不截断后假装上传完整。身份状态、队列和索引与旧脑池分开。

```bash
amh-native export-assets --destination ./shared-export --kind transcript --kind skill --kind rule --kind config
amh-native activate-skills --source-store SOURCE_ID --destination ~/.claude/skills
amh-native activate-rules --source-store SOURCE_ID --instructions ./AGENTS.md
amh-native apply-config --asset ASSET_ID --product codex-cli --destination ~/.codex/config.toml
```

行为资产显式选择后再激活：技能保留脚本/资源结构与来源，规则写入指定项目托管区块。配置保留脱敏原件；目前同产品族可映射常见 model/effort/personality 或 model/language/outputStyle/autoMemoryEnabled 字段，其余字段报告跳过并保存在导出中，不把各厂商不兼容配置盲目覆盖。

常见认证文件、`.env.*`、私钥文件不上传；配置凭证键变体和 URL 凭证会处理。它不是通用秘密管理系统，历史预览应检查所选内容。私有登录凭证不在配对凭据或共享记忆中分发。

## Cowork 与运行环境

```bash
amh-native cowork-plugin --output ./amh-cowork-plugin
```

上面生成通用运行包，适合已经通过 `AMH_NATIVE_HOME` 指定已登记状态目录的运行环境。为当前成员的新 Cowork 运行时生成配对材料：

```bash
amh-native cowork-connect --state ~/.amh-native-sync \
  --output ./amh-cowork-plugin --connection-dir ./private-cowork-connection
```

当前 macOS 宿主机 Hook 模式使用明确的持久目录：

```bash
amh-native cowork-connect --state ~/.amh-native-sync \
  --output ./amh-cowork-plugin --connection-dir ~/.amh-native-sync/cowork-connection \
  --runtime-state ~/.amh-native-sync/cowork-runtime
```

- 插件 ZIP 只包含程序、公开的 Hub/成员/组织绑定信息，以及配置过的本机路径；不包含秘密。一次性配对凭据单独写入连接目录的 `.amh-native-connection.json`，权限为0600，15分钟有效。不要把连接文件放进插件包或贴给模型。
- 指定 `--runtime-state` 后，生成的 Hook 同时固定状态和独立配对文件路径，不依赖任务 cwd。macOS `setup` 默认使用此方式。此包绑定当前机器路径，另一台机器应重新生成；VM/云端运行环境应提供其实际可访问的路径，不能直接复制 Mac 路径。
- 未指定宿主绑定时，插件从 `CLAUDE_PROJECT_DIR` 对应目录寻找连接文件，也支持 `AMH_NATIVE_CONNECTION_FILE`。连接文件过期可重跑命令刷新；Hub/成员与路径不变时不用重装插件。
- Hook 在后台完成登记，获得**同一成员、不同设备**的凭证；已有状态不会被另一个 Hub 或成员覆盖。撤销发起设备会使未使用的配对凭据失效，已登记的运行时需单独撤销。
- 没有显式状态时使用 `CLAUDE_PLUGIN_DATA/amh-native-sync` 或 `AMH_NATIVE_HOME`。不能把变量名称视为持久性保证：实测宿主模式的插件数据位于随会话变化的临时目录，cwd 为 `/private/var/empty`，因此必须用上述持久绑定。
- 原生来源使用产品提供的 `CLAUDE_COWORK_MEMORY_PATH_OVERRIDE`。优先使用 `CLAUDE_PROJECT_UUID`；宿主模式未提供该变量时，仅识别实际来源路径中的 `spaces/<UUID>/memory` 结构。全局或未知目录保持项目未知，不猜测记忆根目录。Hook 只记录元数据，后台做历史基线；未选择的历史不自动上传。相同项目的旧挂载消失后可迁移路径；同路径身份冲突或挂载被替换会隔离来源。
- 配对期间的成功创建事件保存在本地队列。内容稳定且不存在历史冲突时纳入新记忆；发生基线竞争或内容变化的文件会明确列入历史复核，不静默跳过或直接上传。
- 导入后可运行 `/amh-native-org:doctor` 检查运行时目录、项目标识及 Hub 连通性。诊断不读记忆正文或凭证，也不等同于完整验收。

Cowork 不应假定拥有 Claude Code 同样的配置提示界面，因此该接入不依赖插件配置弹窗。[官方组件支持说明](https://claude.com/docs/plugins/platform-support)列出了两者差别。实测本版本的 Hook 在 Mac 上执行，而模型的 Bash 工具在 Linux VM 中执行；两者环境变量和文件系统不同，不能用模型工具里的 `printenv` 推断 Hook 环境，也不能把本机 localhost 当作云任务的服务地址。生产部署仍使用可达的 HTTPS Hub。

上述材料准备完成时，安装器只报告待验证，不会显示四端全部接入成功。

## 运维与恢复

`uninstall` 依据安装回执恢复仍未被后续编辑的配置；发生冲突会报告，不覆盖用户新改动。数据保留。切换 Hub 使用独立 `--state`，不要仅编辑 URL 将旧队列发送到新组织；数据迁移与重新连接是两个操作。

服务端以 SQLite 独立数据库保存身份、资产版本、变更游标和全文索引。提供 systemd 与 Nginx 示例于 `deploy/native-memory/`。部署前验证备份可打开且 `PRAGMA integrity_check` 为 `ok`；先本地回放，再在隔离试点服务联调，不覆盖旧 AMH 数据库。

验证命令：

```bash
python -m pytest tests/native_sync -q
python -m mypy agent_brain/native_sync --follow-imports=silent
```

测试矩阵含四种产品标识的12个跨产品方向和4个同产品方向，是协议/文件适配验收。真实厂商客户端、账户权限与两台真实机器验收单独记录，不用这些测试数字替代。
