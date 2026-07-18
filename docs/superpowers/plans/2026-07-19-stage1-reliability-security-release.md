# Stage 1 Reliability, Security, and Release Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the verified crash, false-green benchmark, realtime credential leak, resource-lifecycle, container, and release-gate gaps required by Stage 1 of the three-stage governance design.

**Architecture:** Keep the current Markdown source-of-truth, Gateway, hook, and adapter semantics intact. Harden each boundary independently with fail-closed validation, explicit ownership, and machine-verifiable release evidence; land each boundary as a separate commit before the consolidated Stage 1 gate.

**Tech Stack:** Python 3.11/3.12, pytest, FastAPI/Starlette, PyJWT, SQLite/sqlite-vec, Typer, Docker, GitHub Actions, Bash.

---

## File map

### Task 1 — Qoder defensive transcript parsing

- Modify: `agent_brain/agent_integrations/qoder.py`
- Modify: `tests/unit/test_adapters.py`

### Task 2 — MemoryData result integrity

- Modify: `agent_brain/evaluation/memorydata_runner.py`
- Modify: `tests/unit/test_external_memory_benchmark.py`

### Task 3 — WebSocket/SSE credential boundary

- Modify: `web/auth.py`
- Modify: `web/api/routes/auth.py`
- Modify: `web/api/routes/events.py`
- Modify: `web/templates/dashboard.html`
- Create: `tests/unit/test_realtime_auth.py`
- Modify: `tests/unit/test_web_sse_tenant_scope.py`
- Modify: `tests/conformance/test_public_hygiene.py`

### Task 4 — Explicit resource ownership

- Modify: `agent_brain/platform/indexing/index.py`
- Modify: `agent_brain/memory/store/write_service.py`
- Modify: `agent_brain/interfaces/sdk/components.py`
- Modify: `agent_brain/interfaces/sdk/sdk.py`
- Modify: `agent_brain/interfaces/cli/_shared.py`
- Modify: `agent_brain/interfaces/cli/commands/crud.py`
- Modify: `agent_brain/interfaces/cli/commands/wiki.py`
- Modify: `agent_brain/interfaces/cli/commands/product_capabilities.py`
- Modify: `agent_brain/interfaces/cli/commands/links.py`
- Modify: `agent_brain/interfaces/cli/commands/recall_drift.py`
- Modify: `agent_brain/interfaces/cli/commands/io.py`
- Modify: `agent_brain/interfaces/cli/commands/maintenance.py`
- Modify: `agent_brain/interfaces/cli/commands/query.py`
- Modify: `web/_base.py`
- Create: `tests/unit/test_resource_lifecycle.py`
- Modify: `tests/unit/test_sdk_client.py`

### Task 5 — Container contract

- Modify: `deploy/Dockerfile`
- Modify: `deploy/docker-compose.yml`
- Modify: `pyproject.toml`
- Create: `tests/conformance/test_docker_contract.py`
- Create: `scripts/docker-smoke.sh`

### Task 6 — Required CI and branch protection

- Create: `scripts/check_mypy_baseline.py`
- Create: `.github/mypy-baseline.txt`
- Modify: `.github/workflows/python-tests.yml`
- Create: `.github/workflows/governance-gates.yml`
- Modify: `.github/workflows/sync-gitee.yml`
- Modify: `.github/workflows/publish-npm.yml`
- Modify: `.github/workflows/deploy-official-site.yml`
- Create: `tests/unit/test_ci_governance_contract.py`

### Task 7 — Stage 1 completion evidence

- Create: `docs/evaluation/stage1-reliability-security-release-readiness.zh.md`
- Modify: `CHANGELOG.md`

---

### Task 1: Make Qoder transcript discovery total over JSON values

**Files:**
- Modify: `agent_brain/agent_integrations/qoder.py:831-846,1305-1323`
- Modify: `tests/unit/test_adapters.py` in `TestQoderAdapter`

- [ ] **Step 1: Add a failing scalar/malformed transcript regression**

```python
def test_qoder_transcript_discovery_skips_non_object_json_rows(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import qoder as qoder_mod

    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        '\n'.join([
            '"scalar"',
            '42',
            'null',
            '["list"]',
            '{broken',
            json.dumps({
                "timestamp": "2026-07-19T00:00:00Z",
                "cwd": str(tmp_path),
                "data": {"command": "AGENT_MEMORY_HUB_ADAPTER=qoder inject-context.sh"},
            }),
        ]) + '\n',
        encoding="utf-8",
    )
    adapter = qoder_mod.QoderAdapter(brain_dir=tmp_path / "brain")

    assert adapter._cwd_from_transcript(transcript) == tmp_path
    assert adapter._transcript_observed_time(transcript) is not None
    assert adapter._classify_transcript_effectiveness(transcript) == "unknown"
```

- [ ] **Step 2: Run the new test and confirm the current crash**

Run:

```bash
pytest tests/unit/test_adapters.py -k 'qoder_transcript_discovery_skips_non_object' -q
```

Expected: FAIL with `AttributeError: '<scalar type>' object has no attribute 'get'`.

- [ ] **Step 3: Guard decoded records before field access**

Apply the same guard in `_cwd_from_transcript` and `_transcript_observed_time`:

```python
try:
    record = json.loads(line)
except json.JSONDecodeError:
    continue
if not isinstance(record, dict):
    continue
```

Keep `_record_uses_amh_cli_search` and `_record_uses_native_search_memory` recursive and total over `object`; do not convert scalar rows into evidence.

- [ ] **Step 4: Add a doctor-level regression**

Create a Qoder projects directory containing a scalar-only `.jsonl`, install the adapter using the existing test fixture pattern, and assert:

```python
report = adapter.diagnose()
assert report.adapter == "qoder"
assert all("Traceback" not in check.detail for check in report.checks)
```

- [ ] **Step 5: Run Qoder and adapter tests**

Run:

```bash
pytest tests/unit/test_adapters.py tests/unit/test_cli_adapter.py -k 'qoder' -q
```

Expected: PASS.

- [ ] **Step 6: Commit the boundary fix**

```bash
git add agent_brain/agent_integrations/qoder.py tests/unit/test_adapters.py
git commit -m "fix: harden qoder transcript diagnostics"
```

---

### Task 2: Require fresh and complete MemoryData result evidence

**Files:**
- Modify: `agent_brain/evaluation/memorydata_runner.py`
- Modify: `tests/unit/test_external_memory_benchmark.py`

- [ ] **Step 1: Add failing tests for empty, stale, short, malformed, and valid artifacts**

Add parametrized tests around `run_memorydata` with a mocked zero-exit subprocess:

```python
@pytest.mark.parametrize(
    ("payload", "expected_reason"),
    [
        (None, "no fresh MemoryData result artifacts"),
        ({"data": []}, "expected at least 2 result rows"),
        ({"data": [{"status": "passed"}]}, "expected at least 2 result rows"),
        ({"data": "wrong"}, "malformed MemoryData result artifact"),
    ],
)
def test_memorydata_zero_exit_requires_complete_fresh_results(
    tmp_path, monkeypatch, payload, expected_reason
):
    # fake_run writes payload only when payload is not None
    run = run_memorydata(
        MemoryDataRunOptions(
            memorydata_repo=memorydata_repo,
            artifact_root=tmp_path / "artifacts",
            max_test_queries=2,
        ),
        prereqs=ready_prereqs,
    )
    assert run["status"] == "failed"
    assert expected_reason in run["reason"]
```

Add a stale root artifact before the run and assert it is ignored. Add a valid two-row artifact and assert `passed`.

- [ ] **Step 2: Run the new integrity cases**

Run:

```bash
pytest tests/unit/test_external_memory_benchmark.py -k 'complete_fresh_results or stale_result' -q
```

Expected: empty/stale/short cases FAIL because the current runner only checks return code and failed rows.

- [ ] **Step 3: Introduce a typed result summary**

Add:

```python
@dataclass(frozen=True)
class MemoryDataResultSummary:
    artifact_count: int
    row_count: int
    failed_count: int
    malformed_count: int


def _summarize_memorydata_results(artifact_root: Path) -> MemoryDataResultSummary:
    artifact_count = row_count = failed_count = malformed_count = 0
    for result_path in artifact_root.rglob("*_results.json"):
        artifact_count += 1
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            malformed_count += 1
            continue
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            malformed_count += 1
            continue
        row_count += len(rows)
        failed_count += sum(
            1
            for row in rows
            if not isinstance(row, dict) or row.get("status") != "passed"
        )
    return MemoryDataResultSummary(
        artifact_count=artifact_count,
        row_count=row_count,
        failed_count=failed_count,
        malformed_count=malformed_count,
    )
```

- [ ] **Step 4: Isolate each execution from old artifacts**

Build a unique run directory before invoking upstream:

```python
run_id = started_at.strftime("%Y%m%dT%H%M%S%fZ")
run_artifact_root = artifact_root / "runs" / run_id
execution_options = replace(options, artifact_root=run_artifact_root)
execution_command = memorydata_command(execution_options)
```

Store `artifact_root` and `run_record` using the unique directory. Do not scan parent directories.

- [ ] **Step 5: Make the verdict fail closed and write the record atomically**

Use this verdict order:

```python
summary = _summarize_memorydata_results(run_artifact_root)
expected_rows = max(1, options.max_test_queries)
reasons: list[str] = []
if result.returncode != 0:
    reasons.append(f"MemoryData exited with {result.returncode}")
if summary.artifact_count == 0:
    reasons.append("no fresh MemoryData result artifacts")
if summary.malformed_count:
    reasons.append("malformed MemoryData result artifact")
if summary.row_count < expected_rows:
    reasons.append(f"expected at least {expected_rows} result rows; found {summary.row_count}")
if summary.failed_count:
    reasons.append(f"MemoryData result contains {summary.failed_count} failed query record(s)")
status = "failed" if reasons else "passed"
```

Write `run-record.json.tmp`, flush and `os.fsync`, then `os.replace` it to `run-record.json`.

- [ ] **Step 6: Run the complete external benchmark unit module**

Run:

```bash
pytest tests/unit/test_external_memory_benchmark.py -q
```

Expected: PASS, including existing redaction and absolute-path tests updated for the per-run directory.

- [ ] **Step 7: Commit the benchmark gate**

```bash
git add agent_brain/evaluation/memorydata_runner.py tests/unit/test_external_memory_benchmark.py
git commit -m "fix: fail closed on incomplete MemoryData runs"
```

---

### Task 3: Remove long-lived JWTs from realtime URLs

**Files:**
- Modify: `web/auth.py`
- Modify: `web/api/routes/auth.py`
- Modify: `web/api/routes/events.py`
- Modify: `web/templates/dashboard.html`
- Create: `tests/unit/test_realtime_auth.py`
- Modify: `tests/unit/test_web_sse_tenant_scope.py`
- Modify: `tests/conformance/test_public_hygiene.py`

- [ ] **Step 1: Write failing cookie and query-token boundary tests**

```python
def test_login_sets_http_only_same_site_session_cookie(client):
    response = client.post("/api/auth/login", json={"username": "admin", "password": "pw"})
    cookie = response.headers["set-cookie"]
    assert "amh_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie


def test_websocket_uses_session_cookie_and_rejects_long_token_query(client):
    token = client.post(
        "/api/auth/login", json={"username": "admin", "password": "pw"}
    ).json()["token"]
    with client.websocket_connect("/ws/events") as ws:
        assert ws.receive_json()["event"] == "connected"
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws/events?token={token}"):
            pass
```

Add a source contract assertion that `dashboard.html` contains neither `/ws/events?token=` nor `/api/events?token=`.

- [ ] **Step 2: Run realtime auth tests and confirm failure**

Run:

```bash
pytest tests/unit/test_realtime_auth.py tests/unit/test_web_sse_tenant_scope.py -q
```

Expected: FAIL because login sets no cookie and both routes require query `token`.

- [ ] **Step 3: Add the session cookie contract**

In `web/auth.py`:

```python
SESSION_COOKIE = "amh_session"
REALTIME_TICKET_EXPIRE_SECONDS = 60


def set_session_cookie(response: Response, token: str, *, secure: bool) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=TOKEN_EXPIRE_HOURS * 3600,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
```

Update login and init routes to accept `Request` and `Response`, call `set_session_cookie`, and retain the JSON bearer token for API compatibility.

- [ ] **Step 4: Add short-lived one-use realtime tickets for non-cookie clients**

In `web/auth.py`, create a realtime JWT with `purpose="realtime"`, `jti`, tenant, role, subject and a 60-second expiry. Protect consumed `jti` values with a lock and purge expired entries before insertion.

```python
def create_realtime_ticket(user: CurrentUser) -> str:
    payload = {
        "sub": user.username,
        "tenant_id": user.tenant_id,
        "role": user.role,
        "purpose": "realtime",
        "jti": secrets.token_urlsafe(18),
        "exp": datetime.now(timezone.utc) + timedelta(seconds=REALTIME_TICKET_EXPIRE_SECONDS),
    }
    return jwt.encode(payload, _secret_key(), algorithm=ALGORITHM)


_consumed_realtime_tickets: dict[str, int] = {}
_realtime_ticket_lock = threading.Lock()


def _consume_ticket_jti(jti: str, expires_at: int) -> None:
    now = int(datetime.now(timezone.utc).timestamp())
    with _realtime_ticket_lock:
        expired = [key for key, expiry in _consumed_realtime_tickets.items() if expiry <= now]
        for key in expired:
            del _consumed_realtime_tickets[key]
        if jti in _consumed_realtime_tickets:
            raise JWTError("realtime ticket already used")
        _consumed_realtime_tickets[jti] = expires_at


def consume_realtime_ticket(ticket: str) -> dict[str, Any]:
    payload = decode_token(ticket)
    if payload.get("purpose") != "realtime" or not isinstance(payload.get("jti"), str):
        raise JWTError("invalid realtime ticket")
    _consume_ticket_jti(payload["jti"], int(payload["exp"]))
    return payload
```

Expose `POST /api/auth/realtime-ticket` behind `get_current_user`.

- [ ] **Step 5: Authenticate realtime connections from cookie or one-use ticket**

In `events.py`, remove `token: str = Query("")`. Resolve identity in this order:

```python
def _realtime_payload(*, cookie_token: str | None, ticket: str | None) -> dict[str, Any]:
    if cookie_token:
        payload = decode_token(cookie_token)
        if payload.get("purpose"):
            raise JWTError("session token required")
        return payload
    if ticket:
        return consume_realtime_ticket(ticket)
    raise JWTError("missing realtime credential")
```

SSE reads `request.cookies.get(SESSION_COOKIE)`; WebSocket reads `ws.cookies.get(SESSION_COOKIE)`. The only query credential is `ticket`, never the long session token.

- [ ] **Step 6: Remove JWT query construction from the dashboard**

Use same-origin cookies:

```javascript
_ws = new WebSocket(`${proto}//${location.host}/ws/events`);
_evtSource = new EventSource('/api/events');
```

Keep REST API bearer headers unchanged for backward compatibility.

- [ ] **Step 7: Add one-use, expiry, tenant and secret-sentinel regressions**

Tests must prove:

```python
from urllib.parse import quote

ticket_response = client.post(
    "/api/auth/realtime-ticket",
    headers={"Authorization": f"Bearer {bearer}"},
)
assert ticket_response.status_code == 200
ticket = ticket_response.json()["ticket"]
url = f"/ws/events?ticket={quote(ticket, safe='')}"
with client.websocket_connect(url) as ws:
    assert ws.receive_json()["event"] == "connected"
with pytest.raises(WebSocketDisconnect):
    with client.websocket_connect(url):
        pass
```

Also capture access/error logs with a long JWT sentinel and assert the sentinel is absent. Preserve the existing cross-tenant SSE/WS tests using cookie authentication.

- [ ] **Step 8: Run security-focused tests**

Run:

```bash
pytest tests/unit/test_realtime_auth.py tests/unit/test_web_auth.py \
  tests/unit/test_web_sse_tenant_scope.py tests/unit/test_web_tenant_isolation.py \
  tests/conformance/test_public_hygiene.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit the realtime credential migration**

```bash
git add web/auth.py web/api/routes/auth.py web/api/routes/events.py \
  web/templates/dashboard.html tests/unit/test_realtime_auth.py \
  tests/unit/test_web_sse_tenant_scope.py tests/conformance/test_public_hygiene.py
git commit -m "fix: remove session tokens from realtime URLs"
```

---

### Task 4: Establish explicit index and client resource ownership

**Files:**
- Modify: `agent_brain/platform/indexing/index.py`
- Modify: `agent_brain/memory/store/write_service.py`
- Modify: `agent_brain/interfaces/sdk/components.py`
- Modify: `agent_brain/interfaces/sdk/sdk.py`
- Modify: `agent_brain/interfaces/cli/_shared.py`
- Modify: CLI command modules using component openers
- Modify: `web/_base.py`
- Create: `tests/unit/test_resource_lifecycle.py`
- Modify: `tests/unit/test_sdk_client.py`

- [ ] **Step 1: Add failing idempotency, ownership and FD tests**

```python
def test_hub_index_context_manager_closes_idempotently(tmp_path):
    index = HubIndex(tmp_path / "index.db", embedding_dim=8)
    with index as entered:
        assert entered is index
    index.close()
    with pytest.raises(sqlite3.ProgrammingError):
        index.connection.execute("select 1")


def test_memory_client_context_manager_returns_fd_to_baseline(tmp_path):
    before = len(os.listdir("/dev/fd"))
    for _ in range(30):
        with MemoryClient(brain_dir=tmp_path) as client:
            client.stats()
            client._components.get_index()
    after = len(os.listdir("/dev/fd"))
    assert after <= before + 3
```

Skip only the FD assertion on platforms without `/dev/fd`; context-manager behavior remains mandatory everywhere.

- [ ] **Step 2: Run the resource tests and confirm failure**

Run:

```bash
MEMORY_HUB_TEST_EMBEDDING=1 pytest tests/unit/test_resource_lifecycle.py -q
```

Expected: FAIL because `HubIndex`, `MemoryClient` and `ClientComponents` do not implement the full close/context-manager contract.

- [ ] **Step 3: Make HubIndex close idempotent and context-managed**

```python
def __enter__(self) -> "HubIndex":
    return self

def __exit__(self, exc_type, exc, traceback) -> None:
    self.close()

def close(self) -> None:
    if self._closed:
        return
    self.connection.close()
    self._closed = True
```

Initialize `_closed = False` immediately after connecting, and reset it when `_reset_index_db` opens a replacement connection.

- [ ] **Step 4: Track WriteService ownership explicitly**

Add `owns_index: bool = False` to the constructor. `for_brain` passes `owns_index=True`; callers that inject an index or callable retain ownership.

```python
def close(self) -> None:
    if not self._owns_index:
        return
    index = self._index
    if index is not None and not callable(index):
        index.close()
    self._owns_index = False

def __enter__(self) -> "WriteService":
    return self

def __exit__(self, exc_type, exc, traceback) -> None:
    self.close()
```

- [ ] **Step 5: Add SDK close and context-manager APIs**

```python
class ClientComponents:
    def close(self) -> None:
        if self._index is not None:
            self._index.close()
        self._feedback = self._retriever = self._index = None


class MemoryClient:
    def close(self) -> None:
        self._components.close()

    def __enter__(self) -> "MemoryClient":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
```

Update the SDK docstring example to use `with MemoryClient(...) as client:`.

- [ ] **Step 6: Add a managed CLI component opener and migrate callers**

```python
@contextmanager
def _managed_components(*, hook: bool = False):
    components = _open_hook_components() if hook else _open_components()
    try:
        yield components
    finally:
        components[1].close()
```

Replace every command-local `_open_components()` / `_open_hook_components()` ownership site in `crud.py`, `wiki.py`, `product_capabilities.py`, `links.py`, `recall_drift.py`, `io.py`, `maintenance.py`, and `query.py` with a `with` block. Do not close indexes obtained from Web `_components()` or dependency-injected test fixtures.

- [ ] **Step 7: Close Web caches during application lifespan shutdown**

```python
@asynccontextmanager
async def _lifespan(application: FastAPI):
    try:
        _components()
    except Exception:
        pass
    try:
        yield
    finally:
        with _components_cache_lock:
            _components_cache.clear()
```

- [ ] **Step 8: Run lifecycle, SDK, CLI and Web cache tests**

Run:

```bash
MEMORY_HUB_TEST_EMBEDDING=1 pytest \
  tests/unit/test_resource_lifecycle.py tests/unit/test_sdk_client.py \
  tests/unit/test_cli_smoke.py tests/unit/test_web_component_cache.py -q
```

Expected: PASS with FD delta inside the frozen window.

- [ ] **Step 9: Commit explicit ownership**

```bash
git add agent_brain/platform/indexing/index.py \
  agent_brain/memory/store/write_service.py agent_brain/interfaces/sdk \
  agent_brain/interfaces/cli web/_base.py tests/unit/test_resource_lifecycle.py \
  tests/unit/test_sdk_client.py
git commit -m "fix: close owned memory index resources"
```

---

### Task 5: Make the Docker image match its default service

**Files:**
- Modify: `deploy/Dockerfile`
- Modify: `deploy/docker-compose.yml`
- Modify: `pyproject.toml`
- Create: `tests/conformance/test_docker_contract.py`
- Create: `scripts/docker-smoke.sh`

- [ ] **Step 1: Add a failing static container contract test**

```python
def test_default_docker_image_installs_web_runtime_and_uses_real_health_route():
    dockerfile = Path("deploy/Dockerfile").read_text(encoding="utf-8")
    compose = Path("deploy/docker-compose.yml").read_text(encoding="utf-8")
    assert 'pip install --no-cache-dir -e ".[web,embeddings]"' in dockerfile
    assert "|| pip install" not in dockerfile
    assert "/api/health" in dockerfile
    assert "/api/health" in compose
```

- [ ] **Step 2: Run the contract test and confirm current mismatch**

Run:

```bash
pytest tests/conformance/test_docker_contract.py -q
```

Expected: FAIL because the image installs undefined `all` and checks `/health`.

- [ ] **Step 3: Define the install contract**

Add a real `all` extra for end users by repeating the `web` and `embeddings` dependency lists in `pyproject.toml`. Keep the Dockerfile explicit:

```dockerfile
RUN pip install --no-cache-dir -e ".[web,embeddings]"
```

Remove the core-only fallback so dependency failure fails the build.

- [ ] **Step 4: Fix health checks**

Use:

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8742/api/health', timeout=3)" || exit 1
```

Update Compose to the same `/api/health` URL.

- [ ] **Step 5: Add an executable Docker smoke script**

`scripts/docker-smoke.sh` must:

```bash
set -euo pipefail
image="agent-memory-hub:stage1-smoke"
name="amh-stage1-smoke-$RANDOM"
trap 'docker rm -f "$name" >/dev/null 2>&1 || true' EXIT
docker build -f deploy/Dockerfile -t "$image" .
docker run -d --name "$name" -p 127.0.0.1::8742 "$image" >/dev/null
port="$(docker port "$name" 8742/tcp | awk -F: 'NR==1 {print $NF}')"
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${port}/api/health" >/tmp/amh-health.json; then break; fi
  sleep 1
done
python -c 'import json; assert json.load(open("/tmp/amh-health.json"))["status"] == "ok"'
```

The script also initializes an admin, logs in, calls `/api/auth/me` with the returned bearer token, restarts the container with the same named volume, and verifies health again.

Use a named volume and these concrete probes after resolving `port`:

```bash
init_json="$(curl -fsS -X POST "http://127.0.0.1:${port}/api/auth/init" \
  -H 'Content-Type: application/json' \
  -d '{"username":"stage1-admin","password":"stage1-password"}')"
token="$(printf '%s' "$init_json" | python -c 'import json,sys; print(json.load(sys.stdin)["token"])')"
curl -fsS "http://127.0.0.1:${port}/api/auth/me" \
  -H "Authorization: Bearer ${token}" >/tmp/amh-me.json
python -c 'import json; assert json.load(open("/tmp/amh-me.json"))["username"] == "stage1-admin"'
docker restart "$name" >/dev/null
for _ in $(seq 1 60); do
  curl -fsS "http://127.0.0.1:${port}/api/health" >/tmp/amh-health-restart.json && break
  sleep 1
done
python -c 'import json; assert json.load(open("/tmp/amh-health-restart.json"))["status"] == "ok"'
```

- [ ] **Step 6: Run static and real container verification**

Run:

```bash
pytest tests/conformance/test_docker_contract.py -q
./scripts/docker-smoke.sh
```

Expected: PASS. If Docker is unavailable locally, the static test must pass and the real smoke remains a required GitHub job before Stage 1 closes.

- [ ] **Step 7: Commit the container contract**

```bash
git add deploy/Dockerfile deploy/docker-compose.yml pyproject.toml \
  scripts/docker-smoke.sh tests/conformance/test_docker_contract.py
git commit -m "fix: align container dependencies and health checks"
```

---

### Task 6: Turn quality signals into required governance gates

**Files:**
- Create: `scripts/check_mypy_baseline.py`
- Create: `.github/mypy-baseline.txt`
- Modify: `.github/workflows/python-tests.yml`
- Create: `.github/workflows/governance-gates.yml`
- Modify: distribution workflows
- Create: `tests/unit/test_ci_governance_contract.py`

- [ ] **Step 1: Add failing workflow contract tests**

```python
def test_core_ci_does_not_silence_type_failures():
    workflow = Path(".github/workflows/python-tests.yml").read_text(encoding="utf-8")
    assert "continue-on-error: true" not in workflow
    assert "check_mypy_baseline.py" in workflow


def test_governance_workflow_has_stable_required_job_names():
    workflow = yaml.safe_load(Path(".github/workflows/governance-gates.yml").read_text())
    assert set(workflow["jobs"]) == {
        "security",
        "benchmark-integrity",
        "docker-smoke",
    }
```

Add assertions that distribution workflows expose explicit missing-secret reasons and are not referenced by the core workflow.

- [ ] **Step 2: Run the CI contract and confirm failure**

Run:

```bash
pytest tests/unit/test_ci_governance_contract.py -q
```

Expected: FAIL because the baseline checker and governance workflow do not exist and mypy is non-blocking.

- [ ] **Step 3: Create a no-new-errors strict mypy baseline gate**

`scripts/check_mypy_baseline.py` runs:

```python
command = [
    sys.executable, "-m", "mypy", "agent_brain", "web", "benchmarks",
    "--no-error-summary", "--no-pretty", "--show-error-codes",
]
```

Normalize to `path: error: message [code]` without line or column numbers, compare multisets with `.github/mypy-baseline.txt`, fail on every new fingerprint, and print resolved fingerprints. Excluding line numbers prevents unrelated insertions from turning unchanged debt into false new errors. Support `--write-baseline` for an intentional audited refresh. Do not silently rewrite the baseline in CI.

Generate the initial baseline from the current 764-error snapshot, then run strict no-baseline mypy over the Stage 1 critical modules with `--follow-imports=skip`:

```bash
mypy --follow-imports=skip \
  agent_brain/agent_integrations/qoder.py \
  agent_brain/evaluation/memorydata_runner.py \
  agent_brain/platform/indexing/index.py \
  agent_brain/memory/store/write_service.py \
  agent_brain/interfaces/sdk/components.py agent_brain/interfaces/sdk/sdk.py \
  web/auth.py web/api/routes/auth.py web/api/routes/events.py
```

- [ ] **Step 4: Make Python CI blocking and complete**

Replace the non-blocking mypy step with:

```yaml
- name: Type regression gate
  run: python scripts/check_mypy_baseline.py
- name: Strict type check for governance-critical modules
  run: >-
    mypy --follow-imports=skip
    agent_brain/agent_integrations/qoder.py
    agent_brain/evaluation/memorydata_runner.py
    agent_brain/platform/indexing/index.py
    agent_brain/memory/store/write_service.py
    agent_brain/interfaces/sdk/components.py agent_brain/interfaces/sdk/sdk.py
    web/auth.py web/api/routes/auth.py web/api/routes/events.py
```

No type step may use `continue-on-error`.

- [ ] **Step 5: Add stable security, benchmark and Docker jobs**

`.github/workflows/governance-gates.yml` runs on PRs and main pushes. Use job ids exactly:

```yaml
jobs:
  security:
    runs-on: ubuntu-latest
  benchmark-integrity:
    runs-on: ubuntu-latest
  docker-smoke:
    runs-on: ubuntu-latest
```

Security runs Task 3 tests; benchmark-integrity runs Task 2 tests; docker-smoke runs Task 5 static and real smoke.

- [ ] **Step 6: Make distribution failures explicit and separate**

For Gitee, npm and website jobs, add a first preflight that emits a machine-readable reason when secrets are absent. A tag publish with missing required release secret fails with that reason; a normal main push mirror/deploy may be skipped with that reason. Do not reference these job names in branch required contexts.

- [ ] **Step 7: Run local workflow and type gates**

Run:

```bash
pytest tests/unit/test_ci_governance_contract.py -q
python scripts/check_mypy_baseline.py
mypy --follow-imports=skip \
  agent_brain/agent_integrations/qoder.py \
  agent_brain/evaluation/memorydata_runner.py \
  agent_brain/platform/indexing/index.py \
  agent_brain/memory/store/write_service.py \
  agent_brain/interfaces/sdk/components.py agent_brain/interfaces/sdk/sdk.py \
  web/auth.py web/api/routes/auth.py web/api/routes/events.py
```

Expected: PASS and zero new global mypy fingerprints.

- [ ] **Step 8: Commit CI governance**

```bash
git add scripts/check_mypy_baseline.py .github/mypy-baseline.txt \
  .github/workflows tests/unit/test_ci_governance_contract.py
git commit -m "ci: enforce stage one governance gates"
```

- [ ] **Step 9: Push and enable main branch protection**

After all required jobs have completed once on the PR, obtain their exact check-run names and apply protection:

```bash
gh api --method PUT repos/liuyang0508/Agent-Memory-Hub/branches/main/protection \
  --input /tmp/amh-main-protection.json
```

Write `/tmp/amh-main-protection.json` with the exact check-run names read from the successful PR run:

```json
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "unit (3.11)",
      "unit (3.12)",
      "hook-tests",
      "security",
      "benchmark-integrity",
      "docker-smoke"
    ]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false,
    "required_approving_review_count": 1
  },
  "restrictions": null
}
```

If GitHub reports a different matrix context spelling, replace only the affected string with the exact `check-runs[].name` value. Read the branch protection endpoint back and save the response as release evidence.

---

### Task 7: Prove Stage 1 completion and publish the evidence

**Files:**
- Create: `docs/evaluation/stage1-reliability-security-release-readiness.zh.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run focused red-to-green suites**

```bash
pytest tests/unit/test_adapters.py tests/unit/test_cli_adapter.py -k 'qoder' -q
pytest tests/unit/test_external_memory_benchmark.py -q
pytest tests/unit/test_realtime_auth.py tests/unit/test_web_sse_tenant_scope.py \
  tests/unit/test_web_tenant_isolation.py tests/conformance/test_public_hygiene.py -q
MEMORY_HUB_TEST_EMBEDDING=1 pytest tests/unit/test_resource_lifecycle.py \
  tests/unit/test_sdk_client.py tests/unit/test_cli_smoke.py \
  tests/unit/test_web_component_cache.py -q
pytest tests/conformance/test_docker_contract.py tests/unit/test_ci_governance_contract.py -q
```

Expected: PASS.

- [ ] **Step 2: Run all repository quality gates**

```bash
ruff check .
python scripts/check_mypy_baseline.py
MEMORY_HUB_TEST_EMBEDDING=1 python -m pytest tests/unit -q
MEMORY_HUB_TEST_EMBEDDING=1 python -m pytest \
  tests/conformance/test_cli_e2e.py tests/conformance/test_mcp_e2e.py -q
./agent_runtime_kit/hooks/test-hook.sh
./tests/schema-tenant-id-test.sh
./benchmarks/quickstart-60s.sh
./scripts/docker-smoke.sh
```

Expected: every command exits 0. Record elapsed time and test totals without copying secrets or private item bodies.

- [ ] **Step 3: Re-run the verified bug probes**

Record current-commit output for:

- Qoder scalar transcript doctor does not throw;
- zero-exit/no-artifact MemoryData run reports failed;
- dashboard and access-log sentinels contain no long JWT;
- 30 SDK client loops return FD count to the frozen window;
- Docker `/api/health`, authenticated API and persistent restart pass;
- branch protection readback lists every required check.

- [ ] **Step 4: Write the readiness report**

The Chinese report includes:

```markdown
## 结论
## 冻结代码与环境
## 已关闭风险逐项证据
## 兼容和升级边界
## CI 与 main 保护证据
## Docker 与运行验证
## 失败历史和最终确认运行
## 阶段一退出门禁对照表
## 阶段二准入结论
```

Every PASS cites a command, result artifact or GitHub run URL. Mark missing external credentials as blocked distribution evidence, not core-code PASS.

- [ ] **Step 5: Update migration notes and commit evidence**

Add to `CHANGELOG.md`:

- realtime clients use cookie or one-use ticket rather than `?token=<JWT>`;
- SDK users can and should use `with MemoryClient(...)` or `close()`;
- old containers must rebuild; old installed hooks remain governed by the existing repair instructions.

Commit:

```bash
git add docs/evaluation/stage1-reliability-security-release-readiness.zh.md CHANGELOG.md
git commit -m "docs: publish stage one governance evidence"
```

- [ ] **Step 6: Push and verify GitHub state**

```bash
git push
gh pr checks 4 --watch
gh pr view 4 --json state,isDraft,mergeStateStatus,headRefOid,url
gh api repos/liuyang0508/Agent-Memory-Hub/branches/main/protection
```

Expected: all required checks pass, PR head equals local head, merge state is clean, and protection readback matches Task 6.

- [ ] **Step 7: Mark Stage 1 complete only after the gate table is fully proven**

Update the working plan so Stage 2 becomes in progress only if every Stage 1 exit row has authoritative current evidence. Otherwise leave Stage 1 active and continue fixing the contradicting row.
