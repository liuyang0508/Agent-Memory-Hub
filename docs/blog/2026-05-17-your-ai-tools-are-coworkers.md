# Your AI tools are coworkers. They should onboard like real ones.

> **STALE DRAFT**: Do not publish without refreshing against README.md, ROADMAP.md, and `memory adapter list --format json`.
> This draft predates the adapter truth-contract matrix and the current 25-tool MCP surface.

> Show HN draft — pending benchmark data (Section 5) and user review before publish.

---

I switched from Claude Code to Codex one day, and lost an entire week of context.

I had spent five days hand-feeding Claude Code the conventions of a new project — the way our team uses git, the reason we picked SSE over WebSockets, the time we discovered our Excel-on-Mac CSV bug, the dozen small decisions you make when you actually build something. Then Anthropic's daily quota hit, I switched to OpenAI's Codex CLI for a single afternoon, and Codex looked at me like I was a stranger.

"What does this project do?" it asked.

Right. I have to do all of that again.

This isn't a Codex problem. It's that **every AI tool I use treats me like a first-time user, because none of them know about each other.** Claude Code doesn't know what Codex learned. Codex doesn't know what Cursor learned. Cursor doesn't know what Cline learned. We have five LLM agents, and they share zero institutional knowledge.

So I started thinking about this differently.

## Your AI tools are coworkers

Real coworkers don't start from zero on their second day. They have:

- A team wiki ("here's how we deploy")
- Decision logs ("here's why we picked Postgres over Mongo")
- Slack history ("Sarah debugged this last month, search her name")
- Onboarding docs ("read these five pages before your first PR")
- Standing orders ("never push to main on Fridays")

Your digital coworkers — Claude Code, Codex, Cursor, Qoder, Cline — have **none of these**. Each one is a brilliant intern on day one, every single conversation, forever.

The fix isn't to fine-tune them. (Real companies don't gene-edit employees.) The fix isn't to give them personalities. (You don't need your coworker to pretend to be human.) The fix is the boring one: **give them a file cabinet, and a discipline for using it.**

## What I built

[Agent Memory Hub](https://github.com/<owner>/agent-memory-hub) is that file cabinet.

It's a Markdown folder, a few shell scripts, and a Python MCP server. About 200 lines of bash and 250 lines of Python doing the heavy lifting. No vector database. No embedding model. No fine-tuning. No SaaS.

```
~/.agent-memory-hub/
├── items/                     ← "what happened" (decisions, episodes, facts, signals)
│   └── mem-*.md
└── playbook/                  ← "how to do" (skills, hooks, disciplines, rules, SOPs)
    ├── disciplines/
    ├── skills/
    ├── hooks/
    └── ...
```

Every LLM agent reads and writes this same folder through MCP. A discipline you taught Claude Code on Monday is automatically pulled into Codex's context when you ask it the related question on Tuesday. A decision you made about CSV encoding lives forever — searchable, reviewable, never asked twice.

Three things make it work:

1. **Markdown-first schema.** Each item has a YAML frontmatter (id, type, agent, tags, sensitivity, created_at) and a body. Six types: `fact`, `episode`, `decision`, `artifact`, `signal`, `handoff`. Each type has body structure requirements — for example, a `decision` must include "决策 / 理由 / 改回去的代价" (decision / rationale / cost-to-revert). The structure is enforced by writing discipline, not by software; it works because humans and LLMs both understand markdown trivially.

2. **MCP protocol for cross-agent access.** I ship a tiny MCP server (`agent_runtime_kit/mcp/server.py`, FastMCP, stdio transport) that exposes 7 tools: `search_memory`, `write_memory`, `list_recent`, `read_memory`, `stats`, `delete_memory`, `list_playbook`. Any MCP-compatible client (Claude Code, Codex 0.130+, Claude Desktop, Cursor, Cline, Continue, Goose) can hit it with a one-line config.

3. **Hooks for ambient context injection.** A SessionStart hook injects the writing discipline + recent active blockers into every new Claude Code session. A UserPromptSubmit hook auto-pulls the top-3 most relevant items into context every time I ask anything. A Stop hook tags session boundaries so I can review what was worth keeping. No prompting required.

The whole install is one command:

```bash
git clone https://github.com/<owner>/agent-memory-hub ~/agent-memory-hub
cd ~/agent-memory-hub && ./install.sh --with-mcp --with-hooks
```

End-to-end, clone to first cross-session memory retrieval is measured by `benchmarks/quickstart-60s.sh`; the script reports clone, install, and first-search breakdown separately.

## The unpopular opinions

I built this knowing it would step on three sacred cows.

### 1. Markdown beats RAG for the kind of memory developers actually want

I keep hearing "but you should put it in a vector DB and embed it!" — and I think this is over-engineering for the most common case.

When I'm pair-programming with an LLM, what I want it to remember is **structured facts** ("we picked SSE because Cloudflare's WebSocket pricing was punitive"), not paragraphs of prose. Vector search excels at finding semantically similar paragraphs; it underperforms on retrieving *specific* facts by topic-centered keywords.

A `decision` item titled "v0.3 write_memory defer dry-run, do it with delete in v0.4" is trivial to retrieve with `grep -i "dry-run"` or `search-memory.sh "dry-run"`. The same item in a vector store competes with every paragraph that happens to mention "dry run" or "defer" — semantic similarity hurts you here.

Plus: vector DB introduces an embedding model dependency (which model? what does it cost per item? what happens when you upgrade?), a database dependency (Pinecone? Weaviate? local SQLite + sqlite-vss?), and a tax on every read and write. For one developer's project notes? **Just use a folder of markdown.** When you grow to 10,000+ items per project, revisit.

### 2. Cross-agent memory does not need fine-tuning

Some research projects (Nous's Hermes, etc.) explore continuously fine-tuning models on per-user data. This is genuinely interesting research, but the wrong tool for "I want my AI coworkers to share context."

Fine-tuning is gene editing your employees. It's expensive, risky, hard to undo, and locks you to one model vendor. **External memory + retrieval is the same as giving your employee a wiki** — cheap, transparent, model-agnostic, instantly editable. When you make a wrong "decision" entry, you `git diff` it and fix it; you don't retrain the model.

### 3. Agents don't need to simulate humans

Projects like OpenHuman explore agents that simulate human personality continuity. Cool research. Not what I need for "remember how I want CSV files formatted."

I don't want my LLM tools to *pretend* to be coworkers. I want them to *work like* coworkers — share context, follow standing orders, leave notes for the next session. The persona simulation is a distraction; the protocol for sharing institutional knowledge is the thing.

## The receipts

(Numbers from `benchmarks/token-savings.sh`, run on 2026-05-XX, 5 test runs.)

**Token cost across sessions, same project:** stale draft section removed.
Refresh benchmark data before publishing; do not publish placeholder numbers.

**Quickstart**: measured as clone → minimal install → first `search-memory.sh` query. (`benchmarks/quickstart-60s.sh`)

**Test suites**: 5 sets, all green
- hooks: 6/6 ✅
- tenant_id schema: 2/2 ✅
- auth_context schema: 3/3 ✅
- playbook schema: 6/6 ✅
- Quickstart benchmark: see `benchmarks/quickstart-60s.sh` output for current machine/network breakdown.

**Cross-agent**: stale draft claim removed. Refresh from `README.md` and `memory adapter list --format json` before publishing; current public wording must distinguish `install-ready`, `docs-only`, and `wip`.

## What's not here

Honesty list. Pre-empting your "why don't you do X" questions:

- **No vector DB.** See section 4 above.
- **No SaaS / dashboard / admin UI.** This is a local-first developer tool, not a B2B platform. (We've reserved L3 enterprise interfaces in the schema — `tenant_id`, `auth_context` — but the SaaS layer is multiple versions away.)
- **No team git sync (yet).** Single-user mode is what currently has product-market-fit. Team mode (L2: git repo synchronization, conflict resolution) is gated on L1 reaching ~1000 users. Premature design wastes effort.
- **No automatic cross-agent skill translation.** If you write a CC superpowers skill, it's not automatically a working Cursor `.cursorrules`. The Playbook namespace (v0.5 alpha as of this post) defines a *normalized format* with `target_agents` hints; each LLM client still needs its own thin adapter. We're using the CC superpowers schema as our baseline. Building adapters for Codex / Cursor / Cline is v0.6+.
- **No agent persona simulation.** See section 6 above.

If you need any of those, this is not the tool for you, and that's fine.

## Try it / what's next

```bash
git clone https://github.com/<owner>/agent-memory-hub ~/agent-memory-hub
cd ~/agent-memory-hub && ./install.sh --with-mcp --with-hooks
```

Roadmap (full version at [ROADMAP.md](https://github.com/<owner>/agent-memory-hub/blob/main/ROADMAP.md)):

- **Current release line**: refresh from `ROADMAP.md` before publishing; the live MCP surface is no longer the old 7-tool draft surface.
- **v0.6 (Q3 2026)**: Playbook adapters for Codex / Cursor / Cline (cross-agent skill actually works, not just docs)
- **v1.0 (Q4 2026)**: Team git sync (L2), if L1 hits ~1000 users
- **v2.0+ (2027)**: Enterprise Open Core (L3) — multi-tenant, RBAC, role-based dashboards for product managers / engineering leadership / sales who need to see what their digital coworkers are learning

**Help wanted, especially:**

- Try the Cursor / Cline / Continue / Goose configs and contribute evidence that can move them through the truth-contract support levels
- Write case studies — "I used Agent Memory Hub for a month on my project, here's what happened"
- Translate the README to Spanish / Japanese / German
- Find bugs in the hook tests on Linux / Windows (current CI is Ubuntu only)

See [CONTRIBUTING.md](https://github.com/<owner>/agent-memory-hub/blob/main/CONTRIBUTING.md). The repo is MIT-licensed, will be permanently open source. The enterprise version (whenever it exists) will be a separate offering on top.

---

*If you find this useful, the best feedback I can get is "I tried it on my project for a week, here's what happened." Issues / PRs / direct messages all welcome.*

*Built by [@<owner>](https://github.com/<owner>) and (soon, hopefully) contributors. Inspired by Andrej Karpathy's "personal LLM-wiki" idea, but extended to multi-agent sharing via MCP.*
