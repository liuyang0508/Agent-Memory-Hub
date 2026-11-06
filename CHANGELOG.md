# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.4.31] — 2026-11-06

### Changed

- Organization recall now works the way Claude Code's and Codex's native memory do: an
  index when a session starts, then the model searches and reads. Injection stays only for
  a topic memory that clears the gate (probability 0.7, lift 0.08) and for a document naming
  something rare from the prompt as a whole token, summaries and indexes included. All of it
  is decided on the Hub, so existing clients get it on upgrade.
- Search finds what a native reader's grep would: a document literally containing a name
  from the question comes first, opening where the name appears. Summaries get semantic
  vectors and no longer rank at 0.55.
- The hook says one line when something was injected and nothing otherwise. The table is
  `amh-native recall-display table`. A handbook part is named by its own section heading.
- The session catalog leaves out what the asking machine already has.

### Removed

- The title-only tier: 30 hinted turns, 0 reads. `hints` stays in the reply, always empty.
- The literal shortcut that dropped the injection bar to 0.1 for any identifier in a body.
  `terminal-bench` matched `agentos-terminal-bench-local`, and `AgentOs` matched everything.
- Injecting an index file on its score, a machine's own memory back to it, and searching a
  short reply with the previous prompt.

### Added

- `amh-native recall --no-prompt`: nothing is injected or sent at prompt time; the catalog
  and the model's own search stay.

### Measured

Held-out tasks written by an independent agent, real `claude -p` with no access outside the
workspace, blind judge, correct / partial / wrong: Sonnet 43/11/15 to 44/14/11 (not a
significant difference), Opus 16/3/4 to 18/3/2, Haiku 9/2/12 to 13/3/7. Cost per run fell
about 25% on Sonnet and 40% on Opus. No real Codex process took part.

## [1.4.30] — 2026-11-06

### Added

- Session digests, off until a person runs `amh-native digests --enable`. A session's own
  file stays on its machine and a product only turns it into memory later, if at all, so
  work done this morning could not be found this afternoon. Once a Claude Code or Codex
  session has been idle for 30 minutes, the worker writes its title, what was asked, its
  last three replies and which files changed, and syncs that like any memory. No model is
  called.
- Lines mentioning a credential are removed whole, unlabelled values, addresses and home
  paths are masked, and a digest the screen flags is not written. Sessions begun before
  enabling are never read, scripts and subagents are skipped, and `--forget` withdraws one.
- The Hub treats them as session summaries: never injected and not listed in the catalog.
  Unlike other summaries they are indexed by meaning and not marked down in search, since
  a natural "pick up this work" query otherwise never reached them. Clients write them
  only to a Hub reporting `session-digest-v1`.

## [1.4.29] — 2026-11-06

### Changed

- Whole conversations can no longer be found or read through the organization tools. A
  model could ask `search_org_memory` for `kinds: ["transcript"]` and read what came back,
  and a conversation holds whatever passed through it. The tool refuses that kind, and
  the Hub refuses it on `/retrieve`, leaves it out of `/search` and answers `/read` and
  `/related` as if it were absent, so older clients cannot reach it either. Automatic
  recall and default search were never affected. Session summaries stay searchable.
- History import: "select all" and "select clean" stop at the types this profile uploads.
  A transcript or skill is still there to tick one by one, or with `--kind`, and asks for
  confirmation as before.

### Known limitation

- `/native/v1/changes`, the device sync feed, still returns transcripts a device uploaded
  to any member holding a token. Stopping the upload and purging the assets removes them;
  new members upload memory only by default.

## [1.4.28] — 2026-11-06

### Added

- A session now starts knowing what organization memory covers: up to 2400 characters of
  topics taken from the documents' own descriptions and task groups, kept fresh by the
  worker, sent again after `/clear` and compaction. With the search tool one of many
  deferred tools, a model otherwise almost never searched mid-task. Session summaries,
  working files, indexes and one person's preferences are left out, and so are IP addresses.
  `recall --disable` and `pause` stop it.

### Changed

- `search_org_memory` returns up to eight short candidates instead of about three long
  ones, at most two from one document, and includes session summaries, ranked below curated
  memory as before. A file path as the query opens that file, and a page read lists the
  files it points to, because a handbook's details are often there.
- A question's research budget is 64000 bytes instead of 32000. A read accepts an id named
  by a recall or another search within an open research, and oversized `limit` and
  `max_bytes` are clamped rather than refused.
- The tool's instructions say when to look, including during the work, in place of
  "only when needed".

### Compatibility

- A new client on an older Hub still searches, with fewer candidates and no topic list. An
  older client on a new Hub behaves as before.

## [1.4.27] — 2026-11-05

### Changed

- Codex's `raw_memories.md` and `extensions/skysight/` are no longer uploaded.
  `raw_memories.md` is consolidation's intermediate input, and `extensions/skysight/` holds
  what Computer History derives from on-screen activity. Everything else in the store,
  including `rollout_summaries/`, is uploaded as before.
- Copies of these files already on a Hub stay there; only deleting the local file, or an
  administrator's `purge-asset`, removes them.

## [1.4.26] — 2026-11-05

### Fixed

- The recall table could describe an injected section as a heading glued to a file name
  ("keywords: 《MEMORY》"), which said nothing. It now shows the section's most telling
  sentence.

## [1.4.25] — 2026-11-05

### Added

- `amh-native upload --disable` stops reading and uploading this device's content while
  recall keeps working. `pause` stops both, which left no way to keep reading without
  writing. Changes made meanwhile are uploaded when uploads are enabled again; what is
  already on the Hub stays there.

### Fixed

- Uninstalling left all of AMH's hooks in place if `settings.json` had changed at all
  since install, such as switching model, so recall kept injecting. It now removes
  exactly what AMH added and keeps the rest, and pauses the profile so anything it could
  not remove stays silent. Installing again resumes.

## [1.4.24] — 2026-11-05

### Added

- The model can say what a recalled memory did. On a sample of turns (20% by default,
  set by the Hub with `AMH_NATIVE_SELF_FEEDBACK_RATE`) Claude Code's hook asks, from
  outside the memory block, and the model answers with the new `memory_feedback` tool:
  used (with the sentence it relied on), not needed, or misleading. The sentence is
  checked on the member's machine and never leaves it. This is observed only: verdicts
  are kept apart from the labels calibration reads, until they have been compared with
  independent judgement. The existing text-overlap rule almost never finds "used".
  The cockpit shows the share as an observation.

### Changed

- Index and summary files need a relevance of 0.9 to be injected in full, and a shared
  identifier no longer excuses them (gate `learned-v1.2`). They touch every topic, and
  one member refreshing theirs moved precision on the same 91 prompts from 61% to 50%.
  With this rule, before and after that refresh: precision 76% and 76%, prompts answered
  only with wrong memories 4 and 4, prompts with a useful full memory 15 and 15 (down
  from 18 and 16). The rule was chosen on these prompts.
- `read_org_memory` no longer needs a search first: the `asset_id` and `revision` a
  recall names can be read directly. Over this machine's 28 sessions, none of the 8
  turns given titles went on to read one; reading took three steps.
- `hub.sh` only mentions a leftover index service when it is running or enabled.

## [1.4.23] — 2026-11-05

### Changed

- A Hub with enough memory (3.5 GB total, 1.5 GB free) now uses the float export of
  the embedding model. Its similarities are identical on x86 and ARM, and it tells
  useful memories apart better than the quantized export does on x86. On the same 91
  real prompts, with the same gate and thresholds: precision 55% to 61%, prompts
  answered only with wrong memories 9 to 7, prompts with a useful full memory 14 to
  18, simulated before release. On the live Hub the store had changed meanwhile and
  measured 52%, 15 and 18; the store change, not the model, explains the gap (1.4.24).
  Smaller hosts stay quantized; `AMH_SEMANTIC_MODEL=large|small` overrides. The
  float export is 641 MB on disk and takes about 0.9 to 1.3 GB of memory. Changing
  export rebuilds the index at startup; recall only trusts literal identifiers until
  that finishes.

### Fixed

- A long memory's excerpt could open mid-word and lose its title. It now opens on a
  whole line where one is near and keeps the title line through the cut.

## [1.4.22] — 2026-11-05

### Changed

- Prompt-time recall injects less. Replaying 91 real prompts on a live Hub, with a
  blind reader judging each memory, full injection was right 37% of the time. The
  bar for full injection rises from 0.3 to 0.7 and for titles from 0.15 to 0.3:
  precision reaches 58%, prompts answered only with wrong memories fall from 14 to
  8, wrong titles from 72 to 40, and prompts reached with a useful memory fall from
  27 to 24, partly through titles the model has to choose to read. The thresholds
  were chosen on these prompts; no untouched set remains. Weekly recalibration can
  tighten the full-injection bar, not relax it.

### Fixed

- A long memory reached the prompt from its opening or its first shared keyword,
  whatever part the prompt had matched. The Hub now hands the hook the section
  around the match, titled, and the client no longer counts one section as having
  read the whole memory.
- Gold cases can name an asset id, so aggregated files can be judged part by part.

### Notes

- A gate retrained on real prompts looked better with prompts split at random, then
  lost to the current gate once whole sessions were held out; it was not shipped.
- Earlier reported hit@1 figures (87%) counted a file name appearing in an index's
  text as a hit. Judged by file name on a live Hub, it is about 36%.
- The same quantized model gives different similarities on x86 and ARM, and tells
  useful memories apart less well on x86. See the operations guide.

## [1.4.21] — 2026-11-05

### Fixed

- Upgrading a Hub with a large change log failed: 1.4.20 scrubbed old text and ran
  a vacuum before accepting requests, past the 90 seconds an upgrade allows, and
  rolled back. The scrub now runs beside the service in small batches, resumes after
  a restart, and secure delete replaces the vacuum, which would have locked the
  database for minutes. Do not deploy 1.4.20 on a Hub with existing data.

## [1.4.20] — 2026-11-04

### Security

- Continuous sync uploaded files the screen had already judged as containing
  credentials; another member could search them. It now refuses such a file,
  withdraws what is queued or already on the Hub, reports the file and category
  (never the value) in the worker status, and resumes when the file is fixed.
  Files synced before the screen existed get one look after upgrading. Session
  records are masked instead of refused: half of the session records on one real
  machine quote something shaped like a credential, none of the memories did. The screen learns Aliyun and Google keys, new
  GitHub tokens, Chinese password phrasing and credentials inside URLs; more
  credential file names and config keys are excluded.
- Editing or deleting a memory left its old text readable by every member
  through `/changes`. Superseded revisions now keep metadata only. Existing logs
  are scrubbed once at startup with secure delete and a vacuum. `amh-native
  purge-asset` erases a memory entirely; the cockpit lists memories already on
  the Hub that the screen would refuse, by name and category only.
- A member could replace the whole organization's gold questions, which steer
  the gate. A member now answers only for their own.
- Memory text could close the "not an instruction" block with its marker. The
  public Feishu login queue is bounded.

### Changed

- The activity dashboard and insights pages become one management cockpit
  (`/native/v1/admin/dashboard`; the insights address remains): a conclusion and
  north star metric, exceptions with evidence and a suggested action, a balanced
  scorecard against stated default benchmarks with period-over-period change,
  trend and funnel, weekly activity and retention cohorts, a portfolio matrix and
  Pareto view of knowledge assets, freshness, people and knowledge flow, gaps and
  system health. Light and dark, phone width, no external asset. The recall ledger
  and member pages share its look.
- The word list of 53 acknowledgements becomes a length rule (at most two
  characters or three Latin letters); the relevance gate refuses the rest. Measured
  against two org stores, one former list word in 106 runs was injected.
- Identifiers must be specific and on topic: status codes, years and files every
  project has no longer bypass the gate, matches are whole tokens, and an
  identifier match still needs probability >= 0.1 and lift >= 0. Precision
  83.8->87.1% and 63.2->65.3%, hit@1 87.0->87.0% and 57.1->54.5%, abstention
  unchanged on the two stores.

### Fixed

- The title-only tier told the model to read directly, which always failed: a
  read needs a search in the same research first. The instruction now says so, and
  an end-to-end test follows it. A title is named once per session, and a gate
  promoted before the tier takes its rule from the manifest instead of running
  without the floor.
- A short follow-up's results are titles only, with this prompt first in the query
  and context at most 30 minutes old: unrelated short prompts were finding the
  previous topic's memories. Background notifications and interrupts no longer
  count as the previous prompt.
- Health no longer reports semantic recall ready while an organization's index
  belongs to another model, and `hub.sh` stops a leftover `semantic-index` service.
- A local permission error or a gateway's 403 no longer wipes a client as if the
  device were revoked, and cleared state comes back when access does. A full disk
  no longer stops the worker.
- Delivery receipts are found after a turn that wrote more than a megabyte, which
  re-injected the memory (14.6% of injections on one machine).
- Usage labels: specifics already in the conversation prove nothing either way,
  and a failed read is not a read.
- `upgrade.sh` restores the model setting on rollback and waits for the rolled-back
  service, keeps a working model when a new one cannot be fetched, honours
  `--no-semantic`, and prunes old releases and backups. The release job checks the
  tag against the built version. A pip timeout still restores the previous client;
  a failed update check retries within the hour.
- The warm connection stays up through a working day's pauses (a cold first prompt
  took 2.8 s against 1.1 s), and a queued request past its budget no longer reaches
  the Hub.

## [1.4.19] — 2026-11-04

### Fixed

- The recall table no longer breaks in a terminal narrower than the table: a
  right border that wrapped to the next line made every row unreadable. The
  hook reads the member's terminal width from the host's controlling terminal
  (about 5 ms; `COLUMNS` and then 126 columns as fallbacks), tightens columns,
  then gives up whole columns, least useful first. The memory column is
  narrower (18 columns) in every layout.
- A part of a split file reads as such: `MEMORY.md 第7/8段`, not `MEMORY.md 7/8`.
- A terminal's own reply to a capability query (`?62;22;52c>1;10;0c`) leaking
  into the prompt box is no longer searched as a question.

## [1.4.18] — 2026-11-04

### Changed

- Prompt-time recall injects a memory in full only if it is clearly closer to
  the prompt than to everyday prompts (similarity minus the memory's
  similarity to a bank of generic prompts, `lift` >= 0.08), in addition to the
  learned gate's probability. Rank features alone let a small store push its
  best match through any prompt: on a 34-memory store unanswerable prompts
  were left alone 43% of the time. With the floor, picked in 23 of 25 grouped
  cross-validation folds over two organization stores (94 and 34 memories),
  held-out: unanswerable left alone 75%->89% and 43%->89%, false injection
  25%->11% and 57%->11%, hit@1 88%->87% and 61%->57%. A literal identifier
  from the prompt is still exempt.

### Added

- Title-only tier: memories the full rule holds back but rates probable
  (probability >= 0.15 and lift >= 0.04, at most two) are named in the
  injected context by title, asset id and revision, never their bodies, for
  the model to read with `read_org_memory` when it needs one. The recall table
  lists them ("只给标题"). Hub capability `recall-hints-v1`; older clients
  ignore the field.
- A short follow-up prompt ("那第二个呢", "发布了告诉我": at most 16 characters,
  no identifier) is searched together with the member's previous prompt from
  the Claude Code transcript; the table says so. The gate still judges every
  candidate.
- `recall-calibrate --lift --hint-threshold --hint-lift`; weekly autotune
  carries the injection rule (floor and tier) into every promoted gate.

## [1.4.17] — 2026-11-04

### Fixed

- Prompt-time recall on a Hub without semantic ranking (model missing, failed
  to load, or index incomplete) no longer injects every keyword match. With no
  relevance gate to score candidates, the hook now injects only memories that
  literally contain an identifier from the prompt (path, error code, version,
  symbol, link) among the top three keyword hits, at most two; the rest are
  held with trace reason `lexical_guard`, and the recall table says so. Tool
  search is unchanged. `AMH_NATIVE_HOOK_LEXICAL_GUARD=0` restores plain keyword
  injection.
- `upgrade.sh` failed with `FileNotFoundError` when the pinned model files were
  already in place but the model manifest was not (operator-placed files); the
  manifest is now always written.
- A configured semantic model that cannot load is reported as
  `semantic.state: unavailable` with a `reason` in health, instead of staying
  `loading` forever.
- The recall table names degraded reasons in words instead of codes.

### Added

- `hub.sh --model-dir DIR` takes the model files from a local directory (a
  host that cannot reach Hugging Face), still checked by sha256. When semantic
  recall does not come up, `hub.sh` says so plainly, lists the files to fetch
  with their sha256, and stops waiting as soon as the Hub reports the model
  unavailable.

## [1.4.16] — 2026-11-04

### Added

- One command installs or upgrades the organization Hub:
  `curl -fsSL https://github.com/liuyang0508/agent-memory-hub/releases/latest/download/hub.sh | sudo bash`.
  It finds Python 3.11-3.13, downloads the release wheel, `upgrade.sh` and the
  model manifest and checks each against the release's `checksums.txt`, builds
  the dependency wheelhouse (PyPI, or the Tsinghua mirror when PyPI is
  unreachable), snapshots the database, runs `upgrade.sh` (pinned model from a
  Hugging Face mirror, atomic switch, rollback on failure), retires a drop-in
  line that pins an older model once the new one is in place (backup kept,
  restored if the Hub then fails to start), and waits for semantic recall to
  index. Exercised in a systemd Ubuntu 24.04 container: fresh install, upgrade
  over a legacy model drop-in, rollback from a broken release, and refusal of
  a tampered wheel.
- `upgrade.sh` uses the interpreter it is given (`AMH_PYTHON`) or the first
  of Python 3.11-3.13 instead of requiring 3.11.

## [1.4.15] — 2026-11-04

### Changed

- Recall table: every memory gets a full-width content line under its row,
  so a file name like `MEMORY.md 5/8` or `SKILL` no longer hides what was
  recalled. The line shows the memory's own `description`; for an index
  chunk, the task groups it contains; for an index, the entries it lists;
  otherwise its first heading and sentence. Skills are named after their
  directory. In Codex's Markdown table this is a 内容 column.

## [1.4.14] — 2026-11-04

### Added

- Client self-update (`autoupdate.py`). The resident worker checks once a day
  and moves the client to exactly the version its Hub reports, never past it,
  so the Hub-first upgrade order holds by construction. The wheel must match
  the release's `checksums.txt`, and a fresh interpreter must import the new
  runtime and report the new version; otherwise the previous version is
  reinstalled. Only an installer-managed virtualenv updates (never a source
  checkout, an editable install or the Cowork runtime), one profile per
  machine updates under a shared lock, and the worker re-executes itself onto
  the new code. `amh-native update [--now] [--on|--off]`; `AMH_AUTO_UPDATE=0`
  turns it off. Exercised end to end in a real virtualenv (1.4.14 → 1.4.15).
- Hub health reports `version`; clients follow it. A Hub older than 1.4.14
  reports none, and its clients keep their version.

## [1.4.13] — 2026-11-04

### Changed

- Follower margin for prompt-time recall: the second and third memory must
  reach half the leading memory's relevance probability, so a strong match no
  longer brings weak ones along (a 38% index chunk behind an 89% answer).
  Cross-validated on the 105-case gold set: hit@1, hit@3 and abstention
  unchanged; precision 74.4% → 76.7%; memories per prompt 1.56 → 1.45. Literal
  identifiers from the prompt still pass. Set in the model manifest
  (`hook_gate.relative`), `recall-calibrate --relative`, kept by autotune.
- Recall table: the 相关 column is now 相关性; a new 使用率 column shows how
  often the memory was used when injected (share and judged turns, from usage
  feedback; "—" before any); every row is separated by a rule; historical
  uploads show the uploader's name as author, without "上传".

## [1.4.12] — 2026-11-04

### Fixed

- Delivery reports and usage labels now drain over a lossy link. One timed-out
  upload used to end the whole report pass, so on a link that drops some new
  connections the queue never emptied (observed: reports stuck for 80 minutes
  while individual requests succeeded in 0.4 s). A pass now moves on to the
  next trace and stops only after three failures in a row.

## [1.4.11] — 2026-11-04

### Added

- Usage feedback for prompt-time recall. At the end of each turn the Stop
  hook judges, on the member's machine, whether the answer used each memory
  the hook injected (`usage.py`): a mark counts only if it is in neither the
  prompt, the earlier session, nor the turn's tool results, so shared project
  vocabulary is not mistaken for use. "used" needs a long specific identifier
  or three separate copied passages; "unused" needs no overlap at all; the
  rest stays unlabeled. A memory the gate held back that the model then
  fetched with `read_org_memory` and used is reported as "consulted". Only
  digests are kept between prompt and Stop; only asset ids and labels reach
  the Hub. On 195 injections from real Claude Code sessions the judge
  labeled 117 "unused" (39 of 39 spot-checked correct), 1 "used" (correct)
  and left 19 unlabeled. Outside Claude Code, where the earlier session is
  not visible, only "unused" is reported.
- The Hub stores each label with the gate's evidence at recall time
  (`recall_labels`, numbers only, 180 days), so calibration can learn from use
  after the trace expires.
- Weekly recalibration from use (`autotune.py`; `serve --autotune-days 7`).
  The gate is refit on the organization's gold questions plus usage labels
  (class-balanced, total weight capped at half the gold candidates; gold
  alone picks the threshold) and promoted only if it passes both checks:
  gold cross-validation utility does not fall at all (hit@1, precision and
  abstention may trade within 0.02), and on the newest quarter of each label
  class, held out, it ranks real use no worse than the live gate beyond the
  paired-bootstrap sampling error (non-inferiority, margin at least 0.02 AUC).
  Every run is recorded; promoted gates are per organization, tied to the
  embedding model, and `recall-autotune --rollback` restores the previous
  one. An operator pin (`AMH_NATIVE_HOOK_GATE`) still wins.
- Members contribute gold questions (`amh-native recall-gold-upload`,
  `POST /native/v1/recall/gold`) and see label counts, the last decision and
  gate history (`amh-native recall-tuning [--run]`,
  `GET|POST /native/v1/recall/autotune`). Health advertises
  `recall-usage-feedback-v1`. `recall-trace` shows each candidate's usage.

### Fixed

- Host background events (`<task-notification>` and command output echoes)
  no longer trigger prompt-time recall. In the sessions above they caused 58
  of 195 injections.
- Usage labels are sent in their own batches, so a Hub that predates them
  cannot reject a batch of delivery states along with them.

## [1.4.10] — 2026-11-04

### Fixed

- The recall API now returns `recall.relevance`, so the hook table's 相关
  column shows the learned relevance probability instead of falling back to
  raw similarity.

## [1.4.9] — 2026-11-04

### Added

- Learned prompt-time relevance (`relevance.py`, gate `learned-v1`). The hook
  injects a memory only when a calibrated logistic model rates it probably
  relevant from eight signals: similarity, gap to the best match, the
  memory's background similarity to a fixed bank of generic prompts (CSLS-
  style hubness correction), the prompt's similarity to the whole store,
  keyword rank, index role, content-term coverage and literal identifiers.
  At most three memories per prompt; near-duplicates (the same note from
  several products or devices, index sections cut at other offsets) fold
  into one. MCP search stays ungated.
- `amh-native recall-eval`: runs a gold set through hook-channel retrieval on
  a snapshot of the Hub database without recording traces, and reports
  hit@1/hit@3/MRR, precision of what was injected, abstention on
  unanswerable prompts, injected count and latency, with bootstrap 95%
  intervals, dev/test and per-tag splits, and a `--baseline` regression exit.
- `amh-native recall-calibrate`: fits the gate on a gold set and picks its
  threshold by grouped 5x5 cross-validation with the one-standard-error rule.
- The shipped jina-v2-base-zh manifest carries the calibrated gate. On a
  105-case gold set (cross-validated) hit@1 85.7%→87.8%, hit@3 92.2%→91.4%,
  precision of injected memories 56.6%→74.4%, unanswerable prompts left
  alone 64.3%→75.0%, memories per prompt 2.87→1.56.
- Health reports `semantic.gate` and `relevance-gate-v1`; recall items carry
  `recall.relevance`, shown as the table's 相关 column.

## [1.4.8] — 2026-11-04

### Changed

- Claude Code and Cowork print a hook message as plain text, so the recall
  table there is now box-drawn and padded by terminal display width (CJK
  counts two columns), with no Markdown marks. Codex keeps the Markdown table
  inside the organization block.

### Added

- `amh-native claude-mod --output DIR` writes a Claude Code mod that draws each
  recall as a transcript row: a colored title and one-line summary, with the
  call, parameters and table behind an expand/collapse button. The mod marks
  the sessions it draws for; the hook then writes its view to
  `recall-view/<session>.json` instead of a message, and a stale mark brings
  the plain message back.

## [1.4.7] — 2026-11-04

### Added

- Semantic retrieval on small CPU-only Hubs: a quantized ONNX encoder
  (onnxruntime + tokenizers, no PyTorch) loaded from a model directory with an
  `amh-semantic.json` manifest. The deploy pins jina-embeddings-v2-base-zh by
  revision and sha256; the workflow stages verified files when the Hub cannot
  reach Hugging Face, and a failed fetch leaves the Hub on lexical ranking.
- `serve` maintains the semantic index itself, encoding only new or changed
  memory and rule revisions; health reports `semantic.state` and advertises
  `semantic-v1` when ready. Parsed vectors are cached per index generation.
- A prompt-time relevance gate (hook channel only): candidates below the
  model's calibrated floor or too far below the best match are not injected,
  except literal identifiers from the prompt that keyword search ranked high.
  On a replica of the organization corpus this moved top-1 from 65% to 85.5%,
  top-3 from 76% to 98.2%, and unanswerable prompts that still received
  memories from 12/12 to 2/12.
- The prompt hook shows what it did: the call, its parameters and a table of
  every recalled memory with rank, source, author, revision, routes,
  similarity and delivery state. Claude Code and Cowork show it as a message,
  Codex inside the injected block. `amh-native recall-display` switches
  between table, summary and off; `amh-native hook-stats` summarizes recent
  recalls without prompt text.
- Acknowledgements such as "1", "好的" or "继续" are not searched, and host
  reminders, earlier organization context and attachment placeholders are
  removed from the search query; pasted material follows the member's words.

## [1.4.6] — 2026-11-03

### Added

- Independent per-device Hub profiles with background uploads to both Hubs and
  an explicit per-profile recall switch. Sync-only setup keeps product Hook/MCP
  ownership with one primary profile; receipt-based detach/activation preserves
  user settings and backup workers.
- Bounded Claude Code source-observation forwarding and a reviewed migration
  plan that preserves excluded-history hashes, missing-path denials and offline
  root quarantine without copying credentials, queues or cloud cursors.

### Fixed

- Recall policy is rechecked across Hook, MCP, cache and projection/asset-write
  boundaries. Interrupted projection writes roll back only unchanged owned
  content, preserving concurrent user edits.
- Background synchronization uses a configurable 30-second request timeout and
  32-record / 45-second soft pass budget, prioritizes memory, retains pending
  work and rescans sources on the next pass. Hook/MCP deadlines are unchanged.
  Records a Hub refuses move behind the rest of the queue, and every pass
  attempts at least one upload even when a slow handshake spent its budget.
- The shared Codex store is shown as `codex` in Hook, MCP, exports and admin
  views. Uploads keep the stored `codex-shared` label: a Hub treats an asset's
  product as immutable and Hubs before 1.4.6 do not know the new label, so
  renaming on the wire would strand every existing Codex memory.
- Projection manifests written before 1.4.6 carry no owner. The profile that
  projects into its own target adopts them, so upgraded projections keep
  updating and revocation or disabling recall still removes the copies.

### Compatibility

- Retains the published 1.4.4 insights and asking-context functionality. Asking
  hints are optional on recall/retrieve and retry without hints on an older Hub;
  asset uploads do not acquire this field. Upgrade Hubs for insight rollups.
## [1.4.5] — 2026-11-03

### Added

- **Warm recall path.** The background worker now hosts a resident recall
  service on a Unix socket in the state directory and keeps one connection to
  the Hub open (`WarmTransport`), so a prompt-time recall costs one round trip
  instead of a TCP handshake, a TLS handshake and the request. The hook tries
  the service first and falls back to the killable subprocess when no worker
  is running. The connection is kept warm only while hooks are using it.
- **Configurable recall budget.** `recall_timeout_seconds` in `config.json`
  (or `AMH_RECALL_TIMEOUT`) sets the total prompt-time budget, default 1.2 s,
  clamped to 0.3–10 s. `amh-native recall-timeout [SECONDS]` shows or sets it
  and rewrites the installed hook entries so the host's own timeout stays
  ahead of the budget. `amh-native status` reports the effective budget and
  whether the resident service is listening. `hook-metrics.jsonl` lines gain
  `via` and `budget_ms`.

## [1.4.4] — 2026-11-03

### Added

- **Insight page `/native/v1/admin/insights` (capability `insights-v1`).**
  Recall usage rolled up by day from the ledger: the delivery funnel
  (hit → selected → packed → emitted → receipt seen → cited), counts per
  channel and per member, retrieval modes and degradation reasons, why
  candidates were dropped, which memories were actually delivered and to how
  many members, reuse across members per producer, and knowledge gaps (prompts
  that matched nothing, grouped by repeat). An observability matrix states per
  product surface which stages are observed, explicit, not recorded or
  unobservable, and the page shows the collection start date instead of
  counting earlier use as zero. Member-level figures are operator-only.
- Rollups live in the additive `recall_daily` / `asset_daily` tables. Days
  inside the trace TTL are recomputed from the raw traces (states keep moving
  as deliveries and feedback arrive); a day is frozen the moment its traces
  expire, so history outlives the 30-day ledger. Expiry now purges whole
  calendar days, so a trace can outlive the TTL by at most one day. `serve`
  refreshes the rollup hourly and the page refreshes it on demand when it is
  older than ten minutes.
- The recall ledger page filters by member, channel and day, shows member
  names, and accepts the Feishu administrator session
  (`/admin/login?target=recalls`); the operator page links to both pages.
- **Asking-side context (capability `asking-context-v1`).** `/recall` and
  `/retrieve` accept an optional `asking` block — hosting product, the
  project the device registered for its working directory, and a 16-hex
  digest of the session or research — stored on the trace as hints only:
  identity and scope still come from the device credential, and nothing is
  inferred when a hint is missing. The hook sends product, registered
  project and a hashed session id; the MCP server sends its `--product`
  label (the installer now writes one per client: claude-code, codex-cli,
  cowork) and a hashed research id. Both fall back to a request without
  the block when a Hub predates the capability. The insight page rolls
  recalls up by asking product and project, counts distinct tasks per
  asset, producer and member, and the ledger page shows where each recall
  came from.

## [1.4.3] — 2026-11-03

### Added

- **Organization recall now carries the ranking signals the local brain had.**
  Candidates are classified by knowledge nature (rule / preference / project
  state / fact / decision / reference / episode) with a provenance tier, dated
  from trusted times only (declared front matter, filename date, then file
  mtime as a last resort, server observation never standing in for content
  time), re-ranked by at most −5% / +3% by a forgetting curve plus
  delivery/citation/support/contradiction signals, and retired only on an
  explicit `superseded_by` that names an existing asset or a past `expires`.
  Opt-in Hopfield association (`serve --hopfield`, `--hopfield-beta`,
  `--hopfield-min-score`) adds indexed neighbours neither route surfaced; the
  thresholds are uncalibrated defaults. `serve --decay/--no-decay` and the
  environment variables `AMH_NATIVE_DECAY` / `AMH_NATIVE_HOPFIELD` control
  both stages (flags win over the environment).
- **Every recall leaves a trace.** `retrieve` and the new hook channel
  `POST /native/v1/recall` record, per candidate, the route ranks, each
  stage's before/after rank and score, and the delivery state (candidate →
  selected → packed → emitted → confirmed → cited, or why it was dropped).
  Disabled stages read 「未启用」. Traces hold identifiers and titles (at most
  50 candidates, titles cut to 80 characters), never bodies, and expire after
  30 days. See them with `amh-native recall-trace`, `GET /native/v1/recalls`
  (this device), the operator page `/native/v1/admin/recalls` with its data
  endpoint `/native/v1/admin/recalls/data`, or `trace_id` in
  `search_org_memory`. Privacy: a trace keeps the first 120 characters of the
  query by default so operators can debug recall; the admin setting
  `recall_query_preview=off` masks it for new and old traces alike.
  `health.capabilities` gains `recall-parity-v1`; the Hub schema gains the
  additive tables `recall_signals` and `recall_events`, the client state the
  tables `emitted_traces` and `recall_reports`.
- **Feedback loop.** `amh-native recall-feedback` / `POST /native/v1/recall/feedback`
  accept cite / support / contradict / confirm for a trace's candidates; the
  hook reports packed / emitted / confirmed / budget / covered through the
  worker. Delivery events are idempotent per state, a member's feedback on a
  candidate counts once per event type, a candidate keeps at most 32 events,
  and a new revision resets the asset's contradiction count. Signals feed the
  next ranking.
- Clients upload bounded `source_meta` (mtime, declared front matter subset,
  `[[wikilinks]]`, headings) with each document; a Hub that predates the
  field is detected via `health` capabilities and the field is dropped. The
  mtime is not part of the content hash, so a re-scan that only changes
  mtimes creates no new revisions. The Cowork plugin bundle gains
  `facets.py`.

### Changed

- The prompt-time hook no longer uses the lexical-only `/search`: it uses the
  hybrid recall channel limited to memory and rule assets, so transcript
  chunks stop crowding out curated memory. `/recall` returns only the fields
  the hook packs, with bodies capped at 64 KB each and 512 KB in total. Old
  Hubs or gateways answering 404 / 405 / 501 fall back to `/search`; a 403 on
  `/recall` alone no longer revokes local state.
- `hook-metrics.jsonl` lines gain `channel`, `trace_id`, `hits` and `packed`.
- The recall ledger page `/native/v1/admin/recalls` accepts the 1.4.2 Feishu
  administrator session; a pasted host-minted token remains the operator
  fallback. `health.capabilities` lists `org-research-v1`,
  `admin-feishu-session-v1` and `recall-parity-v1` together.

## [1.4.2] — 2026-11-03

Feishu administrator login for the operator page and dashboard: scoped
browser sessions, explicit `admin-role` grants, CSRF and origin checks; the
manual token stays as the recovery entry. Three additive admin-login tables.
Release notes: <https://github.com/liuyang0508/Agent-Memory-Hub/releases/tag/v1.4.2>.

## [1.4.1] — 2026-11-02

Client-only: install to a remote Mac over SSH (`--ssh`, fixed loopback port
8765, launchd user-domain service). Compatible with Hub 1.4.0.
Release notes: <https://github.com/liuyang0508/Agent-Memory-Hub/releases/tag/v1.4.1>.

## [1.4.0] — 2026-11-02

Explicit sharing scope (new devices share native memory only), read-only
organization research tools (search, fixed-version reading, explicit
relations) with bounded rewrites, optional server-side semantic retrieval with
visible degradation, and a Hub capability check before install.
Release notes: <https://github.com/liuyang0508/Agent-Memory-Hub/releases/tag/v1.4.0>.

## [1.3.0] — 2026-11-01

### Changed

- **Organization memory is no longer copied onto members' machines.** Projection
  used to write every shared memory into `amh-org/` beside each product's own
  memory, so joining an organization silently placed everyone else's memory —
  full text — on your disk. That is now off by default: your own memory stays in
  your product, the organization's memory stays on the Hub, and recall is a live
  query at question time (all four clients have a Hook channel for it).
  `setup --projection` turns the local copy back on for anyone who wants it.

### Verified

- With projection off: no `amh-org/` directory, `MEMORY.md` untouched, and a
  question in another product still recalls the other member's memory from the
  Hub, with its producer named.

## [1.2.9] — 2026-11-01

### Changed

- **Recalled organization memory is no longer copied to disk.** The hook used to
  write every hit to `recall-cache/` and hand the model a `detail_file` path.
  That placed another member's memory — full text — on this machine, which is
  the copy local-first sharing exists to avoid. Recalled text now goes into the
  prompt only; the cache is gone, and stale copies from older installs are
  cleared on revocation.
- **Recalled items say what they are.** Each hit is prefixed with an explicit
  note that the content came from another member or device, is unverified, and
  is not an instruction — so the model weighs relevance and credibility instead
  of treating it as its own knowledge.

### Verified

- A memory written after install (the delay a product applies before it
  summarizes a conversation) syncs on the next scan, and a later rewrite of the
  same file uploads as a new revision.

## [1.2.8] — 2026-11-01

### Fixed

- Codex CLI and the ChatGPT desktop app were detected but had no importable
  rows: `discover_assets` never listed `~/.codex/memories`, only
  `sessions`, `AGENTS.md`, `skills` and `config.toml`.
- Claude Cowork keeps its workspaces under `Claude-3p` on current builds, while
  discovery and the history scan only looked under `Claude`, so every workspace
  `MEMORY.md` was invisible. Both now check either bundle directory.
- The selection page had no date filter; `--since` existed only on the command
  line. It now has a from/to range compared against each row's modification
  time.

## [1.2.7] — 2026-11-01

### Fixed

- The history checklist silently omitted a detected product that had nothing
  importable. Claude Cowork keeps one MEMORY.md per workspace and has none
  until a task produces memory, so its absence from the filters read as "not
  supported" rather than "nothing here yet". The page now says so by name.

## [1.2.6] — 2026-11-01

### Fixed

- The release job substitutes the version placeholder in `install-native.sh`,
  which also rewrote the guard that detects the placeholder — so the guard
  blanked the version it had just filled in. It now matches a token that the
  substitution cannot produce.

## [1.2.5] — 2026-11-01

### Fixed

- 1.2.4 published the wheel under an unversioned alias, and pip rejects a wheel
  filename without a version. The release now bakes the tag's version into
  `install-native.sh`, so it fetches `agent_memory_hub-<version>-py3-none-any.whl`
  by name and the alias is gone.

## [1.2.4] — 2026-11-01

### Fixed

- 1.2.3 downloaded the wheel to a `mktemp` name, which pip rejects as an
  invalid wheel filename. The published name is kept inside a scratch
  directory instead.

## [1.2.3] — 2026-11-01

### Fixed

- 1.2.2 could not install from the published release: `pip` does not accept
  extras on a URL, so `…/agent_memory_hub-py3-none-any.whl[web]` was read as a
  literal path and returned 404. The installer now downloads the wheel and
  installs it by path.

## [1.2.2] — 2026-11-01

### Changed

- **Organization mode installs from a wheel.** It used to clone the whole
  repository to `~/agent-memory-hub` and install it editable, which put a source
  tree on the member's machine and made the running client follow whatever they
  edited there. The release now builds a wheel, `install-native.sh` installs
  that, and `--org-memory` is dispatched *before* the bootstrap, so the mode no
  longer triggers a clone at all. Existing installations keep their state
  directory; re-running the command upgrades in place.

### Added

- **Browser checklist for history import.** A real machine holds over a thousand
  candidate files, and "1,3,5-9" is not a decision anyone can make from a
  terminal list. The rows now open as a local page with filters for keyword,
  product, kind, risk and modification time, per-row checkboxes, and bulk
  "clean / all / none". Nothing leaves the machine until the member confirms,
  and a blocked row is refused even if the request asks for it.
- **`--since` filter** on import, accepting `2026-10-01` or
  `2026-10-01 09:30:00`.

### Fixed

- ChatGPT desktop was reported as having no importable memory. Its settings
  page ("Codex 记忆 → 本地主机") manages the Codex store at `~/.codex/memories`,
  which is what the client now reads.
- `setup` opened the checklist page even with no terminal attached, where
  nothing could click it, and blocked forever. It now skips the import in a
  non-interactive environment instead of hanging.

## [1.2.1] — 2026-11-01

### Added

- **One-shot server install** — `deploy.sh` stands up a Hub on an
  organization's own host in one run: python and account setup, a versioned
  release directory, `feishu.env` at root:amh-native 0640, the systemd unit,
  and a readiness probe that reports whether Feishu login actually came up.
  It prompts for the App Secret on `/dev/tty`, so `curl | sudo bash -s --`
  keeps the secret out of argv and shell history. Re-running upgrades in place
  without touching the database; a host without systemd is rejected before
  anything is written. Published as a release asset.

### Changed

- The quick start leads with that command and separates team and personal
  installs; step numbers, per-card captions and the intro paragraph are gone.

### Fixed

- The quick start heading's descender was clipped: `.redesign-section h2`
  outranked `.install-title`, so the tight line-height still applied to
  gradient text and cut the g in "Agent" flat.

## [1.2.0] — 2026-11-01

### Added

- **Organization memory mode** — a self-hosted, single-tenant Hub that shares
  native memory across members' Claude Code, Codex CLI and Cowork installs.
  Installed with `install.sh --org-memory --hub <url>`; `--native` still works.
- **Feishu enterprise login** — no invite codes. The member authorizes in the
  browser, then the reusable device credential is fetched by the local process
  over loopback, so only a 120-second one-use ticket ever reaches the browser.
  `redirect_uri` comes from deployment config, never from a request header.
- **Per-file history import** — every pre-install memory file is listed with its
  product, kind, size and a privacy verdict before anything leaves the machine.
  Blocked files (keys, secrets) are never uploaded; `clean` skips the 185
  flagged ones the same way.
- **Operator page** — `/native/v1/admin` with a host-issued token to view
  members and devices, revoke a device, and set the Feishu credentials.
- **Idempotent server upgrade** — `deploy/native-memory/upgrade.sh` installs to
  a versioned release directory, swaps the symlink atomically, and rolls back
  on a failed health check.

### Fixed

- The upgrade script's `EXIT` trap fired on success too, so every successful
  upgrade immediately rolled itself back and exited 1.
- ChatGPT desktop was reported as owning `~/.codex/memories`, which would have
  labelled Codex memories as ChatGPT's. It keeps memory server-side and has no
  local store.
- `all` included blocked files, promising an upload that is always refused.
- `install.sh` ignored unrecognised options, so asking for a mode it did not
  have silently produced a full legacy install.
- `install-native.sh` was stored without its executable bit, breaking the
  documented `./install-native.sh` invocation after a fresh clone.
- The official site had no organization-memory section: it was authored on a
  branch that was never merged.

### Added

- **Stage 1 governance gates** — Python 3.11/3.12, hook, security,
  benchmark-integrity, and real Docker smoke checks are now required on `main`;
  the Docker smoke covers authenticated API access and persistent-volume restart.
- **Unified Injection Gateway** — MCP, SDK, CLI/hooks, brief, and Web prompt
  surfaces now use one fail-closed policy and bounded ContextPack contract.
- **Dual-route recall candidate pipeline** — routed hook recall combines term
  BM25 with complete-question semantic retrieval and a Unicode-aware raw BM25
  fallback while retaining one Gateway authorization boundary.
- **Consolidated hook preflight** — a dependency-free payload parser and one
  verified preflight process now handle runtime event, live prompt, normalization,
  and multimodal evidence before routed recall; whole-process or protocol failure
  retains the evidence-preserving legacy fallback.
- **Raw-NUL input integrity** — the hook now stores original stdin in a private
  mode-0600 file, replays the same bytes to parser/preflight/fallback, and removes
  it on HUP/INT/TERM/EXIT; payload bytes no longer pass through a shell variable.
  Parsing rejects decoded NUL recursively, parser/preflight are managed children,
  protocol pollution uses derivation-only fallback, and a nonzero preflight uses
  full fallback. Empty-prompt attachments retain multimodal evidence and recall.
- **Six-layer recall quality gate** — the stable `recall-quality` CI job reruns
  the versioned calibration, heldout, sanitized production replay, and 41-case
  safety contracts, then rejects stale committed retrieval, admission,
  answerability, temporal, abstention, or Gateway injection evidence.
- **Fresh real-Hook recall evidence** — the same required job now sends every
  Hook-applicable production replay case through the real `inject-context.sh`,
  independently verifies a commit/Hook/corpus/config-bound manifest, and uploads
  the low-sensitivity artifact even on failure. Surface contracts unavailable to
  UserPromptSubmit are reported as `not_applicable`, never as Hook PASS.
- **Multi-agent adapter governance** — all 16 adapters publish versioned
  manifests, six independent readiness states, seven-day evidence TTLs, stable
  lifecycle JSON/reason codes, AMH-owned repair/upgrade/rollback transactions,
  and isolated `shadow` → `canary` → `default` rollout with a per-adapter kill
  switch. The required `adapter-governance` job rejects stale reports, private
  evidence, broken lifecycle contracts, or core-isolation regressions.
- **Trusted lifecycle governance evidence** — a non-advisory
  `lifecycle-governance` workflow job replays committed synthetic supersession, pending,
  graph-drift, surface-parity, and privacy contracts. The content-manifest and
  fixture hashes are reproducible offline and do not claim that a real brain
  dry-run has completed. Its branch-protection required context remains pending
  external repository configuration.
- **Pending operational truth** — lifecycle readiness now reuses its bounded,
  trusted item metadata snapshot when classifying pending records, eliminating
  the second full item scan that could falsely exhaust the pending budget on a
  large brain. The report also exposes low-sensitivity reason counts, record-lock
  hygiene, and pending apply receipt-ledger health.
- **Auditable pending batches** — `memory sync-pending --summary-only` provides
  count-only preview/apply JSON. Explicit pending record/resolution apply writes
  prepared/completed append-only receipts under
  `runtime/pending-apply-receipts.jsonl`; an unmatched prepared entry is reported
  as incomplete when completion append fails. Standalone lock GC writes no
  receipt. Proven orphan record locks are collected only while the global
  pending queue lock is held and after a non-blocking exclusive inode check;
  held locks are preserved without failing GC by themselves.
- **Explicit pending resolutions** — `memory sync-pending` now previews
  per-record audit approval, exact-duplicate acceptance, legacy
  `feedback -> decision` conversion, and standalone orphan-lock GC. `--apply`
  is required to mutate; audit approval never bypasses secrets, duplicate
  acceptance writes no new item, and low-sensitivity record/resolution receipts
  expose digests and counts instead of raw record/item IDs or bodies.
- **Audited pending terminal dispositions** — explicit `--reject ID:reason`
  and `--quarantine ID:reason` resolve review-only or malformed/conflicting
  records without writing memory. Exact JSONL bytes move to
  `pending/resolved/`, active queue age clears, and prepared/completed receipts
  retain the bounded terminal action.
- **Signal-state consistency gate** — lifecycle readiness now fails when an
  active signal mixes terminal tags (`resolved` / `closed`) with active tags
  (`pending` / `blocked`) or closure text lacks a terminal tag. The same finding
  appears as a review-required lifecycle plan action; no semantic rewrite is
  auto-applied.
- **Low-confidence review cohorts** — readiness and maintenance planning now
  separate contested, explicit-source-gap, source-backed, and terminal
  low-confidence memories. `memory review attach-source` adds bounded file,
  HTTPS URL, commit, or memory references without changing confidence or
  bypassing explicit approve/reject.
- **Review evidence closure v1** — `memory review evidence-plan` binds exact
  MemoryItem Markdown digests to content-free source verification, distinguishes
  explicit source gaps from recoverable WriteService provenance, deduplicates
  resource/extraction evidence by provenance root, and routes every contested
  active review item to a contradiction Case or `contested_unpaired`. The
  self-authored `write_input` sidecar remains `traceability_only` and never
  counts as independent truth support. The
  locator-free aggregate is available to admins through Web API and Cockpit;
  preview never fetches URLs or changes confidence.
- **Digest-bound review resolution receipts** — `memory review resolve`
  previews an exact Markdown SHA-256 and requires that digest on apply. The
  mutation revalidates under the item lock and appends prepared/completed
  receipts around the review decision.
- **Contradiction cases** — overlapping pair findings are collapsed into
  deterministic connected-component case IDs, preserving pair/item counts and
  bounded evidence while avoiding duplicate per-pair review work.
- **Recoverable Case adjudication** — `memory review cases` and
  `memory review resolve-case` support digest-bound `select-authority`, `merge`,
  `coexist`, `dismiss`, and `defer` decisions across a whole contradiction Case.
  Containment now runs as a Case-level snapshot transaction with an append-only
  baseline ledger, so accepted resolutions restore the exact pre-containment
  tags/confidence and interrupted containment can be recovered explicitly.
  Semantic similarity alone no longer claims contradiction, project-scope
  terms are excluded from topic matching, and false positives can be dismissed.
  Multi-item
  Git snapshots and prepared/completed/rolled-back receipts make interruption
  explicit; changed items reopen the Case, and an unhealthy receipt ledger
  blocks new decisions. The same workflow is available to Web Admins.
- **Index operational truth** — `memory verify --format json` now compares
  Markdown and `items_meta` IDs, `.index-dirty` repair debt, and Markdown-derived
  supersession against `refs_graph` through an external read-only SQLite
  snapshot. Explicit `memory verify --repair` repairs only affected categories,
  keeps embedding initialization lazy, and requires a clean read-only `after`
  report.

### Changed

- **Breaking / next-major:** `memory sync-pending` 现在默认预览；只有显式
  `--apply` 配合 `--record`、`--safe-only`、逐 record resolution 或 standalone
  lock GC 才会执行对应变更。
  `memory govern apply-lifecycle` 同样默认预览，supersede、archive、
  keep-active、defer 与 revert-supersession 都要求显式 `--apply`。MCP
  `link_memories(..., relation="supersedes")` 也默认预览，只有
  `apply=true` 才提交；普通 relation 仍保持即时链接。
- `memory verify` 的默认 text 输出继续保留原有 item 数量与 missing/orphan
  明细，并新增 marker/graph 聚合；additive JSON 输出只包含数量和闭集 reason，
  不输出 item id、路径或正文。退出码从“ID 集合一致”收紧为三维状态全部 clean。
- `memory reindex` 现在默认清理 Markdown 真相层不存在的派生索引孤儿；
  诊断性 upsert-only 需显式使用 `--no-prune`。Markdown 扫描不完整时
  CLI 以非零状态退出、Web API 返回 409，并且都不会执行 prune。

- Realtime browser clients now use an HttpOnly session cookie; non-cookie clients
  exchange Bearer credentials for a short-lived, one-use realtime ticket instead
  of placing a long-lived JWT in `?token=`.
- Long-running SDK users should use `with MemoryClient(...)` or call `close()`.
  Existing containers must be rebuilt to receive the explicit Web/embedding
  dependencies and HTTP healthcheck. Existing installed hooks still require the
  documented `memory self-update --repair-hooks` and `memory doctor --fix` repair.
- **Breaking / next-major:** Python SDK search now enables
  `context_firewall=True` by default. Callers that explicitly need raw retrieval
  diagnostics must pass `context_firewall=False`; raw mode returns no ContextPack,
  firewall decision, or resource sidecar content.
- Hook cold paths do not load or download semantic models. Users upgrading from
  an older package must refresh/repair installed adapters to receive the routed
  hook; `AGENT_MEMORY_HUB_ROUTED_RECALL=0` only rolls back candidate generation.
- Existing users must upgrade the package and then run
  `memory self-update --repair-hooks` plus `memory doctor --fix`, or rerun the
  idempotent adapter install followed by
  `memory adapter install-verify <adapter> --format json`.
- Adapter install, repair, or upgrade success is no longer presented as runtime
  verification. `verified` additionally requires fresh runtime, context
  injection, and verification evidence; missing proprietary-client proof remains
  a machine-readable blocker.

### Fixed

- Qoder/QoderWork adapter repair now removes stale AMH Hook duplicates across
  temporary and old checkouts without deleting third-party handlers, converges
  MCP command/PYTHONPATH to the stable managed runtime, and fails closed on
  malformed runtime shims. Restart the client after repair before collecting
  fresh context-effectiveness evidence.
- Adapter context probes now reject stale transcript and timestamped injection
  cohort evidence. Old Qoder/QoderWork traces can no longer make a fresh
  `install-verify --context-probe` transaction pass.

### Release status

- **PASS:** committed stage-two quality is calibration 15/15, heldout 10/10,
  sanitized production replay 12/12, and 0 FP / 0 FN across the 41-case public
  safety fixture. Final candidate `b706ae0d915a3975919055367aa9d27a72baeda4`
  passed 连续两轮 independent 30-run hook gates: candidate p50/p95/max were
  1316.281/1390.675/1402.393ms and 1310.445/1382.689/1403.443ms, with 0 errors,
  0 timeouts, 0 protocol pollution, and 0 fallback in both rounds.
- The performance report retains the two pre-optimization tail-latency failures;
  it also preserves all earlier optimized candidates' successful rounds as
  superseded, not failed. Final PASS uses only the observable candidate's two
  consecutive confirmations after fallback-trace review.

### Security

- Prompt resource sidecars enforce public/internal sensitivity plus tenant
  visibility before content is loaded; a MemoryItem can no longer expand prompt
  access to a cross-tenant, private, or secret ResourceRecord.
- Runtime JSONL ledgers reject files above 64 MiB or 20,000 physical rows,
  reject unsafe final-file types, and retain requested tails with bounded memory.
- Public-hygiene scanning distinguishes SCP-style SSH remotes and RFC 5737
  documentation addresses without suppressing real email or RFC1918 findings.

### Planned

- **M5 Platform v0.1** — Next.js admin UI + multi-tenant sync server (L2→L3 bridge)
- **Adapter promotions** — move at least 2 of the 10 WIP adapters out of `WIPAdapter` based on user demand (priority candidates: cursor, continue_dev)
- **LLM-assisted semantic contradiction** — opt-in v1.2 feature behind `--llm-provider` flag

## [1.1.0] — 2026-05-31

Capability-restoration + deep-audit release. Driven by a brutal-honest capability
audit on 2026-05-29 (`docs(audit): deep capability audit report`) which found a
large body of valuable work stranded on divergent worktree branches plus a set of
P0–P3 correctness/security issues in the merged line. This release lands 33 commits:
the full P0–P3 audit remediation (33/35 items, 2 deferred), restoration of the
d3 knowledge-graph web UI and its `[[wiki-link]]` edge derivation, graceful
degradation / offline self-check, and integration of the
abstraction/consolidation/tiering/entities + Obsidian-wiki feature set
(schema 0.3 → 0.4). Capability ledger restored to 21/21; full suite green.

### Added

- **Schema v0.4 — abstraction axis.** `MemoryItem.schema_version` 0.3 → 0.4,
  adding the abstraction / consolidation / tiering / entities fields and Obsidian
  `[[wiki-link]]` interop. Backward-compatible: existing v0.x items load unchanged.
- **d3 force-directed knowledge graph** restored in the web admin (had regressed
  off the main line), now deriving edges from both `refs.mems` and
  `[[wiki-link]]` body tokens (≈133 edges on the live pool: 76 refs + 57 wiki).
- **Graceful degradation + `memory doctor --offline`** (task #15) — core read
  paths and a self-check command operate with no network and no embedder.
- **Token-budget tiered read** (P1-8) and **git brain snapshots** (P1-9).
- **Multilingual embedder** (P3-1), **`reindex --prune/--verify`** (P3-2), and a
  **`migrate`** path (P3-4).
- **`GET /api/auth/needs-init`** first-use detection + test (web onboarding).
- **`?` keyboard-shortcuts cheatsheet**, health score-ring gauge, and
  type-colored activity heatmap restored in the dashboard.
- **Smoke / conformance tests** for previously-untested CLI commands (QC test-gap
  closure), plus a regression guard for `dashboard.html` inline-script integrity.

### Changed

- **`mutate_item` primitive** (P1-10) — scoped, index-aware item mutation.
- **CJK BM25 tokenization** (P1-1) and **components singleton + WAL** (P1-7).
- **rerank score normalization** switched to sigmoid (P1-5); test expectations
  updated accordingly.
- **`make_item_id` suffix widened 4 → 8 hex** to remove flaky id collisions under
  burst writes.
- **Adapter CLI surface** (P2-10) and **adapter robustness** (P3-6) hardened.
- Web admin login restored to the polished purple memory-graph design.

### Fixed

- **P0/P1 safe batch** — timezone validator, tenant guard, index-aware delete,
  archived-item skip, path-traversal guard, async webhooks, rerank sigmoid.
- **P0-3** — write path no longer routed through the audit gate.
- **BOM/CRLF tolerance** (P2-2) and **JSONL import resilience** (P2-4).
- **citation-rot scheme-less URL** handling (P2-9).
- **durable unlink** (P2-5) and **Obsidian sync fixes** (P2-8).
- **web robustness** (P3-8) plus assorted P2-3 / P2-7 / P2-11 / P3-5 fixes.
- Un-escaped template literals in `dashboard.html` login / snapshot JS.

### Security

- **SSE / WebSocket tenant scoping** (P2-12) — event streams are now filtered by
  tenant, closing a cross-tenant leak on the live channels.
- **Export injection hardening** (P2-6).
- **XSS escape in the user list** + `me`-highlight + copy-API-key restored without
  reintroducing the injection vector.
- **Confidence feedback** (P3-3) wired into the evolve / governance path.

### Documentation

- Deep capability audit report + capability ledger (21/21 restored), P2/P3
  apply-plan (conflict-free 21-item batch order), and a doc-currency pass (P2-13).

### Migration notes

- **No data migration required.** Schema 0.4 only widens the model; items written
  by 1.0.x continue to load. Run `memory reindex --verify` after upgrade to confirm
  the index matches the on-disk markdown.

## [1.0.1] — 2026-05-26

v1.1 polish release. Driven by a brutal-honest audit on 2026-05-26 that ran three
parallel tracks: (A) static review of M1–M4 source vs the 1011-line v1 tech spec,
(B) dogfooding the governance pipeline on 187 real brain-pool items, (C) 10-vendor
competitive research including Hermes' provider table, Tencent's
TencentDB-Agent-Memory, and rohitg00/agentmemory (17.7k★). Six BLOCKER fixes plus
six MAJOR fixes plus documentation rewrites — see commit trail on
`worktree-v1.1-polish` branch for the full history.

### Added

- **MCP server tools 4 → 7** — `delete_memory`, `audit_skill`, `audit_outbound`
  added per v1 spec §6.2.2. The earlier 4-tool surface silently violated the spec.
  ([B3])
- **`HubIndex.delete()`** — three-table cleanup (`items_meta` + `items_fts` +
  `items_vec`) so the new MCP `delete_memory` tool can fully evict an item.
- **`memory anti-drift --check-urls`** flag — opt-in real HTTP HEAD probing of
  each URL in old items. Default off keeps the command offline and
  proxy-friendly; opt-in upgrades confidence from 0.4 (age heuristic) to 0.95
  (verified 4xx/5xx/network-error). ([M5])
- **`ItemsStore.last_scan`** — `ScanStats` recording every item skipped during
  `iter_all`, so governance reports can surface parse failures instead of
  silently dropping items. ([M3])
- **`WIPAdapter`** base class — the 10 not-yet-implemented adapters now raise
  `NotImplementedError` from `install()`/`inject_context()` with a clear message
  pointing at the reference implementations, replacing the previous silent
  no-op stubs that returned hardcoded strings. ([M1])
- **Real install for `ClaudeCodeAdapter`** — atomically writes
  `~/.claude/settings.json` hooks (`SessionStart` / `UserPromptSubmit` / `Stop`)
  via tmp-then-rename. Idempotent via hooks-dir prefix detection; uninstall
  removes only hub-owned entries. ([M1])
- **Real install for `CodexAdapter`** — sentinel-bracketed
  (`<!-- BEGIN agent-memory-hub -->` / `<!-- END agent-memory-hub -->`) discipline
  block written to `~/.codex/AGENTS.md`. Idempotent in-place updates; uninstall
  preserves any user-authored content above/below the block. ([M1])
- **Two-pass dedup** in `governance/pipeline.py`: SHA-256 fingerprint
  (O(N), `severity=error`, suggests `refs.mems` supersession) followed by
  project-partitioned jaccard 0.8 (O(M² within project)). ([M4])
- **Conformance fixture set** — 10 items in `tests/fixtures/sample_items/`
  covering all 6 memory types + CJK in id + `+` in id + refs.tags
  forward-compat + numeric session id + tz-aware datetime + v0.5 `key:[]`
  YAML quirk. ([B6])
- **Conformance suite skip policy** — `tests/conformance/conftest.py` rejects
  any `skipif` whose reason does not declare an explicit env opt-in pattern.
  Silent skips become hard failures so CI cannot quietly pass without
  conformance coverage. ([B6])
- **`LOCAL_BRAIN_CONFORMANCE=1`** opt-in test sweep against the developer's
  own `~/.agent-memory-hub/items/`. Replaces the earlier always-skipped
  test that produced false CI green. ([B6])

### Changed

- **`MemoryItem.id` regex widened** from `^mem-\d{8}-\d{6}-[\w\-]+$` to
  `^mem-\d{8}-\d{6}-[^\s/\\]{1,200}$`. Accepts CJK characters, `+`, and other
  non-whitespace non-path-separator identifiers. Forbids path separators to
  keep ids usable as filenames on all OSes. ([B1])
- **`Refs.extra`** changed from `forbid` to `ignore` — historical v0.2 items
  that wrote `refs.tags=[...]` no longer fail validation. Unknown fields
  silently drop on read; the canonical `tags` is still the item top-level
  field. ([B2])
- **`ItemsStore.iter_all`** no longer aborts the entire iteration when a
  single item fails to parse. Errors are recorded in `last_scan.skipped`
  and iteration continues, so governance / drift / evolve survive one bad
  file. ([M3])
- **`EvolveEngine._audit_gate`** rewritten — was calling
  `scanner.scan_file().passed` but `scan_file` returns `list[Finding]`
  (no `.passed` attribute). The previous broad `except` silently rejected
  every proposal. Now fail-closed only on critical/high severity findings;
  unexpected errors propagate instead of being swallowed. ([B4])
- **`DriftDetector` datetime arithmetic** unified to tz-aware. All four
  `datetime.now()` calls now use `datetime.now(timezone.utc)`, matching
  `item.created_at`. No more `TypeError: can't compare offset-naive and
  offset-aware datetimes` on real data. ([B5])
- **`DriftDetector._extract_tool_names`** rewritten with three positive
  patterns (CamelCase ≥4, versioned/namespaced identifiers, short ALL-CAPS
  acronyms) plus a curated stop-word list, replacing "any capitalized word
  minus 4 exclusions". Contradiction confidence dropped from 0.7 to 0.5 and
  labeled "heuristic — not semantic" in code comments. ([M5])
- **`_open_components` split** in `cli.py` — `_store_only()` opens just the
  markdown store (no embedder, no sqlite, offline-safe) and is used by
  `read` / `list-recent` / `govern run` / `anti-drift` / `inspect` / `evolve`.
  `_open_components()` retains the full stack for `write` / `search` /
  `reindex` which genuinely need vector search. Eliminates HuggingFace
  network access during governance operations. ([M2])
- **`GovernancePipeline.run`** now correctly unpacks `(MemoryItem, body)`
  tuples from `iter_all()`. Was treating tuples as items, crashing with
  `AttributeError: 'tuple' object has no attribute 'id'` on the first
  duplicate check.
- **`ROADMAP.md`** rewritten — collapsed two competing milestone numbering
  systems into separate M1–M7 implementation track and PL1–PL6 public-launch
  track. Removed the M2 "Playbook namespace alpha done" fiction (no playbook
  module existed in the codebase). Source of truth pointer corrected to the
  2026-05-19 1011-line tech spec.
- **`STRATEGY.md`** rewritten with honest 16-vendor matrix:
  - **rohitg00/agentmemory** correctly labeled as red-zone threat
    (17.7k★, 53 MCP tools, full audit + drift + evolve trio) instead of
    one-line dismissal.
  - **Tencent/TencentDB-Agent-Memory** correctly described as closest
    design twin (4.1k★ MIT, local-first, SQLite+sqlite-vec+Markdown,
    4-layer pyramid) instead of "top-down enterprise SaaS".
  - **OpenViking** "deep dive pending" replaced with actual research.
  - Differentiation thesis narrowed from "5 features no one else has" to
    "4-feature combination no one else hits" — md-as-source-of-truth ×
    防进-gate × MCP-neutral × md-interop adapters.
  - **Honesty log** section added documenting the falsified claim
    ("none of 14 do all three") so future readers see the correction trail.
- **`adapter` test suite** rewritten in `tests/unit/test_adapters.py` —
  hardcoded "every adapter returns a non-empty string" assertions removed
  (they were the exact tests that hid the stub behavior). New tests:
  registry/config validity across all 12; explicit
  `pytest.raises(NotImplementedError)` for each of the 10 WIP adapters;
  real install integration tests for claude_code + codex against
  monkeypatched HOME.

### Fixed

- **187 real brain-pool items now parse end-to-end** under the v1 schema.
  The pre-v1.1 schema rejected items with CJK ids, items with `+` in id,
  items with `refs.tags=[...]`, and items whose YAML titles had unescaped
  embedded quotes. Combined effect made `govern` / `anti-drift` / `evolve`
  100% non-functional on any pool with historical data. Now: full pipeline
  completes (660 fuzzy duplicates + 12 low-quality items detected, 8 evolve
  proposals all `audit-approved`).
- **`EvolveEngine` audit gate** was rejecting every proposal due to the
  API mismatch above. Now `audit-blocked=0` on the real brain pool —
  no more silent 100% blockage hidden by exception suppression. ([B4])
- **CI "167/167 v0.5 conformance" claim** was unverifiable — the test
  skipped when the local brain dir was missing, which is always the case
  on CI. Replaced with a fixture-based test that runs unconditionally,
  plus an opt-in real-brain sweep gated on `LOCAL_BRAIN_CONFORMANCE=1`.
  ([B6])

### Removed

- Silent no-op `install()` stubs in 10 of the 12 adapter files (replaced
  with explicit `NotImplementedError` via `WIPAdapter`).
- Pre-existing claim that the M2 Playbook namespace was alpha-shipped
  (no implementation existed). Now folded into `decision` + `artifact`
  types per the YAGNI list.

### Security

- **Skill audit pre-gate** integrated into `EvolveEngine._audit_gate`
  with fail-closed semantics: any proposal whose output_preview triggers
  a critical or high severity finding is blocked before write. Lower
  severities (medium / low) are advisory and allowed. ([B4])

### Performance

- **`_check_duplicates`** time complexity dropped from O(N²) global to
  O(N) sha256 pass + O(sum M_k²) within-project jaccard. On the live
  187-item pool this cut the full govern run from 7s to under 2s. ([M4])

### Documentation

- New `CHANGELOG.md` (this file).
- Rewrites of `ROADMAP.md` and `STRATEGY.md` covered above.
- Hook script paths in adapter install messages now show the absolute
  install location so users can audit before running.

### Migration notes

- **No data migration required.** v1.1 widens the schema, never narrows it.
  Items written by v1.0.x continue to load unchanged.
- **MCP clients**: the 3 new tools (`delete_memory`, `audit_skill`,
  `audit_outbound`) are additive. Existing tool calls keep working.
- **`memory anti-drift`** default behavior unchanged; pass `--check-urls`
  to opt into HTTP HEAD probing of URLs (slow, requires network).
- **Adapter callers**: code that called `XxxAdapter().install()` on a WIP
  adapter and ignored the return value will now raise `NotImplementedError`.
  This is intentional — see Removed section.

[B1]: https://github.com/agent-memory-hub/agent-memory-hub/issues/B1
[B2]: https://github.com/agent-memory-hub/agent-memory-hub/issues/B2
[B3]: https://github.com/agent-memory-hub/agent-memory-hub/issues/B3
[B4]: https://github.com/agent-memory-hub/agent-memory-hub/issues/B4
[B5]: https://github.com/agent-memory-hub/agent-memory-hub/issues/B5
[B6]: https://github.com/agent-memory-hub/agent-memory-hub/issues/B6
[M1]: https://github.com/agent-memory-hub/agent-memory-hub/issues/M1
[M2]: https://github.com/agent-memory-hub/agent-memory-hub/issues/M2
[M3]: https://github.com/agent-memory-hub/agent-memory-hub/issues/M3
[M4]: https://github.com/agent-memory-hub/agent-memory-hub/issues/M4
[M5]: https://github.com/agent-memory-hub/agent-memory-hub/issues/M5

## [1.0.0] — 2026-05-19

Initial v1 release. M1–M4 implementation milestones merged to `main`.

### Added

- **M1** — Python core: Pydantic v2 schema, typer CLI, FastMCP server,
  SQLite FTS5 + sqlite-vec hybrid retrieval (BM25 + vector RRF).
- **M2** — Skill Audit Engine: 30+ builtin rules, scanner, audit CLI,
  30+ malicious sample tests, outbound event log.
- **M3** — Anti-drift + Governance: dedup pipeline (jaccard), drift detector
  (contradiction / staleness / citation-rot / drift-cluster), 10K/100K bench.
- **M4** — Self-evolve engine + audit gate + 12 agent adapter
  **plugin architecture** (real-hook installation deferred to v1.1).
- L3 RBAC interfaces reserved (`tenant_id` + `auth_context` fields).
- STRATEGY.md with 14-competitor matrix (later expanded to 16 in v1.0.1).

[Unreleased]: https://github.com/agent-memory-hub/agent-memory-hub/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/agent-memory-hub/agent-memory-hub/compare/v1.0.1...v1.1.0
[1.0.1]: https://github.com/agent-memory-hub/agent-memory-hub/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/agent-memory-hub/agent-memory-hub/releases/tag/v1.0.0
