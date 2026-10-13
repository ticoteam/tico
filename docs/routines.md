# Routines

A routine is a row in Tico: a bot, a time (or a Tico event), a title, and the text the bot is
told. The server runs the clock. When a routine is due it opens a task for the bot with that
text; a Mac claims the task after it wakes and passes readiness, and the bot takes it from there
like any other task. Run now during another task queues a separate run for the routine's task; reading its
notification during the other run does not count as doing that work. Nothing else defines a routine — not a file in the bot's repository, not a
Git branch. Tico organizes the bots' work; what a bot does with a session, a context window or
a compaction is the bot's runtime's business.

## Writing one

Three doors, one table (`schedules`, `backend/routines.py`):

- **The site.** Settings > Routines → *New routine*, or the Routines card on a bot's More tab.
  Expand a routine's row for *Edit*, *Pause*/*Resume* and *Delete*.
- **The `hub` CLI, from a bot's run.** A bot sets up its own; an owner's bot may not touch
  another's. BotOps seeds a new bot's first routines from its template (below).

  ```
  hub routine list [--bot X]
  hub routine set <key> --title "Daily audit" --cron "0 7 * * 1-5" --text-file playbooks/audit.md
  hub routine set <key> --title "Meeting debrief" --on meeting.ready --text "..."
  hub routine update <id> [--title ..] [--text ..] [--cron ..|--on ..] [--timezone ..] [--enable|--disable]
  hub routine delete <id>
  ```

  `key` is a stable name (letters, digits, dots, dashes, underscores). Setting the same key
  again updates the routine in place, so a bot that declares its routines every run makes one,
  not one per run. The MCP tools are the same names: `hub_routine_list`, `hub_routine_set`,
  `hub_routine_update`, `hub_routine_delete`.
- **The API.** `POST /api/v2/bots/{bot}/routines` creates (`key`, `title`, `text`, `cron` or
  `on`, `timezone`, `enabled`); `POST /api/v2/routines/{id}` changes any of those;
  `POST /api/v2/routines/{id}/delete` deletes. `GET /api/v2/routines` is every routine the
  signed-in human may see (no text), `GET /api/v2/bots/{bot}/routines` one bot's with the text,
  `GET /api/v2/routines/{id}/occurrences` what each firing did.

Who may write a bot's routines: the environment owner, the bot's owner, the bot itself, and
BotOps.

A routine's text says which step waits when something is missing, not that the whole run stops: a
missing setup answer, credential or access blocks only the step that needs it (publishing, sending,
a rollout), and the run does everything else and asks for the missing piece by name. A routine that
stops at its first line on an open setup question does nothing every time it fires.

A routine runs on a time (`cron`, five fields, in `timezone`, America/Los_Angeles by default) or
on something happening in Tico (`on`), never both. Tico emits `meeting.ready` when a team meeting with a transcript is first imported (a private meeting emits nothing; `recording.ready` is its old name and is still emitted, so older routines keep firing; see [Meetings](meetings.md)). It emits `market.insight.urgent` when someone reports a market insight with
`--urgent`; that opens the Librarian's urgent market routine and does not fire for an ordinary report. Each event opens one task per routine and per subject, titled `<routine title>:
<meeting title>`; the body is the routine's text, then the event's facts, then the meeting's
notes and transcript. Re-emitting the same event for the same subject does nothing. The event
list is `clients/routines.py EVENTS`.

## What happens when one is due

The scheduler ticks every ten seconds and reads the table. Each row runs independently: a failed
routine, task reminder or PR notice is logged and retried without rolling back other work.
A due routine opens one task owned by the bot, body = the routine's text. Missed occurrences
coalesce into the latest due one. An
unfinished task absorbs later occurrences of the same routine, each of which reminds the bot in
that task; a task waiting on a human is left alone; a task marked Done keeps that status while
the next occurrence opens a fresh one. Editing the text reaches the next occurrence, never a task
already opened. Changing the cron or the timezone moves the next fire forward from now and never
backfills. Pausing keeps the row and its history; the clock skips it.

Deleting a routine keeps its history. A task it opened that no computer has claimed is closed;
one already running finishes. Setting the same key again brings a deleted routine back with its
settings intact.

Bots that are paused or archived get no tasks. External side effects still need tool
idempotency: a coalesced or retried occurrence can repeat an effect.

## Seeing what ran

The Routines page (`#/recurring`) and each bot's Routines card list every routine the signed-in
human can see: cadence, next fire, whether it is armed, whether it is paused, and — when the
bot reads email — its mailboxes. Expand a row for the recent firings: the occurrence, the outcome
(`created`, `coalesced`, `event`), the task's status, when its latest run started, how long
it ran, and a link to the task. `hub sql` reads the same rows: `schedules`,
`schedule_config` (timezone, enabled) and `schedule_occurrences`.

Before taking a bot live, BotOps reads its live Routines with the requester's rights, applies the requested schedule,
and disables unrelated template Routines. `hub_bot_go_live` accepts `routines`, an expected list of the bot's live
Routines; the server checks it before activation and creates nothing. In the CLI, pass the same JSON in a file with
`hub bot go-live <bot> --routines-file <file>` (`hub bot go-live --help` shows it):

```json
[{"id": "<bot>:<routine>", "title": "Weekday review", "cron": "0 9 * * 1-5", "timezone": "America/Los_Angeles", "enabled": true}]
```

`id` (the Routine's id, as `hub routine list` shows it), `title` and `timezone` are required; `cron` and `on` (an
event name) default to `""`; `enabled` defaults to `true`; no other field is accepted. Go-live refuses when a listed
Routine differs from the live one or an enabled Routine is not listed, and a malformed list is refused with the field
named and this shape shown. This also preserves disabled Routines during activation. When go-live waits for the
computer to get the bot's repository (docs/permissions.md), the list is checked again when it finishes.

## Templates

A template's `bot.yaml` may declare `routines:` (`id`, `title`, `cron` or `on`,
`timezone`, `template:` a playbook path, and optionally `enabled: false`). That block is a seed: when BotOps runs
`hub bot create`, the rendered playbooks become the new bot's first routines in Tico, keyed by
their `id`. After that Tico's rows are the routines. Editing the file in the bot's repository
changes nothing; `hub routine set` does. A seeded routine with `enabled: false` is created paused and
visible; the bot (or a human, on the site) turns it on with `hub routine update <id> --enable`. The
starter templates seed the first routine off. Starting Setup switches it on, and `hub bot setup-done`
allows normal work. An owner's requested schedule change applies directly ([Starter bots](starter-bots.md)). `clients/preflight.py` still validates the block so a
broken template is caught before a bot is made from it.

## What this replaced

Until runner 0.5.4 a routine was declared in the bot repository's `bot.yaml` and copied into
Tico by runner heartbeats (or a server-side Git sync behind `TICO_ROUTINES`), with a
version snapshot per change and a task gated on the Mac having checked out the declaring
revision. All of that is gone: `routine_versions`, `routine_tasks`, `routine_sources`,
`routine_deliveries`, the webhook route and the `TICO_ROUTINES*` settings. Migration 28 drops
the tables and keeps every `schedules` row, its settings and its occurrence history. A runner
older than 0.5.4 still reports the manifest's routines in its heartbeat; the server accepts
and ignores them.

Routine and run responses use `bot`. The older `employee` field is a compatibility alias for `bot`;
existing clients may continue to read it.

[Branches](creating-bots.md#branches) follow the original's instructions, but their routines never fire separately.
Timed and event routines stay with the original, so a shared lesson or weekly review runs once.
