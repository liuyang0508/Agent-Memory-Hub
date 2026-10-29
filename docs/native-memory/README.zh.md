# 组织原生记忆端云协同（研发分支）

本模块保存产品原生内容，提供 Hooks 上传、组织云端检索和本地原生读取。它不写旧 AMH MemoryItem，不运行旧提炼、治理或召回管线。默认成员可检索本组织所有项目；项目只参与排序，显式指定时才过滤。

## 状态

代码与回归位于本研发分支，尚未发布到公共安装入口。真实 Codex 引擎已分别验证云端 Hook 消费和关闭云召回后的原生文件消费。Claude Code/Cowork 的真实环境验收及 Cowork 运行时自动绑定仍待完成。两台真实 Mac 与阿里云实例联调未执行，不能将本地协议测试称为全部产品上线验收。

## 安装与成员登记

组织管理员先在 Hub 主机上运行独立服务，并建立成员邀请：

```bash
amh-native serve --db /var/lib/amh-native/hub.sqlite --host 127.0.0.1 --port 8787
amh-native invite --db /var/lib/amh-native/hub.sqlite --tenant linkcrux --name 张三
```

邀请只在命令输出一次；Hub 保存其 hash，有效期默认一天，只能使用一次。管理员自行将邀请交给对应成员，本程序不自动发消息。为既有成员增加设备时，用 `members` 查询稳定 ID，再 `invite --member-id <ID>`。

在包含本功能的代码版本上安装客户端：

```bash
sh install.sh --native --hub https://memory.example.com
```

`--hub` 也支持有有效 TLS 配置的 IP 地址与端口。安装脚本下载地址和组织 Hub 地址彼此独立，不绑定 aihub0508.com。前期可在我们的实例上验证，后续组织自建。HTTP 仅允许本机 loopback 测试；服务端生产部署使用 HTTPS 反向代理。

安装器检测并逐项显示 Claude Code、Codex CLI、Claude Cowork、实际检测到的 Codex/ChatGPT 客户端。通过个人邀请登记设备，显示成员名与稳定 ID，再预览历史和确认配置。ID 自动生成，不要求手填；邀请不是 OpenAI/Anthropic 的登录密码。

新模块使用 `~/.amh-native-sync/`，独立安装环境 `.native-venv/`。已有旧 AMH 的托管 Hook、纪律区块和已识别旧 MCP 通道会按安装回执隔离；用户其他配置保留。Codex Hook 使用生成处理程序的正常信任 hash，不使用绕过信任的启动参数。

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

来源类型包含 `memory`、`transcript`、`rule`、`skill`、`config`。安装器发现常见来源；特殊目录可以显式绑定。Codex CLI 与桌面共用存储时只采集一次；无法从文件证明具体入口的自动绑定标为 `codex-shared`，不伪造 CLI/桌面生产归属。项目未知时仍允许全组织检索。

## 上传、召回与原生读取

- Hook 只通知采集；独立 worker 负责持久队列、网络重试与延迟生成补扫。断网先保存变化，恢复后补传。
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

常见认证文件、`.env.*`、私钥文件不上传；配置凭证键变体和 URL 凭证会处理。它不是通用秘密管理系统，历史预览应检查所选内容。私有登录凭证不在邀请或共享记忆中分发。

## Cowork 与运行环境

```bash
amh-native cowork-plugin --output ./amh-cowork-plugin
```

插件包含独立 Hook/worker 的 Python 运行代码，不包含设备凭证。它必须在实际 Cowork 运行环境中可执行，并将 `AMH_NATIVE_HOME` 指向该环境已经登记的持久状态目录，绑定该环境可读的原生记忆位置。插件可唤醒独立 worker；VM/云任务是否保留进程和目录，需要真实客户端验证。不能把 Mac 的 localhost 或目录路径当成 VM/云端的同一位置。

当前账号组织停用，自动跨运行环境绑定尚未验收。安装器会明确报告“插件包准备好、环境绑定待验证”，不会显示全部接入成功。

## 运维与恢复

`uninstall` 依据安装回执恢复仍未被后续编辑的配置；发生冲突会报告，不覆盖用户新改动。数据保留。切换 Hub 使用独立 `--state`，不要仅编辑 URL 将旧队列发送到新组织；数据迁移与重新连接是两个操作。

服务端以 SQLite 独立数据库保存身份、资产版本、变更游标和全文索引。提供 systemd 与 Nginx 示例于 `deploy/native-memory/`。部署前验证备份可打开且 `PRAGMA integrity_check` 为 `ok`；先本地回放，再在隔离试点服务联调，不覆盖旧 AMH 数据库。

验证命令：

```bash
python -m pytest tests/native_sync -q
python -m mypy agent_brain/native_sync --follow-imports=silent
```

测试矩阵含四种产品标识的12个跨产品方向和4个同产品方向，是协议/文件适配验收。真实厂商客户端、账户权限与两台真实机器验收单独记录，不用这些测试数字替代。
