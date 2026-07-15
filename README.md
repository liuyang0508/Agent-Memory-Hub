# Agent Memory Hub

<p align="center">
  <img src="./docs/visuals/agent-memory-hub-logo-lockup-a-plus-candidate.svg" alt="Agent Memory Hub: A shared second brain for multi-agent systems" width="620">
</p>

> **Every agent session should become fuel for the next one.**
>
> A local-first, traceable, governed shared brain for Claude Code, Codex CLI,
> Cursor, Hermes, Qoder, Wukong, GitHub Copilot, and any agent that can use
> MCP, CLI, or hooks.

[Official Website](https://aihub0508.com/) | [Gitee Mirror](https://gitee.com/liuyang0508/Agent-Memory-Hub) | [Community](#mirrors-and-community) | [中文版](./README.zh.md) | [Strategy](./STRATEGY.md) | [Roadmap](./ROADMAP.md) | [Benchmark Report](#benchmark-report) | [Lifecycle](#trusted-context-lifecycle) | [Capability Map](#engineering-capability-map) | [Architecture Map](#engineering-architecture-map) | [Architecture Notes](./docs/architecture.md)

[![Website](https://img.shields.io/badge/website-aihub0508.com-0ea5e9.svg)](https://aihub0508.com/)
[![Gitee Mirror](https://img.shields.io/badge/mirror-Gitee-C71D23.svg)](https://gitee.com/liuyang0508/Agent-Memory-Hub)
[![Community](https://img.shields.io/badge/community-Agent%20Memory%20Hub%20Technical%20Community-0f766e.svg)](#mirrors-and-community)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](./LICENSE)
[![CI](https://img.shields.io/github/actions/workflow/status/liuyang0508/agent-memory-hub/python-tests.yml?branch=main&label=tests)](./.github/workflows/python-tests.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](./pyproject.toml)
[![Protocol](https://img.shields.io/badge/protocol-MCP-purple)](https://modelcontextprotocol.io)
[![Local first](https://img.shields.io/badge/storage-local%20markdown-green)](#data-model)

For the full technical audit, algorithm walk-through, and detailed benchmark
boundaries, see [README.zh.md](./README.zh.md). This English README is the
concise public overview.

## Mirrors And Community

- Primary repository: [GitHub `liuyang0508/Agent-Memory-Hub`](https://github.com/liuyang0508/Agent-Memory-Hub).
- China-accessible mirror: [Gitee `liuyang0508/Agent-Memory-Hub`](https://gitee.com/liuyang0508/Agent-Memory-Hub).
- Technical community: **Agent Memory Hub Technical Community** / **Agent Memory Hub 技术共创群**. Use it for install help, adapter evidence, recall misses, benchmark samples, and long-term co-building. Scan the WeChat QR code below to join; the current QR code is valid through **July 16, 2026**. If it expires, check the official website or leave an issue on GitHub or Gitee.

<p align="center">
  <img src="./docs/assets/agent-memory-hub-wechat-community-qr-20260716.jpg" alt="WeChat QR code for Agent Memory Hub Technical Community, valid through July 16, 2026" width="320">
</p>

## Trusted Context Lifecycle

<p align="center">
  <img src="./docs/visuals/amh-loop-layered-architecture.svg" alt="AMH trusted context lifecycle: access, maintenance, retrieval, governance, and evaluation" width="100%">
</p>

## Quickstart

### 1. Install Agent Memory Hub

MacOS/Linux (recommended):

```bash
curl -fsSL https://github.com/liuyang0508/agent-memory-hub/releases/latest/download/install.sh | sh
```

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://github.com/liuyang0508/agent-memory-hub/releases/latest/download/install.ps1 | iex"
```

Homebrew:

```bash
brew install --cask liuyang0508/agent-memory-hub/agent-memory-hub
```

NPM:

```bash
npm install -g agent-memory-hub
```

Source checkout from GitHub:

```bash
git clone https://github.com/liuyang0508/agent-memory-hub.git ~/agent-memory-hub
cd ~/agent-memory-hub
./install.sh --verify-only
./install.sh
```

Source checkout from the Gitee mirror:

```bash
git clone https://gitee.com/liuyang0508/Agent-Memory-Hub.git ~/agent-memory-hub
cd ~/agent-memory-hub
./install.sh --verify-only
./install.sh
```

There is only one user-facing installer: `install.sh`. When run through `curl`,
it clones or updates the repository first; when run from the repository checkout,
it installs the CLI, Web Admin, hooks, MCP server, and `/remember`. Homebrew and
npm are installer distribution channels; GitHub Release assets, npm publish, and
the Homebrew tap/cask must be published separately. See
[Release Publishing](./docs/release-publishing.md).

AMH does not update itself from hooks. If a checkout moves, a release is
refreshed, or `memory doctor` reports stale hook paths, repair explicitly:

```bash
memory doctor --fix
memory self-update --dry-run
memory self-update --repair-hooks
```

### 2. Self-check, Uninstall, And Data Boundary

Run the non-mutating self-check before or after install:

```bash
./install.sh --verify-only   # check Python, templates, hooks, and MCP server assets
```

If you installed through the remote command, uninstall through the same entry:

```bash
curl -fsSL https://github.com/liuyang0508/agent-memory-hub/releases/latest/download/install.sh | sh -s -- --uninstall
```

If you are already inside the source checkout:

```bash
./install.sh --uninstall
```

The uninstall boundary is intentionally conservative: it removes only
AMH-managed hooks, MCP configuration, and `/remember`; it keeps user memories,
evidence, and indexes under `~/.agent-memory-hub`. To intentionally erase the
local brain data as well, run:

```bash
rm -rf ~/.agent-memory-hub
```

If you installed a shell package through Homebrew or npm, run the AMH uninstall
first to clean managed config, then remove the package with that package
manager.

### 3. Verify the install

```bash
memory doctor
memory govern readiness --format markdown
memory govern plan --category lifecycle --format markdown
memory govern plan --category lifecycle --format json
memory govern apply-lifecycle <memory-id> --dry-run --format json
memory search "project decision"
memory hook recent --limit 5
```

`memory hook recent` shows whether a prompt injected memory, produced a recall
gap, timed out, or later received outcome feedback with adopted / rejected /
ignored item counts. It is the first check when an agent UI shows no
`<agent_brain>` block. `memory govern readiness` also runs the packaged
query-signal adversarial cases for long Chinese tasks, JSON/config prompts,
OCR/log/code snippets, and weak follow-ups.
`memory govern plan --category lifecycle` lists stale `signal` / `handoff`
review items with age, threshold, and an `archive_or_supersede` recommendation;
it is a dry-run plan and does not archive items by itself. The JSON output also
contains a `review_queue` with item IDs, bounded `memory read` commands, and
`can_auto_apply=false` for Web Admin or script-driven human review.
`memory govern apply-lifecycle` rechecks selected IDs against the current
`review_queue`; it defaults to `--dry-run` and only archives matched items when
you pass `--apply`.

### 4. Open the local admin UI

```bash
memory serve --port 8765 --open
```

The Web Admin binds to `127.0.0.1` by default. To intentionally expose it on
your LAN or another network, pass `--host 0.0.0.0` and rely on the built-in auth
controls plus your own firewall rules. Agents do not need the Web Admin port for
normal memory access; they use CLI, MCP, hooks, or awareness files.
Cross-origin browser access is loopback-only by default; set
`MEMORY_HUB_CORS_ORIGINS=https://example.internal` only when a separate trusted
front end must call the Web Admin API.

## What It Solves

AI tools should not start every session from a blank page, and they should not
dump every transcript into context either.

Agent Memory Hub creates a governed memory layer between those extremes:
raw evidence stays traceable, durable knowledge stays searchable, and context is
injected only after policy checks.

- Claude Code can learn a project convention today; Codex can reuse it tomorrow.
- Raw conversations become evidence; durable conclusions become `items/`; they do not collapse into a context dump.
- Markdown is the truth; SQLite, vector, graph, and runtime ledgers are rebuildable projections.
- MCP, CLI, hooks, SDK, and Web Admin converge on one write, retrieval, and governance pipeline.
- Loop Contract turns long-running work into a portable goal/state/action/feedback/verifier/budget/stop-condition/human-gate contract, so AMH can serve as the multi-agent loop fact layer, verification layer, and governance layer without becoming an automatic runner.

## Benchmark Report

Current benchmark status and reproduction entry points:

<p align="center">
  <img src="./docs/evaluation/amh-full-ranking-optimized-full/all-memory-benchmark-report-preview.png" alt="Full AMH benchmark report rendered from the MemoryData paper figure with AMH appended metrics" width="100%">
</p>

Paper-style evaluation reference:

- [arXiv 2606.24775: Are We Ready For An Agent-Native Memory System?](https://arxiv.org/abs/2606.24775)

The embedded report image is rendered from
`docs/evaluation/amh-full-ranking-optimized-full/all-memory-benchmark-report-preview.html`.
It includes the MemoryData paper figure, AMH appended paper-style bars, the
eight-metric paper coverage matrix, and the AMH local score summary.

| Item | Current value |
|---|---|
| Overall status | `PASS_WITH_MEMORYDATA_FULL` |
| System benchmark | 240 cases, 0 failures, 82.982s |
| LongMemEval-S retrieval | AMH ranking: 500 / 500 cases, R@5 97.40%, R@10 98.40%, MRR 91.29% |
| LongMemEval-S QA / Judge | 500 generation rows, Judge Accuracy 41.60% |
| MemoryAgentBench | AR / TTL / LRU / CR four-dimensional full runs complete |
| MemoryData full-family | LoCoMo, LoCoMo category 5, LongBench, LongBench-v2, and five MemBench slices complete |
| Not claimed as AMH results | DB-Bench missing runner/data; MEMTRON/AgentMemory-Bench source lock is blocked; vendor/self-reported numbers stay reference-only |

| Reproducibility field | Value |
|---|---|
| Reproduce command | `python benchmarks/run_memory_benchmarks.py --output-dir docs/evaluation` |
| Primary artifacts | `docs/evaluation/amh-full-ranking-optimized-full/memorydata-external-benchmark-report.json`, `docs/evaluation/amh-full-ranking-optimized-full/memorydata-external-benchmark-report.zh.md` |

AMH core system gate:

| Metric | Result |
|---|---:|
| Weak-intent block | 100.00% |
| Injectable query detection | 100.00% |
| Recall@10 | 100.00% |
| MRR | 99.78% |
| Firewall include | 100.00% |
| Firewall exclude | 100.00% |
| Reversible ContextPack | 100.00% |
| Indexed items | 1361 |

LongMemEval-S retrieval:

| Result | Status | Cases | R@5 | R@10 | MRR | Boundary |
|---|---|---:|---:|---:|---:|---|
| lexical | passed | 500 / 500 | 89.00% | 93.60% | 78.74% | R@K-only full; excludes answer generation / judge. |
| AMH ranking | passed | 500 / 500 | 97.40% | 98.40% | 91.29% | R@K-only full; excludes answer generation / judge. |

MemoryAgentBench four-dimensional full:

| Dimension | Status | Rows | Key metrics |
|---|---|---:|---|
| Accurate Retrieval AR | passed | 500 / 500 | EM 48.00%; F1 67.30%; EventQA Recall 48.20% |
| Test-Time Learning TTL | passed | 100 / 100 | EM 60.00%; Label Accuracy 60.00%; Label Format 100.00% |
| Long-Range Understanding LRU | passed | 71 / 71 | EM 0.00%; F1 11.29%; ROUGE-L Recall 52.99% |
| Conflict Resolution CR | passed | 100 / 100 | EM 3.00%; Answer Hit 5.00%; Concise Response 100.00% |

MemoryData full-family:

| Result | Benchmark | Status | Scope | Key metrics | Boundary |
|---|---|---|---:|---|---|
| LoCoMo 4cat QA full | LoCoMo | passed | 1540 / 1540 QA | EM 16.04%; F1 36.05%; ROUGE-L Recall 45.70% | Category 1-4 derived from official locomo10; excludes adversarial/category 5. |
| LoCoMo category5 adversarial full | LoCoMoCategory5 | passed | 446 / 446 QA | EM 20.85%; F1 38.95%; ROUGE-L Recall 50.87% | Category 5 adversarial questions scored separately. |
| LongBench rep150 proportional full | LongBench | passed | 150 / 150 rows | EM 27.33%; F1 20.67% | Deterministic 150-row subset, not the 503-question full set. |
| LongBench-v2 503-question full | LongBenchV2Full | passed | 503 / 503 rows | EM 32.21%; F1 23.26% | Official THUDM LongBench-v2 full set through a MemoryData-compatible loader. |
| MemBench simple / noisy / knowledge_update / highlevel / RecMultiSession | MemBench | passed | 500 total rows | EM range 46.00%-91.00%; F1 range 30.00%-63.00% | Public FirstAgent slices only. |

Publication boundaries:

| Boundary | Rule |
|---|---|
| Comparable ranking | Only same runner, same dataset, and same metric can be ranked together. |
| Source lock | A ready source proves traceability, not that AMH has reproduced every external score. |
| External reports | agentmemory, OpenViking, State-Bench, and vendor-published numbers are reference sources unless AMH reruns them locally. |
| Dataset provenance | Derived subsets such as LoCoMo 4cat and LongBench rep150 must be labeled as derived/subset results. |

## Engineering Capability Map

Product capabilities, install path, architecture, flows, and engineering
boundaries:

The engineering chain is explicit: connect agents, capture evidence, write
durable memory, project indexes, retrieve and rank candidates, govern context
loading, then inject only through each agent's real capability boundary.

<p align="center">
  <img src="./docs/visuals/readme-structure-map.svg" alt="Agent Memory Hub engineering capability map" width="100%">
</p>

<details>
<summary>Text fallback for the engineering capability map</summary>

<table>
<tr>
<td width="46%" valign="top">

<pre><code>agent-memory-hub/
|-- agent_brain/
|   |-- interfaces/
|   |   |-- cli/ Typer CLI
|   |   |-- mcp/ FastMCP server and tools
|   |   `-- sdk/ Python client API
|   |-- contracts/ Pydantic and JSON schema contracts
|   |-- platform/ indexing, embedding, doctor, history
|   |-- memory/
|   |   |-- store/ memory maintenance and write funnel
|   |   |-- recall/ search, retrieval, ranking, brief
|   |   |-- context/ context loading and injection
|   |   |-- governance/ audit, drift, review, evolve, reasoning
|   |   `-- evidence/ harvest, resources, import/export, Obsidian
|   |-- agent_integrations/ multi-agent adapters
|   |-- observability/ metrics and health scoring
|   `-- onboarding/ one-command onboarding
|-- agent_runtime_kit/ agent hooks and shell tools
|-- web/ Web Admin
|-- benchmarks/ benchmarks and evaluation
|-- deploy/ Docker / Compose / systemd
|-- docs/ docs, diagrams, verification records
|-- tests/ tests and public-surface locks
|-- pyproject.toml
|-- install.sh
|-- README.md
`-- README.zh.md</code></pre>

</td>
<td width="54%" valign="top">

<table>
<tr><td><b>01 Onboarding</b><br/>Install, diagnose, CLI, MCP, Web Admin, and hook configuration converge on one clear entry path.</td></tr>
<tr><td><b>02 Evidence capture</b><br/>Raw conversation evidence lands in <code>sources/conversations/</code>; resources and extractions land in <code>resources/</code> and <code>extractions/</code>.</td></tr>
<tr><td><b>03 Memory write</b><br/>Durable conclusions land in <code>items/mem-*.md</code> after schema validation, audit, enrichment, and quality checks.</td></tr>
<tr><td><b>04 Index projection</b><br/><code>index.db</code> maintains metadata, FTS, vector, and graph projections; failed writes use <code>pending/</code> as a repairable buffer.</td></tr>
<tr><td><b>05 Retrieval ranking</b><br/>BM25 and vector recall are fused with RRF, then governed by decay, feedback, status, runtime evidence, and freshness filters.</td></tr>
<tr><td><b>06 Context governance</b><br/>Injection defaults to a reversible <code>context_pack</code>: compact prompt text plus <code>detail_uri</code> and retrieve hints for on-demand detail.</td></tr>
<tr><td><b>07 Agent adapters</b><br/>Codex, Claude Code, Qoder, Wukong, Hermes, and others are integrated by hooks, MCP, file context, or provider tools according to real capability boundaries.</td></tr>
<tr><td><b>08 Memory governance</b><br/>Maturity, forgetting curve, retention tiering, conflict detection, review queue, merge, promotion, and evolution loops.</td></tr>
<tr><td><b>09 Observability</b><br/>doctor, runtime ledger, injection cohorts, benchmarks, truth contracts, and public-surface locks prove behavior.</td></tr>
<tr><td><b>10 Docs and delivery</b><br/>README, architecture diagrams, animated storyboard, roadmap, verification records, and examples turn implementation into product understanding.</td></tr>
</table>

</td>
</tr>
</table>

</details>

## Exact Maintenance Order

Maintenance comes before retrieval: AMH has to maintain a trustworthy truth
source before retrieval has reliable material to read. This order explains what
is allowed to become durable memory, how evidence and indexes are repaired, how
feedback changes trust signals, and which governance actions require review.

```text
write signal / candidate / raw transcript / task outcome
-> entry normalization or pending fallback
-> candidate quarantine and review
-> WriteService audit, enrichment, quality checks, evidence sidecars
-> ItemsStore appends Markdown truth
-> sources/writes ledger + resources/extractions evidence
-> index.db projection; .index-dirty / pending repaired by reindex or sync-pending
-> runtime ledgers record injections, gaps, outcomes, adapter events
-> feedback applies only adopted/rejected ids; ignored ids stay unchanged
-> maturity / drift / duplicate / TTL / conversation tier / index drift scans
-> AutoGovernance safe-applies only low-risk actions; high-risk actions become review/evolve proposals
-> approved candidates or audited proposals re-enter the truth layer or an explicit allow-listed execution path
```

<p align="center">
  <img src="./docs/visuals/memory-maintenance-sequence.svg" alt="Maintenance pipeline from write, evidence, index, feedback, governance, and evolution" width="100%">
</p>

| Step | What happens | Source boundary | Output |
|---:|---|---|---|
| 1 | A maintenance signal is produced by explicit write, hook signal, harvest, proactive/semantic candidate, task outcome, or governance command. | A signal is not durable memory. | Write request, candidate, raw evidence, outcome, or scan input. |
| 2 | CLI, MCP, SDK, Web, Hermes, and hook shims normalize to `MemoryItem + body`; if the core path is unavailable, the record lands in `pending/*.jsonl`. | Entry points differ, but the durable write contract should converge. | Normalized write contract or replayable pending record. |
| 3 | Candidate-only material stays in `review/proactive-candidates.jsonl` or carries `needs-review` / `unverified-boundary` tags. | Candidates are quarantined from normal truth semantics. | Review queue item. |
| 4 | `WriteService.write()` runs `audit_memory_text`, field enrichment, review-boundary marking, and quality warnings; critical/high audit findings block unless explicitly bypassed. | Audit is fail-closed for high-risk content. | Written, blocked, warning, or degraded result. |
| 5 | `WriteService` creates `resources/*.json` and `extractions/*.json` from refs or write input where needed. | Evidence sidecars explain provenance; they do not replace `MemoryItem`. | Resource and extraction records. |
| 6 | `ItemsStore.write()` appends `items/mem-*.md`. | The Markdown append is the only durable-write success criterion. | Markdown truth source. |
| 7 | `sources/writes/<item-id>.json` records writer, agent, session, item path, body hash, refs, validity, and source kind. | The ledger is observational provenance. | Write ledger. |
| 8 | `HubIndex.upsert()` projects metadata, FTS, vectors, and `refs_graph`; embedder/index failure marks `.index-dirty` without undoing Markdown. | `index.db` is rebuildable, not authoritative. | Derived index or dirty id. |
| 9 | `memory verify --repair`, `memory reindex --prune`, and `memory sync-pending` repair index drift and replay buffered writes; poison pending records move to `pending/dead/`. | Repair starts from Markdown or pending records. | Repaired index, replayed writes, or dead-letter records. |
| 10 | `Stop`, `conversation ingest`, and `memory harvest` maintain raw evidence in `sources/conversations/`; harvest uses watermarks and span hashes; `conversation rebalance` updates hot/warm/cold/frozen tiers. | Raw transcripts remain evidence, not automatic prompt context. | Conversation evidence, extracted candidates, watermarks, tiers. |
| 11 | Runtime sidecars record adapter events, injection cohorts, recall gaps, and task outcomes without exposing raw prompt/query/body. | Runtime evidence proves events, not truth. | Data-flow and lineage inputs. |
| 12 | `InjectionFeedback` updates only explicitly adopted or rejected injected ids; ignored injected ids stay unchanged. | Retrieval and injection are not adoption. | Support, contradiction, gain, confidence, and index feedback stats. |
| 13 | `GovernancePipeline`, `DriftDetector`, and `score_maturity` scan duplicates, noise, TTL, quality, contradictions, staleness, citation rot, drift clusters, and maturity. | Scans produce findings and recommendations. | Issues, drift findings, maturity actions. |
| 14 | `AutoGovernanceCycle` aggregates maturity, drift, quality, index drift, conversation tiers, and evolve proposals; `--apply` only executes safe actions. | Archive, delete, consolidate, supersede, and skill synthesis stay review-required. | safe_apply, review_required, or blocked actions. |
| 15 | `EvolveEngine` and `DreamingWorker` propose consolidate/promote/archive/crystallize/synthesize_skill actions behind audit gates; approved proactive candidates write through `WriteService`. | High-risk evolution cannot silently rewrite the brain. | Approved item, archive, promotion, skill proposal, or blocker. |
| 16 | `GET /api/data-flow` and `GET /api/memory-lineage` expose redacted maintenance, recall, and evolution read models. | Web read models do not reveal raw prompts, queries, or bodies. | Explainable observability. |

## Exact Retrieval Order

AMH does not treat "recall" as a black box. The combined diagram shows both the
execution order and the ranking/governance factors that decide whether a
candidate becomes injected context:

<p align="center">
  <img src="./docs/visuals/retrieval-complete-flow.svg" alt="Complete retrieval flow from question, filtering, BM25/vector, RRF, decay, feedback, firewall, and ContextPack" width="100%">
</p>

```text
user question
-> metadata and memory-type filtering
-> full-text BM25 and vector recall in parallel
-> RRF fusion
-> optional rerank + confidence/retention/feedback/runtime scoring
-> stale/supersession filters
-> optional MMR/Hopfield/graph expansion
-> context firewall
-> layered context_pack injection
```

| Step | What happens | Why it matters |
|---:|---|---|
| 1 | The user question, CLI/MCP/SDK search call, or `UserPromptSubmit` hook becomes the query signal. | The current prompt and live runtime context stay separate from older memory candidates. |
| 2 | `SearchFilter` narrows the SQL metadata set by memory type, project, tags, excluded tags, tenant, age, and supersession policy. | AMH does not ask the model to guess the memory class; explicit metadata defines the candidate boundary. |
| 3 | Full-text BM25 and vector search run against the allowed IDs. If the embedder is degraded, the pipeline falls back to BM25-only instead of fusing bad vectors. | Exact terms and semantic similarity both get a chance to find candidates. |
| 4 | Reciprocal Rank Fusion combines BM25 and vector ranks into the first candidate pool. | One retrieval path cannot dominate just because its raw score scale is larger. |
| 5 | Optional cross-encoder rerank adjusts the top pool; trust signals then apply confidence, retention decay, feedback value, handoff/status boosts, and runtime evidence. Maturity remains governance and context-loading metadata; current `SearchEngine.search()` does not use it as a direct rank multiplier. | The result is not just "closest text"; it is ranked by usefulness, confidence, freshness, and observed outcomes. |
| 6 | Temporal-state and supersession filters remove stale runtime state and memory that has been replaced, unless the caller explicitly includes it. | Old state can remain auditable without being injected as current truth. |
| 7 | Optional MMR, Hopfield, and `refs_graph` expansion can diversify or expand the result set after the primary ranking stages. | Associative memory is useful, but it stays switchable and benchmark-gated. |
| 8 | `ContextFirewall` evaluates topic fit, temporal fit, confidence, scope, sensitivity, unsafe context, budget, and feedback before injection. | Retrieved does not mean injected. |
| 9 | `context_loading` chooses the smallest useful view: locator by default, overview for facts/decisions/state boundaries, detail only for direct raw evidence or explicit requests. | Agents get enough context to act without receiving every matching body. |
| 10 | `context_pack` injects compact text plus `detail_uri`, retrieve hints, token estimates, and reversible compression metadata when needed. | The prompt stays small, while `memory read --view detail --head N` can still fetch evidence on demand. |

## Engineering Architecture Map

Primary architecture map index and boundary explanations:
[docs/visuals/agent-memory-hub-architecture-map.html](./docs/visuals/agent-memory-hub-architecture-map.html).
The HTML map carries larger diagrams, sequences, and flow expansions.

| Map | README-owned boundary | Detailed view |
|---|---|---|
| Product structure | How users, agents, the shared brain pool, governance surfaces, and delivery surfaces relate; evidence, indexes, and MemoryItems stay separate concepts. | [README view](#product-architecture) / [HTML](./docs/visuals/agent-memory-hub-architecture-map.html#product) / [SVG](./docs/visuals/product-architecture.svg) |
| Technical structure | `agent_runtime_kit/` is the runtime compatibility layer; `agent_brain/` is the core business package; CLI, MCP, SDK, and Web API share the same core. | [README view](#technical-architecture) / [HTML](./docs/visuals/agent-memory-hub-architecture-map.html#technical) / [SVG](./docs/visuals/technical-architecture.svg) |
| Key sequence diagrams | Memory write, next-session recall injection, and maintenance governance loops; every path needs diagnosable inputs, artifacts, and fallback behavior. | [README view](#memory-lifecycle-sequence) / [HTML](./docs/visuals/agent-memory-hub-architecture-map.html#sequence) / [Maintenance sequence](./docs/visuals/memory-maintenance-sequence.svg) / [Complete retrieval flow](./docs/visuals/retrieval-complete-flow.svg) |
| Flow diagrams | Write, retrieval injection, governance evolution, and adapter install/doctor paths; next-stage capability stays dashed or explicitly WIP. | [README view](#data-flow) / [HTML](./docs/visuals/agent-memory-hub-architecture-map.html#flows) / [SVG](./docs/visuals/data-flow.svg) |
| Metrics / governance / collaboration map | How observability, governed maintenance, and multi-agent collaboration form a closed loop around the shared brain; the Chinese README embeds the preview image. | [Editable HTML](./docs/visuals/amh-metrics-governance-collaboration-map.html) / [README preview PNG](./docs/visuals/amh-metrics-governance-collaboration-map.png) |

### Product Architecture

<p align="center">
  <img src="./docs/visuals/product-architecture.svg" alt="Product architecture from collaboration pain points to trusted context" width="100%">
</p>

### Technical Architecture

<p align="center">
  <img src="./docs/visuals/technical-architecture.svg" alt="Technical architecture across integrations runtime brain core and local data" width="100%">
</p>

### Memory Lifecycle Sequence

<p align="center">
  <img src="./docs/visuals/memory-lifecycle-sequence.svg" alt="Unified maintenance and retrieval lifecycle sequence" width="100%">
</p>

### Data Flow

<p align="center">
  <img src="./docs/visuals/data-flow.svg" alt="Data flow from evidence to context pack" width="100%">
</p>

## Product Features

| Capability | Status | Why it matters |
|---|---:|---|
| Evidence + knowledge split | Shipping | Raw conversation evidence lands in `sources/conversations/`; durable knowledge lands in `items/`, so evidence does not become context by accident. |
| Markdown source of truth | Shipping | `mem-*.md` carries frontmatter, body, refs, validity, retention, maturity, and context views. |
| Write audit and enrichment | Shipping | Writes pass schema validation, risk audit, source-boundary checks, runtime enrichment, and quality scoring. |
| Resource and extraction sidecar | Shipping / product surface in progress | `ResourceStore` supports `resources/*.json` and `extractions/*.json`; `memory resource promote-extraction` can promote trusted OCR/ASR/VLM/PDF extraction text into a normal `MemoryItem` while preserving `refs.resources` and `refs.extractions`. |
| Derived index projection | Shipping | `index.db` maintains `items_meta`, `items_fts`, `items_vec`, and `refs_graph`; projections rebuild from Markdown. |
| Retrieval scoring pipeline | Shipping | BM25 + vector recall uses RRF, then rerank, decay, feedback, runtime evidence, freshness, and supersession filters. |
| Optional retrieval trace | Shipping | CLI/MCP search can opt into explainable retrieval traces: initial BM25/vector ranks, stage effects, final rank, and compact signals, without changing default hook injection. |
| Layered context loading | Shipping | Locator is default, overview is optional, detail reads body on demand; vector features use locator + overview only. |
| Reversible `context_pack` | Shipping | `context_pack` is the compressed prompt view plus `detail_uri` and retrieve hints; automatic injection avoids body dumps and agents fetch evidence with `read_memory(..., head=2000, view='detail')` only when needed. |
| Memory Profile export | Shipping | High-confidence MemoryItems can render managed `CLAUDE.md`, `AGENTS.md`, and Cursor rules blocks; profile files are derived surfaces, while Markdown memory remains the source of truth. |
| Context firewall | Shipping | Filters stale, low-confidence, superseded, unsafe, wrong-scope, or query-mismatched context before injection. |
| Raw conversation governance | Shipping | `memory conversation ingest/list/read/rebalance` and MCP conversation tools support message-level evidence, access counts, and hot/warm/cold/frozen tiers. |
| Semantic memory candidates | Shipping | Semantic proactive v2 extracts reusable review candidates into `review/proactive-candidates.jsonl`; approvals still write through WriteService. |
| L2/L3 hierarchy sidecar | Shipping | `memory govern hierarchy --apply` builds `derived/hierarchical-memory.json` by topic/project without mutating `items/`. |
| Maturity and forgetting governance | Shipping | `memory govern maturity` recommends raw/consolidated/skill; decay considers more than time, including access, importance, feedback, and evidence shape. |
| Auto-governance cycle | Shipping | `memory govern auto` builds one dry-run/apply plan across maturity, drift, quality, index drift, and conversation tiering; apply only executes safe actions. |
| Loop Contract governance | Shipping | `memory loop run --contract`, `memory loop contract validate`, `memory loop create --contract`, `memory loop verify`, `memory loop feedback`, and `memory loop gate open/approve/reject` bind goals, state, actions, verifiers, budgets, stop conditions, and human gates to LoopRun evidence. AMH is the multi-agent loop fact layer, verification layer, and governance layer; it is not an unattended runner. |
| Associative-memory expansion | Experimental | Explicit `refs_graph` is shipping; graph expansion, MMR, and Hopfield expansion are switchable enhancements gated by benchmarks. |
| Retrieval benchmark gate | Shipping | `memory benchmark retrieval --cases ...` and Web `/api/retrieval-gate` evaluate recall@1, recall@k, and MRR against the real index; failing thresholds fail the gate. |

Automatic and `auto` search never promotes a candidate to `detail`: discovery
returns locator/overview, then the Agent selects 1-3 relevant items and performs
bounded `read_memory` calls. Explicit `verbosity="detail"` remains available for
deliberate diagnostics; broad explicit-detail search is warned but not blocked.
| Headroom-inspired compression | Shipping / optional external provider | AMH internalizes Headroom-style content routing for search results, logs, diffs, JSON, and text; local CCR sidecars keep no-URI compression reversible, while a `headroom` package/CLI remains an optional provider. |
| Compression benchmark gate | Shipping | `memory benchmark compression` and Web `/api/compression-gate` run few-shot cases that require key anchors to survive, noise to disappear, token savings to stay positive, and reversible retrieval to remain available. |
| System few-shot gate | Shipping | `memory benchmark system` builds weak-intent, title/locator recall, firewall, and `context_pack` cases from real `MemoryItem` metadata to test query gating, retrieval, firewall decisions, and reversible packing together. |
| ML/DL advisory gate | Shipping | `memory benchmark ml-advisory` and Web `/api/ml-advisory-gate` compare baseline vs candidate scores, gate latency/privacy/evidence, and block default promotion even when model deltas look strong. |
| Multi-agent access | Layered rollout | AMH exposes adapter commands, runtime evidence, and Web Admin views for multiple agents. Support-level claims require local doctor / install-verify / runtime evidence on the target machine. |
| Stable Web Admin and observability | Shipping | Local FastAPI admin with a conformance-locked API/WS route surface, data-flow cockpit, memory-lineage read model, `evolution_control`, install-verify/uninstall actions, doctor, runtime ledger, injection cohorts, recall gaps, and verification docs. |
| Memory lineage read model | Shipping | `GET /api/memory-lineage` explains write, storage, retrieval, scoring, context loading, and injection paths; it is part of the Web surface lock, so future route drift must update tests and docs together. |

## Product Roadmap

| Layer | Audience | Status | Shape |
|---|---|---:|---|
| **L1 Personal brain** | One developer using many agents | Shipping | Local Markdown pool + CLI/MCP/hooks. |
| **L2 Team shared brain** | Small teams | Planned | Shared repo/sync model once L1 usage proves demand. |
| **L3 Enterprise memory infra** | Organizations | Reserved | Multi-tenant, RBAC, private cloud/SaaS, dashboards. |

Near-term product work:

- Improve recall drift controls and feedback loops.
- Harden `memory govern auto` with provenance-aware review queues while keeping archive, delete, consolidate, and skill synthesis review-required.
- Tighten adapter verification from `install-ready` to real-client `verified`.
- Extend raw conversation evidence governance: hot/warm/cold policy, extraction provenance, and bounded replay into MemoryItem candidates.
- Extend Loop Contract cockpit views and `memory loop gate open` lifecycle reporting while keeping execution outside AMH's default responsibilities.
- Use benchmark gates to control rerank, GraphRAG, Headroom compression, ML/DL advisory features, and other retrieval enhancements before they change default injection behavior.
- Keep MCP surface tiered and inspectable instead of growing a flat tool sprawl.
- Make README and Web Admin the primary onboarding surfaces.

Longer-term questions are tracked in [ROADMAP.md](./ROADMAP.md) and [STRATEGY.md](./STRATEGY.md).

The editable animated architecture storyboard lives at
[docs/visuals/amh-animated-diagrams-preview.html](./docs/visuals/amh-animated-diagrams-preview.html).

## Shared Brain Overview

<p align="center">
  <img src="./docs/visuals/amh-dynamic-architecture.svg" alt="Shared brain overview diagram" width="100%">
</p>

## Evidence, Memory, And Indexes

<p align="center">
  <img src="./docs/visuals/amh-storage-lifecycle.svg" alt="Evidence memory and index projection diagram" width="100%">
</p>

## Retrieval, Ranking, And Context Governance

<p align="center">
  <img src="./docs/visuals/amh-retrieval-context-pipeline.svg" alt="Retrieval ranking and context governance diagram" width="100%">
</p>

Default retrieval returns the smallest useful context view. Search and hook
injection build a reversible `context_pack`: the prompt receives the smallest
useful locator/overview/detail text while `detail_uri`, token estimates, and
retrieve hints remain available. Detail text is loaded through
`memory read --head N --view detail` / MCP `read_memory(head=N, view="detail")`
only when requested or when evidence policy says it is needed.
When detail text must fit a tight budget, AMH applies Headroom-style local
compression by content type and preserves reversibility through `detail_uri` or
an AMH-local CCR sidecar.
The Compression benchmark gate is the safety check for this path: few-shot
cases fail if compression drops required anchors, keeps known noise, loses
reversibility, or saves too few tokens.

## Retrieval Algorithms And Scoring Skeleton

<p align="center">
  <img src="./docs/visuals/retrieval-score-waterfall.svg" alt="Retrieval scoring formula waterfall" width="100%">
</p>

AMH is not just vector search. Recall is explainable across metadata filters,
BM25, vector neighbors, RRF fusion, optional rerank, confidence, forgetting
decay, feedback, runtime/status evidence, stale/supersession filters, optional
MMR/Hopfield/graph expansion, and pre-injection safety gates.

| English name | Formula / rule | AMH usage | Boundary |
|---|---|---|---|
| **BM25**: Best Matching 25, classic lexical relevance | Term frequency, inverse document frequency, and field-length normalization. | First-hop recall for exact words, file names, commands, paths, and error codes. | Lexical only; it does not model semantic neighbors. |
| **Vector Similarity**: embedding-neighbor recall | `cos(q, p_i)=q·p_i/(||q||·||p_i||)`. | Runs beside BM25 to catch semantic matches with different wording. | Falls back to BM25-only when embeddings degrade. |
| **RRF**: Reciprocal Rank Fusion | `RRF(d)=Σs ws/(k+rank_s(d)+1)`. | Fuses BM25 and vector candidates into the first candidate pool. | Initial recall score, not final injection permission. |
| **Cross-Encoder Reranking** | `rerank_score=sigmoid(cross_encoder(query, doc))`. | Optional top-candidate semantic rerank. | Still goes through decay, feedback, firewall, and benchmark gates. |
| **Ebbinghaus-style Forgetting Curve** | `retention=0.5^(days_since_access/half_life)`. | Computes time retention from per-memory-class half-life. | Uses `last_accessed` first, then `created_at`; not the only score factor. |
| **Decay Coefficient** | `decay_coefficient=clamp(retention×access_multiplier×support_multiplier×gain_multiplier×contradiction_multiplier,0.01,1.35)`. | Adds access, support, gain, and contradiction evidence to recall scoring. | Cannot overpower every relevance signal. |
| **Effective Score** | `S_effective=S_rrf×confidence×decay_coefficient`. | Produces an explainable candidate score after fusion, confidence, and decay. | Still subject to stale, supersession, and firewall filters. |
| **MMR**: Maximal Marginal Relevance | `MMR(d)=λ·relevance(d)-(1-λ)·max_sim(d, selected)`. | Optional diversity rerank so results are relevant without being duplicates. | Diversity enhancement; it does not mutate Markdown truth. |
| **Hopfield / Modern Hopfield Network** | `Hopfield: weights=softmax(β·cos(q,p_i)); attractor=Σ weights_i·p_i`. | Optional associative expansion from first-hop candidate vectors. | Enabled only through `hopfield_expand`; benchmark-gated with `refs_graph` and MMR. |
| **Maturity Score** | `maturity_score = 0.28*source + 0.22*confidence + support + reuse + graph + validation + overview + gain - contradiction - stale`. | Governance recommendation for raw / consolidated / skill maturity. | Not a pre-recall hard filter or live rank multiplier. |
| **Context Loading** | `locator -> overview -> detail_uri(body)`. | Injects the smallest useful context view and keeps detail readable on demand. | Agents receive a reversible `context_pack`, not the full transcript. |
| **Headroom-style Compression** | Content-type routing plus must-keep / must-drop few-shot gates. | Compresses detail views under tight token budgets while preserving a recovery path. | Cannot drop critical anchors or rewrite Markdown truth. |

`--explain` / `include_trace=True` returns an observational trace with initial
BM25/vector ranks, stage effects, final rank, and compact signals for debugging
recall drift.

## Agent Integration Boundary

<p align="center">
  <img src="./docs/visuals/amh-adapter-capability-boundary.svg" alt="Agent integration boundary diagram" width="100%">
</p>

## Data Model

| Object | Source | Purpose |
|---|---|---|
| `MemoryItem` | `~/.agent-memory-hub/items/mem-*.md` | Durable memory conclusion or handoff. |
| `context_views` | item frontmatter | Locator, optional overview, and detail URI for token-efficient loading; vector features use locator + overview only. |
| `context_pack` | injection-time derived object | Compressed prompt view + `detail_uri` or AMH-local CCR sidecar + CLI/MCP retrieve hints; not written to Markdown and rebuildable from `context_views` plus body text. |
| `ConversationMessageRecord` | `sources/conversations/*/messages.jsonl` | Raw message-level conversation evidence with role, source path, byte offsets, hash, sensitivity, and hot/warm/cold/frozen tier. |
| `LoopContract` / `LoopRun` | contract files + `runtime/loops/*.json` | Portable loop contract and runtime ledger for goal/state/action/feedback/verifier/budget/stop condition/human gate, plus structured feedback and readiness evidence. |
| `ResourceRecord` | `resources/*.json` | Raw external material or evidence source. |
| `ExtractionRecord` | `extractions/*.json` | Parsed/OCR/summary/segment evidence derived from resources. |
| Promoted multimodal memory | `items/mem-*.md` + `refs.resources` / `refs.extractions` | A governed MemoryItem created from trusted extraction evidence via `memory resource promote-extraction <ext-id>`; resource and extraction sidecars remain the evidence source. |
| `index.db` | derived | FTS, vector, metadata, and graph index; rebuildable from Markdown source of truth. |
| `runtime/*.jsonl` | derived log | Adapter events, injection cohorts, feedback outcomes. |
| `pending/*.jsonl` | fallback | Buffered writes when core/index is unavailable. |

## Repository Structure

| Path | Responsibility |
|---|---|
| `agent_runtime_kit/` | Runtime assets touched by shells and agents: hooks, MCP launcher, shell tools, discipline docs. |
| `agent_brain/interfaces/cli/` | Typer CLI and command groups. |
| `agent_brain/interfaces/mcp/` | FastMCP server and tiered tool registration. |
| `agent_brain/interfaces/sdk/` | Python client API. |
| `agent_brain/contracts/` | Pydantic schemas and JSON schema contracts. |
| `agent_brain/platform/` | Indexing, embedding, doctor, history, and other technical primitives. |
| `agent_brain/memory/store/` | Memory items, Markdown truth, write service, quality checks. |
| `agent_brain/memory/recall/` | Keyword, full-text, vector, layered retrieval, fusion ranking, feedback weights. |
| `agent_brain/memory/context/` | Context views, reversible context packs, on-demand loading, firewall, injection cohorts, feedback. |
| `agent_brain/memory/governance/` | Audit, drift, conflict, review, merge, promotion, evolution, reasoning. |
| `agent_brain/memory/evidence/` | Raw conversation evidence, import/export, resource evidence, transcript harvest, Obsidian integration. |
| `agent_brain/agent_integrations/` | Claude Code, Codex CLI, Qoder, Wukong, Hermes, and other agent adapters. |
| `agent_brain/observability/` | Runtime events, diagnostics, observability data structures. |
| `web/` | Local FastAPI admin surface and API routes. |
| `benchmarks/` | Retrieval quality and scale benchmarks. |
| `deploy/` | Docker, Compose, systemd, and environment templates. |
| `docs/` | Deep-dive specs, audits, design notes, and verification records. |
| `tests/` | Unit, integration, and public-surface lock tests. |

## Common Commands

```bash
memory write --type decision --title "Use SSE instead of WebSocket" \
  --summary "SSE fits one-way agent progress streaming" \
  --tags "architecture,streaming"

memory search "browser permission" --verbosity locator
memory search "browser permission" --verbosity overview
memory search "browser permission" --context-firewall --format text
memory read mem-YYYYMMDD-HHMMSS-slug --view detail
memory read mem-YYYYMMDD-HHMMSS-slug --view detail --head 2000
memory brief
memory conversation ingest ~/.claude/projects/<project>/<session>.jsonl --agent claude-code
memory conversation list --agent claude-code
memory conversation rebalance
memory benchmark system --max-cases 240 --format summary
memory loop run --contract loop.yaml --format json
memory doctor
memory serve --port 8765
memory serve --host 0.0.0.0 --port 8765   # intentional LAN or remote exposure
```

MCP examples live in [`agent_runtime_kit/mcp/example-configs.md`](./agent_runtime_kit/mcp/example-configs.md).

## Layered Agent Access Model

An adapter is not complete just because an MCP server is registered.  The
agent also needs an awareness surface that tells it what AMH is and when to
use memory.  AMH therefore models access as four separate layers:

| Layer | Necessity | Role | Completion signal |
|---|---|---|---|
| Awareness channel | Required | Tells the agent that AMH exists, what shared memory means, when to search, and when to write. | A stable `AGENTS.md`, `CLAUDE.md`, rules file, provider prompt, or sidecar exists and is managed by the adapter. |
| Automatic channel | Optional but strong | Hooks automatically search, inject, and record lifecycle evidence without relying on model initiative. | Hook events are installed and runtime ledgers observe real events. |
| Tool channel | Required | MCP, provider tools, or CLI give the agent executable read/write capability. | Doctor can prove the tool server/config points to the current AMH brain. |
| Fallback channel | Optional | Digest files, `brain_context.md`, custom instructions, or sidecars keep read-only context available when hooks or MCP are unavailable. | The sidecar is explicit about being fallback context, not verified tool execution. |

MCP-only means the tool is configured; awareness tells the model when to use it.
The strongest adapters combine awareness + automatic hooks + tool access.
Awareness + MCP is the minimum credible base for proactive memory use, while
awareness sidecars without verified runtime evidence remain `install-ready`,
not `verified`.

The Web Admin on port `8765` is a human governance surface over the same local
brain directory, not the default agent execution channel. Adapter installs should
prove CLI, MCP, hook, or awareness access directly; do not treat "Web Admin is
running" as evidence that an agent can read or write AMH memory.
The Web Admin API also avoids wildcard CORS by default; cross-origin callers
outside loopback must be explicitly listed in `MEMORY_HUB_CORS_ORIGINS`.

## Agent Adapter Matrix

These are the same Agent brand assets used by the Web Admin landing cover. This
is an integration-surface map, not a local verified-status matrix. Run
`memory adapter list --format json`, `memory adapter install-verify <adapter>
--format json`, doctor, and runtime/context-effectiveness checks on the target
machine before making a machine-specific evidence claim.

For a single adapter, use `memory adapter install <adapter> --format json` when
you need a machine-readable install result.  `needs_client` means the target
client or CLI is missing; `malformed_config` means AMH refused to overwrite a
broken client config.  Optional adapter failures report `core_impact=none`;
core adapter failures point `repair_command` to
`memory doctor --fix`.

<table class="agent-matrix">
  <tr>
    <th colspan="5" align="left">Integrated</th>
  </tr>
  <tr>
    <td align="center"><img class="agent-logo" src="./web/static/agent-logos/claude-code.svg" alt="Claude Code" width="46" height="46"><br><strong>Claude Code</strong><br><sub>Integrated</sub></td>
    <td align="center"><img class="agent-logo" src="./web/static/agent-logos/codex.png" alt="Codex" width="46" height="46"><br><strong>Codex</strong><br><sub>Integrated</sub></td>
    <td align="center"><img class="agent-logo" src="./web/static/agent-logos/hermes-agent-logo.png" alt="Hermes Agent" width="46" height="46"><br><strong>Hermes Agent</strong><br><sub>Integrated</sub></td>
    <td align="center"><img class="agent-logo" src="./web/static/agent-logos/openclaw-readme.svg" alt="OpenClaw" width="46" height="46"><br><strong>OpenClaw</strong><br><sub>Integrated</sub></td>
    <td align="center"><img class="agent-logo" src="./web/static/agent-logos/openhuman.png" alt="OpenHuman" width="46" height="46"><br><strong>OpenHuman</strong><br><sub>Integrated</sub></td>
  </tr>
  <tr>
    <td align="center"><img class="agent-logo" src="./web/static/agent-logos/opensquilla.png" alt="OpenSquilla" width="46" height="46"><br><strong>OpenSquilla</strong><br><sub>Integrated</sub></td>
    <td align="center"><img class="agent-logo" src="./web/static/agent-logos/qoder.svg" alt="Qoder" width="46" height="46"><br><strong>Qoder</strong><br><sub>Integrated</sub></td>
    <td align="center"><img class="agent-logo" src="./web/static/agent-logos/qoder-work.svg" alt="Qoder Work" width="46" height="46"><br><strong>Qoder Work</strong><br><sub>Integrated</sub></td>
    <td align="center"><img class="agent-logo" src="./web/static/agent-logos/wukong-brand-logo.png" alt="Wukong" width="46" height="46"><br><strong>Wukong</strong><br><sub>Integrated</sub></td>
    <td align="center"><img class="agent-logo" src="./web/static/agent-logos/aone-copilot.png" alt="Aone Copilot" width="46" height="46"><br><strong>Aone Copilot</strong><br><sub>Integrated</sub></td>
  </tr>
  <tr>
    <th colspan="5" align="left">Integrating</th>
  </tr>
  <tr>
    <td align="center"><img class="agent-logo" src="./web/static/agent-logos/gemini.png" alt="Gemini CLI" width="46" height="46"><br><strong>Gemini CLI</strong><br><sub>Integrating</sub></td>
    <td align="center"><img class="agent-logo" src="./web/static/agent-logos/cursor.svg" alt="Cursor" width="46" height="46"><br><strong>Cursor</strong><br><sub>Integrating</sub></td>
    <td align="center"><img class="agent-logo" src="./web/static/agent-logos/github-copilot.png" alt="GitHub Copilot" width="46" height="46"><br><strong>GitHub Copilot</strong><br><sub>Integrating</sub></td>
    <td align="center"><img class="agent-logo" src="./web/static/agent-logos/mulerun.svg" alt="MuleRun" width="46" height="46"><br><strong>MuleRun</strong><br><sub>Integrating</sub></td>
    <td align="center"><img class="agent-logo" src="./web/static/agent-logos/aider.svg" alt="Aider" width="46" height="46"><br><strong>Aider</strong><br><sub>Integrating</sub></td>
  </tr>
  <tr>
    <td align="center"><img class="agent-logo" src="./web/static/agent-logos/cline.png" alt="Cline" width="46" height="46"><br><strong>Cline</strong><br><sub>Integrating</sub></td>
    <td align="center"><img class="agent-logo" src="./web/static/agent-logos/continue.png" alt="Continue" width="46" height="46"><br><strong>Continue</strong><br><sub>Integrating</sub></td>
    <td align="center"><span aria-hidden="true">+</span><br><strong>More Agents</strong><br><sub>Integrating</sub></td>
  </tr>
</table>

## README Style References

Public documentation patterns used here:

- A clear one-line promise before deep technical detail.
- Copyable quickstart commands near the top.
- Feature matrix with honest status labels.
- Architecture diagrams close to the claims they support.
- A capability map plus repository structure that explains module boundaries without requiring a clone-and-grep session.
- Explicit roadmap and truth-contract tables instead of vague "coming soon" language.
- FAQ and deep links for advanced use.

## FAQ

**How is this different from vector RAG?**

This is not a generic document search system. The durable unit is a structured
memory item: fact, episode, decision, artifact, signal, handoff, policy, or
skill. FTS/vector retrieval is only the read path over a Markdown truth source.

**How is this different from mem0 or Zep?**

Agent Memory Hub is local-first, Markdown-first, and MCP-native. It is designed
for cross-tool developer workflows, not only application-user personalization.

**Does it send data to a cloud service?**

No by default. Data lives under `~/.agent-memory-hub/`. Optional local or remote
models may be configured by the user, but the core storage model is local.

**Can I use it without MCP?**

Yes. Use the CLI and shell tools directly. MCP is the standard integration path
for agents that support it.

**What is authoritative: Markdown or SQLite?**

Markdown. SQLite FTS/vector/graph indexes are derived and rebuildable.

## Learn More

- [Strategy](./STRATEGY.md)
- [Roadmap](./ROADMAP.md)
- [Architecture Notes](./docs/architecture.md)
- [MCP Client Configs](./agent_runtime_kit/mcp/example-configs.md)
- [Memory Item Schema](./agent_runtime_kit/schema/memory-item.md)
- [Upgrade Guide](./docs/UPGRADE.md)

## License

[Apache-2.0](./LICENSE).
