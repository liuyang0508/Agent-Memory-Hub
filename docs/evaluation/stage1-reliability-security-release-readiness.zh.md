# 阶段一：可靠性、安全与发布门禁验收报告

日期：2026-07-19（Asia/Shanghai）

冻结功能提交：`58892271d6c6a1c76257922d3cf9a07a8b068d58`

分支：`codex/dual-route-recall`

PR：[liuyang0508/Agent-Memory-Hub#4](https://github.com/liuyang0508/Agent-Memory-Hub/pull/4)

## 结论

阶段一的六类已确认风险均已关闭：Qoder transcript 非对象 JSON 不再使 doctor
崩溃；MemoryData 空产物和不完整产物不再误报通过；实时通道不再把长期 JWT 放进
URL；SDK、CLI、WriteService、Web 缓存的资源所有权已显式关闭；默认 Docker 镜像可
从空环境构建并完成鉴权和持久化重启；核心 CI 已变成 required checks，`main` 已启用
严格分支保护。

本结论只覆盖阶段一。它不把召回准确率增强或多 Agent 产品化提前写成已完成；这两项
分别进入阶段二和阶段三。

## 冻结代码与环境

- 本地工作树：macOS，Python 3.12，仓库 `.venv`；所有 Python 验收命令固定使用
  `.venv/bin/python -m ...`，避免依赖系统 Python 恰好装过的包。
- GitHub：Ubuntu runner，Python 3.11 / 3.12；Docker job 使用 runner 的真实 Docker
  daemon，从 `deploy/Dockerfile` 构建，不复用本地镜像。
- 功能冻结提交：`5889227`。之后的验收报告提交只修改文档和 CHANGELOG，不改变产品
  行为。
- mypy 基线为 702 条已审计历史指纹；门禁拒绝新增指纹，治理关键模块另执行零基线
  严格检查。基线只能用显式 `--write-baseline` 更新。

## 已关闭风险逐项证据

| 风险 | 修复合同 | 当前证据 |
|---|---|---|
| Qoder/QoderWork transcript 崩溃 | JSON 解码后先校验对象类型；scalar、损坏行降级而不崩溃 | `.venv/bin/python -m pytest tests/unit/test_adapters.py::TestQoderAdapterRealInstall::test_qoder_transcript_discovery_skips_non_object_json_rows -q`：1 passed；Qoder 定向模块：38 passed |
| MemoryData 假绿 | 每次隔离 run 目录；必须有新鲜、合法、足量且无失败行的结果；run record 原子写入 | `.venv/bin/python -m pytest 'tests/unit/test_external_memory_benchmark.py::test_memorydata_zero_exit_requires_complete_fresh_results[None-no fresh MemoryData result artifacts]' -q`：1 passed；完整模块：23 passed |
| 长期 JWT 进入实时 URL/日志 | 同源浏览器使用 HttpOnly cookie；非 cookie 客户端使用 60 秒单用途 ticket；拒绝 `token=` 并在响应前清空 ASGI query scope | `.venv/bin/python -m pytest tests/unit/test_realtime_auth.py -q`：8 passed；SSE/WS sentinel 均证明 access-log scope 与 close reason 不含长期 JWT |
| sqlite/索引资源泄漏 | 创建者负责关闭；`HubIndex`、SDK、WriteService、CLI lifecycle、Web cache 均显式 close | `MEMORY_HUB_TEST_EMBEDDING=1 .venv/bin/python -m pytest tests/unit/test_resource_lifecycle.py::test_memory_client_context_manager_returns_fd_to_baseline -q`：30 轮后 1 passed |
| Docker 依赖和健康检查失真 | 镜像显式安装 Web 与 embeddings extra；安装失败即构建失败；HTTP health、鉴权、持久卷重启为同一 smoke 合同 | [governance-gates run 29656943521](https://github.com/liuyang0508/Agent-Memory-Hub/actions/runs/29656943521) 的 `docker-smoke` job |
| CI 可绕过 | lint、type、3.11/3.12、hook、安全、benchmark integrity、Docker 均为 required check；管理员同样受保护 | `gh api repos/liuyang0508/Agent-Memory-Hub/branches/main/protection` 的当前 readback |

## 兼容和升级边界

- REST API 的 `Authorization: Bearer` 继续兼容；浏览器实时连接改用登录后设置的
  `amh_session` HttpOnly cookie。
- 无 cookie 的 WebSocket/SSE 客户端先用 Bearer 调用
  `POST /api/auth/realtime-ticket`，再用 `?ticket=<短时单用途票据>` 连接。长期
  `?token=<JWT>` 默认拒绝，不提供隐式回退。
- SDK 旧调用仍可用，但长期进程应改成 `with MemoryClient(...) as client:`，或在结束时
  显式 `client.close()`。
- Docker 使用者必须重新 build/pull 新镜像；旧镜像不会自动获得依赖与 healthcheck
  修复。数据目录仍挂载到 `/data/brain`，无 schema 迁移。
- 已安装旧 hook 的用户仍需执行
  `memory self-update --repair-hooks && memory doctor --fix`，或重新执行幂等 adapter
  install/verify；本阶段没有偷偷改写用户已安装的 hook 文件。
- 回滚时可回退应用版本，但不能恢复在 URL 中传长期 JWT；非 cookie 客户端应回滚到
  Bearer 换 ticket 的适配版本。

## CI 与 main 保护证据

冻结功能提交对应三组工作流：

- [python-tests run 29656943533](https://github.com/liuyang0508/Agent-Memory-Hub/actions/runs/29656943533)：
  `unit (3.11)`、`unit (3.12)`；每个 job 内含 lint、mypy 无新增、治理关键模块严格
  type check、unit 和 CLI/MCP conformance。
- [Hook unit tests run 29656943518](https://github.com/liuyang0508/Agent-Memory-Hub/actions/runs/29656943518)：
  `hook-tests`。
- [governance-gates run 29656943521](https://github.com/liuyang0508/Agent-Memory-Hub/actions/runs/29656943521)：
  `security`、`benchmark-integrity`、`docker-smoke`。

`main` 保护的权威 readback 为：

- `required_status_checks.strict=true`；
- contexts：`unit (3.11)`、`unit (3.12)`、`hook-tests`、`security`、
  `benchmark-integrity`、`docker-smoke`；
- `enforce_admins.enabled=true`；
- 至少 1 个 approving review，且 `dismiss_stale_reviews=true`；
- force push 和 branch deletion 均关闭。

Gitee、npm 和官网部署属于 distribution checks，不混入普通 PR 的核心质量结论。外部
secret 缺失时工作流输出结构化 `missing_secret` 原因；本报告将其标为外部分发阻塞，
不伪装成核心代码 PASS，也不把它误报成代码失败。

## Docker 与运行验证

`./scripts/docker-smoke.sh` 的真实 CI 路径覆盖：

1. 从仓库构建 `deploy/Dockerfile`；
2. 创建命名卷并把 `/data/brain` 挂载进去；
3. 启动 no-model profile，轮询真实 `/api/health`；
4. 初始化管理员并用 bearer 访问 `/api/auth/me`；
5. `docker restart` 后重新解析 runner 分配的宿主端口；
6. 再次 health，并使用同一管理员密码登录，证明持久卷有效。

本地没有可用 Docker daemon，因此不把静态合同测试冒充真实容器运行；真实 Docker
结论只引用 GitHub `docker-smoke` job。

## 失败历史和最终确认运行

- 首轮 Docker smoke 暴露 GitHub runner 在匿名宿主端口下重启后可能重新分配端口；
  应用、容器内 health 和持久数据均正常，但脚本错误复用了旧端口。最终脚本在重启后
  重新解析 `docker port`，并在失败时输出 inspect、health、ports 和 container logs。
- 初版 CI 在干净 runner 暴露 optional import 与 FastAPI/typing 版本差异；已用明确的
  mypy optional-import 边界和跨版本 Protocol 合同修复，没有把 type job 改回
  `continue-on-error`。
- 本地验收计划曾引用系统 `pytest` 和不存在的 `test_web_component_cache.py`；现固定
  使用仓库 `.venv`，文件名修正为 `test_web_cache_lifecycle.py`。
- 旧失败 run 只保留为诊断证据；阶段一 PASS 只依据上述冻结提交的 fresh runs。

本地当前确认：

- `ruff check .`：PASS；
- `python scripts/check_mypy_baseline.py`：702/702，0 新增；
- unit：2643 passed，2 skipped（两个显式 opt-in few-shot case）；
- CLI/MCP conformance：4 passed；hook shell：6 passed；tenant/schema shell：2 passed；
- quickstart：26 秒，目标小于 120 秒。

## 阶段一退出门禁对照表

| 退出门禁 | 状态 | 证据 |
|---|---|---|
| Qoder/QoderWork 对 scalar 和损坏行不崩溃 | PASS | Qoder 定向回归与 scalar 单例探针 |
| MemoryData 空、旧、少行、失败行、schema 错误均失败 | PASS | 23 项 runner 回归与 benchmark-integrity job |
| WebSocket/SSE URL 与日志不含长期 JWT | PASS | 8 项实时认证测试、ASGI access-log scope sentinel |
| 循环操作后 FD 回到稳定窗口 | PASS | 30 轮 SDK context-manager 探针 |
| Docker 空环境构建并通过真实服务 smoke | PASS | GitHub `docker-smoke` |
| 本地全量和 GitHub required checks 全绿 | PASS | 本报告本地结果与三组 fresh workflow |
| `main` branch protection 生效 | PASS | GitHub protection readback |
| 升级、兼容、回滚边界明确 | PASS | 本报告“兼容和升级边界”与 CHANGELOG |

## 阶段二准入结论

阶段一退出条件已满足，允许进入阶段二“召回质量增强”。阶段二必须继续保持当前
Gateway、租户、敏感级别和 fail-closed 门禁，不能为了提高 recall 扩大授权范围；其
完成标准另按 retrieval、admission、answerability、temporal、abstention、injection
六层指标验收，不能用单一 R@K 代表端到端正确率。
