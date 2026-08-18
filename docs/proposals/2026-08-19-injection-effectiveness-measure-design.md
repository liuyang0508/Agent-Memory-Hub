# Injection Effectiveness Measure — Design

> Status: approved 2026-08-19. Part of the "core memory quality" enhancement track:
> measure first (this spec) → end-to-end QA diagnosis → governance sweep validation.

## Problem

AMH has a half-closed feedback loop:

- Injection cohorts are recorded (`runtime/injection-cohorts.jsonl`,
  `agent_brain/memory/context/injection_cohorts.py`) with `item_ids`, adapter,
  session, query terms and pack metrics.
- Explicit adoption/rejection signals are captured when the *next* user prompt
  contains unambiguous wording ("刚才那批错了" / "those were wrong"), via
  `agent_brain/memory/governance/auto_feedback.py`, wired into
  `agent_runtime_kit/hooks/inject-context.sh`.
- Application is exactly-once and conservative
  (`agent_brain/memory/governance/outcome_feedback.py`): only explicit ids
  reinforce/penalize; an implicit positive applies only when the task succeeded
  with confidence ≥ 0.85 and exactly one item was injected.

What is missing:

1. **Silent cohorts are never closed.** If the user never reacts verbally, the
   cohort has no terminal state — there is no honest denominator for "how often
   do we actually get a signal".
2. **No aggregate view.** Nothing answers "did injected memory help?", grouped
   by adapter / project / memory type / time window. `injection_metrics.py`
   is telemetry sanitization, not effectiveness reporting.

## Goal

Make AMH answer "how effective are injected memories?" with a reproducible,
audit-friendly measure that serves as the yardstick for the next track step
(diagnosing the LongMemEval retrieval→QA gap).

## Non-goals (YAGNI)

- No LLM-based adoption judgment (that belongs to ablation work, track step 2).
- No multi-memory attribution heuristics (track step deferred; noise risk
  conflicts with the project's honesty standard).
- The semantics of `outcome_feedback.py` application are correct and stay
  untouched. This spec adds collection upstream and aggregation downstream.

## Design

### Three-stage structure

```
collection (extend)          application (unchanged)        aggregation (new)
──────────────────           ──────────────────────         ─────────────────
explicit wording   ─┐
                     ├─→ TaskOutcome record ─→ apply_task_    ─→ memory measure
silent cohort       ─┘                          outcome_feedback
closure (sweep)                                                        │
                                                              effectiveness report
```

Signal classes:

- **Strong signals** — explicit wording, existing logic, unchanged. They may
  change confidence.
- **Weak signals** — `closed_no_signal`. Counted in the measure denominator
  only; they never feed back into item confidence.

### 1. Cohort terminal states

Each cohort transitions to exactly one terminal state:

```
injected ──→ closed_adopted     (explicit positive, existing path)
         ──→ closed_rejected    (explicit negative, existing path)
         ──→ closed_no_signal   (observation window elapsed, new)
```

Silence is **not** adoption. `closed_no_signal` only means the window ended
without explicit feedback.

### 2. Closure ledger

Cohort files stay append-only. Terminal state goes into a parallel ledger:

```
runtime/injection-cohort-closures.jsonl
  { cohort_id, terminal_state, closed_at, closed_by }
    closed_by ∈ { session_end, daily_maintenance }
```

Idempotent: the first closure per `cohort_id` wins; later lines are ignored.

### 3. Sweep mechanism

`close_stale_cohorts(brain_dir, *, closed_by, window_hours)` — idempotent,
callable from both hooks and maintenance:

- Primary trigger: session-end hook (`agent_runtime_kit/hooks/session-end-signal.sh`).
- Fallback: daily maintenance (`agent_brain/memory/governance/daily_maintenance.py`).
- Not the UserPromptSubmit injection path: the hot path stays light.

A cohort is closable when it has no terminal state, no matching
`TaskOutcome` (task_id `auto-feedback:<cohort_id>` or
`injection-feedback:<cohort_id>`) and its timestamp is older than
`window_hours` (default 24).

### 4. Explicit wording expansion (small, safe)

Extend the `_POSITIVE` / `_NEGATIVE` regexes in `auto_feedback.py` with a few
additional unambiguous variants in both languages. Rule: prefer misses over
misclassification. No fuzzy judgment.

### 5. Privacy constraints

- Raw prompt text is never persisted (existing invariant, kept).
- Ledgers store only `cohort_id`, terminal state, timestamps, item ids.
- Weak signals never change confidence.

### 6. Aggregation: `memory measure injection`

New command module `agent_brain/interfaces/cli/commands/measure.py`:

```bash
memory measure injection                          # table
memory measure injection --since-days 14 --format json
memory measure injection --by item                # per-item exposure/adopt/reject
```

Core rates (denominator = closed cohorts only; in-window cohorts excluded):

| Metric | Formula | Question answered |
|---|---|---|
| signal coverage | (adopted + rejected) / closed | on how much injection can we even opine |
| adoption rate | adopted / closed | explicitly endorsed share |
| rejection rate | rejected / closed | explicitly harmful share |

`closed_no_signal` counts in the denominator, never in the numerator.
Drill-downs: by adapter / project / memory type (join `items_meta`) /
`--since-days`.

Relationship to existing surfaces:

- `memory hook recent` shows the per-event `usage.injected/adopted/rejected/ignored`
  view; `measure` is the aggregate view over the same sources. Same vocabulary.
- System benchmark remains the synthetic gate (recall/firewall correctness);
  `measure` is the real-usage effectiveness report. Complementary, not competing.
- Wiring into `memory govern readiness` (report low coverage as governance
  debt) is deferred until enough data exists.

### 7. Scope

First version wires only the Claude Code and Codex adapters (hooks already
present, lowest risk). Collection logic itself is adapter-neutral and reused
later.

## Error handling & tests

- Malformed JSONL lines: yield-and-skip (matches `iter_all` pattern). One bad
  line must not break the report.
- Empty ledgers: zero-valued report, exit 0.
- Unit tests: closure idempotency; metric formulas (especially no_signal in
  denominator not numerator); group-by joins; malformed-line skipping;
  sweep window boundary.
- If README documents `measure` output, add a docs truth-contract test.

## Out of scope for this spec

- Ablation harness (track step 2): with-memory vs without-memory generation
  comparison against LongMemEval QA.
- Multi-memory gain attribution (deferred).
- Readiness integration (deferred until data volume justifies it).
