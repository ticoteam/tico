# Learnings

A bot keeps what it learns in its repository: `memory/` (including `memory/learnings.md` and
`memory/decisions.md`) and `knowledge/`. Every change there is a git commit, and the **Learnings**
button beside the bot's name shows those commits as a history, newest first. A small number on the
button counts the updates since you last opened it.

Each update shows its commit subject, who made it and when, the short commit SHA, the files it
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
