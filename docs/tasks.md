# Tasks

Every task sits in a pipeline, at one stage, with one owner, and always says whose turn it is. One function moves
tasks, one read says what a task waits on, and a check every 10 seconds makes sure someone is on it. The full design
is the spec, [Pipelines v3](pipelines-v3.md). This page is the short version and the commands.

## Pipelines and stages

A pipeline is a line of stages with a name, a one-line goal and a manager bot. It ends in two built-in stages, Done
and Closed. A stage has a name, an owner, what a task needs to leave it, an expected time and an optional limit.

A new company has one pipeline, **General**, managed by the General Manager bot:

- **To do.** Owner: the task's assignee. To leave: the owner says done. No limit. Expected time: 1 day for a bot.
- **Done.** When the owner says done, it is Done. Nobody checks it. It can be reopened with a note.

A task with no assignee wakes the General Manager to pick one. Product and Engineering pipelines come later
([Default pipelines](pipelines-v3.md#default-pipelines)).

A task's status is `open`, `done` or `closed`. Nothing else is stored about where it is: its pipeline, its stage and
its owner, all changed only by the engine. Every change raises the task's `version`. A bot's command carries the version
it was last shown, and a move made against an old one is refused: "changed since you looked".

The moves ([The moves](pipelines-v3.md#the-moves)):

- **Forward.** The engine moves the task the moment everything is met and the next stage has room.
- **Push past.** Forward with something unmet, with a reason. Anyone may, bots included.
- **Back.** To an earlier stage. Never refused.
- **Close.** From any stage. Never refused. Its open sub-tasks close with it. A bot gives a reason.
- **Reopen.** From Done or Closed back to a stage, with a note.
- **Reassign.** A new owner for this visit to the stage.

A bot cannot close a task a person asked for. It asks that person instead.

## Whose turn it is

What a task waits on is worked out, never stored. The first match is the line under the title; the task lists all of
them ([the nine states](pipelines-v3.md#whose-turn-it-is-the-nine-states-of-an-open-task)):

1. Snoozed
2. A person's turn: an ask names them, or the task is theirs
3. Paused: it is a bot's turn but the bot can't run (paused, out of credit, quarantined, no computer, a sign-in or a
   worktree missing)
4. A bot's turn
5. Jev is checking
6. GitHub
7. Other tasks: it filed tasks that are still open
8. A full stage
9. A by-hand stage

An open task is never waiting on nothing. **Late** sits on top of any state once a task has been in its stage longer
than the stage's expected time. A person's own task is Late only after its due date.

## Asks and OKs

An ask is a requirement with a person's name on it, added to one task for this visit to its stage. The task does not
leave the stage until it is answered, withdrawn or pushed past. There are two kinds:

- **Answer:** a question, with choices or free text.
- **OK:** approve, or send back with notes. The notes go to the bot word for word.

```sh
tico task ask <id> --to <person> "Which plan do new signups get?" --choices basic,pro --version N
tico task ask <id> --to <person> --kind ok "Approve the launch post" --show page:12 --version N
tico task ask <id> --withdraw -m "why"
```

A bot has one ask of its own open on a task at a time: asking again replaces it. An ask never expires. A person
answers it from Needs you. A bot named on one answers with `tico task answer <id> <requirement> ok|no|again|<answer>`,
or gives it to someone else with `tico task give <id> <requirement> --to <person>`.

Something that leaves the company or spends money (send this email, publish this, spend this) waits on an OK bound to
exactly what the person was shown. The person OKs it from its preview, by their own click; an Assistant or a token
acting for them does not count. The OK is used once, for that content only. A change after the OK needs a new one. If
there is no task, Tico files one in General.

Work a person has to go and do is a sub-task for them, not an ask. An @ in the conversation never makes a task wait.

## Snooze

Snooze is the only hold. It sets a task aside until a date, or with no end ("On ice"). While snoozed, nothing wakes
or chases it, it is not Late, it holds no slot in a stage with a limit, and it leaves Needs you.

```sh
tico task snooze <id> --until 2026-11-14 -m "vendor replies by Nov 14" --version N
tico task unsnooze <id>
```

A bare date is the start of that day, Pacific. A bot gives a reason and should give a date. A bot's snooze with no
date tells the manager bot once; after three snoozes by a bot in one stage, the next goes to the manager bot. When a
snooze ends, the owner is woken once. Finishing or closing a task ends its snooze. `tico task list --snoozed only|all`
lists snoozed tasks.

## Done, closed and reopen

`tico task done <id> -m "what was done"` says the work of this stage is finished. In General that makes the task Done.
Done never pushes past anything: if something is still missing, it is refused with what is missing and what you can do
next.

Close takes a task out of the pipeline from any stage, with a reason from a bot. "Declined" is Closed with a reason.
Closing stops work still running on the task and closes its open sub-tasks. Done does not finish sub-tasks.

```sh
tico task done <id> -m "Sent the invoices" --version N
tico task close <id> -m "Not needed this quarter" --version N
tico task move <id> reopen -m "The export is wrong again" --version N
```

Done and Closed tasks are behind the archive button on the Tasks page. Nothing closes by itself.

## When a bot stops early

A bot's run must leave its task done with the stage, asking a person, waiting on a task it filed, snoozed, or closed.
Anything else is "stopped early". The never-stuck check then wakes the bot again after 2 minutes, 15 minutes and 50
minutes (inside the hour its session stays cached); then its manager bot, three tries more; then one person, once, in
Needs you, with Try again, Reassign, Move back, Close and Snooze. Any change to the task starts the count again. See
[The never-stuck check](pipelines-v3.md#the-never-stuck-check).

A person hands a task back to its owner with **Try again** (`POST /api/v2/tasks/{id}/try-again`).

## Needs you

Needs you is the one list a person checks. It holds every ask with their name, every task they own, every Paused
cause they can fix (one row per cause, not per task), every task the ladder or Late brought to them, and every @ of
them by a person until they reply. A bot's @ only marks the task unread. No chat, email or push goes to a person about
a task.

`GET /api/v2/needs-you` returns the list; `?count=true` returns the one number on the Tasks badge.

## Sub-tasks

A task waits on the tasks it files. File one with `tico task file --parent <id> "title"` (yours unless `--owner`), or
`tico task create --parent <id> ...`. The parent is set when the task is filed and never changes. Sub-tasks nest at
most 5 deep. When the last one ends, the parent's owner is woken once with how each ended. Closing a parent closes its
open sub-tasks. A deleted sub-task counts as Closed for whatever waits on it.

```sh
tico task file --parent <id> --owner engineer "Build the service"
tico task tree <id>
```

`GET /api/v2/tasks/{id}/tree` returns the nested sub-tasks.

### A sub-task on a check model

A bot can have another model look at its work without a second bot:

```sh
tico task child <parent-id> --check-model grok --role review --title "Review PR 655" --body "https://github.com/..."
tico task child <parent-id> --check-model codex:gpt-6.1-sol --title "Check the migration plan" --body "..."
```

`--check-model` takes a runtime or `runtime:model`. With no value it takes the company's check model (Settings >
Models), from a provider other than the parent's authors when one can run it. The sub-task is the filing bot's own.
Only the runtime, model, harness and effort change: the bot's repository, tools and credentials stay its own. The API
takes `check_model` and `check_role` on task create.

## Related tasks

A relation is a note between two tasks. A task never waits on one, except an order in Merge.

| Kind | Means |
|---|---|
| `related` | they belong together |
| `duplicate_of` | the task duplicates the other |
| `follow_up` | the task was split off or followed up from the other |
| `after` | the task merges after the other: in Merge its PRs are not queued until the other has merged |

```sh
tico task relate <id> <other-id>                     # related
tico task relate <id> <other-id> --kind duplicate_of
tico task relate <id> <other-id> --remove
tico task after <id> <other-id>                      # merges after it; --remove takes it off
tico task file "title" --parent <id> --after <other-id>
```

An order is for two tasks that change the same lines: they build and are reviewed side by side, and Merge queues the
later one only once the earlier one has merged ([Pipelines](pipelines-v3.md), "GitHub", Merge). Both pages show it,
"After #40 · Merge" and "Before #42", read-only.

The API is `POST /api/v2/tasks/{id}/relations` with `{"task": "<other id>", "kind": "...", "remove": false}`. In SQL
they are the `task_relations` table ([Hub SQL](hub-sql.md)).

## Links and pull requests

Attach PRs, issues and documents with `tico task link <id> <url>`, or `GET/POST /api/v2/tasks/{id}/links` and
`DELETE /api/v2/tasks/{id}/links/{link_id}`. A PR link shows its checks, mergeability and review state. In General a
linked PR is information: opening, merging or closing it does not move the task. `--wake-once` on a PR link wakes the
task once when the PR's checks finish, a review approves or asks for changes, or a release ships it.

## Conversation

Every task has its own thread. A comment is a post there and reaches the task's [followers](#followers) and whoever it
@mentions. A comment to a bot says how it arrives ([Steer, queue or defer](using-tico.md#steer-queue-or-defer)):

```sh
tico task comment <id> "Use the August numbers." --steer
tico task comment <id> "For later." --defer 4h
```

A comment is talk. It is not progress: a bot that only comments has still stopped early.

## Choosing the model a task runs on

A bot's task runs on the bot's task model. A task can name its own model, effort and harness instead: **Runs on** in
the task window, `tico task create --model <id> --effort <effort> --harness <harness>`, `tico task model <id> <model>`,
or `tico task model <id> --clear`. `POST /api/v2/tasks/{id}/model` does the same (`{"model": null}` clears it). Whoever
may change the bot's model may change a task's model; a bot never does. A task that changes owner loses its own model.

A task can also keep its own **Compact** window, where its conversation compacts: one of the twelve sizes from 100K to
1M, offered only up to what its model holds, or **Same as bot**. It goes with the model as `compact_at` (in
`POST /api/v2/tasks` and `POST /api/v2/tasks/{id}/model`), or alone, which keeps the bot's model and resumes the task's
session; `{"compact_at": null}` puts it back to the bot's. A bot's fallback keeps one the same way; on Same as bot it
takes the bot's window, lowered to fit the fallback model.

## Changing many tasks at once

Select tasks in a list to move, reassign, tag or close them at once. The change goes to the server once, as
`POST /api/v2/task-bulk` with a `filter` (the filters of `GET /api/v2/tasks`, or `ids`, up to 100), an `action`
(`move` with `to`, `owner` with `owner`, `tags` with `add` and `remove`, or `close`), an optional `why` and `dry_run`.
People only. Each task is changed by the same call a single change makes, with the caller's own rights; a task the
caller may not change is skipped with the reason. One call changes at most 200 tasks.

## People on a task by role

Besides its owner, a task can list people or bots under a **role**, a short name the team chooses (`developer`,
`reviewer`). A role has no meaning or rights in Tico and reaches nobody. Task create and update take
`"roles": {"reviewer": ["human:charlie"]}` (each role named replaces its people, `[]` clears one).
`tico task list --member me [--role reviewer]` lists the tasks you are on. In SQL they are the `task_roles` table.

## Files

Attach a file to the task so anyone who can read the task can open it. Uploading the same name on the same task adds a
new version. Each version records who uploaded it, when, its size and type, and an optional note.

```sh
tico task attach <id> report.md --note "Revised introduction"
tico task comment <id> "Choose a draft" --attach draft-a.md --attach draft-b.md
tico task comment <id> "The export" --file <file-id>@1
```

`POST /api/v2/tasks/{id}/files` takes `name`, `text` or `content_base64`, and `note`. `GET /api/v2/tasks/{id}/files`
lists them with their versions. The task's **Files** strip shows each file at its newest version; a comment's pictures
and pages show under it ([Visuals](visuals.md#in-a-chat-or-task-conversation)).

## Followers

A task's **followers** are the people and bots who follow its thread, each at a level: `live` (everything),
`mentions`, `digest` or `muted`.

- The owner follows, at `live`. A new owner follows; the old owner stops unless they chose to follow or were
  @mentioned.
- Filing or commenting on a task does not follow it. An @mention does. `tico task create --follow` follows the task you
  file.
- Only someone who can see the task follows it.
- `tico task followers <id>`; `tico task follow <id> [--level live|mentions|digest|muted] [--who X]`;
  `tico task unfollow <id> [--who X]`.
- API: `GET /api/v2/tasks/{id}/followers`; `PUT` with `{"actor"?: X, "following": true|false, "notify"?: level}`.

## Company and private tasks

A new task is readable by the company's people and bots. Reading does not give the right to change it.

Turn on **Private** to limit reads to the requester, the owner, its readers and the managers of any bot on it. Only
the person who asked for it can make it company-visible again; a bot never can. Reassigning a private task gives the
new owner access and takes it from the old one, unless they are still the requester, a reader or a manager.

### Who can see a private task

A private task is seen by its requester, its owner and its **readers**: people, bots and groups listed on it. A group
names everyone in it, read at each check.

- The task's **Who can see** row shows them; × takes a reader off and + adds one.
- Anyone who may change the task changes its readers. A bot adds only people and bots within its reach. Only a person
  adds a group.
- `tico task create --private --reader X`; `tico task readers list|add|remove <id> [X ...]`.
- API: `GET /api/v2/tasks/{id}/readers`; `POST` with `{"add": [...], "remove": [...]}`; `PUT` with
  `{"readers": [...]}`. A task lists at most 50 readers.
- A reader reads that task only: not the task above it or its other sub-tasks.

### A private task with no person on it

A private task between two bots has nobody who could act on it. Its owning bot (or that bot's manager) may:

- **Bring in one person**, by filing a sub-task for them under it (`tico task create --owner <person> --parent <task>`).
  They become a reader of that task only. Once a person reads it, a hand-off to someone else is refused.
- **Hand work to another bot in a sealed sub-task**: `tico task create --owner <bot> --parent <task>`. The other bot
  reads only the words written into the sub-task, never the parent. Words copied from the private work (12 or more in
  a row, or a secret-shaped value) are refused, naming the check, never the text.
- **File a company-visible task from the private work**: `tico task create --from-private --owner <bot>
  [--parent <task>]`. It carries only its own words, under the same checks, and the private task stays sealed.

`POST /api/v2/tasks/dry-run` makes the same checks as a create and writes nothing.

## Editing and deleting comments

Whoever wrote a comment, a person or a bot, can change its text or delete it, signed in as themselves. Nobody else
can. Only plain comments change; an ask, an answer or a notice does not. Neither an edit nor a delete wakes anyone.
A deleted comment leaves a tombstone with its text cleared.

```sh
tico task comment-edit <id> <comment-id> "Use the August numbers."
tico task comment-delete <id> <comment-id>
```

## Deleting tasks made by mistake

Closing keeps a task as history. A task made by mistake, such as a duplicate, can be deleted instead: by its human
requester, or by anyone who may move any task. Bots close tasks; they never delete one.

```sh
tico task delete <id>
tico task deleted              # deleted tasks you may restore, newest first
tico task restore <id>
```

`POST /api/v2/tasks/{id}/delete`, `GET /api/v2/deleted-tasks` and `POST /api/v2/tasks/{id}/restore` do the same.
Deleting moves the task to the trash with its thread, links, labels and relations; it leaves every list and every
bot's context at once, and keeps its number. A restore puts it back open on a new visit to the stage it was in (the
first stage if that one is gone), or Done or Closed as it ended. Its old asks and wakes are not replayed.

To delete many at once, such as a bulk import run twice, the owner runs the offline command on the server after a
snapshot:

```sh
python -m backend.manage delete-tasks /data/hub.sqlite --ids-file ids.txt          # lists what would go
python -m backend.manage delete-tasks /data/hub.sqlite --ids-file ids.txt --apply  # moves them to the trash
python -m backend.manage restore-tasks /data/hub.sqlite --ids-file ids.txt         # puts them back
python -m backend.manage purge-deleted-tasks /data/hub.sqlite --older-than-days 30 --apply  # removes for good
```

## CLI and MCP

MCP tools mirror the commands one for one (`tico task move` is `tico_task_move`). `--version` is the version you were
last shown: `tico task show`, a list row and every wake print it. Without one a bot's command is refused: "Run `tico task
show <id>` first."

```text
tico task show <id>
tico task done <id> -m "what was done" --version N
tico task move <id> next|<stage>|done|closed|reopen -m "why" --version N
tico task close <id> -m "why" --version N
tico task reassign <id> <owner> -m "why" --version N
tico task ask <id> --to <person> "line" [--kind ok] [--choices a,b] [--show page:12] --version N
tico task ask <id> --withdraw
tico task answer <id> <requirement> ok|no|again|<answer> [-m "note"] --version N
tico task give <id> <requirement> --to <person> --version N
tico task file --parent <id> [--pipeline general] [--owner X] "title"
tico task snooze <id> [--until date] -m "why" --version N
tico task create --owner X --title "..." [--pipeline general] [--parent <id>]
tico task update <id> [--title] [--body] [--due] [--label] [--goal] --version N
tico task list [--status open|done|closed] [--owner X] [--snoozed only|all]
```

`tico task show` prints the version; pipeline, stage and owner; whose turn it is; what the stage needs to leave, met or
not; the next stage; and the task that filed it. A refusal prints what is missing and the commands allowed next. A
refusal never counts toward a bot's quarantine.

## API

| Route | Body | Does |
|---|---|---|
| `POST /api/v2/tasks` | `title, body, owner, pipeline?, parent?, due?, labels?, private?` | create |
| `GET /api/v2/tasks/{id}` | | the task, full |
| `GET /api/v2/tasks` | | brief rows |
| `POST /api/v2/tasks/{id}` | `version` + fields | field edits only |
| `POST /api/v2/tasks/{id}/done` | `note?, version` | the work of this stage is finished |
| `POST /api/v2/tasks/{id}/move` | `to, why?, version` | forward, push past, back, done, close, reopen |
| `POST /api/v2/tasks/{id}/owner` | `owner, why?, version` | reassign |
| `POST /api/v2/tasks/{id}/snooze` | `until?, why?, version` | snooze |
| `POST /api/v2/tasks/{id}/unsnooze` | `why?` | unsnooze |
| `POST /api/v2/tasks/{id}/try-again` | `why?` | a person hands a task back to its owner |
| `POST /api/v2/tasks/{id}/asks` | `to, line, kind?, choices?, show?, version` | ask a person |
| `DELETE /api/v2/tasks/{id}/asks/{requirement_id}` | `why?` | withdraw |
| `POST /api/v2/tasks/{id}/asks/{requirement_id}/answer` | `value, note?, version` | ok, no, again, or an answer |
| `POST /api/v2/tasks/{id}/asks/{requirement_id}/give` | `to, version` | give it to another person |
| `POST /api/v2/tasks/{id}/asks/{requirement_id}/thumbs` | `up, note?` | was this worth asking |
| `POST /api/v2/answers/{id}/undo` | | take an answer back |
| `POST /api/v2/task-bulk` | `filter, action, ...` | people only |
| `GET /api/v2/needs-you` | | Needs you |
| `GET /api/v2/pipelines`, `GET/POST /api/v2/pipelines/{id}`, `POST /api/v2/pipelines/{id}/retire` | | settings |

`to` is `next`, `done`, `closed`, `reopen`, or a stage's id or name. A refusal is 409 with `{"error": <rule>,
"detail": <one line>, "missing": [...], "next": [...], "version": <current>}`.

An OK bound to what was shown has its own routes: `GET /api/v2/oks/{requirement_id}` (the preview, with the
`shown_hash` to send back), `POST /api/v2/tasks/{id}/asks/{requirement_id}/ok` (`shown_hash`; the person's own click),
`POST /api/v2/tasks/{id}/oks` (a bot asks: `to, line, subject, content, show?, version`) and
`POST /api/v2/oks/{requirement_id}/use` (`subject, content, note?`; once, just before the act).

The task as JSON, field by field, is in [the cut-over plan](dev/pipelines-v3-cutover.md#the-task-as-json).
