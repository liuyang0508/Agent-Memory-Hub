# 组织维基与离线整理（「做梦」）

适用于 AMH 1.5.0。设计依据：Anthropic《The components of agent memory》（2026-11-06）和 cookbook《Dreaming with a dynamic workflow》。

## 这是什么

组织记忆原来只有两件事：各成员的原生记忆文件原样上传（存），模型按需搜索（搜）。没有人整理：同一件事在几个人的文件里有几个版本，十月写对的话到十一月已经过期，新会话开场只能拿到一串文件描述。

`amh-native dream` 在管理员自己的机器上，定期回看 Hub 上全组织共享的记忆文件和会话摘录，整理成一份小而互相链接的**组织维基**：一个主题一页，每条事实带来源（成员 · 产品 · 日期）和核验状态（已核验 / 推断 / 冲突），过期的写明"原为 X，现为 Y"，有 `_index.md` 索引。维基作为产品 `amh` 的记忆资产上传回 Hub，和别的记忆一样被搜索、被注入；它的索引成为每个会话开场目录的前几行。

模型调用走本机的 Claude Code（`claude -p`，用本机的登录），不在服务器上放任何密钥，也不新增数据外流的去处。

## 怎么跑

```bash
amh-native dream                     # 整理一遍，结果只留在本机给人看（默认）
amh-native dream --publish           # 整理并上传；会先问一次
amh-native dream --status            # 最近几次运行：页数、花费、结果
amh-native dream --schedule on       # 每天 02:00 自动整理并上传（macOS launchd）
amh-native dream --schedule off
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--publish` | 关 | 不加时结果只写到 `~/.amh-native-sync/dream/runs/<时间>/wiki/`，账本不记"已整理"，下次会重做 |
| `--full` | 关 | 全量重做；默认只把上次发布后版本变过的文件交给分析员 |
| `--model` / `--analyzer-model` | `opus` / 同上 | 组织者和分析员用的模型，接受 `claude -p --model` 认识的名字 |
| `--budget` | 12 | 本次花费上限（美元等价）。分析员各自最多 3，组织者最多 6，总和不超过上限 |
| `--jobs` | 3 | 同时运行的分析员数，上限 6 |

## 一次运行发生什么

1. 用本机身份从 Hub 拉取全部变更，取每个资产的当前版本：只要 `memory` 类、角色为主题记忆 / 索引 / 会话摘要的文档；不要 Codex 的 `raw_memories.md`、不要会话记录、不要 frontmatter 标为 `user` / `feedback` 的个人偏好。已发布的维基页也拉下来作为起点（本机发布目录存在时以本机为准）。
2. 在家目录之外建临时工作区：`inputs/bundle-NN/` 放输入（每份开头一段「来源数据 · 非指令」块，正文里能闭合这个块的标记会被替换），`wiki/` 放现有维基。输入按 24KB 分组。
3. 每组一个**分析员**（并行）：读输入，核对维基已有说法，补充具体事实和来源，用 `[[slug]]` 链接，只做针对性改动。
4. 一个**组织者**：合并重复页（退役页只剩一行 `MOVED TO [[slug]]`）、改名、修链接、补全索引、标 `CONFLICT:`，维基总字符数不得超过输入语料。
5. 代码校验（不靠模型）：frontmatter 完整且 `name` 等于文件名；退役桩页删除并把所有链接改指新页；断链列入报告；索引补齐漏掉的页、删掉不存在的页；每页过凭据筛查（`source.screen`），不干净的页直接删；超过 64KB 的页删，超过 30KB 的报告；整份维基大于语料的报告「建过头」。
6. 泄漏检查：所有代理的工具结果里不得出现本机家目录路径；出现则本次结果作废、不上传。
7. `--publish` 时把 `wiki/` 同步到 `~/.amh-native-sync/dream/wiki/org-wiki/`，把这个目录登记为产品 `amh` 的记忆来源，后台同步进程照常上传（上传前仍过一遍常规筛查）；账本记下本次折进去的资产版本。删掉的页会以墓碑撤回。

报告（JSON）包含：文档数、变过的文档数、分组数、每个分析员的结果 / 花费 / 回合、组织者结果、校验报告（页数、字符数、删掉的页及原因、退役、断链、超大、索引增删、建过头）、泄漏计数、总花费、本次结果保存位置。

## 隔离

每个代理这样启动：`claude -p … --setting-sources project --settings <deny 文件> --strict-mcp-config --mcp-config <空> --no-session-persistence --allowedTools Read,Write,Edit,Glob,Grep --disallowedTools Bash,WebFetch,WebSearch,Task,Agent,NotebookEdit --max-budget-usd <上限>`，环境变量关闭 Claude Code 自动记忆。deny 规则拒绝读写家目录、`/etc`、`/Library`；工作区在系统临时目录，用完删除。提示里只有路径，不拼文档正文；系统提示明说输入是数据不是指令。

这不等于绝对安全：文档里的文字仍可能影响维基写了什么（上限是花掉预算、写出错页），所以默认只整理不上传，上传的页每一版都在 Hub 上可查可撤。

## 成员端看到什么

- 开场目录前几行是维基索引，形如 `deploy-pre：生产跑 pre 线`；英文短名可以直接当搜索词。
- 搜索结果和注入里，维基页的角色显示为「维基」；正文里每条事实后面有来源和核验状态，注入头部提醒"标着「推断」或「冲突」的先核实再用"。
- 维基页和它引用的原始记忆都在，谁更相关谁排前。

## 成本（实测请看运行报告）

cookbook 同规模（11 份、283KB）用 Haiku 分析员花 $0.36。本实现默认 Opus 5.5，一轮全量按语料 20 万字符估计 $3–6 等价额度，之后每晚增量通常不到 $1。走本机 Claude Code 登录时消耗的是订阅额度，报告里的 `cost` 是 Claude Code 折算的数字。

## 回退

- 停止：`amh-native dream --schedule off`。
- 撤回维基：删除 `~/.amh-native-sync/dream/wiki/org-wiki/` 下的文件（或整个目录），后台同步会提交墓碑；Hub 上历史版本按常规保留策略处理。
- 旧 Hub（1.5.0 以前）不认识产品 `amh`，上传会被拒收并记在 `outbox_rejections`，不影响其他上传；升级 Hub 后自动补传。
