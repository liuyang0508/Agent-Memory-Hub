# 安装健康度兼容补丁设计

日期：2026-07-12

## 背景

当前公开版为 v1.1.1。一次 Codex dogfood 排障暴露出两个彼此独立、但都会让用户误判安装健康度的问题：

1. `benchmarks/quickstart-60s.sh` 只隔离 `BRAIN_DIR`，仍继承宿主 `HOME`。benchmark 即使显示 `PASS`，也可能覆盖宿主 `~/.local/bin/memory` 和 `/remember`，随后删除临时 clone，留下指向不存在目标的 CLI shim。
2. `memory doctor` 不调用 adapter doctor，且普通模式不会因表格中的 `ERROR`、`MISSING` 或 `INVALID` 返回非零退出码。Codex `UserPromptSubmit` Hook trust 已失效时，`memory adapter doctor codex` 会正确退出 1，但总 doctor 仍可能显示全绿并退出 0。

本设计提供一个独立的向后兼容 hotfix，不与统一 Memory Injection Gateway 的破坏性 SDK 变化混合发布。

## 已确认根因

### Quickstart 污染宿主

`quickstart-60s.sh` 当前只为安装和搜索设置：

```text
BRAIN_DIR=BENCH_ROOT/brain
```

`install.sh --minimal` 仍从真实 `HOME` 派生以下路径：

- `~/.claude/commands/remember.md`
- `~/.local/bin/memory`
- pip、Python user base 和其他依赖工具缓存

现有 cleanup 只在 `/remember` 原文件已存在时恢复备份；原文件不存在时会留下新文件。CLI shim 没有备份，已有正常 shim 会被直接覆盖。临时 clone 删除后，新 shim 的目标也随之消失。

### General doctor 假绿

`memory doctor` 和 `memory adapter doctor <adapter>` 是两条独立调用链：

- 总 doctor 直接检查 brain、index、Claude 配置摘要和 CLI shim。
- adapter doctor 调用 adapter 的结构化 `diagnose()`，Codex 路径会继续校验 Hook 注册、Hook command identity 和 trust hash。

`memory doctor --fix` 虽然会重新安装 Codex、Claude Code adapter，但修复动作完成后，总 doctor 仍不复诊 adapter。因此还存在“repair action 成功、实际 adapter 仍错误、命令却退出 0”的窗口。

## 目标

- Quickstart 的持久化用户状态、依赖缓存和子进程临时文件都必须落在单次 benchmark 的临时根目录内。
- Benchmark 正常退出、失败退出或收到可捕获信号后，宿主 `HOME` 内容保持不变。
- 总 doctor 只聚合已经存在 AMH-owned footprint 的核心 Hook adapters。
- 已配置 adapter 的 `error` 必须使总 doctor 返回 1，并展示具体诊断与修复命令。
- adapter 的 `warn` 保持现有降级语义，不使默认总 doctor 返回非零。
- `memory doctor --fix` 必须在修复动作后重新诊断修复后形成的全部核心 adapter footprints，再决定最终退出码。
- 默认 doctor 保持只读；只有显式 `--fix` 可以写配置。

## 非目标

- 不重构总 doctor 的全部历史状态和退出码。
- 不把所有已发现的 16 个 adapters 都纳入默认总 doctor。
- 不新增 adapter 安装状态 ledger。
- 不修改 Injection Gateway PR 或 SDK 的 `context_firewall` 默认行为。
- 不在本补丁中发布 Release、合并 PR 或改变版本号。
- 不修复 installer 把所有 adapter 失败都描述为 optional 的相邻文案问题。

## 方案比较

### 方案 A：独立最小兼容补丁，推荐

- Quickstart 内建立隔离 `BENCH_HOME` 和缓存目录。
- 总 doctor 仅诊断具有 AMH-owned footprint 的 Codex、Claude Code。
- 复用现有 adapter `diagnose()` 报告和修复建议。
- 只改变核心 adapter error 的总 doctor 退出码。

优点是改动边界小、可回归、适合 v1.1.x 补丁线；缺点是总 doctor 的其他历史假绿状态继续保留，后续仍需结构化重构。

### 方案 B：把修复并入 Injection Gateway PR

可以减少 PR 数量，但会把安装安全补丁与破坏性 SDK 行为绑定，延迟用户获得修复，也增加审阅和回滚成本，因此不采用。

### 方案 C：重构统一 InstallationDoctorReport

统一定义所有检查的严重级别、JSON 输出和退出码，长期最完整；但会改变多个已有测试和脚本依赖的历史契约，不适合此次 hotfix。

## Quickstart 隔离设计

Benchmark 根目录继续由 `mktemp` 创建，但不再使用容易与系统 `TMPDIR` 混淆的变量名。建议目录结构：

```text
BENCH_ROOT/
├── home/
├── brain/
├── cache/
├── tmp/
├── xdg-config/
├── xdg-data/
├── xdg-state/
├── pyuserbase/
├── cargo/
├── rustup/
├── agent-memory-hub/
└── install.log
```

clone、安装和首次搜索使用同一组显式环境：

```text
HOME=BENCH_ROOT/home
BRAIN_DIR=BENCH_ROOT/brain
AGENT_MEMORY_HUB_BIN=BENCH_ROOT/home/.local/bin
TMPDIR=BENCH_ROOT/tmp
XDG_CONFIG_HOME=BENCH_ROOT/xdg-config
XDG_CACHE_HOME=BENCH_ROOT/cache
XDG_DATA_HOME=BENCH_ROOT/xdg-data
XDG_STATE_HOME=BENCH_ROOT/xdg-state
PIP_CACHE_DIR=BENCH_ROOT/cache/pip
PYTHONUSERBASE=BENCH_ROOT/pyuserbase
CARGO_HOME=BENCH_ROOT/cargo
RUSTUP_HOME=BENCH_ROOT/rustup
```

这些值必须覆盖调用者预先设置的同名变量，尤其是 `AGENT_MEMORY_HUB_BIN`；否则 benchmark 仍可能把 shim 写回宿主自定义目录。如果安装器未来增加新的常规 HOME 写入，它仍会自然落入 `BENCH_ROOT/home`，不需要继续维护逐文件备份清单。

clone、install 和 first-search 三个阶段统一通过同一个 explicit-allowlist environment helper 启动。连续审查已经实证 denylist 无法闭合：`GIT_CONFIG_GLOBAL` 可以注入外部 Git Hook，`PYTHONPATH` 可以通过 `sitecustomize` 写宿主路径，后续还会不断出现新的重定向变量。因此 helper 必须以 `env -i` 为基础，只显式传递：

- 运行命令必需的 `PATH`。
- locale：`LANG`、`LC_ALL`、`LC_CTYPE`。
- 网络代理的大小写形式：HTTP、HTTPS、ALL 和 NO_PROXY。
- 证书读取路径：`SSL_CERT_FILE`、`SSL_CERT_DIR`、`REQUESTS_CA_BUNDLE`、`CURL_CA_BUNDLE`、`PIP_CERT`、`GIT_SSL_CAINFO`。
- 只读认证 socket：`SSH_AUTH_SOCK`。
- benchmark 自己生成并位于 `BENCH_ROOT` 的 HOME、BRAIN、bin、TMP/XDG、pip cache、Python user base、Cargo/Rustup/UV 路径。

不传递任意 `GIT_CONFIG_*`、Git Hook/template/trace/path override、`PYTHONPATH`、`PYTHONSTARTUP`、`PYTHONINSPECT`、`MEMORY_PYTHON`、`AGENT_MEMORY_HUB_PYTHON`、shell startup injection 或其他宿主执行/写入重定向。测试 fixture 的故障模式固化在临时 fixture 脚本和 wrapper 内容中，不能依赖从宿主环境透传 `FAKE_*` 变量。

移除当前真实 `HOME` 下 `/remember` 的备份与恢复逻辑。临时根先保存为局部 candidate；只有 `mktemp` 成功、canonical parent 等于预期外层临时目录、basename 匹配 `amh-bench-*`，并成功写入 ownership marker 后，才能赋给 `BENCH_ROOT` 并设置 `BENCH_ROOT_OWNED=1`。cleanup 只删除同时通过 parent/prefix/marker/ownership 复核的根；失败的 `mktemp` stdout 永远不能进入删除路径。`--keep` 时成功或失败都保留完整根和日志用于审计。

clone、install 和 first-search 三个阶段都必须显式检查退出码。任一阶段失败时，benchmark 输出该阶段的有界日志并返回非零，不能继续计算出 `PASS`。signal teardown 使用可重入状态机：第一次 INT/TERM 设置 `SHUTDOWN_IN_PROGRESS=1` 并立即忽略后续 INT/TERM，由第一次 signal 决定 130/143；随后完成有界 TERM grace、必要时 KILL 整个已验证 phase group、reap leader 和 ownership-safe cleanup。连续 INT→TERM 或 TERM→INT 不能中断首次清理，也不能留下 descendant。

## Core adapter footprint 设计

总 doctor 只处理 `CORE_HOOK_ADAPTERS = ("codex", "claude_code")`，并在调用 `diagnose()` 前判断是否存在 AMH-owned footprint。

新增只读模块 `agent_brain/platform/adapter_health.py`，集中承担以下职责：

- `has_managed_footprint(adapter_name)`：识别当前或部分安装留下的 AMH-owned 配置。
- `diagnose_configured_core_adapters(brain_dir)`：遍历全部核心 adapters，跳过无 footprint 项，并把异常转换为有界 error 结果。
- 返回稳定的轻量结果对象，包含 adapter、status、全部 non-ok checks 和修复建议；`warn` 与 `error` 的原因使用同一承载结构，CLI 只负责渲染和计算最终退出码。

Footprint 检测和 adapter 业务诊断分离：前者只回答“这个 adapter 是否属于本次总 doctor 的责任范围”，后者继续复用 adapter 自己的 `diagnose()`，避免在 CLI 中复制 trust、Hook 或 MCP 规则。

### Codex footprint

满足任一条件即视为已配置或部分配置：

- `AGENTS.md` 包含 AMH `BEGIN` 或 `END` sentinel。
- `hooks.json` 中存在当前或 legacy AMH Hook command。
- `config.toml` 中存在 AMH MCP managed section。

仅存在用户自己的 `.codex` 目录或普通 Codex 配置，不视为 AMH footprint。

### Claude Code footprint

满足任一条件即视为已配置或部分配置：

- `CLAUDE.md` 包含 AMH Awareness Channel sentinel。
- `settings.json` 中存在当前或 legacy AMH Hook command。
- `mcpServers` 中存在 `agent-memory-hub` managed entry。

仅存在用户自己的 `.claude/settings.json`，不视为 AMH footprint。

Footprint 检测必须只读、容忍文件缺失。配置文件存在但无法解析，且文件中可识别 AMH ownership 时，应进入 adapter diagnose 并报告 error，不能静默跳过。

现有 `memory doctor` 中手写的 `Claude Code settings`、`Claude Code hooks` 和 `MCP server` 三行由结构化核心 adapter 聚合替代，不再并行保留。这样可以避免普通非 AMH Claude 配置被误报，也避免同一个 Claude adapter 同时出现两套互相冲突的状态。`/remember`、brain、index、Web dependency 和 CLI shim 等非 adapter 行保持原样。

## Doctor 聚合语义

对每个已配置核心 adapter 调用现有 `diagnose()`，不复制 Hook trust 等业务规则。

| adapter 状态 | 总 doctor 展示 | 总 doctor 退出码影响 |
|---|---|---|
| absent | 跳过，不制造假故障 | 无 |
| ok | `OK` | 无 |
| warn | `WARN`，保留原因 | 仍可退出 0 |
| error | `ERROR`，展示失败检查和 fix | 至少退出 1 |
| diagnose 抛异常 | 转换为 `ERROR`，展示有界异常摘要 | 至少退出 1 |

总 doctor 必须检查全部已配置核心 adapters，不能在第一个错误处短路。

为了保持兼容，本补丁不改变现有 `Memory items EMPTY`、`Search index INVALID`、可选 Web dependency 缺失等行的历史退出码。结构化总 doctor 应在后续 minor/major 版本另行设计。

`memory doctor --fix` 的顺序为：

1. 修复 CLI shim。
2. 重新安装核心 adapters。
3. 运行原有总 doctor 检查。
4. 重新检测修复后形成的全部 footprint，并调用 adapter `diagnose()`；不是只复诊修复前已存在的 adapters。
5. repair action 或 adapter diagnosis 任一为 error，最终退出 1。

## 输出设计

总 doctor 表格新增核心 adapter 行，值至少包含 adapter 总状态。发生 warn 或 error 时，在表格后输出有界详情：

- non-ok check 名称。
- detail。
- fix，例如 `memory adapter install codex` 或 `memory doctor --fix`。

总 doctor 渲染层对新增 adapter 详情和既有 repair action detail 统一执行有界文本处理：移除控制字符并限制为 1,200 个字符。诊断代码不得主动读取或输出原始 prompt、memory body、配置文件全文或环境变量值；异常只展示裁剪后的 `str(exc)`。本补丁不尝试从任意第三方异常中识别所有潜在 secret，但不能新增 raw-config/raw-environment 输出路径。

## 测试设计

### Quickstart RED 测试

构造一个最小临时 Git fixture repo：

- 复制待测 quickstart 脚本。
- fake `install.sh` 像真实安装器一样优先遵循 `AGENT_MEMORY_HUB_BIN`，并模拟写入 CLI shim、`$HOME/.claude/commands/remember.md` 和缓存。
- fake search script 可按用例返回成功或失败。
- 在外层 `HOME` 预置 sentinel，并记录文件 manifest 与 SHA-256。
- 在外层预置 `AGENT_MEMORY_HUB_BIN=<host path>`，证明 benchmark 会覆盖而不是继承它。
- 在外层预置 `PIP_TARGET=<host sentinel dir>`，证明 sanitized environment 会清除宿主 pip 重定向。
- 外层 `GIT_CONFIG_GLOBAL` 指向带外部 Hook 的配置、`PYTHONPATH` 指向带 `sitecustomize.py` 的目录，并预置 `MEMORY_PYTHON` / `AGENT_MEMORY_HUB_PYTHON`；三阶段均不得继承或执行这些入口。

运行 quickstart 后断言：

- 外层 HOME manifest 与内容哈希完全不变。
- 默认 cleanup 不留下 benchmark 安装产物。
- `--keep` 时所有安装产物都位于输出的 `BENCH_ROOT/home`。
- 保留目录中的 memory shim 目标存在并位于同一 `BENCH_ROOT`。
- install failure 和 search failure 都返回非零，执行 cleanup，且外层 HOME 完全不变。
- 收到 `INT` 或 `TERM` 时返回非零，执行一次 cleanup，且外层 HOME 完全不变。
- INT→TERM 与 TERM→INT 连续信号仍完成同一轮 teardown，退出码由首个 signal 决定且无 resistant descendant。
- 失败的 mktemp 即使在 stdout 返回既存 sentinel 目录，也不得删除或修改该目录。
- `--keep` 与 clone/install/search 任一失败组合都保留完整 owned root/log；测试 finally 只删除通过相同 ownership 边界的 fixture 根。

当前实现应因宿主 shim/remember/cache 被修改而稳定失败；修复后通过。

### Doctor RED 测试

- `test_doctor_fails_when_configured_codex_hook_is_untrusted`
- `test_doctor_skips_codex_when_no_amh_footprint_exists`
- `test_doctor_skips_non_amh_codex_configuration`
- `test_doctor_reports_all_configured_adapters_without_short_circuit`
- `test_doctor_keeps_zero_exit_for_adapter_warnings`
- `test_doctor_fix_rediagnoses_adapters_before_returning_success`
- `test_doctor_converts_adapter_diagnose_exception_to_error`
- `test_doctor_bounds_repair_and_adapter_error_details`
- 参数化 `test_has_managed_footprint_codex_variants`：覆盖 AGENTS-only、MCP-only、当前 Hook 和 legacy Hook。
- 参数化 `test_has_managed_footprint_claude_code_variants`：覆盖 Awareness-only、MCP-only、当前 Hook 和 legacy Hook。
- `test_managed_footprint_does_not_false_skip_malformed_owned_json`：malformed JSON 中仍含 AMH ownership 时必须进入 diagnose 并报告 error。

最关键 E2E 测试通过新 subprocess 启动 CLI，并在进程启动前设置临时 `HOME` 和 `BRAIN_DIR`，保证 Codex/Claude 模块级 `Path.home()` 常量绑定到隔离目录；不得只在已导入 CLI 的测试进程内修改 `HOME`。测试先安装 Codex adapter，随后修改 AMH Hook command 而不刷新 trust：

1. `adapter doctor codex` 必须退出 1。
2. 总 `doctor` 也必须退出 1，并输出 `not trusted` 和修复命令。
3. 重新执行 adapter install 后，两条 doctor 链路恢复到非 error。

## 验证计划

Focused：

```bash
sh -n benchmarks/quickstart-60s.sh
python -m pytest tests/unit/test_quickstart_isolation.py -q
python -m pytest tests/unit/test_cli_doctor_adapters.py \
  tests/unit/test_cli_adapter.py \
  tests/unit/test_update_repair_cli.py -q
```

集成：

```bash
./benchmarks/quickstart-60s.sh
python -m pytest tests/ -q
ruff check agent_brain/platform/adapter_health.py \
  agent_brain/interfaces/cli/commands/doctor.py \
  tests/unit/test_cli_doctor_adapters.py \
  tests/unit/test_quickstart_isolation.py
git diff --check
```

Dogfood 只读验收：

```bash
memory doctor
memory adapter doctor codex --format json
memory adapter doctor claude_code --format json
```

## 交付与发布边界

- 分支：`codex/install-health-hotfix`
- 独立 PR 合入 `main`，不混入 `codex/p0-injection-gateway`。
- 本轮不直接合并 PR、不创建 Release、不修改版本号。
- 合并并完成发布检查后，可将兼容补丁发布为 v1.1.x。
- Injection Gateway 保持独立审阅；若保留 SDK 默认行为变化，则按下一重大版本附迁移说明发布。

## 成功标准

- Quickstart 无论成功或失败都不会修改宿主 HOME。
- 已配置 Codex 的 Hook trust 失效时，总 doctor 与 adapter doctor 都返回非零。
- 未安装 AMH Codex/Claude adapter 的用户不会因总 doctor 新增聚合而收到假错误。
- adapter warn 不会破坏现有脚本的成功退出语义。
- `doctor --fix` 不能在 adapter 复诊仍 error 时返回成功。
- Hotfix 可以独立审阅、回滚和发布。
