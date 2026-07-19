# Architecture

How Agent Memory Hub is layered, and the invariants that keep a shared brain
pool durable and inspectable across many agents.

## Two layers

| Layer | Path | Role |
|---|---|---|
| **Runtime bootstrap** | `agent_runtime_kit/` | What agents and shells touch directly: memory **hooks** (`SessionStart`, `UserPromptSubmit`, `Stop`), lifecycle evidence **hooks** (`PreCompact`, `PostCompact`, `SubagentStart`, `SubagentStop`), the **MCP launcher**, write/search shell **tools**, the discipline doc, schema docs, and the harvest daemon. Shell entry points call the Python package and degrade to a pending fallback when Python is unavailable. |
| **Core package** | `agent_brain/` | The source of truth. `interfaces/` owns CLI/MCP/SDK entry points, `contracts/` owns schemas, `platform/` owns technical primitives, `memory/` owns store/recall/context/governance/evidence capabilities, and `agent_integrations/` owns adapter implementations. |

`agent_runtime_kit/tools/write-memory.sh` prefers the Python CLI and, on any failure (or when
forced), appends a **pending** record instead of losing the write — so the bootstrap
layer never silently drops a memory even if the core package can't run.

## The one write funnel — `agent_brain/memory/store/write_service.py`

Every write entry point goes through `WriteService`:

```
MCP write_memory ─┐
CLI memory write ─┤
hook write shim  ─┼─► WriteService.write(item, body, allow_unsafe)
pending replay   ─┤        1. audit gate (fail-closed on critical/high unless allow_unsafe)
harvester        ─┘        2. md append   ← the ONLY thing that means "written"
                           3. index upsert ← best-effort; failure degrades, never blocks
```

Invariant: **the markdown append is the only verdict for "written."** Embedding +
sqlite index upsert are best-effort; if they fail (offline, missing model, locked
db) the write still returns `written` with a `degraded` flag, and the derived index
is repaired later by `memory sync-pending` / `memory reindex`.

## Markdown is the source of truth; the index is derived

`items/mem-*.md` frontmatter is the durable artifact. SQLite FTS5 + sqlite-vec is a
**rebuildable** index over it — never authoritative. This is why a degraded write is
still a real write, and why `reindex` can reconstruct everything from the md alone.

## Durability ladder (Stage B / C) — `agent_brain/memory/store/pending.py`

- **Stage B (degrade):** index/embedder unavailable → md still written, item flagged
  `degraded`, recorded for later index repair.
- **Stage C (buffer):** the whole core write path unavailable (or `MEMORY_HUB_FORCE_PENDING=1`)
  → the record is appended to `$BRAIN_DIR/pending/`. `memory sync-pending` replays the
  queue through the same `WriteService` funnel; repeatedly-failing records park under
  `pending/dead/`. `memory doctor --offline` grades embedder tier + pending depth +
  local MCP reachability and returns a graded report.

`run_doctor(offline=True)` returns the graded `DoctorReport.exit_code`
(`0` / `1` / `2`). The `memory doctor --offline` CLI remains a compatibility
presenter with process exit `0` and displays that report grade.

## Raw conversation evidence — `agent_brain/memory/evidence/conversation_store.py`

AMH now owns a first-class raw conversation evidence layer:

- path: `~/.agent-memory-hub/sources/conversations/<conversation_id>/messages.jsonl`
- record: `ConversationMessageRecord`
- fields: role, text, `content_sha256`, source path/URI, byte offsets, source agent, session,
  project, sensitivity, retention metadata, and hot/warm/cold/frozen tier
- access: CLI `memory conversation ingest/list/read/rebalance`, MCP `list_conversations` /
  `read_conversation`, and automatic snapshot during `memory harvest`
- governance: `conversation_governance.py` computes an Ebbinghaus-style retention
  score from half-life, access count, importance, and age; rebalance persists the
  derived tier back to `messages.jsonl`.

Invariant: **raw conversation messages are evidence, not MemoryItems.** They are
not indexed for automatic prompt injection by default. Extractors can turn spans
into `MemoryItem` candidates with provenance, but `items/` remains the knowledge
layer and `sources/conversations/` remains the evidence layer.

## Context packing — `agent_brain/memory/context/context_packing.py`

AMH's before-inject path uses a reversible context pack instead of dumping full
memory bodies into the prompt:

```
MemoryItem + body
    │
    ├─► select_context_view(locator | overview | detail)
    │
    └─► context_pack {
          text, selected_view, load_reason,
          packed_tokens, full_tokens,
          detail_uri,
          read_memory(id, head=2000, view='detail')
        }
```

Invariant: **`context_pack` is an injection-time derived object, not a new source
of truth.** It can always be rebuilt from `context_views` plus the Markdown body.
Automatic UserPromptSubmit injection and MCP `search_memory(..., verbosity="auto")`
send the compact `text` and retrieve hint first; agents deep-read the canonical
body only after the compact view proves relevant.

Packing is not authorization. Prompt-facing callers may build this derived view
only after InjectionGateway and ContextFirewall approve the hydrated candidate.

Staged-recall invariant: **`auto` selects only `locator` or `overview`**. Agents
select 1-3 relevant candidates before bounded `read_memory` calls. Explicit
`verbosity="detail"` still returns the canonical body for deliberate diagnostics;
broad explicit-detail searches receive a non-blocking governance warning.

## Loop Contract — `agent_brain/memory/loops/`

Loop Engineering uses a contract plus runtime ledger instead of prompt-only
control. `LoopContract` defines goal / state / action / feedback / verifier / budget / stop condition / human gate. `LoopRun` stores contract metadata, required verifiers, structured feedback, and readiness evidence.
`LoopOrchestrator` is the high-level non-LLM controller behind `memory loop run --contract`:
it validates the contract, creates the runtime ledger, runs allowlisted verifiers,
opens required human gates, and completes or blocks the loop from evidence.

Invariant: **AMH is the loop fact layer, verification layer, and governance
layer, not an automatic runner.** High-risk actions remain human-gated, verifier
commands stay allowlisted, and long-term memory writes still go through
`WriteService` or review boundaries.

## Retrieval trace — `agent_brain/memory/recall/retrieval_trace.py`

`Retriever.search(..., explain=True)` attaches an optional trace to final hits:
initial BM25/vector ranks, RRF score, named pipeline stage effects, final rank,
and compact signals. CLI `memory search --explain` and MCP
`search_memory(include_trace=True)` expose this only when requested, so automatic
hook injection stays on the short `context_pack` path.

Invariant: **trace is observational.** It must not change ranking, access
recording, Markdown source data, MCP tool count, or default search output.

## Exact retrieval order — `agent_brain/memory/recall/retrieval.py`

AMH retrieval is a staged pipeline, not a single "semantic search" call:

This canonical retrieval chain applies only to retrieval-backed search and UserPromptSubmit Hook surfaces.

```
user question / search call / UserPromptSubmit
  -> query signal + SearchFilter
  -> metadata filter: type, project, tags, exclude tags, tenant, age, supersession
  -> BM25 full-text recall and vector recall over allowed ids
  -> RRF fusion
  -> status/handoff supplement
  -> optional cross-encoder rerank
  -> confidence + retention decay
  -> feedback value weighting
  -> status/handoff boost and adapter runtime evidence boost
  -> temporal-state filter and Markdown supersession filter
  -> optional MMR
  -> optional Hopfield expansion
  -> optional refs_graph expansion
  -> InjectionGateway
  -> ContextFirewall
  -> locator / overview / detail context loading
  -> ContextPack budget / view selection
  -> approved-hit access recording (once)
  -> prompt surface
```

### Prompt-facing injection authorization boundary

The retrieval-backed safe prompt path is:

`Retriever raw hits -> InjectionGateway -> ContextFirewall -> ContextPack -> prompt surface`

Safe retrieval-backed prompt surfaces call retrieval with `record_access=False`, hydrate raw hits,
submit hydrated candidates to InjectionGateway, and expose only final included
ContextPack entries. A prompt-facing surface must never call `build_context_pack`
directly on raw hits.

Explicit raw CLI diagnostics do not grant injection authorization and do not turn raw hits into ContextPack.
Explicit raw diagnostics keep their normal single raw access record, return no ContextPack, and cannot write an injection cohort.
If InjectionGateway fails, prompt-facing callers fail closed; there is no raw-hit fallback.
Raw overfetch runs with access recording disabled; after Gateway authorization, final included hits are recorded exactly once before the prompt surface returns.
An injection cohort is a neutral observation; authorization is established only by the secure Gateway path that records it.

Prompt-facing recall-gap records persist only a query fingerprint and aggregate counts; they do not store rejected IDs or id:reason evidence.
The lower-level explicit record_gap API may still store rejected_ids and diagnostic evidence for deliberate diagnostic callers.
DataFlow and memory-lineage outputs apply a closed aggregate allowlist to recall-gap evidence, so historical free-text evidence is not re-exposed.

### Dual-route hook recall and rollout boundary

`InjectionGateway` is the logical security boundary for prompt authorization; it
is not a resident semantic service and does not make model availability a hook
precondition. The `UserPromptSubmit` hook forwards the complete normalized task
description through one bounded routed CLI request and one orchestrated pipeline.
Candidate generation can combine
term BM25 with the complete-question semantic route. If the semantic provider is
not already ready, the hook does **not cold-load or download a model**; retrieval
continues with term BM25 plus the Unicode-aware raw BM25 fallback. Every path,
including degraded and feature-flagged paths, still passes through Gateway and
ContextFirewall before building ContextPack.

The hook cold path first streams its original stdin into a mode-0600 private file;
the byte stream never enters a shell variable. The dependency-free **payload parser**
runs once under system Python, reads that file, and returns prompt,
session, cwd, and event fields through a fixed NUL-delimited protocol. After
`_resolve-python.sh` verifies the AMH interpreter identity, one **verified preflight**
process records the runtime event, captures the live prompt,
normalizes recall text, loads bounded multimodal text, and emits multimodal gap
JSON. The parser, preflight, and legacy fallback replay the same bytes from the
private file. HUP/INT/TERM/EXIT traps remove the file. Individual evidence writes
remain fail-open. The parser recursively rejects nested decoded NUL in every JSON
string key and value before any evidence or recall work. The parser and preflight
each run as a managed child, so a signal delivered only to the parent can still
kill, reap, and clean up the active child and both private files.

Fallback scope depends on whether evidence may already exist. A preflight that
exits 0 but emits a polluted or invalid protocol uses **derivation-only fallback**;
runtime and multimodal evidence are not written twice. A nonzero preflight exit
uses the existing **full fallback**, which preserves the runtime event, live prompt,
and multimodal evidence responsibilities. An empty prompt with an attachment still
enters verified preflight; without a verified AMH interpreter, legacy multimodal
capture and derivation preserve the attachment path.

This consolidation changes process topology, not authority or budgets.
InjectionGateway, ContextFirewall, the 2 秒 search budget, stdout cap,
descendant cleanup, adapter output envelope, and feature-off behavior remain unchanged. The
preflight never authorizes candidates, and its fallback never bypasses Gateway.

`AGENT_MEMORY_HUB_ROUTED_RECALL=0` is an emergency compatibility switch that
**only rolls back candidate generation** to the legacy search behavior. It never
disables Gateway, never authorizes raw hits, and never changes the access-recording
boundary.

`memory brief` and `memory search` serve different jobs. `brief` is a bounded
project-resume overview (and `--fail-empty` lets automation distinguish an empty
brief); `search` recalls evidence for a concrete task and should receive the full
task description. They are not fallback aliases, and agents should not invent a
manual keyword gate before search.

This rollout does not implement **Session continuation**. Bare turns such as
“继续”, “确认”, “是”, or “1” remain non-injectable until a later design provides a
trusted session pointer and previous-task state. A short prompt with concrete
topic or entity anchors is evaluated normally.

The stage-two report now records calibration 15/15, heldout 10/10, and sanitized
production replay 12/12, with 0 FP / 0 FN across the 41-case public safety
fixture. The final hardened candidate
`b706ae0d915a3975919055367aa9d27a72baeda4` passed 连续两轮 independent 30-run
hook confirmations: candidate p50/p95/max were
1316.281/1390.675/1402.393ms and 1310.445/1382.689/1403.443ms, with no errors,
timeouts, protocol pollution, or fallback. Candidate-only benchmark tracing is
opt-in, mode 0600, and stores only a closed preflight-path enum; it never stores
prompt, session, cwd, or hook output. Earlier optimized candidates remain in
history as successful but superseded evidence. The machine-readable facts are
`docs/evaluation/stage2-recall-quality-report.json`,
`docs/evaluation/dual-route-hook-benchmark-report.json`, and
`scripts/check-recall-quality.py`.

### Adapter productization governance

Every discovered adapter has a versioned `amh-adapter-manifest/v1` contract for
platforms, client compatibility, events, payload/output protocols, channels,
lifecycle commands, evidence TTLs, feature flag, degradation, and rollback.
Readiness is projected as six independent states:

```
implemented -> installed -> configured -> doctor_passed
                                      |-> runtime_observed
                                      |-> context_injected
all required states + fresh verification + support level -> verified
```

The arrows describe prerequisites, not automatic promotion. The current runtime,
context-injection, doctor, and verification TTL is seven days. Transcript-level
context probes additionally reject transcript files and timestamped cohorts
outside their three-day effectiveness window. A fresh injection cohort proves
that the hook produced a ContextPack; for Qoder-family clients, a matched fresh
client transcript or AMH tool trace is still required to prove the model session
actually used that channel.

`install`, `doctor`, `verify`, `repair`, `upgrade`, and `uninstall` return the
stable `amh-adapter-lifecycle-result/v1` envelope and reason codes. Repair and
uninstall are constrained to adapter-declared AMH-owned paths. Upgrade writes a
private snapshot, verifies hashes, and restores it if the transaction fails.
The low-sensitive provenance ledger stores package/commit/manifest versions,
artifact hashes, action/status/reason, cohort, and backup ID; it never stores
prompt or transcript bodies.

Release state is per adapter. Ordered `shadow` → `canary` → `default` promotion
uses deterministic session buckets; `disabled` is an adapter-local kill switch.
Disabled/shadow/excluded hook invocations return the adapter's clean empty
protocol, while core CLI, MCP, storage, and other adapters remain available.
The generated stage-three report and required `adapter-governance` CI job bind
manifests, lifecycle/system tests, core isolation, real-machine blocker evidence,
hook hash, and privacy scanning to the committed source.

### Budgeted brief authorization boundary

`ItemsStore candidates -> InjectionGateway eligibility -> ContextFirewall -> tier/brief budget -> brief response`

The budgeted resume branch starts from stored candidates, applies eligibility and
policy gates, then enforces its own tier budget before returning the briefing.

### Retrieval scoring invariants

- BM25 and vector recall are fused by reciprocal rank fusion:
  `RRF(d)=Σ_s w_s/(k+rank_s(d)+1)`.
- Decay is applied as `S_effective=S_rrf×confidence×decay_coefficient`.
- `decay_coefficient` is bounded and combines retention, access, support,
  gain, and contradiction signals.
- `maturity` is currently governance/context-loading metadata. It is computed
  by maturity governance and can be written back to item frontmatter, but it is
  not a direct multiplier inside `SearchEngine.search()`.

Invariant: **retrieved does not mean injected.** The retrieval pipeline produces
raw candidates; `InjectionGateway` owns prompt authorization, invokes
`ContextFirewall`, and sends only approved entries to `ContextPack`. Explicit raw
diagnostics remain diagnostic output, not injection authority.

## Exact maintenance order — store, evidence, governance, and evolution

AMH maintenance is not one "cleanup" command. It is the write, evidence,
repair, feedback, governance, and evolution path that keeps the brain pool
auditable:

```
write signal / candidate / raw transcript / task outcome
  -> entry normalization or pending fallback
  -> candidate quarantine and review
  -> WriteService audit, enrichment, quality checks, evidence sidecars
  -> ItemsStore Markdown append
  -> sources/writes ledger
  -> resources/extractions evidence sidecars
  -> HubIndex meta/FTS/vector/refs_graph projection
  -> .index-dirty / pending repair through verify, reindex, sync-pending
  -> raw conversation harvest, watermarking, span dedup, tier rebalance
  -> runtime ledgers: adapter events, injection cohorts, recall gaps, task outcomes
  -> feedback: adopted/rejected only; ignored injected ids stay unchanged
  -> governance scans: duplicate, noise, TTL, quality, drift, maturity, index drift
  -> AutoGovernance safe_apply or review_required / blocked actions
  -> evolve / dream proposals behind audit gates
  -> approved candidates or audited execution paths update the truth layer
  -> data-flow and memory-lineage read models expose redacted observability
```

Maintenance invariants:

- **Candidate is not memory.** Proactive and semantic candidates stay in
  `review/proactive-candidates.jsonl` until approved. Boundary-weak harvested
  items are marked `needs-review` / `unverified-boundary` and low confidence.
- **Markdown remains authoritative.** `ItemsStore.write()` defines durable
  success. `sources/writes`, `resources`, `extractions`, `index.db`, `runtime`,
  `review`, and `derived` are ledgers, sidecars, projections, or candidates.
- **Feedback is explicit.** `InjectionFeedback` reinforces adopted ids and
  penalizes rejected ids. Injected-but-unmentioned ids stay unchanged, so
  exposure alone cannot make an item hotter.
- **Safe apply is narrow.** `AutoGovernanceCycle` can apply low-risk maturity,
  index repair, and conversation-tier changes. Archive, delete, consolidate,
  supersede, and skill synthesis remain review-required or blocked.
- **Evolution is gated.** `EvolveEngine` audits the real payload before
  execution. Some executor paths intentionally use `ItemsStore` directly after
  audit; README marks bulk import and internal governance writes as a P0
  convergence/allow-list boundary rather than pretending every mutation already
  re-enters `WriteService`.

## Transcript harvester (Stage A) — `agent_brain/memory/evidence/harvest/`

Offline-first capture of Claude Code transcripts into the pool:

```
discover_transcripts ─► ConversationStore.ingest_transcript(...)
                          │  snapshot raw message evidence, idempotent by message id
                          ▼
                    read_spans (resume from watermark)
                          │
                          ▼
                    extract_candidates (mechanical, zero-model, secret-redacting)
                          │   dedup by span_hash (sha256 of normalized span)
                          ▼
                    WriteService.write(...)  ← same durable funnel
                          │
                          ▼
                    watermark.set_offset(...)   ← idempotent, resumable
```

- `transcript_reader.py` — streams CC jsonl into `TranscriptSpan`s with byte offsets.
- `watermark.py` — per-transcript resumable byte offset; re-runs harvest nothing-new.
- `extractor.py` — conservative regex/keyword rules → raw (L0) candidates; redacts
  secrets before emitting.
- `dedup.py` — span-level hash so the same transcript region never archives twice.
- `harvester.py` — orchestrator; snapshots raw messages, then `memory harvest [--enrich]`
  writes structured L0 memory candidates.
- `enricher.py` — **optional** LLM upgrade of raw → distilled. If no model is reachable
  (`MEMORY_HUB_NO_MODEL`, or import/probe fails) it is a clean no-op returning 0. The
  mechanical layer already persisted the raw record, so enrichment is pure gravy.
- `agent_runtime_kit/tools/harvest-daemon.sh` — periodic enrich pass when a model is reachable.

The Stop hook (`agent_runtime_kit/hooks/session-end-signal.sh`) tags each session's transcript
`harvest-queue` once (deduped per session) so it can be picked up by `memory harvest`.

## Surfaces

- **CLI** — `agent_brain/interfaces/cli/` package. `cli/_app.py` owns the root Typer app + sub-typers
  (audit/govern/tier/entity/adapter/recall-drift/review/conversation/profile/benchmark/headroom/loop); `cli/commands/*` modules
  hold the command bodies (decorators kept → self-register on import); `cli/_shared.py` holds
  helpers + the import surface. Entry point: `memory = agent_brain.interfaces.cli:app`.
- **MCP** — `agent_brain/interfaces/mcp/` package. `mcp/server.py` owns the FastMCP instance +
  `register_all()`; `mcp/tools/{core,governance,evolve,io,graph,conversation}.py` register the **27**
  operation tools by tier, and onboarding registers one additional `get_usage_guide`
  fallback tool for clients that do not surface prompts/resources; `mcp/tools/_shared.py`
  holds helpers + `_components_cache`.
  `agent_brain/interfaces/mcp/server.py` is the canonical module for `python -m
  agent_brain.interfaces.mcp.server` and the `memory-mcp` console script.
- **Web admin** — `web/app.py` (FastAPI) mounts routers from `web/api/routes/*`.
  The stable API/WS surface is locked by `tests/conformance/test_web_surface_lock.py`.
  `GET /api/memory-lineage` is part of that locked surface, so future route
  additions or deletions must update implementation, tests, README, and diagram
  docs together.

### MCP tool tiers (the one true count)

| Tier | Count | Tools |
|---|---|---|
| **core** (stable) | 10 | write / search / read / update / confirm / stats / list_recent / tag_suggest / delete / brief |
| governance | 6 | audit_skill / audit_outbound / drift_check / govern / batch_confirm / batch_archive |
| io | 5 | export / import / obsidian_export / obsidian_import / gc |
| graph | 3 | graph_memory / link / unlink |
| evolve | 1 | evolve_memory |
| conversation | 2 | list_conversations / read_conversation |
| **operation total** | **27** | guarded by `tests/conformance/test_public_surface_lock.py` |

`brief` (budgeted resume briefing) is core because resuming work is a stable, first-class
operation. README / STRATEGY / ROADMAP must agree with this table; the surface-lock test
also asserts the registered MCP tool count is 28 after adding the onboarding guide fallback.

## Abstraction axis (orthogonal to type/storage)

`L0` raw MemoryItem (agent- or harvester-written) → `L1` consolidated (mechanical merge
of same project/tag raw facts) → `L2` distilled (reviewed principle/SOP). This is a
MemoryItem abstraction axis, not a raw transcript hierarchy. Raw conversation text
lives one layer lower in `sources/conversations/`; extractors may cite it, but it is
not injected automatically.
