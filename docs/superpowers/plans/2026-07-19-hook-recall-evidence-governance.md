# Hook Recall Evidence Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the required recall-quality gate execute the real UserPromptSubmit hook and fail closed on stale, partial, surface-mismatched, or inconsistent recall evidence.

**Architecture:** Extend the versioned production replay corpus with an explicit Hook surface contract, run applicable cases through an isolated real `inject-context.sh` process, and produce a low-sensitivity Run Manifest. A separate validator verifies provenance, case closure, protocol/cohort consistency, and expected injection before CI accepts the run.

**Tech Stack:** Python 3.11/3.12, dataclasses, Pydantic-backed MemoryItem fixtures, SQLite/FTS via HubIndex, Bash UserPromptSubmit hook, pytest, GitHub Actions.

---

## File map

- Modify `agent_brain/evaluation/recall_quality_corpus.py`: parse and validate corpus schema v2 plus `hook_expectation`.
- Create `agent_brain/evaluation/hook_recall_evidence.py`: Run Manifest contracts, deterministic gate derivation, serialization and independent validation.
- Create `agent_brain/evaluation/hook_recall_runner.py`: isolated brain materialization, real hook subprocess execution, cohort/gap correlation and manifest generation.
- Create `scripts/run-hook-recall-evidence.py`: thin runner CLI that always attempts to materialize a terminal manifest.
- Create `scripts/check-hook-recall-evidence.py`: independent strict verifier CLI.
- Modify `tests/fixtures/recall_quality_production_replay_v1.json`: migrate to schema/corpus v2 and declare Hook surface applicability.
- Modify `tests/unit/test_recall_quality_corpus.py`: schema v2 positive and fail-closed tests.
- Create `tests/unit/test_hook_recall_evidence.py`: manifest validation and privacy tests.
- Create `tests/unit/test_hook_recall_runner.py`: protocol parsing and process-result correlation tests.
- Create `tests/system/test_hook_recall_evidence.py`: full production replay through the real hook.
- Modify `tests/unit/test_ci_governance_contract.py`: require the runner, verifier and artifact upload in CI.
- Modify `.github/workflows/governance-gates.yml`: generate, verify and upload fresh Hook evidence in `recall-quality`.
- Modify `scripts/check-recall-quality.py`: state the internal six-layer versus real-Hook evidence boundary.
- Regenerate `docs/evaluation/stage2-recall-quality-report.json` and `docs/evaluation/stage2-recall-quality-readiness.zh.md`.
- Modify `CHANGELOG.md`: document the new fail-closed real-Hook gate.

### Task 1: Add the Hook surface contract to the corpus

**Files:**
- Modify: `agent_brain/evaluation/recall_quality_corpus.py`
- Modify: `tests/fixtures/recall_quality_production_replay_v1.json`
- Modify: `tests/unit/test_recall_quality_corpus.py`

- [ ] **Step 1: Write failing schema v2 tests**

Add tests that require an applicable Hook expectation and a bounded not-applicable reason:

```python
def test_hook_expectation_requires_complete_applicable_contract(tmp_path: Path) -> None:
    payload = _valid_payload()
    payload["schema_version"] = 2
    payload["cases"][0]["hook_expectation"] = {"applicable": True}
    path = _write(tmp_path, payload)

    with pytest.raises(ValueError, match="applicable hook expectation"):
        load_recall_quality_corpus(path)


def test_explicit_project_case_can_be_not_applicable_to_hook(tmp_path: Path) -> None:
    payload = _valid_payload()
    payload["schema_version"] = 2
    payload["cases"][0]["hook_expectation"] = {
        "applicable": False,
        "reason": "explicit_project_scope_unavailable",
    }

    corpus = load_recall_quality_corpus(_write(tmp_path, payload))

    assert corpus.cases[0].hook_expectation.applicable is False
    assert corpus.cases[0].hook_expectation.reason == "explicit_project_scope_unavailable"
```

- [ ] **Step 2: Run the tests and confirm RED**

Run:

```bash
MEMORY_HUB_TEST_EMBEDDING=1 .venv/bin/python -m pytest \
  tests/unit/test_recall_quality_corpus.py -q
```

Expected: FAIL because `RecallQualityCase` has no `hook_expectation` and schema version 2 is unsupported.

- [ ] **Step 3: Implement the Hook expectation contract**

Add these contracts and strict parsing to `recall_quality_corpus.py`:

```python
HookExpectedStatus = Literal["injected", "empty"]
_HOOK_NOT_APPLICABLE_REASONS = frozenset({
    "explicit_project_scope_unavailable",
})


@dataclass(frozen=True)
class HookExpectation:
    applicable: bool
    cwd: str | None = None
    expected_status: HookExpectedStatus | None = None
    expected_item_ids: tuple[str, ...] = ()
    prohibited_item_ids: tuple[str, ...] = ()
    reason: str | None = None


def _parse_hook_expectation(value: Any) -> HookExpectation:
    if not isinstance(value, dict) or type(value.get("applicable")) is not bool:
        raise ValueError("hook_expectation must declare boolean applicable")
    if value["applicable"]:
        required = {"applicable", "cwd", "expected_status", "expected_item_ids", "prohibited_item_ids"}
        if set(value) != required:
            raise ValueError("applicable hook expectation must use the complete contract")
        cwd = value["cwd"]
        status = value["expected_status"]
        if not isinstance(cwd, str) or not cwd.startswith("/sanitized/"):
            raise ValueError("hook expectation cwd must be sanitized")
        if status not in {"injected", "empty"}:
            raise ValueError("unsupported hook expected status")
        expected = _string_tuple(value["expected_item_ids"], "hook expected_item_ids")
        prohibited = _string_tuple(value["prohibited_item_ids"], "hook prohibited_item_ids")
        if set(expected) & set(prohibited):
            raise ValueError("hook expected and prohibited item ids overlap")
        if (status == "injected") != bool(expected):
            raise ValueError("hook injected expectation requires expected item ids")
        return HookExpectation(True, cwd, status, expected, prohibited)
    if set(value) != {"applicable", "reason"}:
        raise ValueError("not-applicable hook expectation only accepts reason")
    reason = value["reason"]
    if reason not in _HOOK_NOT_APPLICABLE_REASONS:
        raise ValueError("unsupported hook not-applicable reason")
    return HookExpectation(False, reason=reason)
```

Require corpus `schema_version == 2`, add `hook_expectation` to required case fields, add the parsed value to `RecallQualityCase`, and export `HookExpectation`.

- [ ] **Step 4: Migrate the committed fixture**

Set:

```json
"schema_version": 2,
"corpus_version": "production-replay-v2"
```

For the eleven Hook-expressible cases, add an applicable expectation using a `/sanitized/...` cwd and the same injected/empty outcome as the top-level contract. For `prod-project-mismatch`, add:

```json
"hook_expectation": {
  "applicable": false,
  "reason": "explicit_project_scope_unavailable"
}
```

- [ ] **Step 5: Run corpus and system replay tests**

Run:

```bash
MEMORY_HUB_TEST_EMBEDDING=1 .venv/bin/python -m pytest \
  tests/unit/test_recall_quality_corpus.py \
  tests/system/test_recall_quality_replay.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit the corpus contract**

```bash
git add agent_brain/evaluation/recall_quality_corpus.py \
  tests/fixtures/recall_quality_production_replay_v1.json \
  tests/unit/test_recall_quality_corpus.py
git commit -m "test: declare real hook recall surface expectations"
```

### Task 2: Implement the fail-closed Run Manifest contract

**Files:**
- Create: `agent_brain/evaluation/hook_recall_evidence.py`
- Create: `tests/unit/test_hook_recall_evidence.py`

- [ ] **Step 1: Write manifest failure tests**

Create fixtures with three cases: injected, empty and not-applicable. Assert that the validator rejects missing/duplicate results, wrong hashes, stdout/cohort mismatch, a prohibited ID and a false PASS status:

```python
def test_manifest_rejects_false_pass_with_missing_case() -> None:
    manifest = _valid_manifest()
    manifest["results"] = manifest["results"][:-1]
    manifest["status"] = "pass"

    failures = validate_hook_recall_manifest(manifest, expected=_expected())

    assert "G0:planned_result_mismatch" in failures
    assert "G0:false_pass_status" in failures


def test_manifest_rejects_stdout_cohort_divergence() -> None:
    manifest = _valid_manifest()
    manifest["results"][0]["cohort_item_ids"] = ["mem-other"]

    failures = validate_hook_recall_manifest(manifest, expected=_expected())

    assert "G1:stdout_cohort_mismatch:case-injected" in failures
```

Also serialize the manifest and assert that `raw_prompt`, `stdout`, `stderr`, `session_id`, real cwd, memory body and token-shaped sentinels do not appear.

- [ ] **Step 2: Run the new test and confirm RED**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_hook_recall_evidence.py -q
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement immutable result and provenance types**

Create `hook_recall_evidence.py` with bounded dataclasses:

```python
@dataclass(frozen=True)
class HookCaseEvidence:
    case_id: str
    applicable: bool
    expected_status: str | None
    actual_status: str
    expected_item_ids: tuple[str, ...]
    observed_item_ids: tuple[str, ...]
    prohibited_item_ids: tuple[str, ...]
    cohort_item_ids: tuple[str, ...]
    protocol_valid: bool
    cohort_consistent: bool
    gap_consistent: bool
    exit_code: int | None
    duration_ms: float
    reason: str

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        for field in ("expected_item_ids", "observed_item_ids", "prohibited_item_ids", "cohort_item_ids"):
            data[field] = list(data[field])
        return data


@dataclass(frozen=True)
class HookRecallExpectedProvenance:
    git_commit: str
    hook_sha256: str
    implementation_sha256: str
    corpus_sha256: str
    corpus_version: str
    config_sha256: str
    require_clean: bool = False
```

The manifest is a plain JSON object assembled by `build_hook_recall_manifest(...)`; the independent validator accepts only dictionaries so the verifier does not trust runner objects.

- [ ] **Step 4: Implement deterministic gate derivation**

Implement `validate_hook_recall_manifest(payload, *, expected)` with these exact checks:

```python
def validate_hook_recall_manifest(payload: object, *, expected: HookRecallExpectedProvenance) -> list[str]:
    failures: list[str] = []
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return ["G0:invalid_manifest_schema"]
    _check_provenance(payload, expected, failures)
    results = _validated_results(payload.get("results"), failures)
    _check_case_closure(payload, results, failures)
    for result in results:
        _check_case_result(result, failures)
    derived = "pass" if not failures else "fail"
    if payload.get("status") != derived:
        failures.append("G0:false_pass_status" if payload.get("status") == "pass" else "G0:incorrect_terminal_status")
    return sorted(set(failures))
```

Use full-match regexes for the 40-hex git commit, UUID run ID and `sha256:<64 hex>` digests. Require completed timestamps, exact planned/applicable/not-applicable/executed count closure, unique result IDs, and one result per planned case.

- [ ] **Step 5: Add atomic JSON writing and loading**

Implement:

```python
def write_manifest_atomic(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
```

- [ ] **Step 6: Run manifest tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_hook_recall_evidence.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit the evidence contract**

```bash
git add agent_brain/evaluation/hook_recall_evidence.py \
  tests/unit/test_hook_recall_evidence.py
git commit -m "feat: add fail-closed hook recall evidence manifest"
```

### Task 3: Run production replay through the real hook

**Files:**
- Create: `agent_brain/evaluation/hook_recall_runner.py`
- Create: `tests/unit/test_hook_recall_runner.py`
- Create: `tests/system/test_hook_recall_evidence.py`

- [ ] **Step 1: Write protocol and correlation failure tests**

Test exact `{}` empty output, exact adapter envelope injection output, malformed JSON, extra envelope fields, timeout and stdout/cohort mismatch:

```python
def test_parse_hook_output_accepts_exact_injection_envelope() -> None:
    raw = json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "[fact] fixture (id:mem-a conf:0.9)",
        }
    }).encode()

    result = parse_hook_output(raw)

    assert result.status == "injected"
    assert result.item_ids == ("mem-a",)
    assert result.protocol_valid is True


def test_parse_hook_output_rejects_stdout_contamination() -> None:
    result = parse_hook_output(b"debug\n{}\n")

    assert result.protocol_valid is False
    assert result.reason == "malformed_hook_json"
```

- [ ] **Step 2: Run the tests and confirm RED**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_hook_recall_runner.py -q
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement isolated fixture materialization**

In `hook_recall_runner.py`, materialize all public corpus items once:

```python
def materialize_hook_fixture_brain(corpus: RecallQualityCorpus, brain_dir: Path) -> None:
    brain_dir.mkdir(mode=0o700)
    store = ItemsStore(brain_dir / "items")
    embedder = HashingEmbedder()
    index = HubIndex(brain_dir / "index.db", embedding_dim=embedder.dim)
    seen: dict[str, tuple[MemoryItem, str]] = {}
    try:
        for case in corpus.cases:
            for raw in case.memory_items:
                item, body = memory_item_from_fixture(raw)
                previous = seen.get(item.id)
                if previous is not None and previous != (item, body):
                    raise ValueError(f"conflicting fixture item: {item.id}")
                if previous is None:
                    seen[item.id] = (item, body)
                    store.write(item, body)
                    index.upsert(item, body, embedding=embedder.embed(body))
    finally:
        index.close()
```

Keep fixture parsing in this module; do not import private helpers from test files.

- [ ] **Step 4: Implement bounded real-process execution**

Use `subprocess.Popen(..., start_new_session=True)` and terminate the process group on timeout:

```python
def run_hook_process(command: Path, payload: bytes, *, env: Mapping[str, str], timeout: float) -> ProcessResult:
    started = time.perf_counter()
    process = subprocess.Popen(
        [str(command)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(env),
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(payload, timeout=timeout)
        return ProcessResult(process.returncode, stdout, stderr, False, _elapsed_ms(started))
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
        return ProcessResult(None, stdout, stderr, True, _elapsed_ms(started))
```

Reject stdout/stderr larger than the configured bound before parsing or storing derived evidence.

- [ ] **Step 5: Correlate stdout with low-sensitivity runtime records**

For each applicable case, generate session `hook-evidence-<run digest>-<case digest>`, execute the hook with isolated `BRAIN_DIR`, then read:

```python
cohorts = tuple(iter_injection_cohorts(brain_dir, adapter=adapter, session_id=session_id))
gaps = tuple(
    gap
    for gap in iter_gap_records(brain_dir)
    if gap.adapter == adapter and gap.session_id == session_id
)
```

Injection requires exactly one cohort and no gap; empty requires no cohort and exactly one recognized gap. Never store the generated session ID or raw query in the manifest.

The case validator assigns semantic mismatches such as missing expected IDs or unexpected context to G2, and timeout/error/duration budget violations to G3. Unit tests must assert the exact failures `G2:missing_expected_items:<case>`, `G2:unexpected_context:<case>`, `G3:hook_timeout:<case>` and `G3:hook_error:<case>`.

- [ ] **Step 6: Build a terminal manifest for every run**

Implement `run_hook_recall_evidence(...) -> dict[str, object]`. Catch per-case errors into failed `HookCaseEvidence` rows, keep running the remaining cases, then derive terminal status with `validate_hook_recall_manifest`. Fatal setup failure returns a `blocked` manifest with a bounded reason and zero fake executed rows.

- [ ] **Step 7: Add the real Hook system test**

```python
def test_real_hook_matches_all_applicable_production_replay_cases(tmp_path: Path) -> None:
    manifest = run_hook_recall_evidence(
        root=ROOT,
        corpus_path=FIXTURE,
        hook_path=ROOT / "agent_runtime_kit/hooks/inject-context.sh",
        adapter="codex",
        timeout_seconds=8.0,
        workspace=tmp_path,
    )

    assert manifest["status"] == "pass", manifest["failed_gates"]
    assert manifest["counts"] == {
        "planned": 12,
        "applicable": 11,
        "not_applicable": 1,
        "executed": 11,
    }
    project = next(row for row in manifest["results"] if row["case_id"] == "prod-project-mismatch")
    assert project["actual_status"] == "not_applicable"
    assert project["reason"] == "explicit_project_scope_unavailable"
```

- [ ] **Step 8: Run unit and real Hook tests**

Run:

```bash
MEMORY_HUB_TEST_EMBEDDING=1 .venv/bin/python -m pytest \
  tests/unit/test_hook_recall_runner.py \
  tests/system/test_hook_recall_evidence.py -q
```

Expected: PASS with 11 real Hook executions and one explicit not-applicable result.

- [ ] **Step 9: Commit the runner**

```bash
git add agent_brain/evaluation/hook_recall_runner.py \
  tests/unit/test_hook_recall_runner.py \
  tests/system/test_hook_recall_evidence.py
git commit -m "feat: replay recall corpus through the real hook"
```

### Task 4: Add separate runner and verifier CLIs

**Files:**
- Create: `scripts/run-hook-recall-evidence.py`
- Create: `scripts/check-hook-recall-evidence.py`
- Modify: `tests/unit/test_hook_recall_evidence.py`

- [ ] **Step 1: Write CLI failure tests**

Invoke the verifier against empty, stale and partial JSON fixtures and require non-zero exit. Invoke the runner with a temporary output and require a terminal manifest even when a fake hook times out.

```python
def test_verifier_cli_rejects_partial_manifest(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(_partial_manifest()), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "scripts/check-hook-recall-evidence.py", str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    assert "G0:planned_result_mismatch" in completed.stdout
```

- [ ] **Step 2: Run tests and confirm RED**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_hook_recall_evidence.py -q
```

Expected: FAIL because the scripts do not exist.

- [ ] **Step 3: Implement the runner CLI**

`run-hook-recall-evidence.py` accepts `--corpus`, `--hook`, `--adapter`, `--timeout-seconds` and required `--output`. It calls the runner and atomically writes the manifest. It exits zero after writing a terminal pass/fail/blocked manifest so the independent verifier owns the CI gate; inability to write a manifest exits 2.

- [ ] **Step 4: Implement the independent verifier CLI**

`check-hook-recall-evidence.py` loads the JSON, recomputes current git/Hook/implementation/corpus/config provenance, calls `validate_hook_recall_manifest`, prints every failure, and exits 1 on any mismatch. `--require-clean` rejects tracked worktree changes; untracked user files are ignored with `git status --porcelain --untracked-files=no`.

- [ ] **Step 5: Exercise the two-process contract**

Run:

```bash
mkdir -p .artifacts
.venv/bin/python scripts/run-hook-recall-evidence.py \
  --output .artifacts/hook-recall-evidence.json
.venv/bin/python scripts/check-hook-recall-evidence.py \
  .artifacts/hook-recall-evidence.json
```

Expected:

```text
hook recall evidence generated: status=pass applicable=11 executed=11
hook recall evidence verified: status=pass applicable=11 executed=11
```

- [ ] **Step 6: Commit the CLIs**

```bash
git add scripts/run-hook-recall-evidence.py \
  scripts/check-hook-recall-evidence.py \
  tests/unit/test_hook_recall_evidence.py
git commit -m "feat: add independent hook evidence runner and verifier"
```

### Task 5: Make real Hook evidence a required CI artifact

**Files:**
- Modify: `.github/workflows/governance-gates.yml`
- Modify: `tests/unit/test_ci_governance_contract.py`
- Modify: `scripts/check-recall-quality.py`
- Modify: `docs/evaluation/stage2-recall-quality-report.json`
- Modify: `docs/evaluation/stage2-recall-quality-readiness.zh.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Strengthen the CI contract test**

Require these commands/actions in `recall-quality`:

```python
assert "scripts/run-hook-recall-evidence.py" in commands
assert "scripts/check-hook-recall-evidence.py" in commands
assert "--require-clean" in commands
assert "actions/upload-artifact@v4" in workflow_text
assert "hook-recall-evidence" in workflow_text
assert "continue-on-error" not in job
```

- [ ] **Step 2: Run the CI contract test and confirm RED**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_ci_governance_contract.py -q
```

Expected: FAIL because the workflow has no real Hook evidence steps.

- [ ] **Step 3: Add generation, verification and upload steps**

Append to the `recall-quality` job after system replay and before the committed report check:

```yaml
      - name: Generate fresh real-hook recall evidence
        env:
          MEMORY_HUB_TEST_EMBEDDING: "1"
        run: >-
          python scripts/run-hook-recall-evidence.py
          --output .artifacts/hook-recall-evidence.json
      - name: Verify fresh real-hook recall evidence
        if: always()
        run: >-
          python scripts/check-hook-recall-evidence.py
          .artifacts/hook-recall-evidence.json
          --require-clean
      - name: Upload real-hook recall evidence
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: hook-recall-evidence
          path: .artifacts/hook-recall-evidence.json
          if-no-files-found: error
```

- [ ] **Step 4: Clarify the report boundary**

Update `render_markdown()` in `scripts/check-recall-quality.py` so the facts section contains:

```python
"- 本 committed 报告验证 routed-core 六层结果；不把它写成真实 Hook PASS。",
"- 真实 UserPromptSubmit Hook 结论由 required CI fresh 生成的 hook-recall-evidence artifact 决定。",
"- explicit project hard-filter 不存在于当前 Hook payload，相关 case 不计入 Hook 分母。",
```

Regenerate reports:

```bash
.venv/bin/python scripts/check-recall-quality.py --write
```

- [ ] **Step 5: Document the release behavior**

Add a `CHANGELOG.md` entry that says the recall-quality gate now executes the real Hook, publishes a low-sensitivity manifest, and separately reports non-applicable surface contracts.

- [ ] **Step 6: Run CI contract and report checks**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_ci_governance_contract.py -q
.venv/bin/python scripts/check-recall-quality.py
```

Expected: PASS.

- [ ] **Step 7: Commit CI and documentation**

```bash
git add .github/workflows/governance-gates.yml \
  tests/unit/test_ci_governance_contract.py \
  scripts/check-recall-quality.py \
  docs/evaluation/stage2-recall-quality-report.json \
  docs/evaluation/stage2-recall-quality-readiness.zh.md \
  CHANGELOG.md
git commit -m "ci: require fresh real hook recall evidence"
```

### Task 6: Complete the release audit and direct push

**Files:**
- Modify if evidence requires it: `docs/evaluation/stage2-recall-quality-readiness.zh.md`
- Do not add: `.artifacts/hook-recall-evidence.json` (ephemeral CI/local evidence)

- [ ] **Step 1: Run focused gates**

```bash
MEMORY_HUB_TEST_EMBEDDING=1 .venv/bin/python -m pytest \
  tests/unit/test_recall_quality_corpus.py \
  tests/unit/test_hook_recall_evidence.py \
  tests/unit/test_hook_recall_runner.py \
  tests/unit/test_ci_governance_contract.py \
  tests/system/test_recall_quality_replay.py \
  tests/system/test_hook_recall_evidence.py \
  tests/system/test_dual_route_recall_matrix.py -q
```

Expected: PASS.

- [ ] **Step 2: Run the real Hook evidence gate twice**

```bash
for run in 1 2; do
  .venv/bin/python scripts/run-hook-recall-evidence.py \
    --output ".artifacts/hook-recall-evidence-${run}.json"
  .venv/bin/python scripts/check-hook-recall-evidence.py \
    ".artifacts/hook-recall-evidence-${run}.json"
done
```

Expected: both runs PASS with the same corpus/Hook/config hashes, 11 executed cases, zero timeout/error/prohibited injection, and different run IDs.

- [ ] **Step 3: Run repository quality gates**

```bash
.venv/bin/python -m ruff check .
.venv/bin/python scripts/check_mypy_baseline.py
MEMORY_HUB_TEST_EMBEDDING=1 .venv/bin/python -m pytest tests/unit -q
MEMORY_HUB_TEST_EMBEDDING=1 .venv/bin/python -m pytest tests/system -q
MEMORY_HUB_TEST_EMBEDDING=1 .venv/bin/python -m pytest tests/conformance -q
./agent_runtime_kit/hooks/test-hook.sh
.venv/bin/python scripts/check-recall-quality.py
```

Expected: all commands PASS. If a pre-existing unrelated failure appears, record the exact command and prove whether the changed files caused it before proceeding.

- [ ] **Step 4: Verify repository hygiene**

```bash
git diff --check
git status --short
git log --oneline -8
```

Expected: only the user's pre-existing untracked `findings.md`, `progress.md`, and `task_plan.md` remain; `.artifacts/` is ignored or absent from staged changes.

- [ ] **Step 5: Audit each design completion condition**

Verify the eight completion conditions in `docs/superpowers/specs/2026-07-19-hook-recall-evidence-governance-design.md` against current files, test output, generated manifests and workflow YAML. Do not infer completion from green unit tests alone.

- [ ] **Step 6: Direct-push the verified commits**

```bash
git push origin HEAD:main
git ls-remote origin refs/heads/main
```

Expected: remote `main` equals local `HEAD`. Monitor the new `main` workflows until `python-tests`, `Hook unit tests`, `governance-gates`, mirror and website workflows reach terminal status; core required checks must be green.
