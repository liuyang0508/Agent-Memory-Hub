# Agent Staged Recall Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every Agent-facing automatic/default recall path return only locator/overview, preserve explicit detail body reads, govern broad explicit-detail searches, and harden Qoder doctor against non-object transcript JSON.

**Architecture:** Keep view selection centralized in `context_loading.py`, add one small shared recall-policy helper for non-blocking broad-detail warnings, and make MCP/CLI/SDK/Web consume the same `ContextPack` result instead of rebuilding divergent snippets. Preserve all explicit `verbosity="detail"` behavior. Extend single-item SDK/Web reads with optional bounded `head/view` controls while keeping old no-argument full reads compatible.

**Tech Stack:** Python 3.11+, Pydantic, Typer, FastAPI, FastMCP, pytest, Ruff.

---

## File map

- Create `agent_brain/memory/context/recall_policy.py`: shared staged-recall constants and broad explicit-detail governance warnings.
- Modify `agent_brain/memory/context/context_loading.py`: ensure `auto` selects only locator/overview.
- Modify `agent_brain/interfaces/mcp/tools/search_tools.py`: emit one context-pack-derived view and preserve explicit detail.
- Modify `agent_brain/interfaces/cli/commands/query.py`: surface non-blocking broad-detail warnings; hook behavior follows the shared auto selector.
- Modify `agent_brain/interfaces/sdk/query.py` and `agent_brain/interfaces/sdk/sdk.py`: compact snippets, governance diagnostics, bounded single-item reads.
- Modify `web/api/routes/item_search.py` and `web/api/routes/item_crud.py`: compact search snippets, governance diagnostics, bounded single-item reads.
- Modify `agent_brain/agent_integrations/awareness.py`, `agent_brain/interfaces/mcp/onboarding.py`, `agent_runtime_kit/AGENT_MEMORY_DISCIPLINE.md`, `README.md`, `README.zh.md`, and `docs/architecture.md`: one staged-recall contract for all adapters.
- Modify `agent_brain/agent_integrations/qoder.py`: skip non-object JSON transcript rows.
- Modify focused tests under `tests/unit/`: red-green coverage for each public surface and truth-contract coverage for adapter guidance.

### Task 1: Enforce the core auto-view invariant

**Files:**
- Create: `agent_brain/memory/context/recall_policy.py`
- Modify: `agent_brain/memory/context/context_loading.py`
- Test: `tests/unit/test_context_loading_views.py`

- [ ] **Step 1: Replace the old raw-evidence expectation with failing staged-view tests**

```python
def test_auto_context_loading_uses_overview_for_raw_item_with_direct_evidence() -> None:
    from agent_brain.memory.context.context_loading import select_context_view

    item = MemoryItem(
        id="mem-20260615-020004-context-auto-policy",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="Raw evidence policy",
        summary="raw evidence locator",
        abstraction="L0",
        refs={"files": ["/tmp/evidence.log"]},
        context_views={
            "locator": "raw evidence locator",
            "overview": "raw evidence overview",
            "detail_uri": "memory://items/mem-20260615-020004-context-auto-policy/body",
        },
    )

    selection = select_context_view(item, "raw evidence detail")

    assert selection.view == "overview"
    assert "raw_direct_evidence" in selection.reasons


def test_auto_context_loading_falls_back_to_locator_when_raw_overview_is_empty() -> None:
    from agent_brain.memory.context.context_loading import select_context_view

    item = MemoryItem(
        id="mem-20260615-020004-context-auto-locator",
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        title="Raw evidence locator fallback",
        summary="raw locator only",
        abstraction="L0",
        refs={"resources": ["res-direct-evidence"]},
        context_views={
            "locator": "raw locator only",
            "overview": "",
            "detail_uri": "memory://items/mem-20260615-020004-context-auto-locator/body",
        },
    )

    selection = select_context_view(item, "raw body marker")

    assert selection.view == "locator"
    assert "raw_direct_evidence" in selection.reasons


def test_explicit_detail_still_selects_detail_for_raw_direct_evidence() -> None:
    from agent_brain.memory.context.context_loading import select_context_view

    item = MemoryItem(
        id="mem-20260615-020004-context-explicit-detail",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="Explicit detail",
        summary="detail locator",
        abstraction="L0",
        refs={"files": ["/tmp/evidence.log"]},
    )

    selection = select_context_view(item, "detail body", requested="detail")

    assert selection.view == "detail"
    assert selection.reasons == ("explicit_detail",)
```

- [ ] **Step 2: Run the focused tests and verify the old auto promotion fails**

Run:

```bash
PYTHONPATH=. python \
  -m pytest tests/unit/test_context_loading_views.py \
  -k 'raw_item_with_direct_evidence or raw_overview_is_empty or explicit_detail_still' -q
```

Expected: the two `auto` tests fail because current code selects `detail`; the explicit-detail test passes.

- [ ] **Step 3: Implement the smallest core selection change**

Update `select_context_view` so `auto` never returns detail:

```python
    view: ContextView = "locator"
    reasons: list[str] = ["default_locator"]

    if _raw_with_direct_evidence(item):
        reasons.append("raw_direct_evidence")

    if _should_load_overview(item, firewall_decision=firewall_decision):
        view = "overview"
        reasons.append(_overview_reason(item, firewall_decision=firewall_decision))

    selection = ContextViewSelection(view, tuple(_dedupe_reasons(reasons)))
```

Extend `_should_load_overview` so direct evidence chooses overview only when one exists:

```python
    if _raw_with_direct_evidence(item):
        return True
```

Change `_raw_with_direct_evidence` to depend only on item metadata, not body presence. Explicit `requested="detail"` remains handled by the existing early return.

- [ ] **Step 4: Add the shared non-blocking governance helper and tests**

Create `agent_brain/memory/context/recall_policy.py`:

```python
"""Shared staged-recall policy for Agent-facing search surfaces."""
from __future__ import annotations

MAX_STAGED_DETAIL_ITEMS = 3
BROAD_EXPLICIT_DETAIL_WARNING = (
    "explicit detail search with top_k>3 bypasses staged recall; "
    "prefer locator/overview search followed by read_memory for 1-3 selected items"
)


def search_governance_warnings(*, verbosity: str, top_k: int) -> tuple[str, ...]:
    if verbosity.strip().lower() == "detail" and top_k > MAX_STAGED_DETAIL_ITEMS:
        return (BROAD_EXPLICIT_DETAIL_WARNING,)
    return ()
```

Add to `tests/unit/test_context_loading_views.py`:

```python
def test_broad_explicit_detail_search_is_warned_but_not_blocked() -> None:
    from agent_brain.memory.context.recall_policy import search_governance_warnings

    assert search_governance_warnings(verbosity="detail", top_k=4)
    assert search_governance_warnings(verbosity="detail", top_k=3) == ()
    assert search_governance_warnings(verbosity="auto", top_k=10) == ()
```

- [ ] **Step 5: Run the full context-loading test file**

Run:

```bash
PYTHONPATH=. python \
  -m pytest tests/unit/test_context_loading_views.py tests/unit/test_headroom_adaptive_compression.py -q
```

Expected: all tests pass; explicit detail compression tests remain green.

- [ ] **Step 6: Commit the core invariant**

```bash
git add agent_brain/memory/context/recall_policy.py \
  agent_brain/memory/context/context_loading.py \
  tests/unit/test_context_loading_views.py
git commit -m "fix: enforce staged auto recall views"
```

### Task 2: Align MCP and CLI/hook search surfaces

**Files:**
- Modify: `agent_brain/interfaces/mcp/tools/search_tools.py`
- Modify: `agent_brain/interfaces/cli/commands/query.py`
- Test: `tests/unit/test_context_loading_views.py`
- Test: `tests/unit/test_cli_smoke.py`
- Test: `tests/unit/test_query_intent_fewshot.py`

- [ ] **Step 1: Add failing MCP tests for auto-body exclusion and explicit-detail compatibility**

Seed a raw L0 item with a direct evidence ref in `tests/unit/test_context_loading_views.py`, then assert:

```python
    auto_hit = search_memory("staged mcp", top_k=5, verbosity="auto")[0]
    assert auto_hit["selected_view"] in {"locator", "overview"}
    assert "body" not in auto_hit
    assert "detail-only marker" not in auto_hit["context_pack"]["text"]

    detail_hit = search_memory("staged mcp", top_k=5, verbosity="detail")[0]
    assert detail_hit["body"].rstrip() == "detail-only marker"
    assert detail_hit["context_pack"]["selected_view"] == "detail"
    assert detail_hit["governance_warnings"]

    bounded_detail_hit = search_memory("staged mcp", top_k=3, verbosity="detail")[0]
    assert bounded_detail_hit["body"].rstrip() == "detail-only marker"
    assert "governance_warnings" not in bounded_detail_hit
```

- [ ] **Step 2: Run the MCP tests and verify auto still leaks detail**

Run:

```bash
PYTHONPATH=. python \
  -m pytest tests/unit/test_context_loading_views.py -k 'mcp and staged' -q
```

Expected: failure on current auto/body behavior and missing governance warnings.

- [ ] **Step 3: Make MCP rendering consume the single context pack**

In `search_memory`:

```python
    warnings = search_governance_warnings(verbosity=verbosity, top_k=top_k)
```

For each hit, use `context_pack.selected_view` and `context_pack.load_reason`; do not call `select_context_view` a second time. Preserve the existing explicit detail branch:

```python
            result["selected_view"] = context_pack.selected_view
            result["load_reason"] = list(context_pack.load_reason)
            if context_pack.selected_view == "overview":
                result["overview"] = item.context_views.overview
            elif context_pack.selected_view == "locator":
                result["snippet"] = item.context_views.locator
            elif verbosity == "detail":
                result["overview"] = item.context_views.overview
                result["body"] = body
            if warnings:
                result["governance_warnings"] = list(warnings)
```

This keeps explicit detail body returns unchanged and prevents auto from manufacturing `body`.

- [ ] **Step 4: Add failing CLI tests for staged auto and broad-detail warning**

In `tests/unit/test_cli_smoke.py`, seed a raw L0 item containing a body-only marker. Assert:

```python
    auto = runner.invoke(app, [
        "search", "staged cli", "--format", "text",
        "--context-firewall", "--verbosity", "auto", "--top-k", "5",
    ])
    assert auto.exit_code == 0, auto.output
    assert "body-only cli marker" not in auto.stdout
    assert "retrieve=\"memory read " in auto.stdout

    detail = runner.invoke(app, [
        "search", "staged cli", "--format", "text",
        "--verbosity", "detail", "--top-k", "5",
    ])
    assert detail.exit_code == 0, detail.output
    assert "body-only cli marker" in detail.stdout
    assert "bypasses staged recall" in detail.stderr
```

- [ ] **Step 5: Surface the shared warning once on CLI stderr**

After parsing verbosity in `query.search`:

```python
    governance_warnings = search_governance_warnings(
        verbosity=verbosity,
        top_k=top_k,
    )
    for warning in governance_warnings:
        typer.echo(f"warning: {warning}", err=True)
```

Do not add `--verbosity detail` to `inject-context.sh`; its existing context-firewall default remains `auto` and receives the new core behavior.

- [ ] **Step 6: Add or extend a real hook regression**

In `tests/unit/test_query_intent_fewshot.py`, reuse its real `inject-context.sh` runner and seed a raw L0 direct-evidence item. Assert the emitted `<agent_brain>` contains the locator/retrieve hint and excludes a body-only marker. This proves Codex, Claude Code, Qoder, QoderWork, and any other hook consumer share the same staged output.

- [ ] **Step 7: Run MCP, CLI, and hook tests**

```bash
PYTHONPATH=. python \
  -m pytest tests/unit/test_context_loading_views.py \
  tests/unit/test_cli_smoke.py tests/unit/test_query_intent_fewshot.py -q
```

Expected: all pass.

- [ ] **Step 8: Commit MCP/CLI/hook behavior**

```bash
git add agent_brain/interfaces/mcp/tools/search_tools.py \
  agent_brain/interfaces/cli/commands/query.py \
  tests/unit/test_context_loading_views.py tests/unit/test_cli_smoke.py \
  tests/unit/test_query_intent_fewshot.py
git commit -m "fix: stage agent search before deep reads"
```

### Task 3: Align SDK search and add bounded SDK reads

**Files:**
- Modify: `agent_brain/interfaces/sdk/query.py`
- Modify: `agent_brain/interfaces/sdk/sdk.py`
- Test: `tests/unit/test_sdk_client.py`

- [ ] **Step 1: Add failing SDK search and read tests**

```python
def test_sdk_auto_search_uses_compact_snippet_and_preserves_explicit_detail(client):
    item_id = client.write(
        type="fact",
        title="SDK staged recall",
        summary="sdk staged locator",
        overview="sdk staged overview",
        body="sdk detail-only marker",
        refs={"files": ["/tmp/sdk-evidence.log"]},
    )

    auto = client.search("SDK staged recall", top_k=5, verbosity="auto")[0]
    assert auto.id == item_id
    assert "detail-only marker" not in auto.snippet
    assert "detail-only marker" not in auto.context_pack["text"]

    detail = client.search("SDK staged recall", top_k=5, verbosity="detail")[0]
    assert "sdk detail-only marker" in detail.context_pack["text"]
    assert detail.governance_warnings


def test_sdk_read_supports_bounded_detail_without_breaking_full_read(client):
    item_id = client.write(
        type="artifact",
        title="SDK bounded read",
        summary="bounded locator",
        body="0123456789",
    )

    bounded = client.read(item_id, head=4, view="detail")
    assert bounded["body"] == "0123"
    assert bounded["body_truncated"] is True
    assert bounded["full_chars"] == 10
    assert client.read(item_id)["body"].rstrip() == "0123456789"
```

- [ ] **Step 2: Run SDK tests and verify failure**

```bash
PYTHONPATH=. python \
  -m pytest tests/unit/test_sdk_client.py -k 'staged or bounded' -q
```

Expected: failures for body-derived snippet, missing warnings, and unsupported read parameters.

- [ ] **Step 3: Implement compact search results**

Add to `SearchResult`:

```python
    governance_warnings: list[str] = field(default_factory=list)
```

Compute one pack and use its text for the snippet in all modes:

```python
        warnings = search_governance_warnings(verbosity=verbosity, top_k=top_k)
        # ...
                snippet=context_pack.text[:200],
                governance_warnings=list(warnings),
```

Because explicit detail packs still contain the body, their existing detail content remains available.

- [ ] **Step 4: Implement backward-compatible bounded SDK reads**

Change the helper signature:

```python
def read_item(
    store: Any,
    item_id: str,
    *,
    head: int | None = None,
    view: str = "detail",
) -> dict[str, Any] | None:
```

Use `render_context_view` for locator/overview. For detail, return the current full body when `head is None`; otherwise return the prefix plus `body_truncated` and `full_chars` when truncated. Change the facade to:

```python
    def read(
        self,
        item_id: str,
        *,
        head: int | None = None,
        view: str = "detail",
    ) -> dict[str, Any] | None:
        return read_item(
            self._components.get_store(),
            item_id,
            head=head,
            view=view,
        )
```

Reject unknown views with the same locator/overview/detail vocabulary used elsewhere.

- [ ] **Step 5: Run SDK tests**

```bash
PYTHONPATH=. python \
  -m pytest tests/unit/test_sdk_client.py -q
```

Expected: all pass, including existing no-argument full reads.

- [ ] **Step 6: Commit SDK behavior**

```bash
git add agent_brain/interfaces/sdk/query.py agent_brain/interfaces/sdk/sdk.py \
  tests/unit/test_sdk_client.py
git commit -m "fix: stage sdk recall and bound deep reads"
```

### Task 4: Align Web search and add bounded item reads

**Files:**
- Modify: `web/api/routes/item_search.py`
- Modify: `web/api/routes/item_crud.py`
- Test: `tests/unit/test_web_api.py`

- [ ] **Step 1: Add failing Web API tests**

Create a raw L0 direct-evidence item with a body-only marker, then assert:

```python
    auto = client.get(
        "/api/search?q=web%20staged&top_k=5&verbosity=auto",
        headers={"Authorization": f"Bearer {admin_token}"},
    ).json()
    result = auto["results"][0]
    assert "web detail-only marker" not in result["snippet"]
    assert "web detail-only marker" not in result["context_pack"]["text"]

    detail = client.get(
        "/api/search?q=web%20staged&top_k=5&verbosity=detail",
        headers={"Authorization": f"Bearer {admin_token}"},
    ).json()
    assert "web detail-only marker" in detail["results"][0]["context_pack"]["text"]
    assert detail["diagnostics"]["governance_warnings"]
```

Add bounded item read coverage:

```python
    bounded = client.get(
        f"/api/items/{item.id}?head=4&view=detail",
        headers={"Authorization": f"Bearer {admin_token}"},
    ).json()
    assert bounded["body"] == body[:4]
    assert bounded["body_truncated"] is True
    assert bounded["full_chars"] == len(body)

    full = client.get(
        f"/api/items/{item.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    ).json()
    assert full["body"] == body
```

- [ ] **Step 2: Run focused Web tests and verify failure**

```bash
PYTHONPATH=. python \
  -m pytest tests/unit/test_web_api.py -k 'staged or bounded_item' -q
```

- [ ] **Step 3: Reuse the context pack for search snippets and diagnostics**

In `/api/search`, compute shared warnings once and render:

```python
    governance_warnings = search_governance_warnings(
        verbosity=verbosity,
        top_k=top_k,
    )
```

Replace `"snippet": body[:200]` with:

```python
                "snippet": context_pack.text[:200],
```

Add to top-level diagnostics:

```python
            "governance_warnings": list(governance_warnings),
```

Do not change `/api/search/fulltext`; it remains an explicit human/admin full-text surface.

- [ ] **Step 4: Add optional `head/view` to item read**

Change the route signature:

```python
async def get_item(
    item_id: str,
    head: int | None = Query(None, ge=0),
    view: str = Query("detail", pattern="^(locator|overview|detail)$"),
    user: CurrentUser = Depends(get_current_user),
):
```

Keep the existing no-argument detail/full response. Locator/overview returns the selected compact field; bounded detail returns truncation metadata matching MCP/SDK semantics.

- [ ] **Step 5: Run Web tests**

```bash
PYTHONPATH=. python \
  -m pytest tests/unit/test_web_api.py tests/conformance/test_web_surface_lock.py -q
```

Expected: all pass; OpenAPI surface remains compatible.

- [ ] **Step 6: Commit Web behavior**

```bash
git add web/api/routes/item_search.py web/api/routes/item_crud.py \
  tests/unit/test_web_api.py
git commit -m "fix: stage web recall and bound item reads"
```

### Task 5: Lock staged recall into every Agent instruction surface

**Files:**
- Modify: `agent_brain/agent_integrations/awareness.py`
- Modify: `agent_brain/interfaces/mcp/onboarding.py`
- Modify: `agent_runtime_kit/AGENT_MEMORY_DISCIPLINE.md`
- Modify: `README.md`
- Modify: `README.zh.md`
- Modify: `docs/architecture.md`
- Test: `tests/unit/test_adapters.py`
- Test: `tests/unit/test_mcp_onboarding.py`
- Test: `tests/unit/test_docs_truth_contract.py`

- [ ] **Step 1: Add failing truth-contract assertions**

In the shared awareness tests, require exact staged guidance:

```python
    assert 'search_memory(..., verbosity="auto")' in awareness
    assert "locator/overview" in awareness
    assert "1-3" in awareness
    assert 'read_memory(id, head=2000, view="detail")' in awareness
```

In MCP onboarding and docs truth-contract tests, require the same order and assert no recommended workflow uses `search_memory(..., verbosity="detail")`.

- [ ] **Step 2: Run contract tests and verify the stricter wording fails**

```bash
PYTHONPATH=. python \
  -m pytest tests/unit/test_adapters.py tests/unit/test_mcp_onboarding.py \
  tests/unit/test_docs_truth_contract.py -q
```

- [ ] **Step 3: Update the shared Awareness Channel**

Replace the two generic search/read bullets with a single explicit sequence:

```python
        "- call `brief_memory` or `search_memory(..., verbosity=\"auto\")` first; auto search returns only locator/overview candidates;",
        "- select only the 1-3 items whose detail is actually needed, then call `read_memory(id, head=2000, view=\"detail\")`;",
        "- reserve explicit search `verbosity=\"detail\"` for deliberate bounded diagnostics, not ordinary Top-K discovery;",
```

Because Qoder, QoderWork, Claude Code, Cursor, Cline, Continue, OpenClaw, Hermes, OpenSquilla, Aone Copilot, and Wukong reuse `render_awareness_block`, this change governs all shared adapter channels. Keep native Qoder/QoderWork/Wukong bridge examples synchronized where they do not reuse the shared renderer.

- [ ] **Step 4: Update onboarding and architecture documentation**

Document these invariants in English and Chinese:

```text
auto -> locator/overview only
explicit detail -> body preserved
ordinary flow -> search/brief, select 1-3, bounded read_memory
broad explicit detail -> non-blocking governance warning
```

Do not claim `/api/search/fulltext`, export, or manual single-item reads are restricted.

- [ ] **Step 5: Run adapter/docs tests**

```bash
PYTHONPATH=. python \
  -m pytest tests/unit/test_adapters.py tests/unit/test_cli_adapter.py \
  tests/unit/test_mcp_onboarding.py tests/unit/test_docs_truth_contract.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit instruction governance**

```bash
git add agent_brain/agent_integrations/awareness.py \
  agent_brain/interfaces/mcp/onboarding.py \
  agent_runtime_kit/AGENT_MEMORY_DISCIPLINE.md README.md README.zh.md \
  docs/architecture.md tests/unit/test_adapters.py tests/unit/test_mcp_onboarding.py \
  tests/unit/test_docs_truth_contract.py
git commit -m "docs: govern staged recall across agents"
```

### Task 6: Harden Qoder doctor transcript parsing

**Files:**
- Modify: `agent_brain/agent_integrations/qoder.py`
- Test: `tests/unit/test_adapters.py`

- [ ] **Step 1: Add a failing mixed-JSON transcript test**

Near the existing timestamp-ordering test:

```python
def test_qoder_transcript_timestamp_skips_non_object_json_rows(tmp_path) -> None:
    from agent_brain.agent_integrations import qoder as qoder_mod

    transcript = tmp_path / "mixed.jsonl"
    transcript.write_text(
        "\n".join([
            json.dumps("plain string"),
            json.dumps(["list"]),
            json.dumps(42),
            json.dumps(True),
            json.dumps(None),
            json.dumps({"timestamp": "2026-07-15T00:00:00Z"}),
        ]) + "\n",
        encoding="utf-8",
    )

    observed = qoder_mod.QoderAdapter(
        brain_dir=tmp_path / ".brain"
    )._transcript_observed_time(transcript)

    assert observed == datetime.fromisoformat(
        "2026-07-15T00:00:00+00:00"
    ).timestamp()
```

- [ ] **Step 2: Run the test and verify the `str.get` crash**

```bash
PYTHONPATH=. python \
  -m pytest tests/unit/test_adapters.py \
  -k 'transcript_timestamp_skips_non_object' -q
```

Expected: `AttributeError` from `record.get`.

- [ ] **Step 3: Add the type guard**

```python
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            timestamp = record.get("timestamp")
```

- [ ] **Step 4: Run Qoder adapter tests and a live doctor smoke**

```bash
PYTHONPATH=. python \
  -m pytest tests/unit/test_adapters.py tests/unit/test_cli_adapter.py \
  tests/unit/test_adapter_onboarding.py -q

PYTHONPATH=. python \
  -m agent_brain.interfaces.cli adapter doctor qoder --format json \
  > /tmp/amh-qoder-doctor-staged-recall.json || true

test -s /tmp/amh-qoder-doctor-staged-recall.json
! rg -n "Traceback|AttributeError" /tmp/amh-qoder-doctor-staged-recall.json
```

The doctor may report configuration warnings/errors, but it must emit JSON and must not crash.

- [ ] **Step 5: Commit the doctor fix**

```bash
git add agent_brain/agent_integrations/qoder.py tests/unit/test_adapters.py
git commit -m "fix: tolerate scalar qoder transcript rows"
```

### Task 7: Cross-surface verification and review

**Files:**
- Verify all files changed by Tasks 1–6
- Update: `docs/superpowers/plans/2026-07-15-staged-agent-recall-governance.md` only to check completed boxes if desired

- [ ] **Step 1: Run the focused cross-surface suite**

```bash
PYTHONPATH=. python \
  -m pytest \
  tests/unit/test_context_loading_views.py \
  tests/unit/test_headroom_adaptive_compression.py \
  tests/unit/test_cli_smoke.py \
  tests/unit/test_query_intent_fewshot.py \
  tests/unit/test_sdk_client.py \
  tests/unit/test_web_api.py \
  tests/unit/test_adapters.py \
  tests/unit/test_cli_adapter.py \
  tests/unit/test_adapter_onboarding.py \
  tests/unit/test_mcp_onboarding.py \
  tests/unit/test_docs_truth_contract.py \
  tests/conformance/test_mcp_e2e.py \
  tests/conformance/test_public_surface_lock.py \
  tests/conformance/test_web_surface_lock.py -q
```

Expected: zero failures.

- [ ] **Step 2: Run the full Python suite**

```bash
PYTHONPATH=. python \
  -m pytest tests/ -q
```

Expected: zero failures; record the exact passed/skipped counts.

- [ ] **Step 3: Run static and repository hygiene checks**

```bash
PYTHONPATH=. python \
  -m ruff check agent_brain web tests
git diff --check
git status --short --branch
```

Expected: Ruff exits 0, `git diff --check` is silent, and status contains only intentional changes.

- [ ] **Step 4: Manually reproduce the original Codex-style Top-5 case**

Use a temporary brain with five raw L0 direct-evidence items. Call MCP `search_memory(..., top_k=5, verbosity="auto")` and assert programmatically:

```python
assert len(hits) == 5
assert all(hit["selected_view"] in {"locator", "overview"} for hit in hits)
assert all("body" not in hit for hit in hits)
assert all("detail-only marker" not in hit["context_pack"]["text"] for hit in hits)
```

Then call one result with explicit `verbosity="detail"` and confirm its body still returns.

- [ ] **Step 5: Request code review**

Review the complete diff against:

- auto never selects detail;
- explicit detail behavior is preserved;
- ordinary snippets cannot bypass the selected context view;
- every Agent-facing instruction uses staged recall;
- broad detail warnings are non-blocking;
- Qoder doctor skips non-object rows without hiding valid timestamps.

Resolve all Critical and Important findings before proceeding.

- [ ] **Step 6: Record the durable result in AMH**

After verification, write one `artifact` memory item containing the branch/commit, modified public surfaces, exact test counts, and the compatibility boundary that explicit detail remains available. Do not store raw test logs or source copies.

## Completion checklist

- [ ] `auto` output domain is locator/overview across core, MCP, CLI/hook, SDK, and Web.
- [ ] Explicit `verbosity="detail"` still returns body/detail text.
- [ ] Search snippets do not bypass staged views.
- [ ] Deep reads are bounded and documented for 1–3 selected items.
- [ ] Broad explicit detail emits a non-blocking governance warning.
- [ ] All adapter instruction surfaces inherit the same staged workflow.
- [ ] Qoder doctor tolerates string/list/scalar/null transcript rows.
- [ ] Focused tests, full suite, Ruff, and diff hygiene all pass with fresh evidence.
