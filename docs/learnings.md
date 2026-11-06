# Learnings

A bot keeps what it learns in its repository: `memory/` (including `memory/learnings.md` and
`memory/decisions.md`) and `knowledge/`. Every change there is a git commit, and the **Learnings**
button beside the bot's name shows those commits as a history, newest first. A small number on the
button counts the updates since you last opened it.

Each update shows its commit subject, who made it and when, a link to the commit on GitHub, the files it
changed and, once the commit has been pushed, its diff. When the run that made it is known, the
update links to its source: the task, or the message in the thread, that the bot was working on. Two
more views show the current `learnings.md` and `decisions.md`, its working rules first (a `## ` section whose
heading says rules), then the newest entries: sections by the date in their heading, and dated `- ` entries by their
`added:` or leading date. An undated part stays under the dated one above it.

## One memory, in the repository

What a bot remembers between turns is its repository's `memory/`, which you read here and can edit. Each turn's
prompt tells the bot to read the working rules at the top of `memory/learnings.md`, follow them, save a rule it is
given there, and add other learnings at the top of the log. The runner turns off each harness's own memory on the
computer, so nothing steers a bot from outside its repository: Claude Code's auto memory
(`CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` on every turn, not only for bots with branches), Codex's memories
(`features.memories = false` on every thread) and the Gemini CLI's `save_memory` tool; a GEMINI.md the CLI saved
before is set aside before each turn, so no turn loads it.

What a bot saved there before is not lost. Before each turn, in whichever checkout it runs (its own or a task's
worktree), the runner copies what the checkout lacks of it into a "Working rules from Claude's memory on this
computer" section near the top of `memory/learnings.md`: each Claude memory with its whole text, whatever
`MEMORY.md` says beyond its links to them, and Gemini's saved memory. Each entry names its source, so a retry never
copies twice, while a same-named memory with other text, from another computer the bot ran on, is still copied; if
a source can't be read, nothing is copied. A flat log after it gets a `## Log` heading. A task turn's closing commit
keeps the copy; in the bot's own checkout its next memory commit does. The move is finished once the repository's
pushed history has it; after that an edit or removal there stands. The runner reads and writes these files as the bot, never with its own rights, and skips a memory file
that is a link. The old files are left in place, unread (`runner-rs/src/harness_memory.rs`).

A stateless bot keeps no memory at all: Counsel, by its template (`hubdb.STATELESS_BY_TEMPLATE`), or any bot whose config
says `stateless: true`. Nothing is copied into its checkout, its prompt says not to read or write `memory/`, `knowledge/`
or `state.md`, its memory folders are not committed for it, and Learn skips it. Each task is judged from
its own text (see [sessions](sessions.md)).

## Where it comes from

Git is the record; the history is a view of it. The server holds no checkout, so the computer
running the bot reports memory commits after each completed run and every five minutes
(`runner-rs/src/memory_history.rs`, `POST /api/v2/bots/{bot}/memory/report`). A report names commits by
SHA, so a retry, an outage or two computers reporting the same repository never make duplicates.
Only the computer the bot is assigned to may report for it.

- **Not pushed** marks a commit that is only on the bot's computer. Its diff is not sent until it
  reaches the repository, and a commit held back because it contains a secret never leaves the
  computer.
- **Learnings and Decisions** show `memory/learnings.md` and `memory/decisions.md` as the shared repository
  has them. Uncommitted or unpushed changes are not sent, and a bot with no shared repository sends commit
  subjects only. Each file may be up to 4,000,000 UTF-8 bytes. If it grows beyond that, its view warns
  that the file is too large and dates the saved copy, or says no saved copy is available. The next report
  of a file that fits replaces the copy and clears the warning.
- **Source.** The prompt asks the bot to end a memory commit's message with `Tico-Run: <run id>`. For
  a bot that is not shared, a commit made during a run is that run's even without the line. A run
  must belong to the bot or its family (an original and its branches share one repository);
  anything else is ignored. Older commits show their author and date without a source.
- **Privacy.** Anyone who can read the bot sees its memory history, as they see its files. The
  source shows only to a reader who may open that task or thread; everyone else sees the update
  without it.

Branches of a shared bot write the same repository, so a lesson one branch pushes shows in every
branch's history and reaches their next run after the repository syncs.

Update the server before its computers ([updates](updates.md)). Computers normally follow the server's
release. A newer runner manually connected to an older server may have large reports refused until that
server is upgraded; the runner retains those reports for retry.

## API

- `GET /api/v2/bots/{bot}/memory?before=&limit=`: `updates`, `unseen`, `documents`,
  `document_status`, `next_before`. Each path in `document_status` has `oversized` and
  `updated_at` (the saved copy's date, empty if none). Add `include_documents=false` to omit file contents
  while keeping their status. The button and history pagination use this; opening the panel fetches current files.
- `POST /api/v2/bots/{bot}/memory/seen`: you opened the history.
- `GET /api/v2/bots/{bot}/dream`, `POST /api/v2/bots/{bot}/dream`: see Dream below.

## Dream

**Dream**, at the top of the Learnings panel, asks the bot to tidy its own instructions and memory, as Claude Code's
`/dream` does: orient, gather what happened in its chats and tasks since its last dream (two weeks back the first
time), consolidate, then prune and index, keeping the working rules short. It may change any of those files, AGENT.md
included, without asking. Midway it may ask the person who started it up to three questions in one message, about the
business only (what to do, for whom, by which rule or priority), never for permission or about the dream itself, and
waits for the answer. A stateless bot keeps no memory and has nothing
to dream about (409).

A dream is an ordinary task (`backend/dream.py`): the bot owns it, the person who clicked asked for it, it carries the
label `dream`, and its body is the instructions. Starting one takes the rights to give the bot a task and goes through
the same checks as any task create; while one is open, starting again returns it (or 409 `busy`, without its
questions, to someone who may not read the bot). Starting one also records a `dream.start` event on the task; that
event, not the label, is what makes a task a dream, since anyone can label a task `dream`. It runs as a task turn with the
bot's own model, and its session resumes after the question. The question is the task's one `hub_task_ask`; ask
ingest never judges it, since the person started the dream to be asked. They answer it in the panel, on the bot's
page, where it shows until answered, or in Needs you; the answer wakes the bot as any answer does. While it works, the bot keeps
the task note at one short line on what it is doing now, and the panel shows that line and how long the dream has run
under "Dreaming…". The bot ends with
one commit of everything it changed, `Dream: ...`, and marks the task done with a short summary. The panel then shows that summary and
the commits whose source is the dream's task, and **Wake up** returns to the history. **Stop** closes the task.

`GET` answers `{dream: {task, status, state, questions, summary, doing, closed_at, started_at, auto, reason}}` for the bot's open dream
(on a shared bot, the caller's own branch, where POST files it), or else the newest one finished in the last day, or `{dream: null}`. `state` is `starting`, `dreaming`, `asking`
(an open question to the requester: `questions` is `{message_id, text, mine}`), `done` or `stopped`.

**Auto dream** is the same dream started without a click: the **Dream** step of the [Maintenance](maintenance.md) run, once a day, on by default. It starts one for each bot whose memory changed a lot since its last dream, and only then.
A bot is due when it is active, keeps memory, has no open dream, its last dream of either kind started at least 48
hours ago (a bot that never dreamed must be 48 hours old), and its memory since that dream's start, or 14 days back if
that is later, changed at least 200 lines over at least 10 memory turns. Counted: `memory/` except `memory/state.md`,
and AGENT.md, AGENTS.md and CLAUDE.md (the runner reports only `memory/` and `knowledge/` today, so instruction-file
changes do not count yet). A dream's own commits do not count, nor the commits of the retired Nightly memory tidy. At most "Most bots per run" start a
run (three by default), most changed lines first; a restart does not start them twice.
`backend/dream_auto.py` holds the rule.

The requester is the bot's nearest human manager (the first person above it in its reporting chain, if still on the
roster), else the company owner. The task is filed as a dream, with a `dream.auto` event under the system actor and
its reason ("Auto dream: 412 memory lines changed in 23 turns since 2026-10-28") as the first line of its body. The
panel shows that reason under the status line. Its instructions say nobody is waiting: the bot applies and commits what
it can settle, and only if it needs the requester it asks at most three business questions in one message, then makes a
second commit when they are answered. The requester did not start the dream, so the question is what they answer, and
it goes through the same exemption from ask ingest. **Automations › Maintenance** switches it off. Its **Run now** starts
the Dream step only when that step is due, as the schedule does; the bot rules above still apply.

## Learn (beta)

Learn is a Maintenance step, off by default and due at most once a day. It reads activity once, scores it with
Tico Decide, and keeps each bot's top items for that bot's next Update turn (`backend/maintenance_steps/learn.py`,
`backend/learnings.py`). No bot reads Slack or mail for this itself. Learn has its own source cursors; Run now
uses Maintenance's step rules.

The nightly timer and its System routines control are retired. Learn uses Maintenance's schedule and its own
"How often" setting (once a day by default). A saved learnings on/off choice carries over at cutover unless someone
has already edited Learn in Maintenance; an inherited default becomes off. The old read-only Automations mirror
is soft-deleted and its unfinished runs are skipped.

**What is read**, newer than the last run (the first run looks back 24 hours), at most 600 items:

- **Chats**: what people wrote in threads with bots. DMs (your Assistant, your Librarian
  DM) are not read. An item from a thread with bots may go only to those bots.
- **Tasks**: people's comments on tasks, and the note of a task that was finished. A private task's
  items may go only to the bots that are its owner or requester.
- **Slack**: people's messages in channels Tico is in (direct messages already arrive as DMs).
- **Mail**: only mail a teammate **sent from their own mailbox** (the sender's address is a person's
  and is the mailbox's own). Inbound mail and shared mailboxes are never read. The quoted reply is
  dropped.
- **Meetings**: live company meetings (not private, not pending review): the title and summary,
  never the transcript.

**Routing.** The decision model (the Tico AI Gateway when the server has a gateway key, then your own TypeSafe key if
you set one, then the team's own AI provider; with none of them the run shows "needs a decision model"; when the team's monthly decision spend cap is
reached, even partway through, the night sends nothing, shows "Skipped", and the same activity is read again on a later
night) answers, per item, whether it is
learnable and, per recipient, whether it is relevant (`questions/learnings-route.json`). An item goes
to every recipient scoring 0.6 or more, unless its learnable score is under 0.2; at most 15 items each,
highest first. The same 15-item limit applies across pending runs when Update sends its request. Recipients are the active bots other than BotOps, the Assistant and the Strategy Bot,
plus the Librarian for how the company works and the market, that had a run in the last 24 hours. A bot
synced in from another platform (Grok, Dots, Muse) never gets a run here, so it gets no packet.

**Delivery.** Items wait in `learning_deliveries` with no task and no Update batch id. The next Update request
carries a short "Learnings for you" block, with at most 15 one-line items and their sources. Sources are checked
again before sending: private tasks and restricted conversations stay out of a company session. Bot Home messages
require company-wide Read access and still pass each message's privacy checks. A bot with no
work gets a delivery-only turn and posts no update. No packet task is created.
Slack items require a stored public-channel event; private channels and unknown audiences stay out. Mailbox
access is restricted, so sent mail is excluded from Update delivery.

The request and its receipts share the `update_queue` id (`update_batch_id`). Retries reuse those items; a redo of
an old update consumes none. Each delivery keeps its original request message id for history and the latest delivery
request id for final-reply feedback. A redo changes neither. Pending deliveries
expire after seven days and count as expired. Selection checks
at most 100 candidate items; when several runs are pending, the highest scores go first and excess items count
as not sent. Original items stay readable on the Learnings page. The bot follows its memory working rules,
commits its changes and reports `learnings_saved: n`, including zero, in its final reply or the update's structured
fields. Saved counts are attributed to the oldest contributing run first. Drops count only reported items;
an absent or invalid count stays unreported. Learn's run details, its Maintenance settings and `learnings.beta_stats(c, days=7)` show reads,
items sent per bot, saved, dropped, unreported, not sent, expired and cost. Decide meters calls under the monthly cap,
including paid failures; a capped run sends nothing and keeps its cursors.

**The page.** Learnings lists runs by day, including old packet tasks and new Update deliveries. It shows memory
commits from the delivery turn, the Librarian's doc and market changes during that turn, and delivery status.
The original request and turn define that history even after an Update redo or queue cleanup.
A bot's changes show only to readers who may read that bot. An item's text shows only when the reader may read
its source today (the thread, task, mailbox or meeting); anything else is counted as private. Old packet tasks
stay readable.

- `POST /api/v2/learnings/run` (owner or admin): Maintenance Run now with `step=learn`; an off step stays off.
- `POST /api/v2/system-routines/learnings/run` is the same Maintenance Run now. Both routes return `{run, started}`, join a live run and accept an Idempotency-Key.
- `GET /api/v2/learnings?before=&limit=`: nights, newest first.
- `GET /api/v2/learnings/{id}`: one night's sections.
- `GET /api/v2/learnings/{id}/packets/{bot}`: one recipient's packet.

## Suggest

Suggest is a free Maintenance step: no billing or model calls. The owner opts in with **Share stats for suggestions**
under Settings > General > Privacy. It starts off and cannot be enabled on a plain open-source install; a hosted
install or Tico Gateway connection is required, and fetching also needs the install's Gateway key. Demo and rehearsal
installs send nothing. This consent is separate from the anonymous usage count.

At most once every 24 hours, Suggest posts `snapshot_version: 1`, active bot counts (total and by BotOps, Goal Manager,
Engineering Lead and Finance template), the open task count and enabled custom automation count to the Gateway's
`/v1/suggestions`. Spend is never sent: when a company monthly usage pool cap is set, its spend and cap are read only by the
local condition check at delivery.
The saved model Gateway's `/v1/gateway` suffix is removed to reach this service route. Unknown facts are omitted.
Names, messages, task text and credentials are never in the JSON; the install key authenticates
the request. A timeout or invalid response fails the step without storing suggestions.

Suggestions wait in a hidden install-owned table. Update resolves each role to an active bot by its template and checks
the condition against fresh counts immediately before sending. A missing operand makes a comparison false, including
`ne`; a stale suggestion becomes `condition_false`. A missing role stays pending. Finance uses the existing Finance bot
template priority. `person_money_cap` uses spend alerts' company-owner lookup and keeper notice path; it waits if that
person cannot be resolved or fresh local monthly spend is unknown or does not exceed the company pool's monthly cap.
A zero-work bot gets a delivery-only turn, and redos consume nothing. Retries re-check conditions and role ownership.

Each bot delivery carries at most ten suggestions and binds to the `update_queue` request id and original delivery
message id. A bot posts acted-on ids as `suggestions_seen`, with
`delivery_batch_id`; another bot or batch cannot acknowledge them. A completed turn without acted-on ids counts as
`delivered` only for the original delivery message; a redo's turn cannot settle it. An unsettled receipt past its
cooldown becomes `expired`. Fingerprints cannot repeat during cooldown;
unreported outcomes are retained before renewing a fingerprint while reporting is on.

**Report suggestion outcomes** starts on. Both privacy switches must remain on for either Gateway call. The step reports
at most 100 receipts to `/v1/suggestions/outcomes`, containing only `suggestion_id` and `outcome` (`delivered`, `acted`, `dismissed`,
`expired` or `condition_false`). Failed reporting keeps receipts unacknowledged for a later run. Both Gateway calls run
outside write transactions. Privacy settings are owner-only at `GET/PUT /api/v2/system/suggestions` and discoverable
through the existing `hub settings` and MCP settings catalogue.
