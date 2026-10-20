# Task types and steps

Every task starts on **General**, with the same statuses and behavior as before. To give work a
pipeline such as Marketing, a mover makes a type in **Settings → Tasks**, adds named steps, and
chooses the status each step means. Types belong to the whole team. General keeps one step for
each standard status.

Choose **Type** when creating a task, or change it in the task modal. A custom type shows a **Step**
dropdown; General shows **Status**. Beside task search, **General** and **Dev tickets** select the task type; the arrow menu lists
other types. A type is always selected, and both the list and board show only its tasks. The board
uses that type's steps as columns. Your selection is remembered, shared links carry it, and new
tasks start with that type. Clearing filters keeps the selected type. Older combined views and
deleted types fall back to General. Finished work appears in the selected type's
Done or Closed steps as well as under Done.

Picking a step sets the task's status. Steps can move in any order. Bots, older runners and
existing scripts can keep setting `status`:

- If the current step has that status, it stays there.
- Otherwise Tico picks the first step with that status, in the type's step order.
- If none matches, the step clears. The task keeps its status, and the UI shows that status until
  someone picks a step.

Changing a task's type preserves its status and finds a matching step in the new type. Use an
empty step to clear just the step. Status permissions, completion notes, closing and reopening
work the same way for step changes. Moving between two steps with the same status changes only
the step, without another completion note or notice. Step changes appear in the task history.

Movers (the owner and humans on the leadership, product or engineering teams) create, rename,
reorder and delete types and steps. Move every task off a step before deleting it, including
finished tasks. Move tasks to another type before deleting the type. Changing an occupied step's
status is refused: move the tasks explicitly or create a new step. Renaming and reordering steps
preserves task statuses and history.

A task on a custom type is a ticket on that type's board, not an ask. The rule that shapes a
request to a person (a title that starts with a verb, the ask in the first line, under 120 words
outside quoted drafts) applies to General tasks only, so a ticket keeps the title and the long
description it was written with. The plain-English check on a title a bot writes (no reference
numbers, no all-caps words) also applies to General tasks only, so a bot can file "#18945 (B/F)
Fix the account page" as written. It still needs a title. A decision for a person stays on General.

Whoever may change a task's other fields (its owner, its requester, the owner or requester of a task
above it, a delegate or a mover) may also rename it. A new title gets the checks a new task's title
would: never empty, at most 300 characters, a verb first and no internal codes on a General task for
a person, plain English when a bot writes it on General, and no other live task between the same
requester and owner, under the same parent, with that title. The old and new titles are in the task's history, and a task's own conversation keeps the
new title as its subject.

A linked pull request never moves a task, on any type: opening, merging or closing the PR, a
release tag and a deploy only change the link's state. A person (or a bot) moves the task.

## Work that is waiting

Keep actively worked tasks in **Doing**. When another task must finish first, relate the blocker,
then set **Waiting**:

```sh
hub task relate <task> <blocker> --kind blocked_by
hub task update <task> --status waiting --note "Needs the build environment"
```

### Waiting for a milestone, not the whole task

Often what a task needs is an earlier milestone of another task, not that task being done: a pull request merged
while its ticket stays In QA, or a release published while its checklist stays open for its QA watch. Wait for the
milestone itself:

```sh
hub task wait <task> --on pr:https://github.com/<owner>/<repo>/pull/943   # until that pull request merges
hub task wait <task> --on pr:<other task>                                  # the one pull request linked on it
hub task wait <task> --on release:v0.3.64                                  # until that release is published
hub task wait <task> --on task:<other task>                                # the same as blocked_by
hub task wait <task> --on pr:<url> --remove                                # take it off
```

The task shows it ("Waits for PR #943 to merge"). When the pull request merges or the release is published (the
GitHub webhook), the owner gets one wake; the task it came from keeps its own step, and nothing is closed or moved.
A wait made after the milestone is woken once by the minute sweep instead, and a merge or release whose webhook
never arrived (the server was down) is checked with GitHub every few minutes, in repositories the GitHub App reaches. Replays and the sweep never wake it twice. `release:` takes the server's release repository unless
named (`release:<owner>/<repo>@v0.3.64`). The MCP tool is `hub_task_wait`; the API is
`POST /api/v2/tasks/{id}/waits` with `{"on": "pr:<url>", "remove": false}`; each task answer carries
`milestone_waits`.

When the task waits on a person to act instead (a decision, access, a host only they can fix),
name them with `--on` and say plainly what you need in the note:

```sh
hub task update <task> --status waiting --on ana --note "Restart the build host? It refuses SSH, and I'll rerun the deploy after."
```

Tico turns that into one small subtask owned by the person: its title is the ask (its question, when it has one),
its body is the note, and Decide shows it with Yes and No when it asks a yes-or-no question. The bot's task stays the
bot's: it is Waiting, blocked on that subtask, with the note "Blocked on Ana: <the ask>", and it is not itself a card
in Ana's **Needs you**. The update returns the subtask as `asked_in`. When Ana marks it done or declines it (Yes or
No on her card), the task opens again with "Ana answered: <what she said>" as its note and the bot is woken once on
it. Nobody asks her again whether she answered.

One question, one card. If this rule already filed that person a card in the task's tree (its root and everything
under it), no second card is made: the task is blocked on the existing one, and a different ask is added to it as a
comment. Only such cards are joined, never another task the person was given, and never across privacy: a private
task's ask gets its own private card, one per task, which its later asks join. A bot's own open question to the person under the task (`hub task
question`) is the card too. `hub task question` asked from a run on one of the bot's own tasks is filed under that
task when no `--parent` is given. The answer follows the blocker link, so a progress note the bot adds while it waits
doesn't lose it, and a declined or closed card no longer blocks the task. The note says the person answered only when they
finished the card; when someone else closed it, it names who did.

One open question per topic. A bot's `hub task question` to a person while its earlier one on the same topic still
waits on them (unanswered, the task not finished) updates that one instead of filing a second: the same topic is the
same `--topic` key under any task, or, with no key, the same parent task. The person keeps one card with the latest
wording and options, its body lists the earlier asks under "Asked before", and nothing new is sent: no second Needs
you item, no Slack line, no run. The ask's clock restarts, so a default goes ahead only after the team's days from
the latest wording. The reply says `folded: true`. A different decision on the same task takes its own `--topic`;
once the question is answered, declined or closed, the next one on the topic opens a new card. An answer picked on
the earlier wording is refused ("reworded"), so the person answers what the card says now. A key is compared by its
letters and digits in any script, case folded; one with none is refused.

The note must be a plain ask: one or two sentences, at most 40 words, with no ids, hashes, file paths, `s3://` links
or tool names. Without one the update is refused, saying what failed. A task with an open structured question to
the person (`hub task question`) is already the card: it waits on them as itself, and its note is only warned
about. Only the bot that owns the task names the person, and a private task can only ask a person who can read it,
or, on a private task with no person on it, the one person it brings in as its guest (see "A private task with no
person on it: its guest").

Bot tasks that were waiting on a person before this rule are moved to it within the hour when their note is a plain
ask; the rest stay last in the person's Needs you while their bot is asked once to restate the ask.

Needs you, Decide and the Assistant's lineup put work that traces back to a person first: a
person requested the task, or it sits under a task a person requested (only tasks the viewer may
open count). A person's own to-do does not lift itself. A bot's own work follows unless it is due
within a day, overdue, or has waited three days (one day for an approval or a question). A card
still waiting for its bot to explain stays last. Decide shows "From <name>" on such a card; the
item's `from_person` and `pressing` fields carry the same facts.

A bot may do this for its own task, including a task it requested itself. The blocker must exist
and be accessible; a finished blocker does not justify waiting. An unanswered question, an open
child task, a pending approval or a person named with `--on` can also justify waiting. Finishing a blocker clears the dependency
and wakes the waiting bot. **Waiting** does not mean a person must review the code.

When another bot must act, file a subtask for it; finishing the subtask wakes the waiting task:

```sh
hub task create --parent <task> --owner botops --title "Send the disk usage snapshot"
hub task update <task> --status waiting --note "Needs BotOps's disk snapshot"
```

A note alone wakes nobody. A bot's task waiting with no blocker, subtask, approval, question or
person goes back to open after two hours (unless its due date says to look later), and the update
that parks it warns the bot.

For unblocked open or doing tasks, automatic stalled-task wakes are limited to three in a rolling
24 hours, spaced at least 30 minutes apart. Updating a note does not reset that limit. Repeated
stalls are escalated to BotOps with a separate diagnostic for each source task. If BotOps itself is
stuck or unavailable, the diagnostic goes to the human requester, or the team's default human for
bot-requested work. An unresolved diagnostic is reused on later days. Health warns when repeated
runs have escalated a public task without progress; an explicit run is still available.

Task worktrees have a separate lifecycle. The 50-worktree limit stays in effect, and cleanup
waits until the owning bot is idle so it cannot remove a checkout in use. A supported cleanup
request continues to occupy its slot until the runner removes it. For merged work that is still
awaiting release, request cleanup of the worktree link rather than marking the task Done or Closed
just to free space.

## Picking work back up

Each completed turn on a task leaves a **checkpoint** on it: the turn's final reply, up to 1,500
characters, with when (`checkpoint_at`) and which bot (`checkpoint_by`). The full task view
(`GET /api/v2/tasks/{id}`) and the task a run is handed both carry it; task lists leave the text
out. When a turn on the task starts a fresh session (no saved thread to resume), its prompt opens
with where the task stood after its last turn. A reply that drew on a private task the task's
readers cannot read is not kept as a checkpoint.

The task's **Runs** section (`GET /api/v2/tasks/{id}/run-view`) shows that checkpoint ("Where it left off") with its
author and time, then the task's last 20 attempts, newest first. Each try says its outcome, why it failed or lost its
lease, and what followed it: run again automatically or by a person, resumed or dismissed with the decision's note, or
waiting for a person. Under it are the subagents the try started, as a tree with each one's type, model, duration and
result, and the shells and monitors it ran in the background; one still running is also listed at the top while the
run is live. Background work is reported by Claude runs; other harnesses show none. Only the file or program a call
touches is shown, never its arguments. An attempt is left out unless you can read its bot, the conversation it
answered and every private task it carried; an automatic decision shows the hub's own words, never what the run saved.

Waiting work is not starved: a bot's queued jobs are claimed oldest first (a person's chat message
still goes ahead), so a newer task never jumps one that has been waiting. Task ranks stay as people
set them.

A task still on the board with no activity for 7 days (no update, comment or checkpoint, no run
going, and no question already waiting on someone) gets one **Still wanted?** question with
**Keep** and **Close**: to its requester when a person asked for it, otherwise to its owner when a
person owns it; a task only bots are on is left alone. It shows in that person's Needs you and
Decide. **Keep** restarts the clock; **Close** closes the task as theirs with a note. Nothing
closes without the answer, and the task is asked again only after new activity and another idle
week (`stale_asked_at`). While the team has **Stale ticket review** on, these questions stop: its
suggestions cover the quiet week.

### Stale ticket review

Stale ticket review finds open tasks that look finished or forgotten and suggests what to do with
them. It only suggests: nothing changes unless the task's owner accepts.

**Turn it on.** Go to **Settings > General** and switch on **Stale ticket review**. It is off by
default, and an owner or an admin can turn it on or off. (A server started with
`TICO_STALE_REVIEW=1` has it on by default; a choice saved in Settings wins.)

**What it looks for.** Once an hour, Tico checks every open, doing, waiting and review task that is
not snoozed and has no run going. A task is a candidate when one of these is true:

- every pull request linked to it is merged, closed or shipped;
- its parent task is done or closed;
- every task blocking it is done;
- its snooze ended more than a day ago and nothing has happened since;
- it has the same title as an older open task with the same owner;
- nothing has happened on it for 7 days (3 days for a task in progress).

Private tasks are never looked at, so none of their words go to Tico Decide. Tico Decide judges
only a candidate that is new or has changed since it was last looked at, up to 30 an hour; the
rest wait for the next hour. An hour with nothing new does nothing: no Decide call, no comment, no card.

**What the owner gets.** At most one suggestion per task a week, and only a suggestion:

- **A bot** gets one comment on the task, for example "Stale check: PR #12 merged Oct 16. Suggest:
  mark done." The bot decides what to do.
- **A person** gets one **Stale tickets: N suggestions** card in **Needs you**, with a row per task:
  the reason and the suggestion (**Mark done**, **Close it** or **Check it is still needed**). Each
  row has **Accept** and **Skip**; **Accept all** accepts every row on the card that has an
  **Accept** button. A **Check it is still needed** row has only **Skip**: open the task to decide.

**Accept does what you could do by hand, and nothing more.** The same rules apply: the owner
can mark a task done, and its requester or another authorized person can close it; anything else
is refused. If the task changed after the card showed it, that row is refused and stays on the card,
so open the task to see what changed. **Accept all** applies only the rows you saw: a row that
arrived since stays on the card.

**Still wanted? questions stop.** While Stale ticket review is on, Tico doesn't send the
**Still wanted?** questions described above: its suggestions cover the quiet week.

### When nobody answers a bot's ask

A bot's question may say what it will do if nobody answers (`hub task question ... --if-no-answer "Yes"
--default-kind reversible [--by <date>]`; MCP `if_no_answer: {do, kind, by}`), and the card says it: "If you don't
answer by Fri, Oct 16, Writer goes ahead: Yes". When that step is reversible and internal and the person has said
nothing by then (the team's days after the ask, 3 by default, or its later `by`; `by` is at most 14 days away and
`--default-kind` is required with `--if-no-answer`), Tico answers for them with "No answer from Ana by
Oct 16, so Writer goes ahead with what it said it would do: Yes", which wakes the bot with that choice. A send,
spending money or a delete never goes ahead without an answer: the card says the bot waits. The owner turns this off,
or changes the days, in Settings > Humans (**Bots go ahead on their recommendation**).

An ask to a person with no word from them in 14 days expires: the bot goes ahead on its default when the rule above
allows it, or is told to drop the work or ask again. A task a bot handed a person (they own it, the bot asked for it)
that they have not touched in 14 days closes the same way, unless it is due later or waits on someone else, and the
bot hears why. Hourly, `backend/ask_deadlines.py`.

## Snoozing a task

**Snooze** sets a task aside until a date: it tells its bot or person that nobody plans to get to it
before then. Its requester, its owner, anyone on it by role, or a mover snoozes it from the task's
**…** menu (Tomorrow, Next week, In two weeks, In a month, or a typed date), with
`hub task snooze <id> --until 2026-11-02`, `hub_task_snooze` or `POST /api/v2/tasks/{id}/snooze`
with `{"until": ...}`. A bare date means the start of that day, Pacific; a date-time with a zone is
taken as it is. Snoozing again moves the date. The task keeps its status, owner, goal and place;
`snoozed_until` and `snoozed_by` say until when and who. A snoozed task still counts toward its goal.
When someone other than its requester snoozes a task, the requester is told.

Until that moment:

- it is off the board, the Open list, Needs you, Decide and `hub task list`. The **Snoozed** tab on
  the Tasks page (shown while something is snoozed), `hub task list --snoozed only|all` and
  `GET /api/v2/tasks?snoozed=only|all` list it; a read by id or number always finds it.
- nothing automatic moves it: no stall wake or status check, no stranded or stuck sweep, no
  **Still wanted?** question and no **Due:** reminder. Tico's notices about it reach its owner bot
  quietly, read on the bot's next run instead of starting one, and so does another bot's comment.
  A person's comment still reaches the bot as usual, **Run now** still runs it, and notices to
  anyone else on it (a requester bot it is handed to for review) arrive as usual. A snooze survives
  reassignment: the new owner hears of the task quietly and is woken when the date comes.

When the date comes, the task is back everywhere and its owner bot gets one **Snooze over** notice,
which also stands in for a due date that passed during the snooze. **Unsnooze** (the **…** menu, the
**Snoozed to** chip on the task, `hub task unsnooze <id>` or `POST /api/v2/tasks/{id}/unsnooze`)
brings it back early and wakes its owner bot once. Finishing, declining or closing a task, or
moving it to review, ends its snooze.

## Done and Closed

**Done** means the owner reports that the work is complete, with a result note. **Closed** means
the requester or an authorized human accepts the result or cancels the task. Closing unfinished
work does not mark it Done. Both statuses appear in the **Done** view; the task history keeps
the separate completion and closing events.

A person finishes a bot's request in one tap: **Done** asks nothing. A comment typed in the box
where you press Done (the task's message box, the note box on a Decide card or the reply box on a
Needs you card) and not yet sent goes with Done as the outcome; a card's Done never takes one left
in the task window. Done on several selected tasks at once sends no comment: an unsent one stays in its
task's box. The bot that asked is told either way, as *<name> marked this done: <title>*, with the
comment on the next line when there is one. When a bot needs a specific answer it asks a yes/no
or choice question instead.

Done stays Done, including tasks the owner requested for itself, old bot-requested tasks,
and completed routine runs. Closing is a separate decision; it records `closed_at` and
`closed_by`, preserving the completion time if the work was already Done. Earlier history
is preserved, including tasks that older versions closed automatically. Use **Close task** in the
Done task’s menu, or bulk **Close**, to accept completion. A waking comment delegates further
work, not permission for the assignee bot to accept its own result.
Completed work does not block a new task with the same requester, owner and title; unfinished
duplicates are still refused.

## Types bots work on

Every bot can read company-visible tasks subject to existing bot activity restrictions. Types
control additional permission to comment, file subtasks or change work in **Settings → Tasks**,
with `bots` on the task-types routes or `hub task type update "Dev ticket" --bots work`.

| `bots` | Additional permission for unrelated bots |
|---|---|
| `parties` (the default) | None beyond reading company tasks |
| `read` | Comment on company tasks and file subtasks |
| `work` | Also change step, owner, due date, description, order and links |

Private overrides every type permission. Closing, ready or closed steps keep their existing
human controls. Changing type cannot publish a private task.

Labels are a mover's, with one exception for bots: a bot adds and removes classification labels
(`qa`, an area, a priority) on a task it owns, asked for, or whose type it manages (the type's
`manager`), whatever the type's `bots` setting. A public roadmap label needs its exact approved
publish approval (`hub task publication-preview <id> --label ...` for the labels the task keeps,
then `hub task label <id> --add roadmap --approval-id <id>`), and `goal:on`/`goal:off` need a mover.
The same goes for a task a bot creates: it may carry classification labels, but one created with a
public roadmap label or `goal:on`/`goal:off` is refused before anything is written; create it without
the label, then add it on the task as above.
Lane and number are a mover's alone ([Permissions](permissions.md#tags)).
A bot's comment is a message to the people on the task, under the usual rules for reaching them.

A person's comment wakes the task's bot when the bot owns the task, and is never quiet on a task
with no bot on it. On a task a bot filed and a
person owns, the owner's comment wakes the bot that asked them, @mention or not: it is almost always
a reply. Anyone else's comment there wakes the bot only when it @mentions the bot (`@ops`, or its
name) or the task waits on that person; any other comment reaches the bot on its next turn. A person @mentioned in a
comment (by id, the part of their email before the @, full name, or first name when no one else
shares it) gets the comment as a question, which puts the task in their Needs you; when the
comment also wakes the bot, or names several people, each person gets their own copy of the question.

## Numbers and the order within a step

A mover can make a custom type **numbered**: a board of tickets, worked through on the board. Its
tickets stay out of their owner's **Needs you** unless one asks that person something, and a declined
one stays out of its requester's. Each task created on a numbered type, or moved onto one, gets the
team's next number: one sequence for the whole team, like one board's ticket numbers,
the highest number yet plus one, starting at 1. A number never changes, even if the task later
moves to another type. Turning numbering on does not number the tasks already on the type. To keep
an imported ticket's number, a mover passes `number` when creating it, or gives it once to a task
that has none; a number another task has is refused (`422 duplicate`). Wherever a task id is
accepted, `#18945` names the task with that number (`%2318945` in a URL; quote it in a shell).
The digits alone are not read as a number, since a cut-short id can look the same.

Numbers range from 1 to 999999999; an exhausted sequence refuses a new number without creating a task.

A mover can also make a custom type **steps only**: its tasks are finished or brought back only by
moving to a step. Closing, declining, reopening or finishing one directly (`close`, or a `status`
without a `step` that enters or leaves done, closed or declined) is refused (`422 kind`) for every
person and bot, and the task window offers no Done, Close or Reopen on them. A task that should not
be on the board is deleted instead. Working statuses still change without a step (a bot's ask sets
waiting), and filing a task onto the type and the keeper's own automation are unaffected. Such tasks
are not asked "Still wanted?", their goal asks the bot to move them to a step and a met goal does not finish them, and Needs you says to move them
rather than marking them done or closed. Without it, a bare status lands the task in the first step
with that status, which on a board is rarely the one anyone meant.

A task also has a place within its step, `step_rank`, lower first, apart from `rank`, its place in
its owner's queue. A task that enters a step, when it is created or its step, type or status
changes, goes to the end of the step, or to the top when it is created with `top`. The people on
the task and movers set `step_rank` to move it within the step. On the board, a type's columns are
in that order.

## CLI and MCP

```sh
hub task types
hub task create --owner content --title "Draft the campaign" --body "Use the brief." --type Marketing
hub task update <task-id> --step "Legal review"
hub task update <task-id> --title "(B/F) Account page: improve the copy"
hub task update <task-id> --status review
hub task update <task-id> --type General
hub task update <task-id> --step ""
hub task type update "Dev ticket" --numbered --steps-only
hub task create --owner ben --title "(B/F) Fix the account page" --type "Dev ticket" --number 18945
hub task show '#18945'
hub task update '#18945' --step "On deck" --step-rank 2.5
hub task list --type "Dev ticket" --sort step
```

`hub_task_types` lists types and ordered steps. `hub_task_create` and `hub_task_update` accept
`type` and `step`, as ids or names, and `number`; `hub_task_update` also takes `step_rank` and `title`, and
`hub_task_list` takes `type`, `step` and `sort`. Movers manage definitions with `hub_task_type_create`,
`hub_task_type_update` and `hub_task_type_delete` (with `numbered` and `steps_only`), or `hub task type create|update|delete`.

For example, put this JSON array in `steps.json`:

```json
[{"name":"Draft","status":"open"},{"name":"Legal review","status":"review"}]
```

Then run `hub task type create --name Marketing --steps-file steps.json`. To edit steps, first
read `hub task types Marketing`, keep the ids of retained steps in the JSON array, and run
`hub task type update Marketing --steps-file steps.json`. Array order supplies positions unless
explicit positions are provided. Omitted steps are removed; new steps omit `id`.

## API and SQL

`GET/POST /api/v2/task-types` list and create types. `GET/POST /api/v2/task-types/{id}` read and
update one. `DELETE /api/v2/task-types/{id}`, or `POST /api/v2/task-types/{id}/delete`, deletes an
unused type. Create and update accept `numbered` and `steps_only`; updates accept `name` and a replacement `steps`
array, retaining existing step ids. A type's answer includes `role`: `product-roadmap` or `engineering` on the types
roadmaps follow ([roadmaps](roadmaps.md)), else null; Tico sets it, no write does. All writes use the existing
Idempotency-Key contract. Task
create and update accept `type`, `step` and `number`, and update accepts `step_rank` and `title`; task answers
include `type_id`, `step_id`, a `type` object, a `step` object (null when unmapped), `number` and
`step_rank`.

`GET /api/v2/tasks` takes `type` and `step` (ids or names; a step name without `type` means that
step in every type), `number`, and `sort=step`: by the step's position, then `step_rank`, then
when the task was created. `GET /api/v2/tasks?type=Dev%20ticket&sort=step` is a board's columns,
in order. Add `updated_since=<ISO-8601 time with timezone>` to poll only changed tasks; `brief=true`
leaves out bodies and acceptance criteria. `hub task list` and `hub_task_list` accept the same filters,
including with `--all`/`all`, and support `number` lookup.

Type create and update also accept `bots` (`parties`, `read` or `work`). With `work`, any bot may
change a task of that type and close it once it is in a ready step, so a deploy bot can move shipped
tickets to Done; moving a task into ready stays with people.

`task_types` and `task_steps` are readable through SQL. Join them to the caller's visible `tasks`
using `tasks.type_id` and `tasks.step_id`; the task visibility rules still apply.

## Subtasks and PRs

Subtasks can nest to any depth. Whoever files a subtask, person or bot, is its requester. When a
worker finishes a subtask under a bot-owned task, its result returns directly to the immediate
parent's owning bot, in that parent's task session. An active turn takes it as an input; otherwise
the parent's next turn resumes there. There is no requester delivery session on the completed
subtask and no relay or `hub task run` needed. The worker keeps its own independent task session.
A bot that filed the subtask may close it explicitly; until then it stays Done. The parent's
requester hears when the parent itself finishes, so a manager's task → builder task → review
subtask sends the review back to the builder, and the completed build back to the manager.

A review verdict on a subtask's comment or final reply takes the same direct path. Its comment,
Done note and final reply are one delivery for that verdict and review cycle; reopening the
subtask permits a new review. Completed or closed parents record late results quietly. Private
subtask text returns on the parent only when that parent's readers may receive it; otherwise the
requester receives it on the private subtask as before. Ask and answer messages keep their own routing.
Subtasks never inherit a human's delegation anchor from a bot's request. BotOps acts with the
filing bot's rights when that bot asks it to work on a subtask.

Parent owners can track descendants they can read, without gaining Read on hidden bots.
Only the task's owner or requester, or a human mover, can re-parent it; bots must also own,
request or manage the new parent. Moving or detaching a subtask also requires its current
parent's owner or requester, or a human mover. Moving a task moves its whole subtree,
and cycles are refused.
A parent may be marked Done or closed, by a bot or a person, while subtasks are still open. The
subtasks stay open, and the parent's history and its Finished or Closed notice end with
"Still open:" and their titles. Finishing or moving away the last open subtask wakes the
immediate parent's owner with “All subtasks done”, unless its result already resumed that parent or
that owner made the change (a bot finishing a subtask it filed
for itself, which runs in its own session, still wakes the parent).
Closing a parent cancels it; later subtask completion does not wake its owner.
Done, Closed and Declined count as finished. Open descendants under a finished subtask do not
block its parent.

```sh
hub task child <parent-id> --owner engineer --title "Build the service" --body "Use the plan."
hub task tree <task-id>
hub task relate <task-id> <new-parent-id> --kind parent
hub task relate <task-id> <parent-id> --kind parent --remove
```

The corresponding MCP tools are `hub_task_child_create`, `hub_task_tree` and `hub_task_relate`.
`hub task create --parent <id>` files a new task as a subtask; the API takes
`"relations": [{"task": "<id>", "kind": "parent"}]` on task create.
`GET /api/v2/tasks/{id}/tree` returns nested subtasks with `id`, `title`, `status`, `owner`,
`pr_state` and `children`. Task detail and list answers include `children_summary` with descendant
counts: `total`, `open`, `done`, `prs_total` and `prs_merged`, plus `direct_total` and
`direct_done` for direct children. Counts include only visible subtrees. Totals include all
visible descendants; `open` ignores work below finished subtasks. Closed, unmerged PRs are
excluded from the PR totals.

Attach as many PRs as the task needs with `hub task link`. `GET/POST /api/v2/tasks/{id}/links`
list or attach links; `DELETE /api/v2/tasks/{id}/links/{link_id}` removes one. The older POST
with `remove` still works. People who can read a task can add or remove its links. Bots
need task rights; worktree links keep their worktree rules.
PR URLs outside the connected GitHub org are plain links. PR links include repository,
number, branch, checks, mergeability, review state and pending review comments. The task's `pr_state` shows the worst active PR:
Failing, Conflict, Changes requested, Open, then Merged. Closed PRs are excluded; shipped PRs
count as merged. A PR link is information only: adding, removing, opening, merging, closing or
shipping a PR leaves the task's status and step as they are.
`hub task link <id> <pr url> --wake-once` arms one wake for the PR's next finished checks, approving or
change-requesting review, or release ([GitHub App](github-app.md)); it also leaves the task as it is.

Opening a task returns its last known PR state immediately and schedules a background
refresh for repositories the App can reach. Refreshes are grouped, capped at 20 links and
cached for three minutes; failures back off from five minutes up to one hour. People
can still move tasks without webhooks or a successful refresh.

Failing checks, new conflicts, requests for changes, review comments from others and PRs
closed without merging wake the owner with the specific item, grouped into one notice within
three minutes. Conflicts wake once per head until a known clean result clears the marker.
A new push resets mergeability to Unknown while GitHub recomputes it; label and text edits
keep the last known mergeability. Pending or passing checks and the bot's own comments
do not wake it. Tico recognises the GitHub App identity, configured bot login and PR author.
A head pusher counts as the bot only when their login matches the PR author. Requests for
changes always wake the owner, including requests from those identities.
A merged PR becomes Shipped once the running release includes its merge commit; the task stays
where it is. PRs in another repository stay Merged.

Sessions show who is working a task ([Sessions](sessions.md) is the overview). The primary session is the owning bot's task session, recorded
by Tico when its turn starts in the task's own lane (every bot by default, unless set to `session: bot`, on a runner
that runs lanes); a bot's shared thread is never one. The task's set-up worktrees on that computer are added to
its session, with each repo's AGENTS.md (else CLAUDE.md) in the prompt as rules for writing code there. A task has at most one primary: a newer thread or a
new owner's session replaces it, and the old one stays as an alternate. Anyone with link rights can add other sessions to track:
a thread URL, a `claude --resume <id>` or `codex resume <id>` command, or another reference
(`hub task link <id> <value> --session`, `hub_task_link` with `kind: session`, or Sessions + on the task).
They are for tracking only: task chat and comments go to the owning bot, or to the bot that asked when a person owns the task.

A subtask someone else files for a bot under its task does not get a session of its own. A turn on a task runs in its
**session root**: Tico walks up the parents the same bot owns (up to 20) and takes the topmost, so the bot's turns on
such a subtask continue its parent's session, in the parent's lane and worktree, one at a time with the parent's
turns. A subtask the bot filed for itself is its own root: it starts in its own session and lane, beside its parent
and the bot's other tasks, up to the bot's turn limit. A parent owned by someone else, or one with different readers
(one task private and the other not, or two private tasks whose people cannot each read both), ends the walk, so a
bot's subtask under a person's task is its own root. The primary session, the Copy session
ID and the checkpoint all live on the root, and a fresh session on such a subtask starts from the root's checkpoint;
such a subtask shows no session.
`subtask_session: own` on a bot (`hub bot update <slug> --subtask-session own`, `hub_bot_update`, or the bot
definition API) keeps every subtask its own session; unset or `parent` is the default. A runner from before this
change keys every task, subtasks included, on its own session, and Tico gives it lanes the same way.

### A subtask on a check model

A bot can have another model look at its work without a second bot:

```sh
hub task child <parent-id> --check-model grok --role review --title "Review PR 655" --body "https://github.com/..."
hub task child <parent-id> --check-model codex:gpt-6.1-sol --title "Check the migration plan" --body "..."
```

`--check-model` takes a runtime (its provider's recommended model) or `runtime:model`. With no value (`auto` in the
API) it takes the company's check model (Settings > Computers > Models, [How it works](how-it-works.md)): the first in its
order from a provider other than the parent's authors and runnable on the bot's computer. The authors are every
maker on the parent's finished runs (a fallback counts) and the provider its owner runs on; with no other provider it
is refused, never run on an author's own. `--role` is `review` or `check` (the default). The API takes `check_model` and `check_role` on task create; the MCP tool is
`hub_task_child_create` with the same fields.

- The subtask is the filing bot's own, under a task it owns. Its turns run on the named runtime and model with no
  fallback to another provider, always in a session of their own, and read only the subtask's room: name the PR
  or files in its body. Its turns carry none of the bot's next-run tasks or notes.
- Only the runtime, model, harness and effort change. The bot's repository, tools, credentials and isolation are
  its own, so a check never runs with more than the bot has.
- Only a provider the company has enabled (Settings > Computers > Models) and a current model can be named, and the
  bot's computer must not report that runtime missing or signed out. A computer that reports it signed out is not
  handed the subtask's turns.
- Task detail shows the subtask's `check_model`, and the run's provenance (`ran_on` on the task's runs) names the
  model that ran. When the bot marks the subtask Done or Declined, its owner is woken on the parent with
  "Review done (ran on <model>): <title>" (or "Subtask declined") and the note, so the verdict lands in the parent's
  session. Closing it is a cancellation and wakes nobody.
- A usage limit on the check's model holds that subtask alone until the provider's reset: the bot's other work
  goes on, on its own model, and the bot is not marked limited. Held 12 hours with every turn stopped on the limit,
  it is a Health Monitor setup finding (`check_limited`, docs/health-monitor.md). Once the subtask is done, closed or
  declined, its held turn is let go instead of retrying after each reset; a person's message on it stays queued.
- When the limit is on the computer's default login (no subscription) and Health already marks that login out of
  quota (`default_login_limited`, docs/health-monitor.md), the check does not wait: its next turn runs at once on
  the next check model in the company's order (Settings > Computers > Models) that is not from a provider whose work it
  checks, not on another login out of quota there, and that the bot's computer can run. The task's history and the
  bot's status say where it moved. It goes back to the model it asked for once a turn on that login completes or
  the provider's reset passes, or once the bot moves to another computer or gets a subscription for that tool. With
  no next model, it keeps waiting as above.
- The check model belongs to the bot that filed it while it owns the subtask. Handed to another bot, the subtask
  is that bot's ordinary task, on its own model. Subtasks under a check continue the check's session, never the
  parent's.
- Known limits: readiness is read per computer, not per subscription profile; a check model cannot be removed;
  a provider disabled after filing still runs; a computer that stays signed out of the check's runtime leaves the
  subtask waiting with no notice; a check held by its model's limit waits for the reset even when a person comments
  on it or presses Try now.
- A reviewer bot set to `review_model: check` (`hub bot update <slug> --review-model check`, `hub_bot_update`, or the
  bot definition API) runs every subtask it is handed under another's task as a `review` on the company's check
  model, chosen against the parent's authors the same way, and its verdict comes back the same way. When the authors
  are unknown (a person's task with no runs) or no other provider can run it, the subtask is filed as usual on the
  reviewer's own model, and its history says why. Subtasks under its own tasks, and its top-level tasks, keep its
  own model.

Every task a bot owns has its own room (a `kind: task` conversation), even one filed from a chat: its comments,
notices, notes and replies go there, and the chat it was filed from gets one quiet line, "Started task: <title>"
(`refs.task_started`). A bot's open task filed into a chat room before this moves to a room of its own the next time
a turn on it is claimed; its earlier messages stay in the chat, and the new room opens with a line saying so. A
person's own tasks, and the Assistant's (whose turns act for the person of the room), keep their chat room.
A turn reads only its own lane's messages: the main session reads the main chat, with no task traffic, and a task's
session reads that task (and its subtasks that share the session). The main chat hears where its bot's tasks got to
from short quiet lines (`refs.task_report`): `<title>: blocked.` when the task waits on a person (a person's subtask
under it, or `waiting --on <person>`), `done.`, `declined.` or `stuck.`, with the first 140 characters of the note
or checkpoint. These start no run; the main session reads them the next time something wakes it. The same state is
not reported twice in a row, and a private task is reported only in a room whose every reader may read it, otherwise
not at all. A runner from before lanes (`task_lanes_v2`) still reads the whole room.

The task's page shows that room as a chat with the task's session, under **Conversation**, in place of a list of
comments. The bot's updates and replies are messages, and each reply opens to the steps of the run behind it. What
people say shows on the right under their names, as in a bot's chat with several people; your own line has no name.
Changes to status, type, owner and the like are one-line markers between the messages. Under a message you send, the
line from the bot's chat shows what happens to it: **Sent**, **Queued** (and why, when the bot is busy or at a limit),
**Starting**, then **<Bot> is working…** with its newest step, opening to the steps so far, until the reply, or
**Reply failed** when the turn errors. It follows the bot your message went to, which on a task you own that a bot
asked is that bot, and the owner's own runs as before; a bot's message to another bot does not take it. Whoever
manages the bot gets **Stop** beside it, the same stop as in the bot's chat (`POST /v2/bots/{slug}/stop`). The box at
the foot is the chat's composer, and what you type there is a comment: it wakes the bot as before. Before you send,
the box says when no bot will get the message (everyone on the task is a person), or when it waits for the bot's next
run on the task; an @mention of the bot that asked wakes it, and the note goes. `GET /v2/tasks/{id}` carries this
view: each bot reply in `comments` has its `run`; `execution` is the owning bot's newest run in the task's own room,
or a newer one for a person's message there, shaped as in a conversation snapshot with `from_person`; and
`comment_to` (`bot`, `wakes`, `mention`) is who your next comment reaches. A task still in a chat room (one filed into
a chat before task rooms) has no `execution`: that room's runs belong to the chat.
`hub task comment`, `hub_task_comment` and `POST /v2/tasks/{id}/comments` are unchanged.

Tico spot-checks running work. A bot's open or doing task whose own session ran in the last day (or runs now) and
that has made no progress for 30 minutes (no checkpoint, comment, status change, message from its bot or finished
turn) gets one "Status check: checkpoint where you are, or mark it blocked or done." in its session. Thirty minutes
later with still no progress it is reported stuck in the main chat, and when a person asked for it they get a
question on it in Needs you, whose answer wakes the bot; the question is withdrawn when the task is done. These share the stall watcher's spacing and its cap of three
a day, after which BotOps gets a task to find out why.

`task_goal: on` on a bot (`hub bot update <slug> --task-goal on`, `hub_bot_update`, or the bot definition API; off by
default) works each task it owns as a goal: the turn carries `task.goal` (the title and its acceptance criteria), and
the bot is told to keep at it until that holds, then mark the task done with a summary, or file a subtask for the
person it waits on. A `goal:on` or `goal:off` label on a task overrides the setting. A person's task never has one.
On Claude Code and Codex (a runner with `task_goals_v1`) the goal is the harness's native goal, kept in the task
room like a chat goal: the first turn in the task's session sets it beside the task's prompt, and later turns
continue it. It pauses after the usual number of tries without progress, and waiting on a person, another bot, CI or
a review ends the turn rather than polling. Met: the task is marked done with the goal's reason if the bot has not
marked it already. Paused or stopped: the task stays open and the main chat gets a `blocked` or `stuck` line. Other
harnesses, and older runners, work it from the prompt alone.

When the requester (or a person) closes a task its bot had already marked done or sent for review, the bot is not
woken; a close note that differs from the bot's own done note still reaches it, without starting a run.

When a bot's task is blocked on a person's approval or input, the bot files a subtask for them under it
(`hub task create --owner <person> --parent <task> ...`, or `hub task question ... --parent <task>` for a pick or
Yes / No) rather than a new top-level task. When the person marks it done, declines or closes it, the bot is woken
once on the parent and picks up in the parent's session (a private subtask that not everyone on the parent may read
tells the bot on the subtask instead). A quick question stays `hub task ask` on the task, and a
send or publish that needs a yes stays `hub approval request`.

## Related tasks

Every link between two tasks is a relation, read `<task> <kind> <other>`:

| Kind | Means | Shown as |
|---|---|---|
| `parent` | the task is a subtask of the other (one parent a task; no loops) | Part of, and Subtasks on the parent |
| `blocks` / `blocked_by` | the task blocks, or waits on, the other (several blockers allowed; no loops) | Blocks, Blocked by |
| `related` | they belong together: the same bug seen twice, two halves of one change | Related |
| `duplicate_of` | the task duplicates the other | Duplicate of, Duplicated by |
| `follow_up` | the task was split off or followed up from the other | Follow-up of, Follow-ups |

```sh
hub task relate <task-id> <other-task-id>                      # related
hub task relate <task-id> <other-task-id> --kind blocked_by
hub task relate <task-id> <other-task-id> --kind parent --remove
```

The MCP tool is `hub_task_relate`; the API is `POST /api/v2/tasks/{id}/relations` with
`{"task": "<other id>", "kind": "...", "remove": false}`, and task create takes `"relations": [...]`.
Each write reads back what it stored, in words from both ends, in `readback` (the CLI prints it as `hub: stored:`):
`#237 "…" waits for #233 "…"` and `#233 "…" blocks #237 "…"`. A `blocks` sent where `blocked_by` was meant reads
the other way round, so check that line before reporting a dependency as fixed.
Either task's id, prefix or number works. A parent or a blocker follows the rules those always had (the
task's parties or a mover; a blocker is the owner's or a mover's); the other kinds need the right to
change both tasks, as for links. Finishing a blocker takes its relation off and wakes the bot owner
once nothing else blocks it. Each change is in both tasks' history.

Every task answer carries `relations`, by kind: each `{id, title, status, owner, direction}`, where
`direction` is `out` when the task is the subtask, blocker, duplicate or follow-up, `in` for the other
end, and `both` for `related`. Its own subtasks are `children` instead. A task the reader cannot open is
never listed, and a bot's run gets the relations it may read in its assigned task. Deleting a task keeps
its relations in the trash; restoring it puts back each one whose other task still exists. In SQL they
are the `task_relations` table ([Hub SQL](hub-sql.md)).

## People on a task by role

Besides its owner, a task can list any number of people or bots, each under a **role**: a short
name the team chooses (`developer`, `reviewer`, `qa`, `designer` ...). Tico keeps no list of roles
and gives them no meaning or rights; a client decides what they mean, such as which board column
waits on the reviewer. One person may hold several roles, and the owner may hold any of them too.

```json
"roles": {"developer": ["human:bob"], "reviewer": ["human:charlie"], "qa": ["human:matt", "bot:qa-bot"]}
```

```sh
hub task update <task-id> --role developer=bob --role reviewer=charlie   # replace a role's people; NAME= clears
hub task list --member me                                                # every task you are on
hub task list --member me --role reviewer                                # the ones you review
```

`hub_task_update` and `hub_task_create` take `roles` (`{role: [people]}`; each role named replaces
that role's people, `[]` clears one, a role not named stays); `hub_task_list` and `GET /api/v2/tasks`
take `member=<actor>` and optionally `role=<name>`. The API shape on task update and create is the
same `"roles"` object. Whoever may change the task's other fields may change who is on it; on a
private task only people who can read it can be added. Each change is in the task's history
(`role:<name>`) and on the live events stream. Deleting a task takes its people to the trash and
back. In SQL they are the `task_roles` table ([Hub SQL](hub-sql.md)).

## Files, versions and questions

Attach a file to the task so anyone who can read the task can open it. Uploading the same name
on the same task adds a new version, even when another teammate uploads it. Another task or an
archived file starts a separate file. Older attachments appear as v1 without rewriting existing data.
An existing attachment URL without `?v` keeps opening its original v1 bytes after adoption.
Use `?v=N` to open a later version of that legacy ID. Newly minted versioned file IDs open
their latest version without `?v`.
Each version records who uploaded it, when, its size and type, an optional note (up to 500 characters),
and a question with its answers. Media metadata can be null until processing finishes.

```sh
hub task attach <task-id> report.md --note "Revised introduction" --choices "Approve,Request changes"
hub task comment <task-id> "Choose a draft" --attach draft-a.md --attach draft-b.md --ask ask.json
hub task answers <task-id>
```

`--ask` reads a JSON object from a file; it and `--choices` are alternatives. The shorthand builds
one question (`id: verdict`) with the given options and an Other text answer. MCP tools
`hub_task_attach` and `hub_task_comment` accept `ask`; attach also accepts `note`. Comment's
`attachments` contains references such as `file-id@2`. `hub_task_answers` lists recorded answers.

To give a bot's task a file that is already in Tico, such as one sent in a chat, a person comments with its
reference instead of uploading it again: `hub task comment <task-id> "The export" --file <file-id>@1`. When the
person can open the chat file, the task gets its own copy (the same bytes, no new upload), which anyone who can
read the task can open; the chat file itself keeps its own access and stays put if the task changes. Sharing the
same file again points at the copy already there. A bot, or a person who cannot open the file, gets `File version
not found on this task` and attaches a copy with `--attach` instead.

```json
{"questions":[{"id":"verdict","header":"Review","question":"Is this ready?",
 "options":[{"label":"Approve"},{"label":"Request changes","description":"Say what to change"}],
 "multi":false,"other":true}],"who":null}
```

Questions may be on a comment or a file version. An ask has one to four questions, each with
an id (up to 40 characters), header (30), question (300), and zero to six options. Labels are up
to 60 characters and descriptions up to 200. An option can link to a version using `file: "<id>@<n>"`.
`multi` defaults to false; `other` defaults to true. Question ids and labels must be unique.
Unknown fields are rejected. `who` names a person or bot for Needs you; otherwise the requester
is highlighted (the owner when the requester asks). Every open ask counts in the task's
`open_asks`, including older unanswered questions.

Uploading any file or version requires comment permission. Anyone who may comment on the task
can answer, except the asker. Read permission alone does not grant comment or answer permission. Answers validate question ids and option labels; Other
text is accepted only when the question allows it. A dismissal closes the ask too. Every answer
is retained, oldest first, as an existing answer message and a readable task comment. Answers
follow the comment wake rule: a bot, task owner or requester, or human mover wakes the task's bot; other teammates' answers wait for its next turn. The Computer receives the comment
text and one `answer: {...}` block; older Computers still read the text.
The bot decides what to do and whether to move the step. Ask once per version and act on the
answer; attach a new version when the work changes.

The API uses the existing ask/answer protocol (`messages.kind`, `refs.questions`, `refs.target`,
and `refs.answer`); plain questions and text replies keep working. Plain comments keep structured
questions open; an older Computer's plain answer closes its target and reads as `{by, text, at}`.
Needs you excludes closed, cancelled, archived and declined tasks, even with an open question.
The version's metadata points to its ask message. These routes use the usual Idempotency-Key contract:

- `POST /api/v2/tasks/{id}/files` accepts the existing `name` plus `text` or `content_base64`, and
  optional `note` and `ask`. The result includes `file_id` and `version`, alongside the existing
  `file` and `link` fields.
- `GET /api/v2/tasks/{id}/files` returns files, including archived files, with versions newest first.
- `PATCH /api/v2/files/{id}/versions/{n}` accepts `note` and `ask`, by the version's uploader.
- `POST /api/v2/tasks/{id}/comments` accepts `text`, `ask`, and version references in `attachments`.
  `GET` lists the comments, including `ask` and `answers` on questions, and `answer` on responses.
- `POST /api/v2/tasks/{id}/answers` accepts `target: {comment: id}` or
  `target: {file: id, version: n}`, `answers: {question_id: [label, ...]}`, optional `other`,
  or `dismiss: true`. Answer every question. For an allowed Other reply, use an empty label list
  or omit that question id when supplying the shared `other` text.
  `GET` lists the structured answers.

Comment and version `ask` objects include `questions`, `who` and `by` (the asker's actor).
Comment `refs.attachments` includes each attached version's `id`, `name` and `version`.
Task list and detail rows include `cover: {url, width, height}` or null. The cover is the newest
image version's thumbnail (or the image itself) or a video's poster; its URL includes `?v=N`.
Archived files are excluded, dimensions may be null, and each task page computes covers in one query.
For multi-question reviews, `other` is one string for the entire answer.
## Files and questions on the page

A task's **Files** strip has one tile per file at its newest version: a thumbnail or poster when there
is one, the name, the version and its age (`v3 · 2h`), and a dot while a question on it is open.
Click a tile to open it under the strip. Markdown renders (long files fold after about 30 lines),
CSV and JSON lists show as a table (first 200 rows), images show full size (← and → step through
the task's images), video and audio stream with their poster, a PDF shows page 1 and opens in a new
tab, and SVG, HTML and other files offer a download. The `v1 · v2 · v3` switcher changes version;
**Compare** shows two versions side by side, or a line diff for text. Each version shows who added
it, when, its note and its question. Esc or the tile again closes it.

A comment's pictures, SVG, visual pages, video and audio (up to 4) show under its text as cards; see
[Visuals in replies](visuals.md#in-a-chat-or-task-conversation). A visual on a comment is a normal versioned file on
the task, listed in the **Files** strip. Its card shows the version the comment was posted with; when the file has a
newer one, **Newer version** opens the newest. **Open in file viewer** in the card's menu opens the file's tile here
at the posted version. The comment's other files show a small thumbnail for an image and a chip for anything else;
either opens that file's tile at that version.

A question (on a comment or a file version) shows its options as buttons. One question that takes
one answer sends on the first click; otherwise pick, optionally type in **Other**, then **Send**.
**Dismiss** declines it. Anyone who may comment can answer, except whoever asked, who sees the
question without buttons. The answer appears as a comment, and the question folds to its answers
(all kept, newest last); **Answer** adds another.

Pictures linked in task details, comments and chat (`![](https://…)` or a bare `.png`, `.jpg`,
`.jpeg`, `.gif` or `.webp` link) show inline; click one for full size. They are loaded from their
own site, not through Tico. YouTube, Vimeo and Loom links open in the viewer.

On the board, a task with a picture shows the newest one as a cover, and a dot marks an open
question. **Pin** in the Tasks toolbar keeps the current view and filters under **Pipelines** in
the left rail, for you only; ✕ on a pin removes it. Pins are links: nothing moves a task's step
on its own.


## Company and private tasks

New ordinary tasks are readable by active company people and bots. Reading does not grant
permission to change, reassign or complete work. Type bot settings continue to grant comment,
subtask and work permissions; they cannot override Private or restricted bot activity.

Turn on **Private** in the existing task controls to limit future reads to the requester and
current assignee, human or bot. The assignee may tighten visibility; only the human requester
can publish again. Bots cannot publish a private task, including when acting for a person.
Bot-requested private work stays private until a future explicit human publication mechanism
exists. Mentions, administrators, managers, parent ownership and delegations grant no access.
Reassigning a private task gives the new assignee access and removes the previous assignee's
future access unless they remain its requester. Reassignment never clears Private.

### A private task with no person on it: its guest

A private task between two bots (or a bot's private task to itself) has nobody who could act on it, so its owning
bot may bring in one person, its **guest**. The first time that bot hands them work from the task, they become its
guest: a subtask for them under it (`hub task create --owner <person> --parent <task>`), a question to them on it
(`hub task question`), or setting it waiting on them (`--status waiting --on <person>`). The task's history records
who it reached, and its Private row names them.

- The guest reads that task as its requester and owner do: its words, comments and files, even if they may not read
  those bots' activity. Nothing else: not the task above it, its other subtasks, or the bots' other work. Everyone else
  still gets "not found".
- A run that also carries private work the person cannot read reaches nobody: the subtask, the question and the
  wait are all refused.
- There is one guest. A hand-off to a different person is refused, naming the guest; hand the work to them instead.
- Only the bot that owns the task brings a guest in; the requesting bot cannot. Reassigning the task ends the
  guest's access; a new bot owner may then bring in one person of its own.
- When a private task above it has a person on it, only someone who reads that task above can become the guest. From
  the task's own private work the hand-off is refused; otherwise the person gets their subtask alone, as before, and
  does not become the guest.
- The guest's answer on their subtask or question comes back to the task, and wakes its owner, as on any task.
- Approvals stay refused from private work (they go to the default approver): ask the person with a subtask or
  `hub task question`.
- `POST /api/v2/tasks/dry-run` makes the same privacy checks as the create and returns the same refusal text.

Task-linked messages, files and previews, execution output, SQL results, search context and saved
responses apply the same current-participant boundary. Retained room grants and attachment
uploader ownership do not restore revoked access. File downloads use authenticated delivery
with `no-store`; private task notices stay out of Slack, and batch records remain on the task.
Bot status hides free-form private context. Automatic KPI readings are withheld for bots with
private history because their mixed history cannot support a safe public view. Permission
changes apply to the next request; an already running read may finish using its original access.

The existing bot Settings editor offers **Create private tasks by default**. This also covers
requests assigned to that bot, including its shared branches. The Legal template enables it
by default; Tico does not guess from names. A human may clear the create checkbox to choose
company visibility explicitly. Supported structured output and automated delivery from a private
task retain its access restrictions. A private parent's subtasks are private and have their own
two participants. Publishing a child requires detaching it from a private parent.

Upgrade retains every task, message and attachment. Existing tasks whose requester and assignee
have known ordinary roster/config identities stay company-visible. Legal-template and sensitive
default bots, their branches and private descendants become private. Missing roster/config
identities, malformed sensitive defaults, unresolved branch sources and missing parents fail
closed as private. Any already stored privacy is preserved. No names or task text determine
privacy. The remaining participant retains access to orphaned private work; restore the original
roster identity or use separately approved offline recovery when neither participant exists.
There is no owner, manager or service-key recovery bypass. Old clients omit the
new optional fields and keep working; new tasks created through an old runner receive the same
server defaults. Direct legacy database inserts that omit privacy stay private.

Future reads, task context, files, links, counts and notices enforce current access. Content
already downloaded or delivered to a bot, an external service or a person cannot be recalled.
Authorized participants can retain or copy information elsewhere, including while working on
another task. Tico does not reliably classify arbitrary untagged prose or track every private
read as a restriction on later work. Persistent bot sessions and external copies are not recalled.

Task numbers, queue order and import numbering retain their existing semantics. Opaque number
and rank gaps may remain; they do not disclose hidden task content or participants.

## Editing and deleting comments

Whoever wrote a comment, a person or a bot, can change its text or delete it, signed in as
themselves. Nobody else can, the owner included, and neither can the Assistant or BotOps on the
author's behalf. Only comments can be changed. A question, an answer, a notice, an approval or a chat
line in a bot's room that mentions the task cannot.

An edit gets the checks a new comment gets, and the comment is marked as edited; Tico's task view
shows "edited" beside its time. A delete takes the comment off the task. Neither wakes anyone or
sends anything. Both move the task's updated time and add a line to its history. The audit log
(`events`) records the change without retaining old text.

A deleted comment leaves a tombstone with its text cleared. It is excluded from SQL for everyone,
including the owner, and is excluded from future comment reads and bot context. Cached write replies are refreshed
so retrying an earlier call does not return old or deleted text. If it started a bot run that has not
begun yet, the run is cancelled. The questions it answered are open again, and the bot it woke loses the delegation that
came with it. An edit is not sent again: a bot that next reads the comment gets its current text.

Only plain comments can change. Attachment and structured-review comments are refused because
their independent file and answer records are retained. Already delivered plain comments can
still be edited or deleted in Tico; queued outbound copies stop and no retroactive change is sent
to external services. Provider threads, retained bot sessions and already delivered external
copies cannot be recalled here. Current task read access is
required on every call, including an idempotent retry.

```sh
hub task comment-edit <task-id> <comment-id> "Use the August numbers."
hub task comment-delete <task-id> <comment-id>
```

The comment id is the `id` in the task's `comments` (`hub task show`). MCP: `hub_task_comment_edit`
(`id`, `comment_id`, `text`) and `hub_task_comment_delete` (`id`, `comment_id`). The API routes are
in [Task comments](api.md#task-comments).

A question is not a comment and is never edited or deleted. When the person answers it somewhere else, such as in
chat, the question's asker or the task's owner bot records their words as its answer, marked as quoted by whoever
recorded it; without a quote, the asker withdraws a question no longer wanted, which stays in the thread. Either way
it leaves the person's Needs you and the bot's counts, and nothing is sent or woken.

```sh
hub task ask-resolve <task-id> <question-id> --quote "Use the August numbers."
hub task ask-resolve <task-id> <question-id>
```

MCP: `hub_task_ask_resolve` (`id`, `comment_id`, optional `quote`).

## Deleting tasks made by mistake

Closing keeps a task as history. A task made by mistake, such as a duplicate, can be deleted
instead: by its human requester, or by anyone who may move any task, signed in as themselves.
Bots and delegated sessions close tasks; they never delete one.

```sh
hub task delete <task-id>
hub task deleted              # deleted tasks you may restore, newest first
hub task restore <task-id>    # or its number
```

`hub_task_delete`, `hub_task_deleted` and `hub_task_restore`, and `POST /api/v2/tasks/{id}/delete`,
`GET /api/v2/deleted-tasks` and `POST /api/v2/tasks/{id}/restore` do the same. A client can tell a
server offers this by the `deleteTask` operation in `GET /api/v2/openapi.json`.

Deleting moves the task to the trash with its events, links, labels, delegations, reminders, a
service key's mapping, and its conversation with every message in it. Nothing reads it there: it
leaves lists, boards, search and every bot's context at once. Its number stays with it, so no new
task takes it. Restoring puts the same rows back; a link to a task that is still deleted comes back
unset, and the answer says which. Whoever deleted the task, its requester, or anyone who may move any
task can restore it.

A task carrying work is refused (`409 has_work`, close it instead), so deleting never touches what
someone did: a bot turn, a job, an approval, a bot's own file, a routine occurrence, a meeting
delivery, or a subtask. Files attached to the task or its comments are not work: they go with the
task, no one can open them while it is in the trash, and a restore brings them back. The audit log keeps `task.deleted`, `task.restored` and `task.purged` events with who did it
and the task's title.

To delete many at once, such as a bulk import run twice, the owner runs the offline command on the
server after a snapshot ([environments.md](environments.md)). The whole list is refused, and
nothing changes, if any id is unknown or any task carries work. The trash keeps everything until it
is purged, also offline:

```sh
python -m backend.manage delete-tasks /data/hub.sqlite --ids-file ids.txt          # lists what would go
python -m backend.manage delete-tasks /data/hub.sqlite --ids-file ids.txt --apply  # moves them to the trash
python -m backend.manage restore-tasks /data/hub.sqlite --ids-file ids.txt         # puts them back
python -m backend.manage purge-deleted-tasks /data/hub.sqlite --older-than-days 30          # lists
python -m backend.manage purge-deleted-tasks /data/hub.sqlite --older-than-days 30 --apply  # removes for good
```

A purge cannot remove a deleted task's attached files: their versions and activity are append-only
and their bytes may be shared with another file. They stay, and no one can open them again, not even
whoever attached them, unless the same file is also attached somewhere they can still read. The purge
says so: its answer lists them under `files_left` (how many, their versions, file bytes, the ids of
files added to the task and, under `attachments`, the files attached when it was created), before
and after `--apply`, and each `task.purged` event names the ones its task left.
