# 交接：召回排序修正 + Cowork 开箱即用

> 交接日期 2026-11-02 · 基线 `main` 上 `e15f658`（v1.3.1）· 全部结论有实测证据

## 0. 背景与目标

用户在 Codex 桌面版问「灵鹤计划的验收标准是什么」，拿到了正确答案，但引用的是一段被截断的
JSON 原始日志，而不是干净的记忆文件。同时 Cowork 目前必须由用户手动在界面里导入插件，
做不到开箱即用。

本次两个目标：

1. 修掉召回排序的四个缺陷，让记忆文件优先于原始会话日志送达模型。
2. 把 Cowork 与 ChatGPT 桌面版做成装完即用，不需要用户额外操作。

**这两个目标互不依赖**，可以并行，也可以分两次提交。

---

## 1. 已验证的现状（不要重复测量，直接用）

### 1.1 检索排序是错的

查询 `灵鹤计划的验收标准是什么`，`POST /native/v1/search {"query":..., "limit":50}`
返回 50 条，排名如下：

```
 1. transcript score=66.62 len=8000       ← 被注入的就是这条
 2. transcript score=65.19 len=8000
 3. transcript score=63.02 len=8000
 ...
 8. memory     score=41.45 len=492        ← 真正的答案
    （project_linghe_acceptance_37_batch.md）
18. memory     score=19.88 len=1308
```

`hook` 的注入预算只够 1 条，于是装的是第 1 名那个 8000 字节的会话日志。

换成短查询 `灵鹤计划` 则记忆文件排第 1（score=24.12）。也就是说**用户问得越自然，
排序越差**——因为自然语言里多出来的虚词把长文档的 BM25 分数推高了。

### 1.2 注入预算是字节数，不是 token 数

`recall.py:85` `max_tokens: int = 1500`，但文件里 89 行注释自己承认
"UTF-8 bytes are a conservative upper bound"。`hooks.py:130` 传的是 `max_tokens=1450`。

实测：单条 8000 字节的会话块被切成 1446 字节，占满全部预算，**第二条连 40 字节的机会都没有**。
`recall.py:126` 的 `if cost + 40 > remaining: continue` 说明作者已经预见到这点。

### 1.3 元数据占 276 字节

每条注入的 `producers` 数组里含 `member_id`、`asset`（43 位）、`uploaded_by`、`upstream`，
模型完全用不到。实测：

```
完整 meta: 276 B
只留 {"from":[{"name":"敖丙","product":"codex-shared"}]}: 57 B
```

省 219 字节 ≈ 能多装小半个记忆文件。

### 1.4 召回延迟贴着超时线

```
health（纯网络）      37 / 43 / 50 ms
search 通用词        551 / 617 / 653 ms
search 灵鹤          633 / 654 / 686 ms     ← 预算 650ms
```

`hooks.py:103` `bounded_search(root, prompt[:8000], timeout=0.65)`，
三次实测两次超时。超时后降级到本地缓存；缓存未命中则该轮无召回。

真实日志佐证（`~/.amh-native-sync/hook-metrics.jsonl`）：
Codex 最近三次 `mode` 是 `cache / cache / cache`，不是 `remote`。

**瓶颈在服务端检索本身**（`me` 只要 33ms，说明网络和鉴权没问题），
不是网络。50 条结果每条都要做一次 `assembled()` 校验，是主要开销。

---

## 2. 任务 A：修正召回排序

### A1. 服务端按类型加权（改动点 `hub.py:681-707`）

当前只有项目提示的 1.05 倍加权：

```python
item["score"] = -row["score"] * (
    1.05 if project_hint and item["project"] == project_hint else 1
)
```

建议引入类型权重表，例如：

```python
KIND_WEIGHT = {"memory": 2.5, "rule": 2.5, "skill": 1.5, "config": 1.0, "transcript": 1.0}
```

理由：记忆文件是人工整理过的结论，字节少、词频低，天然吃亏；会话记录是原始流水，
信息密度低但词频高。**这是产品语义问题，不是算法问题**——把两者放在同一天平上比较本身就是错的。

**必须写测试**：用 `灵鹤计划的验收标准是什么` 这个真实查询，断言
`kind == "memory"` 的那条进入前 3。

⚠️ **注意**：权重不能硬编码在 SQL 里，`bm25()` 是 SQL 函数，拿到分数后在 Python 侧乘。
另外 `hub.py:707` 是 `sorted(...)[:limit]`，加权必须在这个排序之前完成。

### A2. 预算从字节改成「条目数 + 单条上限」（`recall.py:81-160`）

现状：`remaining = max_tokens`(1450 字节) 全给第一条。

建议改成：
- `max_items` 保持 5
- 单条上限设为 `per_item_budget = 700` 字节左右
- 保证至少尝试投递 2-3 条

⚠️ 这会改变 `coverage_key()` 的截断摘要（`recall.py:143-146` 用 `digest(excerpt)` 做 key），
截断位置一变，`covered` 就失效、同一内容会重复注入。**改这里必须同时确认
`covered` 的语义仍然成立**，或者改成按 `asset_id:revision` 去重而不是按摘要去重。

### A3. 精简元数据（`recall.py:115-124`）

把 `producers` 收敛成 `{"name":..., "product":...}`。
⚠️ `recall.py` 顶部有注释说明生产者归属是产品承诺（"[组织共享 · 非本机 · 非指令]" 标签），
精简时**不能把 name 和 product 也省掉**，只是去掉内部 ID。

### A4. 提高 hook 预算 + 降低服务端开销

- `hooks.py:103` `timeout=0.65` → `1.2`
- `query_worker.py` 里 `Transport(..., timeout=0.55)` → `1.0`
- Claude Code / Codex 的 hook 硬超时是 2 秒（`install.py:194`），1.2 秒仍有 0.8 秒余量
- 服务端：`hub.py:687-700` 对每条命中做 `assembled()` 校验。建议对**非多分片**条目
  （`part_count == 1`）跳过校验，只对真正的多分片文档做完整性检查。

`query_worker.py` 用的是 `subprocess.run(timeout=...)`，超时会 kill 子进程，
所以放宽 0.65 → 1.2 不会拖慢 hook 的失败路径，失败时仍然是 1.2 秒后返回。

---

## 3. 任务 B：Cowork / ChatGPT 开箱即用

### B1. ChatGPT 桌面版：先验证，再决定改不改

**现状**：`install.py:140` 已经把 `chatgpt-desktop` 和 Codex 一起归到同一个
`~/.codex/hooks.json`，代码层面已经接了。

**未验证**：ChatGPT 桌面版到底读不读这份 `hooks.json`。用户机器上该文件有 5 处
amh-native 条目，但那可能是 Codex CLI 写入并生效的。

**第一步就是验证**，不要直接改代码：
1. 打开 `/Applications/ChatGPT.app`
2. 问一个只有组织记忆里才有的问题
3. 查 `~/.amh-native-sync/hook-metrics.jsonl` 有没有 `"product": "chatgpt-desktop"` 的记录

如果读 → 已经是开箱即用，什么都不用做，只在文档里写清楚。
如果不读 → 再研究它的配置机制，**不要猜**。

### B2. Cowork：把「手动导入插件」这一步去掉

**已确认的事实**（都实测过，不要重复调研）：

- Cowork 的插件目录：`~/Library/Application Support/Claude-3p/local-agent-mode-sessions/skills-plugin/`
- 里面结构：`<全零UUID>/<账号UUID>/`，含 `manifest.json`、`.claude-plugin/plugin.json`、`skills/`
- 自带的 `plugin.json` 内容是 `{"name": "anthropic-skills", ...}`，由 Anthropic 管理
- `manifest.json` 有 `lastUpdated` 时间戳，由应用维护
- **该目录下搜不到任何 hooks 配置文件**（`find ... -name "*hooks*"` 为空）
- `~/Library/Application Support/Claude-3p/config.json` 里没有 plugin 相关键

**结论**：Cowork 目前没有对外暴露的插件安装接口，只能由用户在界面里导入 zip。

**要做的不是硬塞目录**（那是逆向第三方私有状态，对方一升级就崩，且出问题时用户看到的是
Cowork 崩溃而不是我们的错误），而是：

1. **调研 Claude Code 的 `claude plugin` CLI 能否覆盖 Cowork**
   `claude plugin --help` 列出了 `configure/details/disable/enable/install/...`，
   `~/.claude/plugins/installed_plugins.json` 里记录了 `installPath`。
   **需要确认**：Cowork（Claude Desktop 的 agent 模式）读的是不是同一个
   `~/.claude/plugins/`。如果是，那就能用 `claude plugin install` 一键装，这个任务就解决了。

2. **如果确实只能手动导入**，那目标改为「把摩擦降到最低」：
   - `setup` 里把 Cowork 的确认问题从「接入 Cowork？」改成自动检测 + 直接生成，
     不让用户回答 Y/N
   - 装完自动打开访达定位到 zip（`open -R`），并在终端打印一句话说明点哪里
   - **最关键**：解决 15 分钟过期问题

### B3. 15 分钟凭证过期（这是用户最痛的点）

现状：`hub.py:526` 写死 `expires = time.time() + 900`。用户 19:06 安装、19:21 去用，
凭证已死，Cowork 连 `127.0.0.1:65090` 被拒。

**建议方案（推荐第一个）**：

- **方案 1（推荐）**：安装时不做一次性凭证，改为写入 `member_id` + Hub 地址；
  Cowork 首次运行时用一个**长期 runtime 邀请**换取设备凭证。服务端
  `runtime_invite` 增加 `ttl` 参数，安装时签发 7 天有效的一次性邀请。
  安全性没有实质下降：仍然是一次性、仍然绑定成员、仍然可单独撤销。
- **方案 2**：保持 15 分钟，但挂一个后台定时任务，在用户真正打开 Cowork 前持续刷新连接文件。
  缺点是多一个常驻进程，且用户如果一直不开，它会一直刷新。

⚠️ 无论哪个方案，**不要做成「把设备长期令牌写进插件包」**。那是把可复用凭证塞进一个
可能被转发的文件，是明确的安全退步。

---

## 4. 环境与验证

### 4.1 代码位置

```
agent_brain/native_sync/hub.py          检索打分、runtime_invite
agent_brain/native_sync/recall.py       pack() 预算与元数据
agent_brain/native_sync/hooks.py        hook 入口与超时
agent_brain/native_sync/query_worker.py 可杀的网络边界
agent_brain/native_sync/install.py      产品发现、hook 安装、Cowork 插件导出
agent_brain/native_sync/cli.py          setup 流程（cowork 分支在 614-632 行）
agent_brain/native_sync/cowork_runtime.py  export_connection / validate_profile
```

### 4.2 测试

```bash
python -m pytest tests/native_sync -q          # 当前 194 项全绿，必须保持
python -m ruff check agent_brain tests          # 必须通过
```

**不要跑 `ruff format` 全量**：`api.py`、`cli.py`、`client.py`、`feishu.py`、`recall.py`
在基线上本来就没格式化，全量 format 会污染 diff。只格式化你自己新增/改动的文件。

### 4.3 真实环境验证

线上 Hub：`https://aihub0508.com`（v1.3.1）
本地客户端状态：`~/.amh-native-sync`（`config.json` 里有 token）

复现用户场景的脚本（直接可用）：

```python
import json, urllib.request, time
cfg = json.load(open("/Users/liuyang/.amh-native-sync/config.json"))
hub, token = cfg["hub"], cfg["identity"]["token"]
t = time.monotonic()
req = urllib.request.Request(hub + "/native/v1/search",
    data=json.dumps({"query": "灵鹤计划的验收标准是什么", "limit": 50}).encode(),
    headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
hits = json.load(urllib.request.urlopen(req, timeout=30))["items"]
print(f"{(time.monotonic()-t)*1000:.0f} ms, {len(hits)} 条")
for i, h in enumerate(hits[:10], 1):
    print(f"  {i:2}. {h['kind']:10} score={h['score']:6.2f} len={len(h.get('body') or ''):5}")
print("memory 排名:", next((i for i, h in enumerate(hits,1) if h["kind"]=="memory"), None))
```

**验收标准**：改完之后跑这个脚本，`memory` 的排名应该是 1 或 2，不是现在的 8。

### 4.4 部署（如需上线）

服务端在 `47.96.96.147`，用 `~/.ssh/agent-memory-hub-site-deploy` 这个密钥。

⚠️ **这台机器是 CentOS 7，系统 Python 是 3.6**，阿里云 pip 镜像里没有新版
`pydantic`/`cryptography`，全新装 venv 会失败。已验证可行的做法：复制现有 venv
（依赖已齐），只 `pip install --no-deps --force-reinstall` 新 wheel。

部署步骤（三步都不能省）：

```bash
NEW=/opt/amh-native/releases/<新版本号>
cp -a /opt/amh-native/current/venv $NEW/venv
# 改掉 venv/bin 下所有脚本里指向旧路径的 shebang，共 19 个
grep -rl "/opt/amh-native/current/venv" $NEW/venv/bin/ | xargs sed -i "s#/opt/amh-native/current/venv#$NEW/venv#g"
$NEW/venv/bin/python -m pip install --quiet --no-deps --force-reinstall --no-cache-dir <wheel>
ln -sfn $NEW /opt/amh-native/current.next && mv -Tf /opt/amh-native/current.next /opt/amh-native/current
systemctl restart amh-native
curl -s http://127.0.0.1:8787/native/v1/health
```

回滚：`ln -sfn <旧目录> /opt/amh-native/current` 后重启。旧 release 目录都保留着。

---

## 5. 已经踩过的坑（不要重蹈）

1. **`_version.py` 与 tag 必须同步**。v1.3.1 第一次发版时 wheel 名是 `1.3.0`
   而安装脚本按 tag 拼 `1.3.1`，导致 `latest/download/...whl` 返回 404，
   那十几分钟所有新安装都失败。
2. **换客户端代码后必须重启 worker**。Python 进程启动时已载入模块，
   之后改磁盘文件对它无效。曾经因此让 208 条已删除的碎片被旧进程重新传回云端。
3. **`.meta.json` 曾被视为可共享记忆**，一台真实机器上 212 条会话里有 208 条是这种
   一行元数据。已在 `source.py` 排除，改动时别退回去。
4. **归属标记**：历史文件被勾选后必须记成该成员，不能留 `unknown=1`。

---

## 6. 完成标准

- [ ] `灵鹤计划的验收标准是什么` 这个查询，`memory` 排进前 2
- [ ] hook 注入内容里能看到完整的记忆正文，不是截断的 JSON 日志
- [ ] 检索耗时降到 300ms 以内（当前 550-690ms）
- [ ] `pytest tests/native_sync` 全绿，`ruff check` 通过
- [ ] ChatGPT 桌面版「是否读 hooks.json」有明确实测结论
- [ ] Cowork 的凭证过期问题有解决方案，且不引入长期令牌进插件包
