# Tasks

Every task sits in a pipeline, at one stage, with one owner, and always says whose turn it is. One function moves
tasks, one read says what a task waits on, and a check every 10 seconds makes sure someone is on it. The full design
is the spec, [Pipelines v3](dev/pipelines-v3.md). This page is the short version and the commands.

## Pipelines and stages

A pipeline is a line of stages with a name, a one-line goal and a manager bot. It ends in two built-in stages, Done
and Closed. A stage has a name, an owner, what a task needs to leave it, an expected time and an optional limit.

A new company has one pipeline, **General**, managed by the General Manager bot:

- **To do.** Owner: the task's assignee. To leave: the owner says done. No limit. Expected time: 1 day for a bot.
- **Done.** When the owner says done, it is Done. Nobody checks it. It can be reopened with a note.

A task with no assignee wakes the General Manager to pick one. Other pipelines come later
([Software factory](software-factory.md)).

A task's status is `open`, `done` or `closed`. Nothing else is stored about where it is: its pipeline, its stage and
its owner, all changed only by the engine. Every change raises the task's `version`. A bot's command carries the version
it was last shown, and a move made against an old one is refused: "changed since you looked".

The moves ([The moves](dev/pipelines-v3.md#the-moves)):

- **Forward.** The engine moves the task the moment everything is met and the next stage has room.
- **Push past.** Forward with something unmet, with a reason. Anyone may, bots included.
- **Back.** To an earlier stage. Never refused.
- **Close.** From any stage. Never refused. Its open sub-tasks close with it. A bot gives a reason.
- **Reopen.** From Done or Closed back to a stage, with a note.
- **Reassign.** A new owner for this visit to the stage.
- **Move to another pipeline.** The same task, with its number, label, history, comments, files and links, into a
  stage of another pipeline (the first by default). See below.

A bot cannot close a task a person asked for. It asks that person instead.

### Moving a task to another pipeline

Task ⋯ › **Move to…** lists every pipeline's stages ("Backlog › On ice"); typing finds by each word ("back ice"). The
why is optional for a person; a bot always gives one. On a board, ⋯ › **Select** (or a shift-, ⌘- or Ctrl-click on a
card) picks tasks; **All in <stage>** picks a stage's, and **Move to…** moves them in one write, then says what moved
and what stayed and why ("Moved 214. 3 didn't move: 2 are in a bot's running turn, 1 you can't read"). Those that
stayed stay picked.

A stage's own ⋯, at the right of its column's header, works on the tasks the column shows (the search and filter
applied): **Move all to…** (this pipeline's stages, Done, then other pipelines'), **Close all** (Undo reopens them),
**Assign all to…**, **Snooze all…** and, for whoever may change the pipeline, **Edit stage**. Move all and Close all ask
first with the count ("Close 22 tasks?"). Each is one write that makes the single change on every task with the
caller's rights; a task that can't change stays, and the board says how many and why.

- It is a re-file, not a forward move: the old stage's unmet requirements don't hold it. Entering the new stage is a
  normal entry: its owner rule, stage rules, limit and wake apply (a by-hand stage wakes nobody).
- The task keeps its label: D18450 stays D18450 on the Backlog board, and `D18450` still finds it. The prefix is the
  task's own, from the pipeline it was made in.
- The old stage's open asks are withdrawn, a snooze ends, and what the old stage did on entering is undone as a back
  move undoes it. Sub-tasks stay in their own pipelines, linked as they were.
- A Done or Closed task lands in the new pipeline's Done or Closed, never reopened.
- Refused, and listed in a move of many: a task whose bot is mid-turn (`task_turn_running`), a full stage
  (`task_stage_full`), one the mover can't read. There is no undo: move it back the same way.
- Anyone who can read the task may move it, into any pipeline. A bot never lands a task past a person's OK, and only a
  person moves a task into a private pipeline (entering one makes it private and adds the pipeline's readers).
- The history reads "Steven moved it from Dev › Ideas to Backlog › On ice — why". The event is `task.moved` (from and
  to pipeline and stage, who, and the bulk move's id); the old stage's leaves and the new stage's enters fire on
  `task.step_changed`.

Removing a stage that holds open tasks asks where they go, in any pipeline: the save answers 409 `stage_has_tasks`
(the stages, and a suggested `move_to`) until it carries `move_tasks_to: {pipeline, stage}`. The tasks move first, then
the stage goes. Retiring a pipeline that holds open tasks takes the same `move_tasks_to` (a stage of another
pipeline) on `POST /api/v2/pipelines/{id}/retire`.

## Whose turn it is

What a task waits on is worked out, never stored. The first match is the line under the title; the task lists all of
them ([the ten states](dev/pipelines-v3.md#whose-turn-it-is-the-ten-states-of-an-open-task)):

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

An ask is an OK: a requirement with a person's name on it, added to one task for this visit to its stage. The task does
not leave the stage until it is answered, withdrawn or pushed past. The person approves, or sends it back with notes,
and the notes go to the bot word for word. A question is not an ask: a bot asks it in chat, with a ❓ (below).

```sh
tico task ask <id> --to <person> --kind ok "Approve the launch post" --show page:12 --version N
tico task ask <id> --withdraw -m "why"
```

A bot has one ask of its own open on a task at a time: asking again replaces it. An ask never expires. A person
answers it from Needs you. A bot named on one answers with `tico task answer <id> <requirement> ok|no`, or gives it to
someone else with `tico task give <id> <requirement> --to <person>`.

Something that leaves the company or spends money (send this email, publish this, spend this) waits on an OK bound to
exactly what the person was shown. The person OKs it from its preview, by their own click; an Assistant or a token
acting for them does not count. The OK is used once, for that content only. A change after the OK needs a new one. If
there is no task, Tico files one in General.

Work a person has to go and do is a sub-task for them, not an ask. An @ in the conversation never makes a task wait.

### ❓ on a bot's message

A bot that needs one person's reply marks its own chat message ❓ for them. Nothing is filed and nothing is sent: the
message shows a small, muted "?" at its top right and is in that person's Needs you, under its task's title (or the
bot's name in a direct chat). In Flow the item is that chat, at the marked message, with its composer: a reply there
clears it and Flow moves on.

```sh
tico task ask <id> --to <person> "Should the launch note go out on Friday?"   # on the task's chat
tico chat ask --to <person> "Should the launch note go out on Friday?"        # in a direct chat
```

- Only a bot marks, only its own message, and only for a person who can read that chat. The message follows the "Asks
  to a person" rule like any ask.
- A bot has one open ❓ per chat: a new one replaces it, and the bot is told so. It puts every question to a person in
  one ❓, numbered when there are several.
- A bot can't say done, move its task on or close it while its own ❓ there is open: it waits for the reply, or takes
  the question back first by deleting its message (`tico thread delete <message id>`).
- It clears when that person replies in the chat (or their Assistant does, on their word), when the task is done,
  closed, merged or deleted, or when the message is deleted.
- While it is open the never-stuck ladder does not chase the bot. Once the person replies, the reply is progress and
  the task is back on the ladder; a bot's snooze of the task ends.
- Give to… hands it to another person; Snooze hides it from you until then, and a new ❓ is a new item.

## Snooze

Snooze is the only hold. It sets a task aside until a date, or with no end. While snoozed, nothing wakes
or chases it, it is not Late, it holds no slot in a stage with a limit, and it leaves Needs you.

```sh
tico task snooze <id> --until 2026-11-14 -m "vendor replies by Nov 14" --version N
tico task unsnooze <id>
```

A bare date is the start of that day, Pacific. A bot gives a reason and should give a date. A bot's snooze with no
date tells the manager bot once; after three snoozes by a bot in one stage, the next goes to the manager bot. When a
snooze ends, the owner is woken once. Finishing or closing a task ends its snooze. `tico task list --snoozed only|all`
lists snoozed tasks.

On a pipeline's board a snoozed task is grayed at the bottom of its stage, with the day it wakes (or "Snoozed"); its
moon is its one button, Unsnooze.

### Product's Ideas

Product's Ideas is an ordinary stage. An idea that is not snoozed moves on to Spec by itself when Spec has room, oldest
first, once Jev has checked it against open and recent Product tasks (the `idea-check@1` questions, one call per pass
for up to 20 ideas, `backend/idea_checks.py`). When Jev is sure (0.85 or over) an idea is the same as task #N, the idea
is closed as a duplicate of #N, linked to it, and its person is added to #N's requesters with an "Also asked by"
comment. A person's idea that matches a bot's idea still in Ideas closes the bot's instead. Under 0.85, a failed call,
or no answer within 2 minutes: the idea goes on as it is.

A bot's own idea is filed snoozed with no end date, and only a person unsnoozes it. A bot filing a person's ask files
it for them, from the message they asked in; the task is then the person's, and its history names the bot:

```sh
tico task create --pipeline product --owner bot:pm --title "Export to CSV" --for human:ana --from-message <message id>
```

It is refused unless that person sent the message and the bot can read its conversation.

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

A duplicate is closed by merging it into the task that stays ([Merge and make a subtask](#merge-and-make-a-subtask)):
its details move there first, and it is closed with "Archiving this one as a duplicate of #N; its details were added
there." Reopening it brings it back; what moved stays on the other task.

## When a bot stops early

A bot's run must leave its task done with the stage, asking a person, waiting on a task it filed, snoozed, closed, or
blocked on another task (depends_on; never a snooze). While its own pull request's checks run, the turn is GitHub's.
Anything else is "stopped early". The never-stuck check then wakes the bot again after 2 minutes, 15 minutes and 50
minutes (inside the hour its session stays cached); then its manager bot, three tries more. Then the ladder rests: the
task stays with its bot, nobody more is woken for it, and the built-in Quiet bot tasks rule tells the pipeline's
manager once it has been quiet 6 hours (one batched wake per manager). When no manager can hear of it (none, the
manager owns it or may not read it, it was Late already, a person owns it, or it waits only on a release a person
cuts), the bot that stalled asks a person who reads it, in its own words, once ("I'm stuck on Build: ... Can you help
or tell me what to do?"), and BotOps asks for it when it cannot run; the question goes when the task moves on. Any change to the task starts the count again. See
[The never-stuck check](dev/pipelines-v3.md#the-never-stuck-check).

A person hands a task back to its owner with **Try again** (`POST /api/v2/tasks/{id}/try-again`).

## Needs you

Needs you is the one list a person checks, and it holds only what someone asks of them: every open ❓ and OK
with their name, every @ of them by a person until they reply, and two things only they can fix before a task can move:
every Paused cause they can fix (one row per cause, not per task) and a private task that waits on someone being let
in. A task they own with nothing asked of them is in Tasks, not Needs you, and so is a task the never-stuck ladder could
take no further. A bot's @ only marks the task unread. No chat, email or push goes to a person about a task.

So whatever needs a person to act asks them, and only bots write questions, never Tico. A bot that files a task for a
person (`tico task create --owner <person>`) files its own task, with the title as its question to them; Tico's own
notices to a person (an automation on hold, legal mail, a held webhook, a rule that asks for a person, a stopped bot)
are the person's task with BotOps' question on it; the Strategy Owner's asks are its own tasks with its question, which
BotOps asks for it while it cannot run. The question goes when the task moves on.

`GET /api/v2/needs-you` returns the list; `?count=true` returns its one number (beside Flow in the Tasks header; the sidebar shows Flow's mark while it is above 0, and a phone's Flow tab turns jade).

When Flow (the screen that walks the list one ask at a time) opens, it orders the items by a hidden score that the
screen never shows. The score is worked out then, not on a timer, from the task alone, never from who is looking. Flow
shows no suggestion, pick or note: a person reads the ask and presses the button.

Flow no longer suggests rules at All done. Rules people added there before keep binding their page check: Jev still
asks each of the next page judged at that check, as one more yes/no part beside the check's own. To add a rule now,
edit the stage. A bot that has been corrected on the same thing several times asks you whether to make it a standing
rule (`skills/add-to-flow/SKILL.md`).

**Want more? Ping your bots.** All done offers this button: each bot you manage gets one message from you pointing
at Tico's `skills/add-to-flow/SKILL.md`, and the screen says "Asking your bots" while their questions come in. A bot
reads what is written down first and asks only what would change what it does next: at most three, each its own task
with one question to you (a ❓ on the task's chat), so they arrive in Flow as they are filed. Asking nothing is a good result.
The Flow mark on a bot's page, or a team's, opens Flow on that bot's (or team's bots') items only, and its ping goes to
them, whoever manages them. A pinged bot asks the person who pinged it, for a day, even when they are not its manager
([Reach](reach.md)); a bot that uses the skill unprompted asks its own supervisor. A supervisor bot answers only from a
recorded rule or decision, citing it, and otherwise passes the question up as asked. When the answer comes, the bot
updates what it owns first, in the section the answer belongs to (the Strategy Owner, the strategy's sections), then
remembers it (`memory/decisions.md` or its learnings), and closes the question's task. A setup-made bot that reports to
the owner ends its own "Set yourself up" task with the skill, once, for them ([Onboarding](onboarding.md)); no ping is
ever automatic after that. `POST /api/v2/flow/ping` takes `scope` (`reports`, `bot` or `group`) and `id`, and
reaches only bots you may read, never BotOps or the Assistant; a bot you pinged in the last 10 minutes is listed as
`recent` and not messaged again. There is no CLI or MCP command for it yet.

## Sub-tasks

A task waits on the tasks it files. File one with `tico task file --parent <id> "title"` (yours unless `--owner`), or
`tico task create --parent <id> ...`. The parent is set when the task is filed; a person can also put a task under
another afterwards, from the Tasks board (see [Merge and make a subtask](#merge-and-make-a-subtask)). Sub-tasks nest at
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
tico task child <parent-id> --check-model grok --role review --title "Review PR 655" --message "https://github.com/..."
tico task child <parent-id> --check-model codex:gpt-6.1-sol --title "Check the migration plan" --message "..."
```

`--check-model` takes a runtime or `runtime:model`. With no value it takes the company's check model (Settings >
Models), from a provider other than the parent's authors when one can run it. The sub-task is the filing bot's own.
Only the runtime, model, harness and effort change: the bot's repository, tools and credentials stay its own. The API
takes `check_model` and `check_role` on task create.

## Merge and make a subtask

On the Tasks board, on a desktop, drag a task onto another task's card: that card is outlined, not its column, and
letting go asks what to do. Letting go over a column's empty space still moves the stage. Only a person who can change
both tasks can do this; bots cannot.

- **Make it a subtask.** The task goes under the other. Both keep their owners, stages and pipelines, and the other
  waits on it like any sub-task. History on both says who did it.
- **Merge the tasks.** The task you dropped on stays. In one change (if any part is refused, nothing changes):

| Part of the dropped task | After the merge |
|---|---|
| First message, files and pages | Added to the kept task under "From #N", with its title. Files and pages are linked, not copied |
| Open sub-tasks | Move under the kept task, before the close, so they stay open |
| Comments | Stay on the dropped task. The kept task's note says how many |
| Open asks, OKs and ❓ | End with it. The kept task's note lists each one, so its owner can ask again |
| Followers and requesters | Added to the kept task. Teammates who asked for the merged task also follow the kept one, so they hear when it is done |
| Labels and related links | Added to the kept task. Its own stage, due date and pipeline stay; the other's due date is in the note |
| The task itself | Closed with "Archiving this one as a duplicate of #N; its details were added there.", related as `duplicate_of` |

A note on the kept task lists what came in. When its owner is a bot, the bot is woken once to fold in what is relevant;
a person is not woken. When the two owners differ, Merge asks who keeps the task; by default its own owner does, and
choosing the other is a reassign with the usual checks. The dialog shows what will move before you confirm, and
disables a choice that would make a loop, nest past 5 levels, join a private task with a company one, or (for Merge)
lose an open pull request or cross pipelines, saying why.

`GET /api/v2/tasks/{id}/merge?into=<other id>` returns both tasks, why each choice cannot be done (null when it can)
and what a merge would move. `POST /api/v2/tasks/{id}/merge` takes `{"into": "<other id>", "owner": "<optional>"}`;
`POST /api/v2/tasks/{id}/parent` takes `{"parent": "<other id>"}`. Both are for people only.

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
later one only once the earlier one has merged ([Pipelines](dev/pipelines-v3.md), "GitHub", Merge). Both pages show it,
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

A comment that names a bot's open OK on the task (`ask`: its requirement id, in `POST
/api/v2/tasks/{id}/comments`) is a reply to it: it is written to the bot that asked and wakes it, even when that bot is
not the one the task's comments go to first, and the OK stays open. The person the OK waits on may wake the bot this
way. A bot's ❓ is answered by a reply in its chat, not by a comment.

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

## The task's Human

A task may name one **Human**: the person accountable for it, whoever owns it, such as the developer on a ticket a bot
owns. It is optional. The owner still has the work and is the one woken; the Human is the person the task falls to
first. An OK or a page that names nobody goes to them and is in their Needs you, and the owner bot's wake
tells it to ask them. Naming one who cannot read a private task
is refused (it adds no reader). A Human who left, cannot sign in or can no longer read the task is passed over for the
rest of the ladder. Task create and update take `"human": "human:charlie"` (`""` takes them off); the task carries
`human_id`. `tico task create|update --human <person>`. It stays when the task moves to another pipeline.

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

### Attach a page to a page requirement

```sh
tico task attach <id> page:<file id>[@<version>] --req "Spec page" --version N \
  --changed "Sources on every evidence row" --changed "Cut the repeated second section" \
  --answered "Sources on the evidence table: done, each row links its query" \
  --answered "Phone picture: not done, it needs a person's phone"
```

`POST /api/v2/tasks/{id}/pages` takes `requirement`, `page`, `version`, and, all optional, `note` (500 characters),
`changed` (up to 5 one-line bullets of 120 characters: what changed in this version) and `answered` (up to 12 entries
`"<point>: done|not done, <why>"`, one per point of the person's last send back; the bot splits their note into points).
An entry not in that form is refused with the form. A note or line that repeats 12 or more words in a row from the
person's last send back is refused: "Don't quote their note back; say what you did with each point (--answered)". The
MCP tool `tico_task_attach` takes the same fields. The decision view shows them on the next OK of the page.

`GET /api/v2/tasks/{id}/asks/{requirement id}/rounds` lists every version attached to that page requirement, oldest
first: when and by whom, the note, `changed` and `answered` given with it, Jev's verdict and reason (no p-value), and
each OK or send back given while it counted.

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
- A private task stuck because the next stage's owner or someone it asks can't see it says so on its person's Needs
  you card, naming everyone shut out ("Stuck in Build: Reviewer 1 can't see this task"), with **Let Reviewer 1 see
  it**: one press adds exactly them as readers and moves the task on (`POST /api/v2/tasks/{id}/move` with
  `{"to": "next", "let_see": ["<actor>", ...]}`, the list the task's `turn.share` gives; refused, adding nobody, when
  it now waits on someone else). A bot is never told how to share it: its refusal names the person to ask.

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
tico task move <id> --to "Backlog/On ice" -m "why" --version N    # another pipeline (its first stage without /Stage)
tico task move --from "Dev/Ideas" --to "Backlog/On ice" -m "why"  # every open task in a stage
tico task move --from "Dev/Ideas" closed -m "why"                 # close (or move inside its pipeline) a stage's tasks
tico task reassign --from "Dev/Ideas" <owner>                      # a person's: every open task in a stage
tico task snooze --from "Dev/Ideas" [--until date]                 # a person's: every open task in a stage
tico task close <id> -m "why" --version N
tico task reassign <id> <owner> -m "why" --version N
tico task ask <id> --to <person> "line" [--kind ok] [--show page:12] --version N
tico task ask <id> --withdraw
tico task ask <id> --to <person> "line"          # a ❓ on the task's chat
tico chat ask --to <person> "line" [--conversation <id>]
tico task answer <id> <requirement> ok|no [-m "note"] --version N
tico task give <id> <requirement> --to <person> --version N
tico task file --parent <id> [--pipeline general] [--owner X] "title"
tico task snooze <id> [--until date] -m "why" --version N
tico task create --owner X --title "..." [--pipeline general] [--parent <id>]
tico task update <id> [--title] [--due] [--label] [--goal] --version N
tico task list [--status open|done|closed] [--owner X] [--snoozed only|all]
```

`tico task show` prints the version; pipeline, stage and owner; whose turn it is; what the stage needs to leave, met or
not; the next stage; and the task that filed it. A refusal prints what is missing and the commands allowed next. A
refusal never counts toward a bot's quarantine.

## API

| Route | Body | Does |
|---|---|---|
| `POST /api/v2/tasks` | `title, message, owner, pipeline?, parent?, due?, labels?, private?` | create |
| `GET /api/v2/tasks/{id}` | | the task, full |
| `GET /api/v2/tasks` | | brief rows |
| `POST /api/v2/tasks/{id}` | `version` + fields | field edits only |
| `POST /api/v2/tasks/{id}/done` | `note?, version` | the work of this stage is finished |
| `POST /api/v2/tasks/{id}/move` | `to, why?, version` | forward, push past, back, done, close, reopen |
| `POST /api/v2/tasks/{id}/move` | `to_pipeline, to?, why?, version` | to another pipeline |
| `POST /api/v2/tasks/move` | `ids` or `from: {pipeline, stage}`, `to_pipeline?, to?, why?` | many: to another pipeline, or (`to` alone) each in its own: `{batch_id, moved, skipped: [{id?, code, reason}]}`, at most 500 |
| `POST /api/v2/tasks/owner` | `ids` or `from`, `owner, why?` | reassign many; a person's: `{batch_id, changed, unchanged, skipped}` |
| `POST /api/v2/tasks/snooze` | `ids` or `from`, `until?, why?` | snooze many; a person's: as above |
| `POST /api/v2/tasks/{id}/owner` | `owner, why?, version` | reassign |
| `POST /api/v2/tasks/{id}/snooze` | `until?, why?, version` | snooze |
| `POST /api/v2/tasks/{id}/unsnooze` | `why?` | unsnooze |
| `POST /api/v2/tasks/{id}/try-again` | `why?` | a person hands a task back to its owner |
| `POST /api/v2/tasks/{id}/asks` | `to, line, kind?, show?, version` | ask a person |
| `DELETE /api/v2/tasks/{id}/asks/{requirement_id}` | `why?` | withdraw |
| `POST /api/v2/tasks/{id}/asks/{requirement_id}/answer` | `value, note?, version` | ok or no |
| `POST /api/v2/tasks/{id}/asks/{requirement_id}/give` | `to, version` | give it to another person |
| `POST /api/v2/answers/{id}/undo` | | take an answer back |
| `POST /api/v2/task-bulk` | `filter, action, ...` | people only |
| `POST /api/v2/tasks/{id}/questions` | `to, text` | a bot's ❓ on the task's chat |
| `POST /api/v2/questions` | `to, text, conversation_id?` | a bot's ❓ in a direct chat |
| `POST /api/v2/questions/{id}/give` | `to` | give a ❓ to another person |
| `GET /api/v2/needs-you` | | Needs you |
| `GET /api/v2/pipelines`, `GET/POST /api/v2/pipelines/{id}`, `POST /api/v2/pipelines/{id}/retire` | | settings |

`to` is `next`, `done`, `closed`, `reopen`, or a stage's id or name. A refusal is 409 with `{"error": <rule>,
"detail": <one line>, "missing": [...], "next": [...], "version": <current>}`.

An OK bound to what was shown has its own routes: `GET /api/v2/oks/{requirement_id}` (the preview, with the
`shown_hash` to send back), `POST /api/v2/tasks/{id}/asks/{requirement_id}/ok` (`shown_hash`; the person's own click),
`POST /api/v2/tasks/{id}/oks` (a bot asks: `to, line, subject, content, show?, version`) and
`POST /api/v2/oks/{requirement_id}/use` (`subject, content, note?`; once, just before the act).

The task as JSON, field by field, is in [the cut-over plan](dev/pipelines-v3-cutover.md#the-task-as-json).
