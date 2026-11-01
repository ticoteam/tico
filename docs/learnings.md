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
entry IDs and exact heading titles select cited logs; unresolved external references
are never fetched.

The exact rule headings are `Working rules` and `Standing rules`, optionally followed
by parenthetical guidance. Top-level Working rules end at the first dated heading;
second-level sections end at the next peer heading. Dated headings/bullets inside
Standing rules are logs; a subsequent undated top-level bullet resumes rules. Imported
`Working rules from ...` headings are log, never rules. Unrecognized prose remains
untouched. This is a Markdown-entry contract, not a generic Markdown rewriter.

Run the child without write tools, in a disposable checkout with no publication or
main-branch credentials. Independently collect before/after manifests of **every**
checkout path: tracked, untracked, ignored, symlink targets and file modes. Manifest
values must identify content AND kind/mode. Pass these to `evaluate`; any difference,
even under memory, flags the proposal. Always discard the whole child checkout at
settlement, on success, failure or cancellation. An attachment is returned through the
child result channel, never written into that checkout. The module trusts these
manifests, not a child's claim that it wrote nothing.

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
links to exact originals. Retire/archive/move-log edits have empty replacements.
A fact move supplies its unchanged original as replacement and a normalized
`knowledge/*.md` `destination`. Other operations cannot set a destination. Paths
containing traversal, backslashes or control characters are rejected.

### Checks and output

`evaluate(snapshot, raw_json, since, today, checkout_before, checkout_after,
conflict_check, previous_learnings)` applies anchored edits to an in-memory scratch
copy, archives every removed original verbatim, and computes a unified diff. Retired
rules include a retirement date and replacement edit number. Dated-note moves append
the original under a Log heading. More than a quarter of actual working rules touched
flags the proposal; exactly a quarter is permitted. Dated notes do not enter that
count. Log entries strictly older than 30 days must be removed and present verbatim
in the archive before an offer. Thirty-day-old entries remain eligible to stay.
Historical old entries outside the packet cannot be edited by the model: they produce
a review flag rather than silently widening the read scope.

`conflict_check(agent_text, rules)` is a required trusted semantic check of both old
and proposed rules against AGENT.md. It returns a list of conflict descriptions. Its
absence, failure or any conflicts flag the proposal; the public diary reports only
the count. Do not substitute the edit-producing child's unchecked 'no conflicts'
claim. This module does not pretend lexical matching proves semantic consistency.
The orchestration slice must provide this check before enabling offers.

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
