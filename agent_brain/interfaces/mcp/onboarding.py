"""Canonical onboarding text + the ``get_usage_guide`` MCP tool.

Why this file exists
--------------------
MCP has three surfaces a server can use to *teach* a connecting agent how
to use it: tool descriptions, prompts, and resources. Real-world clients
support different subsets:

- Claude Desktop / Cursor: full support (tools + prompts + resources)
- Qoder / Cline: tools are reliable; prompts/resources may not be surfaced
- Headless / programmatic clients: usually just tools

To guarantee EVERY client can discover how to use amh correctly, we
publish the same guidance through all three surfaces. This file owns the
single source of truth (``USAGE_GUIDE`` + the 3 scenario constants) so
``prompts.py`` and ``resources.py`` simply re-export them and the
``get_usage_guide`` tool below works as a tool-surface fallback.
"""
from __future__ import annotations

from typing import Any


USAGE_GUIDE = """\
# agent-memory-hub · Agent Usage Guide

You are connected to **agent-memory-hub (amh)**, the user's persistent
memory brain. The brain is the user's single source of truth for prior
work, conventions, decisions, debugging recipes, prompts, and reusable
artifacts. Treating it as a first-class collaborator is mandatory, not
optional.

## The Three Core Habits

### 1. SEARCH BEFORE YOU ANSWER  (most important)

Whenever the user's request touches a named entity (project, tool, person,
framework, error, command, decision, convention), **call `search_memory`
FIRST** — before reasoning, before generating, before claiming anything
from your own knowledge.

Triggers to search:
- The user mentions a specific project, repo, file, function, library.
- The user uses anaphora: "this", "that", "earlier", "last time",
  "上次", "之前", "还记得", "接着".
- You are about to make a factual claim or recommendation that the
  brain might already encode (or contradict).
- The domain overlaps with anything the brain has covered before
  (architecture, code review, debugging, prompts, decisions).

Typical flow:
```
search_memory(query="<full task description>", top_k=5, verbosity="auto")
  → inspect each hit["context_pack"] first
  → auto returns locator/overview only; spot the 1-3 relevant items
  → if context_pack.text is enough: answer from packed context
  → only when needed for evidence, code, logs, stack traces, or exact wording:
      read_memory(id, head=2000, view="detail")
  → cite the id and say when you performed a bounded detail read.
```

Use `brief_memory` to recover the overall project state; use `search_memory`
for relevance to the current concrete task. They are complementary, not
fallbacks. Pass the full task description to search instead of first reducing
it to model-chosen keywords. A `project` argument is a hard filter: provide it
only when the user explicitly names the project or the cwd mapping is certain.
Never infer a project from natural-language similarity and use that guess as a
hard filter.

Reserve explicit search `verbosity="detail"` for deliberate bounded diagnostics,
not ordinary Top-K discovery. Broad explicit detail remains available but may
return a non-blocking staged-recall governance warning.

When in doubt, search. A cheap search beats an expensive hallucination.

### 2. WRITE PROACTIVELY  (do not wait to be asked)

The user almost never says "remember this". You must recognise capture
moments yourself and call `write_memory` proactively:

| Signal                                            | Type        |
| ------------------------------------------------- | ----------- |
| You produced a reusable prompt / recipe / template| `skill`     |
| You produced a PR / doc / file / dataset (a ref)  | `artifact`  |
| You finished a chunk of work + its outcome        | `episode`   |
| You made a non-obvious choice + rationale         | `decision`  |
| You learned an objective fact / constraint        | `fact`      |
| A blocker / alert / change notice is active       | `signal`    |
| You are handing a task off to another agent       | `handoff`   |
| A rule crystallised from repeated episodes        | `policy`    |

Rules of thumb:
- Bias toward writing. Trivial drafts are filtered later by `gc_memory`.
- Always provide a `title`, a 1-2 sentence `summary`, and a `body`
  containing enough context that future-you can act on it cold.
- Tag generously. Call `tag_suggest(text=summary)` to keep vocabulary
  convergent.

### 3. LINK AFTER YOU WRITE  (chain into the graph)

Immediately after a successful `write_memory`, if the new item relates to
items you saw during the same task's `search_memory`, call:

```
link_memories(source=new_id, target=related_id, relation="refs"|"supersedes"|"refines"|"contradicts"|"derives")
```

Unlinked memories are orphans — they are discoverable only by direct
search. Linking turns the brain into a graph, which is what enables
`graph_memory`, `evolve_memory`, and `drift_check` to reason across items.

## Session Lifecycle

```
┌─────────────────────────────────────────────────────────────────────┐
│ START OF SESSION (especially on "resume" / "continue" / "接着上次") │
│   brief_memory(project=..., budget_tokens=1500)                     │
│   → spot 1-3 most relevant items → read_memory those ids            │
├─────────────────────────────────────────────────────────────────────┤
│ EACH USER QUESTION                                                  │
│   search_memory(query=<full task description>, verbosity="auto")   │
│   → inspect context_pack                                             │
│   → read_memory(id, head=2000, view="detail") only when needed      │
│   → answer (with id)                                                │
├─────────────────────────────────────────────────────────────────────┤
│ AFTER PRODUCING ANY REUSABLE ARTIFACT                               │
│   write_memory(type=..., title=..., summary=..., body=..., tags=)   │
│   link_memories(new_id, related_id, relation=...)                   │
├─────────────────────────────────────────────────────────────────────┤
│ END OF TASK / SESSION                                               │
│   (optional) brain_stats() → if grade dropped, drift_check + govern │
│   (weekly)   gc_memory(dry_run=True) to preview transient cleanup   │
└─────────────────────────────────────────────────────────────────────┘
```

## The 8 Canonical `type` Values  (do not invent new ones)

- `fact`     — an objective statement / constraint you learned
- `episode`  — something you did + its result
- `decision` — a key choice + the rationale behind it
- `artifact` — a reference to a produced PR / doc / file / dataset
- `signal`   — an active blocker / alert / change notice
- `handoff`  — a task package explicitly handed to another agent
- `policy`   — a rule crystallised from repeated episodes (evolve-grown)
- `skill`    — an executable recipe or prompt template (evolve-grown)

## Hard Rules

- **Never** answer a user question whose context the brain may hold
  without first calling `search_memory`.
- **Never** finish producing a reusable artifact (prompt, checklist,
  recipe) without calling `write_memory`.
- **Never** call `delete_memory` to "clean up" — write a superseding
  item and `link_memories(new, old, "supersedes")` instead.
- **Never** invent new `type` values; pick the closest of the 8 above.
- Bulk reads are forbidden; use `brief_memory` for browsing and
  `search_memory(..., verbosity="auto")` context packs for triage.

## Surfacing What You Did

When you used the brain to answer, briefly tell the user:
> "I checked your brain: memory `mem-2026-…` says X, so I … "

When you wrote to the brain, tell the user:
> "Captured this as `mem-2026-…` (type=skill, tags=[…]) so we can reuse it."

This builds the user's trust that the brain is being maintained on their
behalf and gives them a clear pointer to audit/edit.
"""


BEFORE_ANSWERING = """\
# Before You Answer · amh trigger checklist

You just received a user message. STOP. Before reasoning or generating:

1. Does the message reference any of these? If YES → `search_memory` now.
   - A named project, repo, file, function, framework, tool, person.
   - Anaphora: "this", "that", "earlier", "last time", "上次", "之前".
   - A domain the brain may cover: architecture, code review, debugging,
     prompts, decisions, conventions.
   - A factual claim or recommendation you are about to make from your
     general knowledge (the brain may contradict it).

2. Call `search_memory` with the full task description:
   `search_memory(query="<full task description>", top_k=5, verbosity="auto")`
   Use `brief_memory` for overall project recovery and `search_memory` for the
   current concrete task; neither is a fallback for the other. The `project`
   argument is a hard filter, so set it only when the user explicitly names
   the project or the cwd mapping is certain. Never guess it from natural
   language.

3. Auto search returns locator/overview only. Inspect `context_pack.text`,
   `context_pack.selected_view`, and `context_pack.retrieve_hint`, select the
   1-3 relevant hits, then call
   `read_memory(id, head=2000, view="detail")` only when needed for
   evidence, code, logs, stack traces, or exact wording.

Reserve explicit search `verbosity="detail"` for deliberate bounded diagnostics,
not ordinary Top-K discovery.

4. Cite the memory id in your reply so the user can audit:
   > "Per mem-2026-XX-YY (your memory on …), …"

When in doubt, search. Cheap search > expensive hallucination.
"""


AFTER_PRODUCING_ARTIFACT = """\
# After Producing an Artifact · amh capture checklist

You just produced something reusable: a prompt template, a checklist, a
decision framework, a debugging recipe, an architecture rationale, a
non-trivial code snippet, a config recipe, a workflow.

DO THIS NOW, do not wait to be asked:

1. Call `write_memory` with:
   - `type`: closest of fact / episode / decision / artifact / signal /
     handoff / policy / skill   (usually `skill`, `artifact`, or
     `decision` for the things you produce)
   - `title`: 5-12 words capturing the artifact
   - `summary`: 1-2 sentences with the gist + when to use
   - `body`: the FULL artifact, with enough context for cold reuse
   - `tags`: 3-5 tags (call `tag_suggest(text=summary)` if unsure)
   - `project`: if scoped to a project, set it

2. Capture the returned `id`.

3. If the artifact relates to items you saw during this task's
   `search_memory`, call:
   ```
   link_memories(source=new_id, target=related_id, relation="refs")
   ```
   Use `"supersedes"` if it replaces an outdated item, `"refines"` if it
   extends an existing one.

4. Tell the user:
   > "Captured as `mem-...` (type=..., tags=[...]) so we can reuse it."

Trivial drafts are filtered later by `gc_memory`. The cost of writing
too much is far lower than the cost of losing knowledge.
"""


END_OF_TASK = """\
# End of Task · amh wrap-up checklist

The task is wrapping up. Consider these lightweight hygiene calls:

1. `brain_stats()` — Did the health grade change? If it dropped one
   letter, suggest running `drift_check()` + `govern()` next session.

2. `drift_check(staleness_days=180)` — Only if the user signalled they
   want a brain-health review, or if `brain_stats` showed regression.

3. `gc_memory(dry_run=True)` — Weekly cron-ish: preview what transient
   items (session-end / auto-captured / needs-review) would be cleared.
   Show the preview to the user before running with `dry_run=False`.

4. Make sure you actually wrote down every reusable artifact you
   produced this task. If you skipped any, write them now.

Do NOT do all four every session — they cost time. Pick what fits the
task's importance.
"""


def get_usage_guide(section: str | None = None) -> dict[str, Any]:
    """Return the canonical amh usage guide for connecting agents.

    Use this when your MCP client does not surface prompts/resources, or
    when you want the guide as part of a tool result you can quote back.

    WHEN TO USE
    -----------
    Call this ONCE at the start of a session (if your client doesn't
    surface the `agent_workflow_guide` prompt automatically) to load the
    canonical workflow into your reasoning context. Optional re-read at
    scenario moments via `section="before_answering"`,
    `"after_producing_artifact"`, or `"end_of_task"`.

    Args:
        section: Optional. One of:
            - None (default): the full ``agent_workflow_guide``
            - ``"before_answering"``: trigger checklist for incoming questions
            - ``"after_producing_artifact"``: capture checklist
            - ``"end_of_task"``: wrap-up hygiene checklist
    """
    sections = {
        None: ("agent_workflow_guide", USAGE_GUIDE),
        "agent_workflow_guide": ("agent_workflow_guide", USAGE_GUIDE),
        "before_answering": ("before_answering", BEFORE_ANSWERING),
        "after_producing_artifact": ("after_producing_artifact", AFTER_PRODUCING_ARTIFACT),
        "end_of_task": ("end_of_task", END_OF_TASK),
    }
    if section not in sections:
        return {
            "status": "error",
            "reason": f"unknown section {section!r}",
            "available_sections": [k for k in sections if k],
        }
    name, body = sections[section]
    return {
        "section": name,
        "format": "markdown",
        "content": body,
    }


def register(mcp) -> None:
    """Register the ``get_usage_guide`` tool on the FastMCP instance."""
    mcp.tool()(get_usage_guide)


__all__ = [
    "USAGE_GUIDE",
    "BEFORE_ANSWERING",
    "AFTER_PRODUCING_ARTIFACT",
    "END_OF_TASK",
    "get_usage_guide",
    "register",
]
