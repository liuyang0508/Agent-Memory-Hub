# 每台设备双 Hub 上传，只有主 Hub 召回

适用客户端版本：1.4.4。

每个 `--state` 是独立设备 profile：有自己的 Hub 身份、源文件共享选择、SQLite、上传队列、游标和后台 worker。两个 profile 可读取同一份产品原生记忆，但不能复制对方的 `config.json`、数据库或凭证。一个 Hub 故障只影响自己的队列；不会从备用 Hub 召回。

`recall_enabled` 是独立的本机召回开关，旧 profile 缺省为开启。关闭后 Hook 不注入组织内容，MCP 的 search/read/related（包括已开始的 research）、NativeClient 搜索和缓存读取、投影均不提供组织内容；上传 worker 和共享选择继续运行。它不撤回已进入产品旧会话的上下文。`pause` 会暂停上传，不能用来实现只上传的备份。

## 新设备

```sh
# 主 Hub：安装本机产品 Hook / amh-org MCP。
amh-native setup --hub https://PRIMARY_HOST --state "$HOME/.amh-primary"

# 备份 Hub：独立飞书登录和历史选择；只登记同步来源、启动自己的 worker。
amh-native setup --hub https://BACKUP_HOST --state "$HOME/.amh-backup" --sync-only

# 主 profile 把 Claude Code 来源发现事件转给备份。
amh-native observe-to --state "$HOME/.amh-primary" --target "$HOME/.amh-backup"

amh-native status --state "$HOME/.amh-primary"
amh-native status --state "$HOME/.amh-backup"
```

`install-native.sh` 支持并转发 `--sync-only`、`--projection/--no-projection`、`--state`、`--no-service` 等选项。`--sync-only` 与 `--projection` 互斥；它不安装、替换或导出产品 Hook、MCP、Cowork 插件，也不改产品原生读取开关。重复 setup 保留已有源选择，不自动导入被跳过的历史。各 Hub 的历史导入需各自明确选择。

默认 macOS setup 安装各 profile 独立的后台服务；其他系统应分别以 `amh-native worker --state ...` 托管两个 worker。`--no-service` 不启动常驻服务。

## 从原单 Hub 切换

先确认目标客户端包含本功能，并在新 state 以 `--sync-only` 完成独立登录和来源登记。旧主 profile 在准备和验证期间仍可召回。不要复制旧 config/数据库。

```sh
amh-native setup --hub https://PRIMARY_HOST --state "$HOME/.amh-primary" --sync-only

# 使用匹配版本的 Python；默认仅写审查计划，源 profile 只读。
python scripts/native-copy-approved.py --source "$HOME/.amh-old" \
  --target "$HOME/.amh-primary" --plan /tmp/amh-approved-plan.json
# 审查计划的两个 Hub、source_policy、来源、selected/skipped 数量，再应用。
python scripts/native-copy-approved.py --source "$HOME/.amh-old" \
  --target "$HOME/.amh-primary" --plan /tmp/amh-approved-plan.json --apply
amh-native sync --state "$HOME/.amh-primary"
amh-native status --state "$HOME/.amh-primary"

# 新 Hub 数据完成独立核验后，切换本机产品。
amh-native detach-products --state "$HOME/.amh-old"  # 只读冲突预检
amh-native recall --state "$HOME/.amh-old" --disable
amh-native detach-products --state "$HOME/.amh-old" --apply
amh-native recall --state "$HOME/.amh-primary" --enable
amh-native activate-products --state "$HOME/.amh-primary" \
  --product codex-cli --product codex-desktop --product claude-code
amh-native observe-to --state "$HOME/.amh-primary" --target "$HOME/.amh-old"
```

一次性脚本只复用旧 profile 的 `selected=1` 文件及已启用来源范围，并把该范围明确列进计划。计划另列旧 profile 的全部历史否决哈希，以及未选路径（含缺失路径）的指纹和文件身份；应用时使用新 source/asset ID 保留这些否决元数据，避免删除、改名或重新出现的旧历史被视作新文件。离线的已登记来源保留否决路径并进入持久隔离；目录恢复不会自动解除，须独立审查离线期间未见的文件。原隔离来源继续隔离。每个当前文件重跑完整正文隐私/密钥过滤；未选历史、敏感/密钥、隔离来源、缺失或不安全路径均跳过并计数。应用前重新比较全部计划与哈希；文件或身份改变须重新预览。应用占用两个 profile 的 worker 锁，忙时直接失败，不停服务。仅登记来源、选择版本，不发网络请求；新 Hub 的身份、资产、队列和游标由新 profile 自行生成。重复应用保留同样选择。被过滤的文件留在新 profile 的排除历史中。脚本不把云端其他成员的记忆复制到新组织，也不能证明其内容具有业务授权；应审查计划后再应用。

sync-only 时客户端搜索有意关闭。切换前的新 Hub 数据核验可用独立服务端只读检查；若临时开启新 profile 的本机召回进行验证，验证后应关闭，直到正式接入产品。新 profile 没安装 Hook/MCP 时不会自动参与产品召回。

`detach-products` 只删除与旧安装 receipt 完全匹配的 Hook handler 和 `amh-org` MCP 项。保留用户后来增加的 Hook、设置、其他 MCP、原生记忆、共享选择、队列与 worker；不恢复旧 AMH 自动链路或旧记忆纪律文本。任何已修改的托管 handler/MCP 都使整次 detach 停止并报告冲突。再次运行时不会删除新主 profile 的配置。预览不是事务锁，应用时会再次读取并校验文件。

召回状态可用 `amh-native recall --state ...` 查看；`--enable` 明确恢复召回，并解除 sync-only 标记，但不自动安装产品集成。固定 `amh-org` 名称只允许一个 profile；不会静默覆盖另一个 profile。

## 新目录、旧历史与故障

Codex CLI 和本地 Codex 桌面模式共用固定原生目录，两个 worker 独立扫描。Claude Code 的未来项目根通过主 Hook 的 SessionStart/成功 Write 事件发现。`observe-to` 最多授权 4 个备份，绑定目标 Hub/成员/设备身份；目标必须已经禁用召回。取消用 `--remove`。改变目标身份后需重新授权。

Fanout 不发送查询，不读取组织记忆，不持久化 prompt 或工具正文，只记录来源路径、文件 stat 身份与事件信息。每个备份队列最多 1024 个去重事件，Hook 不等待备份网络或 SQLite；备份 worker 才扫描和建立历史基线。新根已有文件默认排除，只有身份未变的成功创建事件可豁免初始基线；已排除文件不会因后来编辑或重复事件自动入选。同一新根的多个创建事件会一起处理。失效创建事件（文件已变更或消失）保守登记为排除历史/待复核，并移出活动队列；无法确认身份的缺失根会隔离。仅瞬时 I/O 失败留在活动队列重试，复核线索位于 `source-observation-review/`。事件丢失、队列达到上限时采取保守行为，可能需要独立运行 `import` 审查历史，不会猜测并上传。

本功能不把主 Hub 不可用视为改用备用 Hub 的授权。主 Hook 可按已有规则使用主 profile 自身显式启用的缓存；只上传备份始终不参与召回。

## 投影与 Cowork 边界

双 Hub 建议保持默认 `--no-projection`。新投影 manifest 记录 profile owner；清理或覆盖不属于该 profile 的 manifest 会报告冲突。没有 owner 的旧 manifest 不自动认领或删除，须独立审查和清理；`status`/`storage-conflicts.json` 保留处理线索。用户编辑的托管文件仍会保留，关闭召回不等于已从产品磁盘移除这些手工编辑的副本。 投影因策略关闭或其他可捕获异常中断时，会按本次写入哈希回滚未被修改的新文件、恢复此前版本；并发用户编辑保留并报告冲突。此机制不声称提供强制终止进程后的持久日志恢复。

Cowork 有独立身份和运行时状态。新导出的宿主机绑定插件（setup 或 `cowork-connect --runtime-state ...`）将 runtime 通过 `recall-parent.json` 绑定宿主 profile 的召回开关；每次 Hook/MCP 请求重读，父 profile 缺失或禁用时不召回。旧插件包含冻结 runtime，必须升级并重新导出/导入，不能假设仅升级宿主包就改变了旧插件。已经配对的旧 Cowork runtime 仍需单独检查其实际绑定。

本次双上传支持 Codex CLI、本地 Codex 桌面模式与 Claude Code。Cowork 来源依赖其运行时观察；这里没有把 Cowork sandbox 的动态挂载发现转发到另一个 Hub，也没有证明两个独立 Cowork runtime 同时稳定运行。`--sync-only` 会跳过 Cowork，因此不能据此宣称 Cowork 已完成双 Hub 上传。宿主召回开关测试不能代替真实 Cowork 导入和会话验收。
