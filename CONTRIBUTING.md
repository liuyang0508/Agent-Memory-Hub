# Contributing to Agent Memory Hub

Thanks for your interest! Read this before opening a PR.

## Quick links

- [Strategy](./STRATEGY.md) (90-second read on why this exists)
- [Roadmap](./ROADMAP.md) (where we're going)
- [Architecture](./docs/architecture.md) (implementation shape)

## Most-wanted contributions

1. **Cross-agent integration testing** — try Cursor / Cline / Continue / other LLM CLIs and document what works
2. **Case studies** — "I used Agent Memory Hub for a month and here's what happened"
3. **Translations** — Spanish, German, Japanese
4. **Bug fixes** for the [hook-tests.yml CI](./.github/workflows/hook-tests.yml)

## What we are NOT looking for (yet)

- L2 team git sync features (waiting for L1 user pull — see strategy §3.2)
- Vector embedding integration (philosophical conflict — see strategy §6.4)
- Enterprise SaaS features (L3 territory — see strategy §3.3)

If you're not sure, open a discussion first.

## Setup

```bash
git clone https://github.com/<owner>/agent-memory-hub.git
cd agent-memory-hub
./install.sh --with-mcp --with-hooks
```

Tests:

```bash
./agent_runtime_kit/hooks/test-hook.sh        # 6 hook unit tests
./tests/schema-tenant-id-test.sh  # schema validation
./benchmarks/quickstart-60s.sh    # 60s benchmark
```

## PR checklist

- [ ] Tests pass (run all 3 above)
- [ ] If you added a memory type / schema field, update `agent_runtime_kit/schema/memory-item.md`
- [ ] If you added a hook / tool, add a unit test
- [ ] If user-facing change, update README + ROADMAP if applicable
- [ ] Commit message format: `<type>: <subject>` where type ∈ {feat, fix, docs, refactor, test, chore}

## Code style

- Bash: `set -euo pipefail`, posix-compatible (we support bash 3.2 on macOS)
- Python (MCP server): black-compatible, type hints encouraged
- Markdown: keep lines ≤ 120 chars

## Issue reporting

Use issue templates for bugs / features. For security issues, see [SECURITY.md](./SECURITY.md).

## License

Contributions are under Apache-2.0 (see [LICENSE](./LICENSE)).
