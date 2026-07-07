# Strategy

> Why Agent Memory Hub exists, what we're building, and what we're explicitly **not** building.
> **Honesty note (2026-05-26 revision)**: previous versions of this file overstated our differentiation gap by claiming "none of 14 competitors does all three (anti-drift + skill audit + 12 adapters)". Subsequent 10-vendor research (Hermes table + Tencent + rohitg00) **disproved this claim**: rohitg00/agentmemory does cover all three. The matrix below is the honest re-read.

## The metaphor

Real employees have wikis, decision logs, Slack history, onboarding docs. Your **digital employees** — Claude Code, Codex CLI, Cursor, OpenClaw, Hermes Agent, OpenHuman, OpenSquilla, Qoder, Wukong, GitHub Copilot — currently have none of these.

Agent Memory Hub is the **personnel file + decision register + onboarding system** for your AI coworkers. Markdown-first, MCP-native, infrastructure-tax-free, local-first.

## Hero anchor (the line that should appear in every artifact)

> **Your AI tools are coworkers. They should onboard like real ones — from a shared, auditable, portable brain.**

## Core differentiation — narrowed but defensible

Against **16 competitors across 3 categories**, the honest core differentiator is the **combination** of four properties, not any single one:

1. **Markdown-as-source-of-truth** — md files are the durable artifact; sqlite + vector are derived index. Re-buildable from scratch.
2. **Skill audit as a "before write" gate** (防进) — not audit-trail after the fact. Critical/high severity blocks the memory before it lands.
3. **MCP-native, agent-neutral** — a stable 10-tool core surface (write/search/read/update/confirm/stats/list_recent/tag_suggest/delete/brief) plus explicitly-tiered extended operation tools (governance/io/graph/evolve/conversation), 27 operation tools total, plus one onboarding guide fallback tool for MCP clients that do not surface prompts/resources; no proprietary client SDK; any MCP-aware agent reads/writes via the same wire format.
4. **Adapter ecosystem via markdown interop** — adapter records share state through md files, not through a custom protocol. Current local truth-contract status is 11 `verified` adapters, 4 `install-ready` adapters, and 1 `wip` stub; do not describe `wip` adapters as integrated or blocked `install-ready` adapters as `verified`.

**Who else hits all four**: nobody (as of 2026-05-26). Closest is rohitg00 (3/4 — fails on (1), uses proprietary iii-engine).

### Category A — Design-philosophy peers (4)

| Competitor | Their bet | Where we differ |
|---|---|---|
| **Karpathy LLM-Wiki** | Single-LLM personal markdown diary, hand-curated | Multi-agent relay across 12+ tools, automated audit/governance, no manual curation required |
| **Tencent / TencentDB-Agent-Memory** (4.1k★ MIT) | local-first, SQLite + sqlite-vec + Markdown, 4-layer pyramid (L0 Conversation → L1 Atom → L2 Scenario → L3 Persona), token-efficient | **Closest design twin**. Our edge: MCP-native standard tools, skill audit pre-gate, 12-agent adapter ecosystem. Theirs: 4-layer progressive distillation (more sophisticated long-term memory model). **Watch and learn from.** |
| **OpenHuman** | Persona simulation (fake humanity for chat companion) | Coworker collaboration — we don't need to fake personality, just share context |
| **Hermes (Nous Research)** | **Agent framework** defining a 5-tool memory-provider abstraction | **We aim to BE a Hermes provider**, not compete with the framework. PL6 success = listed in their provider table. Distribution channel, not threat. |

### Category B — Technical memory libraries (3)

| Competitor | Their bet | Where we differ |
|---|---|---|
| **🚨 rohitg00/agentmemory** (17.7k★ Apache-2.0, v0.9.21) | Aggressive multi-feature: 53 MCP tools + 124 REST endpoints + 12/6/22 hooks for CC/Codex/OpenCode, full Ebbinghaus decay, SHA-256 dedup, git snapshots, team_share/lease/signal multi-agent, complete audit-trail / TTL+contradict / consolidate+crystallize+sentinels self-evolve | **Red-zone threat — covers our 3-tier pyramid (audit + drift + evolve) AND has shipped at scale.** Our remaining differentiators: (a) **md-as-source-of-truth** vs their proprietary iii-engine binary store; (b) **skill audit as 防进-gate** (block-before-write) vs their **audit-trail** (record-after-act); (c) a **tiered 27-tool surface** (stable 10-tool core + explicitly-grouped extended tiers, including conversation evidence) vs their flat 53-tool sprawl — tiering, not raw count, is the inspectability edge. Honest read: we are smaller, simpler, more inspectable, more portable — *if* we keep those as features rather than apologize for them. |
| **mem0** | Cloud SaaS, server-side LLM extraction | Local-first, no data egress, private-by-default. Our governance runs offline. |
| **langmem / Zep** | LangGraph-framework-embedded persistent memory | Cross-framework via MCP + hooks. Agent-neutral. |

### Category C — Hermes provider ecosystem (7)

We can be one of these. The Hermes table dimensions are storage / cost / tools / dependencies / unique feature. Below is our differential against each.

| Provider | Storage / cost | Unique feature | Our advantage |
|---|---|---|---|
| **OpenViking** | Self-hosted / Free (AGPL-3.0) | Filesystem hierarchy `viking://` L0/L1/L2 + tiered loading | MIT (not AGPL), three-tier governance pyramid (audit/drift/evolve), adapter registry with explicit support levels. Their tiered-loading idea is **worth borrowing** in a future L2 read-path optimization. |
| Honcho | Cloud / Paid | Dialectic user modeling + session-scoped context | Local + no per-session pricing + 6 type schema (decision/episode/etc) instead of session-only state |
| Hindsight | Cloud / Local PG / Free / Paid | Knowledge graph + reflect synthesis | Markdown-first; we now ship a lightweight KG (entities/relations tables + 1-hop `graph_neighbors`, derived from md — not a separate graph DB), and our `reflect`-equivalent is `memory evolve` in dry-run |
| Holographic | Local / Free | HRR algebra + trust scoring (only 2 MCP tools, very academic) | Pragmatic retrieval (FTS5 + vec + RRF, plus query expansion, opt-in cross-encoder rerank and confidence/decay weighting), 27 MCP operation tools (10-tool stable core + tiered extended), production tests. Their HRR is genuinely novel but probably overkill — we don't compete on this axis. |
| RetainDB | Cloud / $20/mo | Delta compression | Local + free + raw markdown (no compression layer to invalidate) |
| ByteRover | Local/Cloud / Free/Paid | Pre-compression extraction | No lossy compression — markdown is the source of truth |
| Supermemory | Cloud / Paid | Context fencing + session graph + multi-container | Cross-agent shared pool (their multi-container is single-agent scoped); we're MIT not Paid |

### Category D — Closed first-party memory features (2, footnote)

These are not competing products to install — they're memory features bundled into closed agent platforms. We are explicitly NOT trying to displace them; we coexist as the cross-platform brain for users whose work spans multiple agents.

| | Their bet | Where we differ |
|---|---|---|
| **ChatGPT memory** (OpenAI) | Per-account, opaque, closed | Portable across agents, open spec, user-owned md files |
| **Claude memory** (Anthropic) | Per-account, opaque, closed | Same. Our brain pool can be read by Claude Code AND Codex AND Cursor — first-party memories cannot. |

## What collapsed in this revision (honesty log)

- Old claim "**None of the 14 do all three (anti-drift + skill audit + 12 adapter)**" → **falsified** by rohitg00/agentmemory which ships all three at greater scale.
- Old description of rohitg00 as "Aggressive LLM lifecycle consolidation" → corrected to a full feature description (17.7k★, audit/drift/evolve trio, proprietary iii-engine).
- Old description of Tencent as "Top-down enterprise SaaS" → corrected to actual repo state (4.1k★ MIT, local-first, no enterprise binding).
- Old "OpenViking deep dive pending" → research now folded in; their tiered loading is a borrowing candidate, not an existential overlap.

## Open Core three-layer model

| Layer | Who | Pricing | Time horizon |
|---|---|---|---|
| **L1** Personal brain pool | Single dev, multi-tool | MIT, free, forever | ✅ Now (v1.1 polish) |
| **L2** Team shared brain | 3–10 person teams | MIT (same OS) | After 1000+ L1 users |
| **L3** Enterprise infrastructure | 100+ orgs (OPC / OPT / managers / sales / engineers) | Open Core (closed enterprise version) | After 6-month L1 metrics validate |

**Current focus: L1.** L3 schema interfaces reserved (`tenant_id` / `auth_context`). L2 deferred until L1 user pull demands it.

## What we're NOT building (YAGNI list)

- L2 team git sync (waiting for L1 pull)
- L3 multi-tenant SaaS implementation (only interfaces)
- LLM-assisted semantic contradiction detection (v1.2 question)
- Commercial SaaS dashboard / admin UI
- 53-tool MCP sprawl à la rohitg00 — we keep a small stable 10-tool core and group the rest into explicit operation tiers (governance/io/graph/evolve/conversation, 27 operation tools total; 28 registered including the onboarding guide fallback) for inspectability, rather than a flat undifferentiated surface
- Vector embedding as primary search (kept as secondary RRF signal; primary remains FTS5 because md is the source of truth)

## Open questions ("new continents")

8 explicit unsolved areas guide future exploration: cross-agent skill protocol normalization, L1→L2 migration path, L3 commercial form, semantic memory enhancement, MCP protocol evolution coupling, privacy/compliance enforcement, memory aging, multilingual brain.

## Success metrics (PL5 = 2026-11)

- GitHub stars ≥ 5,000
- Hacker News front page ≥ 1 time
- 5+ LLM agents at `install-ready` or better. Current evidence-backed adapter set includes 11 `verified` adapters (Aider, Aone Copilot, Cline, Codex CLI / Codex, Continue, Cursor, GitHub Copilot, Hermes Agent, OpenHuman, OpenSquilla, QoderWork) and 4 `install-ready` adapters (Claude Code, OpenClaw, Qoder, Wukong); `verified` still requires adapter-gate evidence. Current `wip` set includes MuleRun only.
- Quickstart < 60 seconds
- ≥ 1 launch-quality long-form post
- **Listed as a Hermes memory provider** (distribution leverage)
- Dogfooding scorecard: hub's own brain pool passes its own `memory govern run` + `memory anti-drift` without errors

## Contribute

See [CONTRIBUTING.md](./CONTRIBUTING.md). Most-wanted help: cross-agent integration testing (Cursor / Cline / Continue), case studies, translations, and real install paths for the remaining WIP adapter stubs.
