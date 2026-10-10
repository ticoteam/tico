# Task types and steps

Every task starts on **General**, with the same statuses and behavior as before. To give work a
pipeline such as Marketing, a mover makes a type in **Settings → Types**, adds named steps, and
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

When the task waits on a person to act instead (a decision, access, a host only they can fix),
name them with `--on` and say exactly what they need to do in the note:

```sh
hub task update <task> --status waiting --on ana --note "Restart the build host; it refuses SSH"
```

The task is then in that person's **Needs you** and their batch as one item, its title and that
note, and the bot counts toward `needs_human` on its status. When the person comments on the task
(or says done or answers in a batch) the bot wakes and the task leaves their list; the bot sets it
waiting on them again if it still needs something. Only the bot that owns the task names the
person. A new status, note or owner clears `--on` unless the same update names the person again,
and `--on ""` clears it by hand. A private task can only wait on a person who can read it, and a
task made private stops waiting on anyone who cannot.

A bot may do this for its own task, including a task it requested itself. The blocker must exist
and be accessible; a finished blocker does not justify waiting. An unanswered question, an open
child task, a pending approval or a person named with `--on` can also justify waiting. Finishing a blocker clears the dependency
and wakes the waiting bot. **Waiting** does not mean a person must review the code.

For unblocked open or doing tasks, automatic stalled-task wakes are limited to three in a rolling
24 hours, spaced at least 30 minutes apart. Updating a note does not reset that limit. Repeated
stalls are escalated to BotOps with a separate diagnostic for each source task. If BotOps itself is
stuck or unavailable, the diagnostic goes to the human requester, or the team's default human for
bot-requested work. An unresolved diagnostic is reused on later days. Health warns when repeated
runs have escalated a public task without progress; an explicit run is still available.

Task worktrees have a separate lifecycle. The ten-worktree limit stays in effect, and cleanup
waits until the owning bot is idle so it cannot remove a checkout in use. A supported cleanup
request continues to occupy its slot until the runner removes it. For merged work that is still
awaiting release, request cleanup of the worktree link rather than marking the task Done or Closed
just to free space.

## Done and Closed

**Done** means the owner reports that the work is complete, with a result note. **Closed** means
the requester or an authorized human accepts the result or cancels the task. Closing unfinished
work does not mark it Done. Both statuses appear in the **Done** view; the task history keeps
the separate completion and closing events.

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
control additional permission to comment, file subtasks or change work in **Settings → Types**,
with `bots` on the task-types routes or `hub task type update "Dev ticket" --bots work`.

| `bots` | Additional permission for unrelated bots |
|---|---|
| `parties` (the default) | None beyond reading company tasks |
| `read` | Comment on company tasks and file subtasks |
| `work` | Also change step, owner, due date, description, order and links |

Private overrides every type permission. Closing, ready or closed steps and labels keep their
existing human controls. Changing type cannot publish a private task.
A bot's comment is a message to the people on the task, under the usual rules for reaching them.
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
hub task type update "Dev ticket" --numbered
hub task create --owner ben --title "(B/F) Fix the account page" --type "Dev ticket" --number 18945
hub task show '#18945'
hub task update '#18945' --step "On deck" --step-rank 2.5
hub task list --type "Dev ticket" --sort step
```

`hub_task_types` lists types and ordered steps. `hub_task_create` and `hub_task_update` accept
`type` and `step`, as ids or names, and `number`; `hub_task_update` also takes `step_rank` and `title`, and
`hub_task_list` takes `type`, `step` and `sort`. Movers manage definitions with `hub_task_type_create`,
`hub_task_type_update` and `hub_task_type_delete` (with `numbered`), or `hub task type create|update|delete`.

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
unused type. Create and update accept `numbered`; updates accept `name` and a replacement `steps`
array, retaining existing step ids. All writes use the existing Idempotency-Key contract. Task
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

Subtasks can nest to any depth. A human-created subtask keeps a human parent's requester for
notices; under a bot-requested parent, the person filing it is its requester.
A bot-created subtask is requested by the filing bot, which receives its completion
notice and may close it explicitly. Completed subtasks stay Done until then.
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
parent's owner with “All subtasks done”, unless that owner made the change.
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

A comment that carried files shows a small thumbnail for an image and a chip for anything else;
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
someone did: a bot turn, a job, an approval, a file, a routine occurrence, a meeting delivery, or a
subtask. The audit log keeps `task.deleted`, `task.restored` and `task.purged` events with who did it
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
