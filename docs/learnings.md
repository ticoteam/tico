# Learnings

A bot keeps what it learns in its repository: `memory/` (including `memory/learnings.md` and
`memory/decisions.md`) and `knowledge/`. Every change there is a git commit, and the **Learnings**
button beside the bot's name shows those commits as a history, newest first. A small number on the
button counts the updates since you last opened it.

Each update shows its commit subject, who made it and when, a link to the commit on GitHub, the files it
changed and, once the commit has been pushed, its diff. When the run that made it is known, the
update links to its source: the task, or the message in the chat, that the bot was working on. Two
more views show the current `learnings.md` and `decisions.md`.

## Where it comes from

Git is the record; the history is a view of it. The server holds no checkout, so the computer
running the bot reports memory commits after each completed run and every five minutes
(`runner/memory_history.py`, `POST /api/v2/bots/{bot}/memory/report`). A report names commits by
SHA, so a retry, an outage or two computers reporting the same repository never make duplicates.
Only the computer the bot is assigned to may report for it.

- **Not pushed** marks a commit that is only on the bot's computer. Its diff is not sent until it
  reaches the repository, and a commit held back because it contains a secret never leaves the
  computer.
- **Learnings and Decisions** show `memory/learnings.md` and `memory/decisions.md` as the shared repository
  has them. Uncommitted or unpushed changes are not sent, and a bot with no shared repository sends commit
  subjects only.
- **Source.** The prompt asks the bot to end a memory commit's message with `Tico-Run: <run id>`. For
  a bot that is not shared, a commit made during a run is that run's even without the line. A run
  must belong to the bot or its family (an original and its branches share one repository);
  anything else is ignored. Older commits show their author and date without a source.
- **Privacy.** Anyone who can read the bot sees its memory history, as they see its files. The
  source shows only to a reader who may open that task or chat; everyone else sees the update
  without it.

Branches of a shared bot write the same repository, so a lesson one branch pushes shows in every
branch's history and reaches their next run after the repository syncs.

## API

- `GET /api/v2/bots/{bot}/memory?before=&limit=`: `updates`, `unseen`, `documents`, `next_before`.
- `POST /api/v2/bots/{bot}/memory/seen`: you opened the history.

## Nightly learning run

Once a night, at or after 3:00 (Pacific, the same clock as daily updates), Tico reads the day's
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

**Routing.** The decision model (TypeSafe when the server has its key, else the team's own AI
provider; with neither the night shows "needs a decision model") answers, per item, whether it is
learnable and, per recipient, whether it is relevant (`questions/learnings-route.json`). An item goes
to every recipient scoring 0.6 or more, unless its learnable score is under 0.2; at most 40 items each,
highest first. Recipients are the active bots other than BotOps, the Assistant and the Goal Manager,
plus the Librarian for how the company works and the market.

**Packets are tasks.** Each recipient gets one private task, "Nightly learnings: <date>", with the
instructions and its packet: each item's source, link, the quoted text, its score and who else got it.
A bot changes only its own repository and commits with `Tico-Run:`; a change to its instructions or
settings goes to BotOps as a task. The Librarian edits docs directly (every edit is a version) and
reports market facts with `hub market report`.

**The page.** **Learnings** in the account menu lists the nights. A night shows Bots (memory commits
made by the runs that worked its packet task), Docs (the Librarian's doc versions while its task was
open) and Market (its market changes then), each change with its source and diff. A bot's changes show
only to readers who may read that bot. A packet's text shows only when the reader may read the source
today (the chat, the task, the mailbox, the meeting); anything else is counted as private.

- `POST /api/v2/learnings/run` (owner): tonight's run now; 409 when it is running or already ran.
- `GET /api/v2/learnings?before=&limit=`: nights, newest first.
- `GET /api/v2/learnings/{id}`: one night's sections.
- `GET /api/v2/learnings/{id}/packets/{bot}`: one recipient's packet.
