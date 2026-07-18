# 多 Agent 产品化治理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把现有 adapter 接入能力收敛成版本化 manifest、六阶段真实状态、统一生命周期合同、可过期证据、单 adapter 发布控制和可复核发布门禁，并以 Codex/Qoder 与 Claude Code/QoderWork 两批真实链路证明合同可复用。

**Architecture:** 保留现有 `AdapterBase`、doctor、runtime event、verification record 和 onboarding 主链路，在其上新增三个聚焦模块：静态 manifest 是能力事实源，lifecycle record 是变更与 provenance 事实源，release control 是 shadow/canary/default/disabled 事实源。`AdapterCapability` 只做只读投影，将 doctor、runtime、injection cohort、verification 与 TTL 合成为六阶段状态；CLI、Web、文档和 CI 全部消费同一投影，不维护手工数字。

**Tech Stack:** Python 3.11/3.12、dataclass、Typer、FastAPI、JSONL、pytest、ruff、mypy、GitHub Actions。

---

## 文件边界

- 新建 `agent_brain/agent_integrations/manifests.py`：manifest v1 数据结构、16 个 adapter 声明和完整性校验；不读本机状态。
- 新建 `agent_brain/agent_integrations/lifecycle_records.py`：低敏生命周期/provenance JSONL、稳定 reason code、freshness 计算；不执行安装。
- 新建 `agent_brain/agent_integrations/release_controls.py`：单 adapter shadow/canary/default/disabled 与 kill switch；不修改 core memory 配置。
- 修改 `agent_brain/agent_integrations/capabilities.py`：把 manifest、doctor、runtime、cohort、verification 和 TTL 投影为六阶段状态。
- 修改 `agent_brain/product/adapter_onboarding.py`：统一 install/doctor/verify/repair/upgrade/uninstall 事务与 JSON 合同。
- 修改 `agent_brain/interfaces/cli/commands/adapters.py`、`web/api/routes/adapters.py`：CLI/Web 只调用 product 层并暴露一致结果。
- 新建 `scripts/generate-adapter-governance.py`：从 manifest 和冻结证据夹具生成机器/人类可读报告并支持 `--check`。
- 新建 `tests/fixtures/adapter_productization_evidence.json`：确定性证据场景，不包含 prompt、正文、绝对私有路径或 token。
- 新建 `docs/evaluation/stage3-adapter-productization-report.json` 与 `docs/evaluation/stage3-adapter-productization-readiness.zh.md`：生成物。

### Task 1: 冻结 manifest v1 与六阶段状态模型

**Files:**
- Create: `agent_brain/agent_integrations/manifests.py`
- Modify: `agent_brain/agent_integrations/capabilities.py`
- Test: `tests/unit/test_adapter_manifests.py`
- Test: `tests/unit/test_adapter_capabilities.py`

- [x] **Step 1: 写 manifest 完整性和状态语义的失败测试**

```python
def test_every_registered_adapter_has_complete_v1_manifest(tmp_path):
    from agent_brain.agent_integrations.manifests import manifests_for_all

    manifests = manifests_for_all(tmp_path)
    assert len(manifests) == 16
    assert {item.schema_version for item in manifests} == {"amh-adapter-manifest/v1"}
    assert all(item.lifecycle.install and item.lifecycle.uninstall for item in manifests)
    assert all(item.evidence.runtime_ttl_seconds > 0 for item in manifests)


def test_verified_requires_fresh_doctor_runtime_and_injection(tmp_path):
    cap = _capability_with_frozen_fresh_evidence(tmp_path, adapter="codex")
    assert cap.states == {
        "implemented": True,
        "installed": True,
        "configured": True,
        "doctor_passed": True,
        "runtime_observed": True,
        "context_injected": True,
    }
    assert cap.verified is True
```

- [x] **Step 2: 运行测试确认当前没有 manifest/六状态字段**

Run: `.venv/bin/pytest -q tests/unit/test_adapter_manifests.py tests/unit/test_adapter_capabilities.py`

Expected: FAIL，原因是 `agent_brain.agent_integrations.manifests` 不存在或 `AdapterCapability.states` 不存在。

- [x] **Step 3: 实现不可变 manifest v1**

```python
MANIFEST_SCHEMA_VERSION = "amh-adapter-manifest/v1"

@dataclass(frozen=True)
class AdapterEvidencePolicy:
    runtime_types: tuple[str, ...]
    runtime_ttl_seconds: int
    context_ttl_seconds: int
    verification_ttl_seconds: int

@dataclass(frozen=True)
class AdapterManifest:
    schema_version: str
    adapter_id: str
    adapter_version: str
    platforms: tuple[str, ...]
    client_version_range: str
    hook_events: tuple[str, ...]
    payload_schema: str
    output_protocol: str
    channels: tuple[str, ...]
    lifecycle: AdapterLifecycleCommands
    evidence: AdapterEvidencePolicy
    feature_flag: str
    degrade_mode: str
    rollback_mode: str
```

为 16 个 registry key 提供显式声明；Codex/Qoder/Claude Code/QoderWork 声明真实 hook event/output protocol，其余 adapter 对不支持的 channel 使用空 tuple，不用虚假占位能力。

- [x] **Step 4: 在 capability 投影中加入 manifest 和六阶段状态**

```python
@dataclass(frozen=True)
class AdapterCapability:
    # 保留现有兼容字段
    manifest: dict[str, object]
    states: dict[str, bool]
    evidence_freshness: dict[str, object]
    verified: bool
    verification_blockers: list[str]
```

`verified` 必须同时满足 `configured`、`doctor_passed`、`runtime_observed`、`context_injected`、最新 passed verification 未过期且 adapter 未被 kill switch 禁用；旧 `support_level` 继续由该结果兼容投影。

- [x] **Step 5: 运行聚焦测试并提交**

Run: `.venv/bin/pytest -q tests/unit/test_adapter_manifests.py tests/unit/test_adapter_capabilities.py tests/unit/test_adapter_runtime_events.py`

Expected: PASS。

```bash
git add agent_brain/agent_integrations/manifests.py agent_brain/agent_integrations/capabilities.py tests/unit/test_adapter_manifests.py tests/unit/test_adapter_capabilities.py
git commit -m "feat: add versioned adapter capability manifests"
```

### Task 2: 增加可过期证据与低敏 provenance

**Files:**
- Create: `agent_brain/agent_integrations/lifecycle_records.py`
- Modify: `agent_brain/agent_integrations/capabilities.py`
- Test: `tests/unit/test_adapter_lifecycle_records.py`
- Test: `tests/unit/test_adapter_runtime_events.py`

- [x] **Step 1: 写 TTL、顺序和隐私失败测试**

```python
def test_stale_runtime_and_verification_cannot_keep_adapter_verified(tmp_path):
    now = datetime(2026, 7, 19, tzinfo=timezone.utc)
    _write_old_runtime_verification_and_cohort(tmp_path, now=now - timedelta(days=8))
    state = lifecycle_evidence_summary(tmp_path, "codex", now=now)
    assert state.runtime.fresh is False
    assert state.verification.fresh is False
    assert state.context_injection.fresh is False


def test_lifecycle_record_is_low_sensitive(tmp_path):
    record = record_lifecycle_event(tmp_path, adapter="codex", action="install", status="passed", reason_code="OK")
    serialized = json.dumps(record.to_dict())
    assert "prompt" not in serialized.lower()
    assert "token" not in serialized.lower()
    assert record.package_version
    assert record.commit
```

- [x] **Step 2: 运行测试确认当前 evidence 永不过期且没有 provenance 记录**

Run: `.venv/bin/pytest -q tests/unit/test_adapter_lifecycle_records.py tests/unit/test_adapter_runtime_events.py`

Expected: FAIL，原因是 `lifecycle_evidence_summary` 和 `record_lifecycle_event` 不存在。

- [x] **Step 3: 实现 append-only lifecycle record 与 freshness**

```python
LifecycleAction = Literal["install", "verify", "doctor", "repair", "upgrade", "uninstall", "release"]
LifecycleStatus = Literal["passed", "failed", "blocked"]
LifecycleReasonCode = Literal[
    "OK", "UNKNOWN_ADAPTER", "ADAPTER_WIP", "ADAPTER_DISABLED",
    "CLIENT_MISSING", "CONFIG_MALFORMED", "DOCTOR_FAILED",
    "RUNTIME_MISSING", "CONTEXT_MISSING", "EVIDENCE_STALE",
    "OWNERSHIP_CONFLICT", "BACKUP_FAILED", "ROLLBACK_FAILED",
    "INVALID_PROMOTION", "INTERNAL_ERROR",
]
```

记录只包含 adapter/action/status/reason/timestamp/package_version/commit/manifest_version/artifact SHA-256/backup id/cohort，不写 prompt、transcript、memory body、tool arguments、secret 或原始绝对路径。

- [x] **Step 4: 给 capability evidence summary 增加 `now` 与 TTL 参数**

保持旧调用默认兼容；新 capability 路径显式传 manifest TTL。解析失败或未来时间戳按 stale/invalid 处理，不能提升 verified。

- [x] **Step 5: 运行聚焦测试并提交**

Run: `.venv/bin/pytest -q tests/unit/test_adapter_lifecycle_records.py tests/unit/test_adapter_runtime_events.py tests/unit/test_adapter_onboarding.py`

Expected: PASS。

```bash
git add agent_brain/agent_integrations/lifecycle_records.py agent_brain/agent_integrations/capabilities.py tests/unit/test_adapter_lifecycle_records.py tests/unit/test_adapter_runtime_events.py
git commit -m "feat: govern adapter evidence freshness and provenance"
```

### Task 3: 统一生命周期合同并补 repair/upgrade/rollback

**Files:**
- Modify: `agent_brain/product/adapter_onboarding.py`
- Modify: `agent_brain/agent_integrations/capabilities.py`
- Modify: `agent_brain/agent_integrations/lifecycle_records.py`
- Modify: `agent_brain/agent_integrations/__init__.py`
- Modify: `agent_brain/agent_integrations/codex.py`
- Modify: `agent_brain/agent_integrations/qoder.py`
- Modify: `agent_brain/agent_integrations/claude_code.py`
- Modify: `agent_brain/agent_integrations/qoder_work.py`
- Test: `tests/unit/test_adapter_onboarding.py`
- Create: `tests/system/test_adapter_lifecycle_contract.py`

- [x] **Step 1: 写统一结果、幂等、ownership 与失败回滚测试**

```python
def test_codex_lifecycle_is_repeatable_and_owned_only(isolated_codex_home):
    first = execute_adapter_action(isolated_codex_home.brain, "codex", "install")
    second = execute_adapter_action(isolated_codex_home.brain, "codex", "install")
    repaired = execute_adapter_action(isolated_codex_home.brain, "codex", "repair")
    removed = execute_adapter_action(isolated_codex_home.brain, "codex", "uninstall")
    removed_again = execute_adapter_action(isolated_codex_home.brain, "codex", "uninstall")
    assert [first.status, second.status, repaired.status, removed.status, removed_again.status] == ["passed"] * 5
    assert isolated_codex_home.user_hook_still_present()


def test_upgrade_failure_restores_owned_snapshot(isolated_qoder_home, monkeypatch):
    execute_adapter_action(isolated_qoder_home.brain, "qoder", "install")
    monkeypatch.setattr(isolated_qoder_home.adapter, "install", _raise_runtime_error)
    result = execute_adapter_action(isolated_qoder_home.brain, "qoder", "upgrade")
    assert result.status == "failed"
    assert result.rollback_status == "passed"
    assert isolated_qoder_home.owned_config_matches_pre_upgrade()
```

- [x] **Step 2: 运行失败测试**

Run: `.venv/bin/pytest -q tests/system/test_adapter_lifecycle_contract.py tests/unit/test_adapter_onboarding.py`

Expected: FAIL，原因是统一 executor、repair 和 upgrade 尚不存在。

- [x] **Step 3: 扩展 adapter 可选 ownership 接口**

```python
class AdapterBase(ABC):
    def owned_paths(self) -> tuple[Path, ...]:
        return ()
```

四个试点 adapter 只返回 AMH 管理的配置文件/脚本容器；备份写入 `$BRAIN_DIR/backups/adapters/<adapter>/<backup-id>/`，备份 manifest 保存相对槽位和 SHA-256。restore 只覆盖备份时已确认归属 AMH 的块或文件，遇到 ownership 冲突返回 `OWNERSHIP_CONFLICT`。

- [x] **Step 4: 实现统一 action executor**

```python
def execute_adapter_action(
    brain_dir: Path,
    name: str,
    action: Literal["install", "doctor", "verify", "repair", "upgrade", "uninstall"],
    *,
    verifier: str = "product",
    context_probe: bool = False,
) -> AdapterLifecycleResult:
    ...
```

所有出口返回 `schema_version/adapter/action/status/reason_code/message/state_before/state_after/evidence/repair_command/provenance`。repair 先 doctor，只调用幂等 install 修复 AMH-owned 漂移；upgrade 先备份、执行 install、doctor，失败自动 restore 并再次 doctor；uninstall 重复执行仍返回 passed/no-change。

- [x] **Step 5: 跑四 adapter 系统合同并提交**

Run: `.venv/bin/pytest -q tests/system/test_adapter_lifecycle_contract.py tests/unit/test_adapter_onboarding.py tests/unit/test_adapters.py`

Expected: Codex、Qoder、Claude Code、QoderWork 的 install/doctor/repair/upgrade/uninstall 合同全部 PASS。

```bash
git add agent_brain/product/adapter_onboarding.py agent_brain/agent_integrations/__init__.py agent_brain/agent_integrations/codex.py agent_brain/agent_integrations/qoder.py agent_brain/agent_integrations/claude_code.py agent_brain/agent_integrations/qoder_work.py tests/unit/test_adapter_onboarding.py tests/system/test_adapter_lifecycle_contract.py
git commit -m "feat: unify adapter lifecycle transactions"
```

### Task 4: 加入单 adapter cohort、kill switch 与 core 隔离

**Files:**
- Create: `agent_brain/agent_integrations/release_controls.py`
- Modify: `agent_brain/product/adapter_onboarding.py`
- Modify: `agent_runtime_kit/hooks/inject-context.sh`
- Test: `tests/unit/test_adapter_release_controls.py`
- Test: `tests/system/test_adapter_core_isolation.py`

- [ ] **Step 1: 写 promotion 与隔离失败测试**

```python
def test_release_control_requires_ordered_promotion(tmp_path):
    assert set_adapter_release(tmp_path, "codex", "shadow").status == "passed"
    assert set_adapter_release(tmp_path, "codex", "default").reason_code == "INVALID_PROMOTION"
    assert set_adapter_release(tmp_path, "codex", "canary").status == "passed"
    assert set_adapter_release(tmp_path, "codex", "default").status == "passed"


def test_disabled_adapter_does_not_disable_core_cli_or_mcp(tmp_path):
    set_adapter_release(tmp_path, "qoder", "disabled")
    assert run_memory_search(tmp_path).returncode == 0
    assert list_required_mcp_tools()
    assert run_qoder_hook(tmp_path).status == "adapter_disabled"
```

- [ ] **Step 2: 运行失败测试**

Run: `.venv/bin/pytest -q tests/unit/test_adapter_release_controls.py tests/system/test_adapter_core_isolation.py`

Expected: FAIL，原因是 release control 和 hook kill switch 不存在。

- [ ] **Step 3: 实现原子 release control**

```python
ReleaseStage = Literal["shadow", "canary", "default", "disabled"]

@dataclass(frozen=True)
class AdapterReleaseControl:
    adapter: str
    stage: ReleaseStage
    cohort_percent: int
    updated_at: str
    reason: str
```

记录存储在 `$BRAIN_DIR/runtime/adapter-release-controls.json`，0600、临时文件+replace 原子写。promotion 只允许 shadow→canary→default；任何阶段可进入 disabled；恢复必须回 shadow。

- [ ] **Step 4: 在 hook 最前面加入低成本 adapter 级 fail-open-to-core 检查**

禁用时 adapter hook 返回对应客户端的合法空上下文协议并记录低敏 `AdapterDisabled` runtime event；不退出 core 进程、不改 MCP/CLI 配置、不删除 memory 数据。

- [ ] **Step 5: 跑隔离测试并提交**

Run: `.venv/bin/pytest -q tests/unit/test_adapter_release_controls.py tests/system/test_adapter_core_isolation.py tests/unit/test_adapter_runtime_events.py && bash tests/hook-protocol/test-hook-protocol.sh`

Expected: PASS，严格协议 stdout 无污染。

```bash
git add agent_brain/agent_integrations/release_controls.py agent_brain/product/adapter_onboarding.py agent_runtime_kit/hooks/inject-context.sh tests/unit/test_adapter_release_controls.py tests/system/test_adapter_core_isolation.py
git commit -m "feat: add isolated adapter release controls"
```

### Task 5: 统一 CLI/Web 状态、命令和 reason code

**Files:**
- Modify: `agent_brain/interfaces/cli/commands/adapters.py`
- Modify: `web/api/routes/adapters.py`
- Modify: `agent_brain/product/cockpit.py`
- Test: `tests/unit/test_cli_adapter.py`
- Test: `tests/unit/test_web_api.py`
- Test: `tests/unit/test_cockpit_summary.py`

- [ ] **Step 1: 写 CLI/Web parity 失败测试**

```python
def test_cli_and_web_expose_same_six_state_contract(runner, client, admin_headers):
    cli = json.loads(runner.invoke(app, ["adapter", "list", "--format", "json"]).stdout)
    web = client.get("/api/adapters/capabilities", headers=admin_headers).json()
    assert cli == web
    assert set(cli[0]["states"]) == {
        "implemented", "installed", "configured", "doctor_passed", "runtime_observed", "context_injected"
    }
```

- [ ] **Step 2: 运行失败测试**

Run: `.venv/bin/pytest -q tests/unit/test_cli_adapter.py tests/unit/test_web_api.py tests/unit/test_cockpit_summary.py`

Expected: FAIL，当前 CLI/Web 没有 repair、upgrade、release 和统一 reason code。

- [ ] **Step 3: 将所有 mutating command 路由到统一 executor**

CLI 增加：

```text
memory adapter repair <name> --format json
memory adapter upgrade <name> --format json
memory adapter release <name> --stage shadow|canary|default|disabled --cohort-percent N --format json
```

Web 增加：

```text
POST /api/adapters/{name}/repair
POST /api/adapters/{name}/upgrade
POST /api/adapters/{name}/release?stage=...&cohort_percent=...
```

旧 install/verify/uninstall 路径保留，但响应改为同一个 schema；错误 HTTP 状态只由 reason code 映射，payload 不丢失。

- [ ] **Step 4: 更新 cockpit next_action**

优先级固定为：disabled→enable-shadow，未 implemented→unsupported，未 installed→install，未 configured/doctor→repair，stale→verify，未 runtime→wait-runtime，未 injection→trigger-recall，全部满足→verified。

- [ ] **Step 5: 跑 parity 测试并提交**

Run: `.venv/bin/pytest -q tests/unit/test_cli_adapter.py tests/unit/test_web_api.py tests/unit/test_cockpit_summary.py tests/unit/test_adapter_onboarding.py`

Expected: PASS。

```bash
git add agent_brain/interfaces/cli/commands/adapters.py web/api/routes/adapters.py agent_brain/product/cockpit.py tests/unit/test_cli_adapter.py tests/unit/test_web_api.py tests/unit/test_cockpit_summary.py
git commit -m "feat: expose adapter lifecycle governance surfaces"
```

### Task 6: 生成治理报告并建立 required CI gate

**Files:**
- Create: `tests/fixtures/adapter_productization_evidence.json`
- Create: `scripts/generate-adapter-governance.py`
- Create: `docs/evaluation/stage3-adapter-productization-report.json`
- Create: `docs/evaluation/stage3-adapter-productization-readiness.zh.md`
- Modify: `.github/workflows/governance-gates.yml`
- Modify: `tests/unit/test_docs_truth_contract.py`
- Create: `tests/unit/test_adapter_governance_report.py`

- [ ] **Step 1: 写生成物新鲜度和隐私失败测试**

```python
def test_stage3_report_is_fresh_and_manifest_derived(repo_root):
    report = json.loads((repo_root / "docs/evaluation/stage3-adapter-productization-report.json").read_text())
    assert report["schema_version"] == "amh-adapter-productization-report/v1"
    assert report["manifest_count"] == 16
    assert report["pilot_batches"][0]["adapters"] == ["codex", "qoder"]
    assert report["pilot_batches"][1]["adapters"] == ["claude_code", "qoder_work"]
    assert report["privacy"]["prohibited_field_count"] == 0
```

- [ ] **Step 2: 运行失败测试**

Run: `.venv/bin/pytest -q tests/unit/test_adapter_governance_report.py tests/unit/test_docs_truth_contract.py`

Expected: FAIL，报告和生成器尚不存在。

- [ ] **Step 3: 实现确定性生成器**

`scripts/generate-adapter-governance.py --check` 在以下任一情况退出 1：manifest 缺字段、夹具 schema/hash 不符、两个 pilot batch 未覆盖同一生命周期合同、reason code 非法、kill switch/core isolation 未通过、生成物与当前代码不同、公开字段命中 prompt/transcript/token/secret/私有绝对路径。

- [ ] **Step 4: 生成并检查报告**

Run: `.venv/bin/python scripts/generate-adapter-governance.py && .venv/bin/python scripts/generate-adapter-governance.py --check`

Expected: `adapter-governance: PASS manifests=16 batches=2 privacy=PASS`。

- [ ] **Step 5: 增加稳定 CI job**

在 `.github/workflows/governance-gates.yml` 新增 job id/name `adapter-governance`，执行聚焦 lifecycle/system tests 和生成器 `--check`；禁止 `continue-on-error`，并在 docs truth test 中固定 job 名和命令。

- [ ] **Step 6: 运行 gate 并提交**

Run: `.venv/bin/pytest -q tests/unit/test_adapter_governance_report.py tests/unit/test_docs_truth_contract.py && .venv/bin/python scripts/generate-adapter-governance.py --check`

Expected: PASS。

```bash
git add tests/fixtures/adapter_productization_evidence.json scripts/generate-adapter-governance.py docs/evaluation/stage3-adapter-productization-report.json docs/evaluation/stage3-adapter-productization-readiness.zh.md .github/workflows/governance-gates.yml tests/unit/test_docs_truth_contract.py tests/unit/test_adapter_governance_report.py
git commit -m "ci: require adapter productization evidence"
```

### Task 7: 真机证据、发布一致性和三阶段总审计

**Files:**
- Modify: `docs/evaluation/stage3-adapter-productization-report.json`
- Modify: `docs/evaluation/stage3-adapter-productization-readiness.zh.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/architecture.md`
- Modify: `.github/workflows/governance-gates.yml`

- [ ] **Step 1: 在独立备份下跑第一批真机生命周期**

Run:

```bash
memory adapter install-verify codex --context-probe --format json
memory adapter repair codex --format json
memory adapter upgrade codex --format json
memory adapter install-verify qoder --context-probe --format json
memory adapter repair qoder --format json
memory adapter upgrade qoder --format json
```

Expected: 每条均为 `status=passed`、稳定 reason code、fresh provenance；不清理用户自定义 hook。若客户端当前不提供可证明 context 的 transcript，报告必须保留 `context_injected=false`/blocker，不能伪造 verified。

- [ ] **Step 2: 跑第二批同合同真机/隔离证据**

Run:

```bash
memory adapter install-verify claude_code --context-probe --format json
memory adapter repair claude_code --format json
memory adapter upgrade claude_code --format json
memory adapter install-verify qoder_work --context-probe --format json
memory adapter repair qoder_work --format json
memory adapter upgrade qoder_work --format json
```

Expected: 输出 schema/reason/provenance 与第一批一致；任何缺少真实客户端证据的项保持 blocker。

- [ ] **Step 3: 验证包版本、commit、hook hash 与文档一致**

Run: `.venv/bin/python scripts/generate-adapter-governance.py --check && git diff --exit-code docs/evaluation/stage3-adapter-productization-report.json docs/evaluation/stage3-adapter-productization-readiness.zh.md`

Expected: PASS，报告记录当前 `agent_brain.__version__`、HEAD、hook SHA-256 和 manifest version。

- [ ] **Step 4: 跑完整三阶段退出门禁**

Run:

```bash
.venv/bin/ruff check .
.venv/bin/python scripts/check-mypy-baseline.py
.venv/bin/pytest -q tests/unit
.venv/bin/pytest -q tests/system
.venv/bin/pytest -q tests/conformance
bash tests/hook-protocol/test-hook-protocol.sh
.venv/bin/python scripts/check-recall-quality.py
.venv/bin/python scripts/generate-adapter-governance.py --check
```

Expected: 全部 PASS；mypy 不新增债务；stage1 security/release gates、stage2 recall-quality、stage3 adapter-governance 均保留 required job。

- [ ] **Step 5: 更新公开文档和提交**

文档只引用生成报告数字；明确“implemented/installed/configured/doctor/runtime/injected”区别、evidence TTL、kill switch 和真实 blocker，不把 install-ready 写成 verified。

```bash
git add docs/evaluation/stage3-adapter-productization-report.json docs/evaluation/stage3-adapter-productization-readiness.zh.md README.md CHANGELOG.md docs/architecture.md .github/workflows/governance-gates.yml
git commit -m "docs: publish multi-agent productization evidence"
git push origin codex/dual-route-recall
```

- [ ] **Step 6: 等待远端 required checks 并复核保护规则**

Run: `gh pr checks 4 --watch && gh api repos/liuyang0508/Agent-Memory-Hub/branches/main/protection/required_status_checks`

Expected: unit (3.11)、unit (3.12)、hook-tests、security、benchmark-integrity、docker-smoke、recall-quality、adapter-governance 全绿且 strict=true。

---

## 自审结果

- 规格 6.1：Task 1/2 覆盖版本化 manifest、六状态和 TTL。
- 规格 6.2：Task 3/5 覆盖幂等 install、doctor、repair、upgrade/rollback、owned-only uninstall、JSON reason code。
- 规格 6.3：Task 4/7 覆盖两批试点、shadow→canary→default 和单 adapter 隔离。
- 规格 6.4：Task 2/3/5/6 覆盖 provenance、CLI/Web、生成文档、cohort/kill switch。
- 规格 6.5：Task 7 覆盖真机合同、第二批复用、发布一致性、core 存活和 required checks。
- 未引入模型改写、远端控制面或全 adapter 一次性升级；这些不属于阶段三退出所需最小闭环。
