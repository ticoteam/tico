# Learnings

A bot keeps what it learns in its repository: `memory/` (including `memory/learnings.md` and
`memory/decisions.md`) and `knowledge/`. Every change there is a git commit, and the **Learnings**
button beside the bot's name shows those commits as a history, newest first. A small number on the
button counts the updates since you last opened it.

Each update shows its commit subject, who made it and when, a link to the commit on GitHub, the files it
changed and, once the commit has been pushed, its diff. When the run that made it is known, the
update links to its source: the task, or the message in the chat, that the bot was working on. Two
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
or `state.md`, its memory folders are not committed for it, and the nightly learnings skip it. Each task is judged from
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
  source shows only to a reader who may open that task or chat; everyone else sees the update
  without it.

Branches of a shared bot write the same repository, so a lesson one branch pushes shows in every
branch's history and reaches their next run after the repository syncs.

Update the server before its computers ([updates](updates.md)). Computers normally follow the server's
release. A newer runner manually connected to an older server may have large reports refused until that
server is upgraded; the runner retains those reports for retry.

## API

- `GET /api/v2/bots/{bot}/memory?before=&limit=`: `updates`, `unseen`, `documents`,
  `document_status`, `next_before`. Each path in `document_status` has `oversized` and
  `updated` (the saved copy's date, empty if none). Add `include_documents=false` to omit file contents
  while keeping their status. The button and history pagination use this; opening the panel fetches current files.
- `POST /api/v2/bots/{bot}/memory/seen`: you opened the history.

## Nightly learning run

Once a night, at or after 3:00 (Pacific, the same clock as daily updates; an owner or admin moves the time or
switches it off in Settings > System), Tico reads the day's
activity once and hands each recipient what is relevant to it (`backend/learnings.py`). No bot reads
Slack or mail for this itself.

**What is read**, newer than the last run (the first run looks back 24 hours), at most 600 items:

- **Chats**: what people wrote in chats with bots. Personal rooms (your Assistant, your Librarian
  room) are not read. An item from a chat with bots may go only to those bots.
- **Tasks**: people's comments on tasks, and the note of a task that was finished. A private task's
  items may go only to the bots that are its owner or requester.
- **Slack**: people's messages in channels Tico is in (direct messages already arrive as chats).
- **Mail**: only mail a teammate **sent from their own mailbox** (the sender's address is a person's
  and is the mailbox's own). Inbound mail and shared mailboxes are never read. The quoted reply is
  dropped.
- **Meetings**: live company meetings (not private, not pending review): the title and summary,
  never the transcript.

**Routing.** The decision model (the Tico AI Gateway when the server has a gateway key, then your own TypeSafe key if
you set one, then the team's own AI provider; with none of them the night shows "needs a decision model"; when the team's monthly decision spend cap is
reached, even partway through, the night sends nothing, shows "Skipped", and the same activity is read again on a later
night) answers, per item, whether it is
learnable and, per recipient, whether it is relevant (`questions/learnings-route.json`). An item goes
to every recipient scoring 0.6 or more, unless its learnable score is under 0.2; at most 40 items each,
highest first. Recipients are the active bots other than BotOps, the Assistant and the Strategy Bot,
plus the Librarian for how the company works and the market, that had a run in the last 24 hours. A bot
synced in from another platform (Grok, Dots, Muse) never gets a run here, so it gets no packet.

**Packets are tasks.** Each recipient gets one private task, "Nightly learnings: <date>", with the
instructions and its packet: each item's source, link, the quoted text, its score and who else got it.
A new night closes the bot's earlier packet if no run has picked it up yet, so unread nights never pile up.
A bot changes only its own repository and commits with `Tico-Run:`; a change to its instructions or
settings goes to BotOps as a task. The Librarian edits docs directly (every edit is a version) and
reports market facts with `hub market report`.

**The page.** **Learnings** in the account menu lists the nights. A night shows Bots (memory commits
made by the runs that worked its packet task), Docs (the Librarian's doc versions while its task was
open) and Market (its market changes then), each change with its source and diff. A bot's changes show
only to readers who may read that bot. A packet's text shows only when the reader may read the source
today (the chat, the task, the mailbox, the meeting); anything else is counted as private.

- `POST /api/v2/learnings/run` (owner or admin, the same as Run now on Nightly learnings in Settings > System): tonight's run now; 409 when it is running or already ran.
- `GET /api/v2/learnings?before=&limit=`: nights, newest first.
- `GET /api/v2/learnings/{id}`: one night's sections.
- `GET /api/v2/learnings/{id}/packets/{bot}`: one recipient's packet.

## Nightly memory tidy contract (developer integration)

`backend.memory_tidy` is the read-only proposal boundary. It does not schedule a
pass, call a model, create a reviewer card, persist a run, or apply Keep. Those callers
must enforce the pilot list, named model without fallback, low effort and the $50
estimated-cost stop before calling a model. Ordinary bot/task model settings remain
unchanged. This module requires no database or model spending.

### Inputs and child isolation

The orchestrator supplies a pushed snapshot mapping paths to UTF-8 text, including
`AGENT.md` for the trusted conflict check. `build_packet(snapshot, since=...)` returns
only rules, recent log entries and entries cited by rules; only `model_input()` may
be sent to the edit-producing child. Do not send the snapshot, `Packet.all_logs`, run
transcripts, evidence files or arbitrary knowledge. The initial pass uses `since=None`.
Later passes should supply `previous_learnings`, the exact learnings snapshot at the
last completed kept or discarded pass, to select changed undated notes. Without that
snapshot, undated log history fails closed. Same-day dated notes remain included
because their dates do not identify which appeared after a timestamp. Source dates,
entry IDs and exact heading titles select cited logs. Exact originals cited by source
ID are also read from the archive; unrelated archive contents and external references
are never sent to the child.

The exact rule headings are `Working rules` and `Standing rules`, optionally followed
by parenthetical guidance. Top-level Working rules end at the first dated heading.
Second-level sections end at the next undated peer or shallower heading; dated peer
headings remain mixed notes inside that section. An explicit Log, Learning log or
Dated learnings heading ends rules at any depth. Dated headings/bullets inside
second-level rules are logs; a subsequent undated top-level bullet resumes rules. Imported
`Working rules from ...` headings are log, never rules. Unrecognized prose remains
untouched. This is a Markdown-entry contract, not a generic Markdown rewriter.

Run the child without write tools, in a disposable checkout with no publication or
main-branch credentials. Independently collect before/after manifests of **every**
checkout path: tracked, untracked, ignored, symlink targets and file modes. Manifest
values must identify content AND kind/mode. Pass these to `evaluate`; any difference,
even under memory, flags the proposal. Always discard the whole child checkout at
settlement, on success, failure or cancellation. An attachment is returned through the
child result channel, never written into that checkout.

Criterion 24 additionally requires the runner to skip `finish_place`'s automatic
memory/knowledge commit and push for a pass check. Suppress the ordinary
"committed for you" instruction; the pass prompt explicitly prohibits all file
writes. Do not use stateless mode: the child must retain its permitted memory reads.
Manifest validation alone cannot stop an automatic commit that happened before
validation. These runner controls are a prerequisite supplied by the platform slice,
not an effect of this pure module. Child output is consumed by code and the child is
closed without an ordinary parent wake turn, per the engineering run-path decision.
The module trusts these manifests, not a child's claim that it wrote nothing.

### JSON attachment

Return one object with `edits` and `diary` (at most three lines). Numbers are consecutive
integers starting at 1. Each edit contains:

```json
{
  "number": 1,
  "operation": "generalise",
  "targets": [{"entry_id": "packet entry ID", "old_text": "exact packet text"}],
  "replacement": "- One rule. [sources: packet entry ID]\n",
  "sources": ["packet entry ID"]
}
```

Operations are `merge`, `generalise`, `resolve_contradiction`, `retire`, `archive_log`,
`move_fact` and `move_log`. Targets are individual whole entries from the packet;
repeated targets, wrong old text and whole-file rewrites fail. Every replaced entry
must be a source. New rules list each source ID in their replacement text, retaining
links to exact originals. IDs use exact content and its occurrence number, so changes
above an entry do not change its identity. Each archived original records that same
ID and exact character length; `resolve_source(snapshot, entry_id)` resolves current
or archived text and validates archived content against the ID. Cited archived entries
have kind `source` and cannot be edit targets. Retire/archive/move-log edits have empty replacements.
A fact move supplies its unchanged original as replacement and a normalized
`knowledge/*.md` `destination`. Other operations cannot set a destination. Paths
containing traversal, backslashes or control characters are rejected.

### Checks and output

`evaluate(snapshot, raw_json, since, today, checkout_before, checkout_after,
conflict_check, previous_learnings)` applies anchored edits to an in-memory scratch
copy, archives every removed original verbatim, and computes a unified diff. Retired
rules include a retirement date and edit number. Dated-note moves insert
the original under the existing Log heading, creating one if needed. More than a quarter of actual working rules touched
flags the proposal; exactly a quarter is permitted. Dated notes do not enter that
count. Log entries strictly older than 30 days must be removed and present verbatim
in the archive before an offer. Thirty-day-old entries remain eligible to stay.
When a nonempty model proposal leaves historical old entries outside the packet,
code adds numbered `archive_log` edits for those entries, using exact originals.
The model never reads or targets that older history. The combined attachment, counts
and diff include these mechanical moves for review. Empty model edits remain
`no_change`, with no archival-only card. A kept nonempty proposal must archive every
old entry; the check resolves its exact framed archive record rather than a substring.

`conflict_check(agent_text, rules)` is a required trusted semantic check of both old
and proposed rules against AGENT.md. It returns a list of conflict descriptions. Its
absence, expected operational failure or any conflicts flag the proposal; the public diary reports only
the count. Do not substitute the edit-producing child's unchecked 'no conflicts'
claim. This module does not pretend lexical matching proves semantic consistency.
Unexpected checker exceptions propagate without returning an offer; the caller must
settle them as failed runs. The orchestration slice must provide this check before
enabling offers.

Token/key patterns share the company-file detector, including labelled and opaque
values. Newly introduced secret-shaped strings anywhere in the submitted JSON or
scratch changes are rejected unless already in memory. Flag results expose no raw
attachment, diff, scratch files or secret values. Existing secrets are not newly
introduced; this does not grant permission to transmit them to a model or person.

The server-generated diary contains operation counts, working-rule character sizes
before/after, the 20,000-character target and check outcome, in at most three lines.
The child diary is validated but never trusted for counts, budget or flags. Results:

| Status | Caller action |
| --- | --- |
| `offer` | Persist numbered attachment, diff and diary for the named reviewer; Keep remains a separate action. |
| `flagged` | Record the sanitized reasons and notify the reviewer; never show Keep or apply scratch changes. |
| `no_change` | Log `no change`; create no card and no edit attachment. |

No UI, API endpoint, schema, runner setting or everyday memory is modified by this
module. Caller integration and independent conflict checking remain separate from
these pure checks. Focused synthetic regressions live in `backend/tests/test_memory_tidy.py`.

### Nightly memory tidy reviewer cards

`backend.memory_tidy_cards.file_card` is the trusted filing seam. Call it in the
filing transaction with an `offer` from `backend.memory_tidy.evaluate`, the ledger
pass id/start, base memory commit, named reviewer actor, pass model and the ledger's
estimated USD cost (including subscription usage). GPT-6.1 Sol's current table
rates are $2 input, $0.10 cached and $10 output per million; filing calculates cost,
while cards preserve its estimate. Do not use a stand-in rate or “unmeasured”.
Skipped/flagged/no-change results create no card and do not replace an existing one.
Only this internal seam files a proposal; there is no client offer endpoint.
The pilot model floor forbids Haiku-tier passes; the card rejects Haiku-labelled
filings as defense in depth alongside filing admission. This never changes the
bot’s everyday model.

The persistent record is independent of the private parent/check tasks. File it
before closing both tasks. The named reviewer retains access after those closures
and the approval-expiry sweep; this is covered with synthetic tasks, not a live
pilot run. Filing writes no message, approval or job and does not wake the parent.

A human reviewer sees the card in Needs you. A bot reviewer uses
`hub api GET memory-tidy/cards` and `hub api GET memory-tidy/cards/<id>`; both
interfaces admit only that named reviewer, regardless of task ownership. Lists
omit large diffs; the detail read shows the checked diff, diary (at most three
lines), model, ledger estimate and base memory commit. The UI treats memory text
as plain text. Discard uses `POST memory-tidy/cards/<id>/decide` with
`{"decision":"discard"}` and an idempotency key, without touching the repository.

A new offered pass replaces the previous open card for that bot; pass ids make
retries harmless and pass starts reject late older offers. Unreviewed cards leave
the list at two business days using the existing team Pacific weekday clock;
the existing hourly sweep persists withdrawal. No new routine is enabled.

Keep is visible but disabled until the separate Keep slice installs application.
The server refuses `keep` without changing the open proposal. That slice can read
stored `edits_json` and provenance under the same reviewer authorization, apply
under the push lock, and mark `kept` only after a successful push. A failed Keep
must leave the card open. Keep execution, pass scheduling and pilot activation
are separate from this card change.

Nightly memory tidy admits one pilot check at a time. It accounts for the finished
pass's estimated usage, including subscription, failed and withdrawn usage, before
the next check starts. At or over $50 it admits no more checks and reports the final
total and any overshoot to Head of Product; a running check finishes or reaches the
05:00 Pacific withdrawal. Unknown usage holds the next admission.

The runner pins the upstream commit and supplies a scoped packet to an empty
workspace, without Hub MCP or checkout tools. A fresh verification turn on the
same named model checks the before and proposed rules against AGENT.md. Both turns
use low effort and their combined usage is charged to the pass. Protected runner
endpoints hold the snapshot and whole-checkout manifests; a model cannot submit
those proofs. Code checks the result, saves its numbered-edit attachment, files the
independent reviewer card, then closes the private container and child. Older
computers without packet support skip the pass rather than run an ordinary turn.
