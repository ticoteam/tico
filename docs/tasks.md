# Task types and steps

Every task starts on **General**, with the same statuses and behavior as before. To give work a
pipeline such as Marketing, a mover makes a type in **Settings → Tasks**, adds named steps, and
chooses the status each step means. Types belong to the whole team. General keeps one step for
each standard status except waiting and declined (below).

Choose **Type** when creating a task, or change it in the task modal. A custom type shows a **Step**
dropdown; General shows **Status**. Beside task search, **General** and **Dev tickets** select the task type; the arrow menu lists
other types. A type is always selected, and both the list and board show only its tasks. The board
uses that type's steps as columns. Leading backlog steps (open, before the first step with work under
way) fold into one strip, as do the trailing done, closed and declined steps; click a strip to unfold
it. A task that finishes leaves the board unless its finished steps are unfolded. Done tasks are under
**Done**, closed ones under **Closed**. On a desktop, drag a card to another column or to a new place
in its column: it saves the step and `step_rank` at once, and a card dropped on a folded strip goes to
the last backlog step or the first finished one. On a phone, change the step in the task's **Step**
row. A card with an open blocker says **Blocked by** it across its top; click that to show only the
card and its blockers. The faces over the board filter by owner; **Q** shows only yours, **X** clears.
When a Dev ticket's bot owner or a bot assigned the optional `builder` task role is quarantined, its card, column and ticket page show
when the builder resumes in UTC, or that a person must clear it. The notice clears when the quarantine lifts.
Your selection is remembered, shared links carry it, and new
tasks start with that type. Clearing filters keeps the selected type. Older combined views and
deleted types fall back to General.

Picking a step sets the task's status. Steps can move in any order. Bots, older runners and
existing scripts can keep setting `status`:

- If the current step has that status, it stays there.
- Otherwise Tico picks the first step with that status, in the type's step order.
- If none matches, the step clears. The task keeps its status, and the UI shows that status until
  someone picks a step.
- Waiting is not a step: a waiting task stays in its working step (or the type's first open or doing
  step) and shows a **Waiting** tag on its card and row. A declined task goes to the type's closed
  step. A step can't be given status declined; the Product roadmap keeps its public Declined step.
- A team can add a step with status waiting, a column of its own for parked work such as "Waiting On
  Dependency". Only a move by hand fills it: a task that starts waiting never lands there by itself.
  A person's move into it is a hold that stays until someone moves the task on, and updates leave the
  step and its tasks where they are.

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

Whoever may change a task's other fields (its owner, its requester, their managers, the owner or requester of a task
above it, a delegate or a mover) may also rename it. A new title gets the checks a new task's title
would: never empty, at most 300 characters, a verb first and no internal codes on a General task for
a person, plain English when a bot writes it on General, and no other live task between the same
requester and owner, under the same parent, with that title. The old and new titles are in the task's history, and a task's own conversation keeps the
new title as its subject.

A linked pull request never moves a task, on any type: opening, merging or closing the PR, a
release tag and a deploy only change the link's state. A person (or a bot) moves the task.

## Managers acting on tasks

A bot or person above a task's owner or requester on the org chart inherits that
party's task permissions through the entire chain, including department groups.
Managers can read a bot report's private tasks, change its work, manage blockers and
links, comment, ask questions, snooze and manage the owner's worktrees. A manager's
comment or question wakes the owner bot as a requester's does. History and comment
authorship name the manager who acted.

The two sides keep their distinct rights: an owner bot's manager, bot or person, can
complete work but not close or reopen it; a requester's manager can close and reopen it. The existing rule protecting a
person's closure still applies. Managers gain no rights on unrelated private tasks;
person-only private tasks stay with their parties and guest. Managers gain
no right to edit or delete someone else's comments or decide a named approval, and
no new credential, sending, spending, merge or bot administration permissions.
Publishing a private task still requires its human requester. Task types, labels
that trigger workflows and review handoff checks retain their existing restrictions.

## Software review handoffs and QA defaults

On the engineering board, moving a ticket with a linked open PR into **Review 1** or **Review 2** checks CI first. All-green checks at each PR's current head count as passing test evidence without a written receipt, even when a note reports a failure there. Creating a **Review 1:** or **Review 2:** child task makes the same checks before queuing its reviewer. A review child also needs a linked PR. Other boards keep their usual flow.

A failing check refuses the move and names the check. A failed GitHub check attempt still blocks after a green rerun at the same head: fix the failure and push a new head. The server requests all check attempts rather than GitHub's default latest-only results; legacy commit statuses keep the latest status per context and any earlier failure. Pending checks are allowed with a passing written receipt. No checks, or only skipped/neutral checks, also require a written passing receipt naming the current head. At least one successful check and no pending or failed checks are needed to replace that receipt; skipped/neutral companions are allowed. A missing, stale, failed or unrecognised receipt refuses the move and names the current PR head and newest receipt head. The ticket keeps its step and the reviewer stays asleep. If someone other than the Builder tried the move, the Builder also gets the same comment once per ticket, check kind and head. When GitHub is connected, the server reads the current head and checks before accepting a handoff; an unavailable or incomplete read refuses it.

A refused move is remembered, and Tico makes it itself once what refused it changes: the checks on the PR, the ticket's note, or a new message on the ticket (a receipt posted). Nobody needs to ask for a retry. When GitHub cannot be read, the ticket keeps its current step and Tico tries again every 5 minutes. Timeouts, rate limits, permission errors, missing installation access and other upstream read failures do not file DevOps or manager tasks or wake the ticket owner. Engineering and Health each receive one deferred source notice when access is lost and one when it returns. A PR link Tico cannot resolve, or one with no recorded head, still needs its local record fixed. A remembered move is dropped when the ticket moves elsewhere or after three days.

A ticket has at most one open **Review 1**, **Review 2**, **QA** or **Post Release QA** subtask for a pull request head. A second request for the same check on the same head (or when the head is unknown) is refused with the open subtask's id; a push gives a new head and with it a new check. Reviewers and Security give a verdict and never move the ticket: a bot that owns a Review 1, Review 2 or Security subtask of a Dev ticket (and does not own the ticket) is refused a status, step or type change, and told to finish its review task with a verdict. The Builder, QA and people move the ticket.

### Check pools: who takes a ticket's check

Each check step (**Review 1**, **Review 2**, **In QA**, **Post Release QA**) can have a pool: the bots that may do that check, and how many open checks each may hold (2 unless set, up to 20). Set them on the step in Settings → Tasks → Edit type (`check_pool` and `check_limit` on each step of `POST /api/v2/task-types/{id}`); a step with no pool files nothing by itself. The editor and bot-assignment picker show a bot's configured model and provider beside its name. When the latest completed run used a different or unknown provider, they show that runtime evidence separately; it describes that run, not the next one. A Dev tickets type starts with the proposed pools, with the bots the company has: Review 1 and Review 2 `pr-reviewer-sol`, `reviewer-2`, `reviewer-xai`, `reviewer-muse`; In QA `qa-engineer`, `qa-claude`, `qa-xai`, `qa-muse`; Post Release QA `qa-engineer-copy`.

When a ticket enters a step with a pool and has a linked PR but no open check there for its current head, Tico files the check (`Review 1: PR #412`, `QA: PR #412`, `Post Release QA: PR #412`, the head recorded) as the ticket's owner would, so the same gate applies and the verdict comes back to the Builder. It prefers a different model company from the Builder, then orders eligible pool bots by fewest open checks and longest since their last run. Eligible means active, on a computer seen in the last 5 minutes, not paused or held, under its limit, never the Builder, and for Review 2 not a bot that did Review 1. The model company is the Builder's company as its runs on the ticket actually ran (the Builder's configured company only while none of them is known), and a bot's company is the one a check given to it runs on: its task model's when it has one that can run now, else its configured model's, or what its last few such runs ran on. Each check step has an **Another model company** setting (`other_company` in its flow, see Step flow), `prefer` by default, and it decides what a bot of the Builder's company may do. With `prefer`, that bot takes the check only when no other pool bot is free, rather than the check waiting, for a review and a QA check alike, and a bot whose company Tico can't name comes after one of another company it can. With `require`, a bot of the Builder's company is never given the check, not even when it is the only free one: the check waits when every free bot runs on that company (the reason says so), and a bot of the Builder's company that finishes its own check does not take it. Require also never gives the check to a bot whose company Tico can't name (a model it doesn't know or a reseller picks, such as Cursor's auto, or part of a recent run on one), and while any part of the Builder's runs on the ticket is of unknown company the check waits for every bot. The bot is woken at once.

With no free pool bot, the check waits on the ticket: its page shows **Waiting for a reviewer** (or **Waiting for QA**) with the reason, and the first bot in the pool to finish a check takes it. A pool member only takes checks this way. An empty pool waits the same way. A Builder that files the same check meanwhile is told it already exists. The wait ends when the check is filed or the ticket leaves the step.

A reviewer or QA records the verdict as fields on its own subtask: `hub task done <id> --verdict approve|changes|pass|fail --head <sha>` (also `hub task update --verdict --head`, the `hub_task_done` and `hub_task_update` tools, and `verdict` and `verdict_head` on `POST /api/v2/tasks/{id}`). A `VERDICT: approve PR #12 head <sha>` clause anywhere in its note, comment or final reply fills the same fields when no flag is given; the latest one wins. A Review 1, Review 2, QA or Security subtask of someone else's ticket that a bot owns, marked done with no verdict since it was last reopened, is refused with the `--verdict` hint; reopening a check clears its verdict. A person's own subtask with one of those titles is an ordinary task and finishes without a verdict. Tico waits for the exact judging run's usage record, records the configured and executed model families on the check, and accepts the verdict even when the run used a fallback or its family is unknown. Duplicate delivery does not move the ticket or wake the Builder twice.

A person who owns a Dev ticket, or manages it (the company owner, someone on a team that may move any task, or a person above the owner on the org chart), may **waive its QA step** for one exact pull request head when QA cannot run in time: `hub task waive-qa <id> --head <full sha> --reason "..."` (`POST /api/v2/tasks/{id}/qa-waiver` with `head`, `reason` and optionally `step`). Only the ticket's current **In QA** step can be waived. CI, Review 1, Review 2 and release approval cannot be. The waiver is refused, naming what is missing, unless the head is the current head of one of the ticket's open pull requests, and every hosted check on each of those pull requests has passed, read from GitHub at that moment (a skipped check doesn't count as a pass, and a failed attempt still blocks after a passing re-run). A push recorded while GitHub is being read refuses it too; retry. That read needs the company's GitHub App: without it the waiver is refused, because check webhooks alone can't tell a pass from a skip. No review subtask may still be open, and the latest Review 1 and Review 2 must each have approved that exact head. A waiver stands in for a missing QA result, never a failed one: a QA `fail` at that head, or one that names no head, refuses it. A bot cannot waive QA, including BotOps or the Assistant acting for a person. The waiver records the actor, reason, head, step, pull request and time as a `qa_waiver` event in the ticket's history. The task view lists the ticket's waivers under `qa_waivers`, each with `current` (false once a push moves the pull request to a new head). A waiver is never a QA verdict: it sets no `verdict` and leaves the QA subtask alone. The task page shows it as **QA waived** in amber, never as a pass. The ticket's owner is told once, unless they waived it themselves. It does not move the ticket or merge anything. The owner merges at that exact head, and a new head needs a QA result or a new waiver.

Receipts stay ordinary text, for example `Tests: base <40-character SHA> head <40-character SHA>: 9 passed, 0 failed`. The result and head belong on one line. Each result clause belongs to its own head. Phrases about earlier or repaired failures do not claim that the current run failed; an explicit result such as `40 passed, 2 failing/errored` still does. The shared reader used by review handoffs and the release checklist accepts ticket notes, owner messages, keeper ready-for-review notices and review-child evidence from the reviewer or Builder. The review gate also counts receipts posted by bots above the owner in its `reports_to` chain and people who may edit the ticket. This changes neither their task permissions nor privacy: the mover and Builder must both be able to read the evidence. A written failure cannot be cleared by another written passing rerun at that same head; all-green GitHub checks can supersede it. Refusals name the next action and who can supply it.

At engineering-ticket creation, `change_paths` supplies the known paths for the QA rule. The CLI accepts repeated `--change-path` flags. An explicit `qa` boolean (`--qa yes` or `--qa no`) overrides the rule; an author-supplied `qa` tag is retained.

| Changed paths | Default |
| --- | --- |
| `ui/`, `desktop/`, `app/` | `qa`, with a not-checkable-live note for the rendered result |
| Bot or chat behaviour (`bots/`, `chat/`, backend chat/message/wake/execution modules) | `qa` |
| Docs, CI, scripts, nested tests, backend-only code outside the behaviour classes | No `qa` |
| Mixed paths | `qa` if any path needs it |

On a PR's first registration, the server reads its complete changed-file list and applies the same default to its linked engineering tickets, including tickets whose paths were unknown at creation. The initial read retries in the background if GitHub is unavailable. Later pushes do not reclassify the ticket. Explicit ticket choices and the PR author's `qa`, `no-qa` or `qa:no` labels override defaults. This sets the Tico ticket's tag; it does not edit GitHub labels or expand bots' label permissions. `qa_not_checkable_live` (`--qa-not-checkable-live`) also requests the candidate-only QA note.

### Step flow: what a step does with a ticket

What Tico does with a ticket at a step is a setting on that step (`flow` on each step of `POST /api/v2/task-types/{id}`),
never read from the step's name, so any board can be set up its own way. Step references take a name or an id and are
kept by id, so renaming a step keeps its flow. All keys are optional:

| Key | What it does |
| --- | --- |
| `on_pass`, `on_fail` | Make the step a check step. A pass or approve moves the ticket to `on_pass` (`merge`: merging is next and the Builder is told once CI is green); changes, a fail, or a check finished with no verdict (its comments) move it to `on_fail`. A pass at a head where this step already gave a fail, changes or comments moves it to `on_fail` too: only a new head clears that, as a failed CI check or test receipt at a head is cleared only by a new one. |
| `after_green` | A work step: green CI on a head newer than the one its check judged moves the ticket here (`merge` to merge next). Without it the owner moves the ticket on when its work is done. |
| `on_merge` | The one step a ticket goes to when its pull request merges, from any earlier step. |
| `skip_tag` | A ticket with this tag passes the step: it goes straight to `on_pass`, and the skip is recorded. |
| `timeout_hours` | A check still open this long is closed unanswered and the round skipped: the ticket goes on as if it passed with no comments, unless a check there already gave a fail, changes or comments at the pull request's head, in which case it moves to `on_fail`. |
| `status_hours`, `late_hours` | A ticket stalled this long at the step gets its bot owner one status check; one this long at the step gets its manager one notice. Dev working steps default to 2 and 4. |
| `assign` | `{pool, slots}`: an unowned ticket here goes to the least busy pool bot with a free slot (tickets past this step it holds). |
| `backlog` | `{over, per, notify, every_hours}`: more than `over` tickets at the step (`per: step`) or for one owner (`per: owner`) tells `notify`: `manager` (the type's), `owner`, `owner_manager` (one level up on the org chart), or a named bot or person. Once every `every_hours` (4) while over. |
| `can_hold` | `false`: a ticket here is never put on hold; a merged change is reverted or fixed forward. |
| `gate` | Entering the step needs green CI and a test receipt at the head (the review gate above). |
| `check` | The kind of check the step asks for: `review`, `qa` or `release_qa`. Review pools, the one-open-check rule, Review 2 never going to an earlier review's bot, QA waivers and blocked checks find their steps by this, so a renamed check step keeps working. A check request is titled with the step's name (`QA` for a QA step); `Review <n>`, `QA` and `Post Release QA` still name the board's nth review, its QA and its after-release check. |
| `other_company` | On a check step only: `prefer` (the default, also when left out) or `require`. Prefer gives the check to a bot of another model company than the Builder's before one of the Builder's company, which takes it only when no other is free. Require never gives it to one of the Builder's company: the check waits when every free bot runs on that company. See Check pools. |

A Dev tickets type is given the Dev board's flow once: Review 1 and Review 2 are `review` checks, In QA is `qa` and
Post Release QA is `release_qa`; reviews send their feedback back to the Builder's Fixes step
whichever way they go, In QA passes to the merge (skip tag `skip-qa`), Pending Release takes merged tickets, and
released steps never hold. Change any of it on the steps.

GitHub access is reported restored only after a successful current head and checks read. An expired or cancelled move stops retrying that move; its disappearance never proves access returned. Engineering and Health retain the single source notice while recovery remains unconfirmed.

## Automatic starts

Assigned bot work starts as soon as its computer has a free turn. Tico picks the highest-ranked eligible
On Deck task (or an open general task), moves it to In Progress, and uses that task's own session.
Snoozed work, unfinished prerequisites and pending milestone waits stay parked. Existing snooze-end,
blocker-done and milestone events make the task eligible again; no manual nudge is needed.

In **Settings → Bots → Edit**, **Keep N tasks going** is blank by default (no task cap). Set a positive
number to cap active work, or **0** to turn automatic starts off. Waiting for a person, a check, a review
child or a workspace frees a place. Delegated review children have no task cap; every review uses its
own session as computer turns become free. Usage holds, computer readiness and computer capacity
still apply. The Bots page shows how many tasks are going and how many eligible tasks wait to start.

Only a Doer starts its open tasks by itself; a Reviewer starts only its review and QA requests, and a Lead, Responder or
Monitor starts a task when it is handed to them ([Profiles](how-it-works.md#profiles)). A Responder is given no task at all,
and a Reviewer is given only a review or QA subtask: filing any other task for it is refused with the reason.

`hub bot turns-at-once <bot> 2` caps how many of the bot's turns run at once across every computer; `unlimited` lifts
it. The bot editor and `POST /api/v2/bots/{bot}/turns-at-once` (MCP tool `hub_bot_turns_at_once`) set the same value. At
the limit its queued work waits and says so, on every computer.

`hub bot keep-tasks-going <bot> 3` sets a cap; `off` turns it off and `unlimited` restores the default.
Omit the value to read it. The matching MCP tool and `POST /api/v2/bots/{bot}/keep-tasks-going` use
the same permissions and revision check. **Settings → System** lists **Start assigned tasks** using
WHEN/THEN wording; its `start_task` catalog action uses the same task delivery and claim checks.

Hand a ready batch of On Deck tickets to builders in one pass. Each assigned ticket already has its
own session and can start on a free turn; it does not need another Lead turn for each ticket.
The ordinary task cap and prerequisite checks still apply to the whole batch.

If an open assignment has no attempt after one hour, **Recover unstarted assignments** wakes its
owner once, including a Lead. Waiting, snoozed and deferred work is excluded. The recovery wake
does not bypass a task cap, an explicit automatic-start off switch or any execution hold. If it
still has no attempt two hours after that wake, Health and its manager's Needs you show
**Assigned, not started**, the task title and its current refusal reason. Only readers of that
task see it; being the manager does not grant access to a private task. An attempt, a wait or
completion clears the item. A new assignment starts its own one-hour clock.

Health checks the queued job's lane and claim refusal even when the bot has another turn running.
For claimable work it measures the job the claim would take first, including priority for a person's
message, so an older job does not hide a person waiting for a reply or make a young queue look stuck.
The oldest queued job outside a running lane continuously observed refusing claims for twelve
hours becomes a stuck item, with the current reason. A different job or reason, a pause or hold,
or a gap of more than five minutes in observations starts a new clock. An old queue alone does
not establish twelve hours of refusal. Missing task conversations and failed recovery delivery
are recorded once and shown with the unstarted assignment, rather than silently omitted.

## Work that is waiting

Keep actively worked tasks in **Doing**. When another task must finish first, relate the blocker,
then set **Waiting**:

```sh
hub task relate <task> <blocker> --kind blocked_by
hub task update <task> --status waiting --note "Needs the build environment"
```

### Holds

A task's API view (`GET /api/v2/tasks`, a task's read, the `tasks` live event, Needs you, the MCP tools and the hub
CLI) lists what holds it in `holds`, empty when nothing does:

```json
"holds": [{"kind": "blocked", "reason": "Needs the build environment", "since": "2026-11-01T17:02:11.204Z",
           "until": null, "by": "human:ana", "cause": "manual"},
          {"kind": "snoozed", "reason": "Check the staging deploy", "since": "2026-11-01T17:05:40.118Z",
           "until": "2026-11-03T08:00:00.000000Z", "by": "human:ana"}]
```

- `kind` is `blocked` or `snoozed`, at most one hold of each kind on a task.
- `blocked` is set when the task moves to a waiting step or status: by a person or bot (`hub task update --status
  waiting --note ...`, a move to **Blocked** on the board), or by Tico when what it waits on is tracked (a blocker,
  a subtask, a question, an approval, a review gate). It clears when the task moves on to a working step, or when its
  last tracked wait clears. `cause` says which: `manual` (a person's or bot's move; `reason` is its note's first
  line), `dependency`, `stranded`, `gate`, `check` or `worktree` (Tico's own). `by` is the actor who moved it there
  for `manual`, else `keeper` (Tico). `until` is null: a block lasts until it clears.
- `snoozed` is set by a snooze ([Snoozing a task](#snoozing-a-task): `POST /api/v2/tasks/{id}/snooze`,
  `hub_task_snooze`, `hub task snooze`) and cleared by an unsnooze, its date passing, or the task finishing.
  `reason` is the snoozer's note (left out of a `brief=true` list row), `until` its date, `by` the snoozer, `since` when it was last set.
- `since` is when the hold began; `by` is an actor id (`human:<id>`, `bot:<slug>` or `keeper`); `reason` and `until`
  are null when there is none.

The holds are read-only on a task: the moves and endpoints above set and clear them. What a hold is made of keeps its
own field: `waiting_on` (the person it waits on), `relations.blocks`, `worktree_waits`, `check_waits`,
`milestone_waits` and `deferred`. Task list filters are unchanged: `?snoozed=only|all` lists snoozed tasks, and a
blocked task is a `waiting` one (`?status=waiting`).

### Waiting for a milestone, not the whole task

Often what a task needs is an earlier milestone of another task, not that task being done: a pull request merged
while its ticket stays In QA, or a release published while its checklist stays open for its QA watch. Wait for the
milestone itself:

```sh
hub task wait <task> --on pr:https://github.com/<owner>/<repo>/pull/943   # until that pull request merges
hub task wait <task> --on pr:<other task>                                  # the one pull request linked on it
hub task wait <task> --on release:v0.3.64                                  # until that release is published
hub task wait <task> --on task:<other task>                                # the same as blocked_by
hub task wait <task> --on pr:<url> --note "Check the migration notes, then QA"  # a note comes back in the wake
hub task wait <task> --on pr:<url> --remove                                # take it off
```

The task shows it ("Waits for PR #943 to merge"). When the pull request merges or the release is published (the
GitHub webhook), the owner gets one wake; the task it came from keeps its own step, and nothing is closed or moved.
A wait made after the milestone is woken once by the minute sweep instead, and a merge or release whose webhook
never arrived (the server was down) is checked with GitHub every few minutes, in repositories the GitHub App reaches. Replays and the sweep never wake it twice. `release:` takes the server's release repository unless
named (`release:<owner>/<repo>@v0.3.64`), and a version tag only (major.minor at least). Who may add or remove a
wait is who may add a blocked_by: the task's owner, or a person on a team that moves tasks. `--note` on a pr: or
release: wait is shown on the task and comes back in its wake as `Your note:`; a repeat with a note replaces it. A
deleted task keeps its waits in the trash and gets them back when restored. The MCP tool is `hub_task_wait`; the API is
`POST /api/v2/tasks/{id}/waits` with `{"on": "pr:<url>", "remove": false}`; each task answer carries
`milestone_waits`.

### Reconnect cards

When a bot's work on a task stops because an account or a credential needs a person, Tico puts one card in the
task's thread ("Sales Bot can't reach Google (ana@acme.example)."), with the fix for the person who can make it and
"Waiting on <person>" for everyone else. The task waits on the card: it is in that person's Needs you under connect,
and no stall check, stranded sweep or news wakes the bot meanwhile. When the account works again the card says
Reconnected and the owner gets one wake ("Google (ana@acme.example) is reconnected. Continue."); the task goes back to
the step it held. One card per task and account; tasks waiting on the same account are one Needs-you item. While
another card or hold still stands on the task, the fix is kept and told once, with the last hold to clear. A card
stands only while its task is unfinished and keeps the owner it was opened for, and its bot is not archived; otherwise
it says Not needed and asks nothing (a credential card's request is cancelled, so it can no longer be saved). The
person is one who can read the task; on a private task nobody else gets the card. When a task is made private and a
card's person cannot read it any more, a sign-in card moves to the operator or owner who can. Any other card says Not
needed, and the task stops waiting on it.

| What stopped | Who fixes it | Fix on the card | Fixed when |
| --- | --- | --- | --- |
| The connector's Google sign-in for a message bot's mailbox (`google:<mailbox>`) | the mailbox's person | Reconnect Google | every service that refused it (mail, calendar) reports that mailbox working, or the person connects it again |
| The provider refused a computer's sign-in or key for one profile (`login:<computer>:<runtime>:<profile>`, `_default` for the computer's own login) | the bot's operator, else the owner | Sign in (the computer's pop-up, or Settings > Computers) | the computer reports that profile's login ready, or a run on that profile completes (not a fallback's) |
| A credential the bot (or BotOps, for any bot) asked for from a task (`secret:<bot>:<VARIABLE>`) | the person who wrote to it there, else its operator or the owner; for BotOps, the person it acts for | the credential card | the variable is stored for or granted to the bot; cancelling the card ends the wait too |

A bot held for failed sign-ins with a standing login card has no second Needs-you item; the fix releases it, and the
item comes back if the card is retired unfixed. Each card's
message carries `refs.reconnect` (`key`, `service`, `label`, `person`, `fix`, `status`). The tables are
`task_reconnects` and `google_signin_refusals` (each refusing service per mailbox); other accounts (GitHub, an MCP server's sign-in) are new keys through the same path
(backend/reconnects.py).

When the task waits on a bot doing prerequisite work (a builder, a reviewer, QA), wait on that bot's task, then
name the bot with `--on` and give the reason in the note:

```sh
hub task wait <task> --on task:<the bot's task>
hub task update <task> --status waiting --on bot:pr-reviewer --note "Needs the receipt-gate fix merged and QA'd first."
```

The task and its card show "Waiting on Builder (OpenAI)" with that reason. The blocker holds the task, so its owner
is not woken while the prerequisite stays open. When that task is done, declined or closed and nothing else blocks
this one, its owner is woken once with the outcome ("Unblocked: … is finished" or "was declined") and picks it back
up. `--on bot:<slug>` is accepted only for an active bot that owns the first open task this one is blocked by, the
one its views name: a bot alone does not say which of its work must finish, so an inactive bot, an unknown name or a
bot that does not own that task is refused with how to link it, and nothing changes. A subtask needs neither: its result comes back to its parent on its own.

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
it; a board ticket the ask held stays at the step it was in, and its hold clears there. Nobody asks her again whether she answered.

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

The note must be a plain ask: one or two sentences, at most 40 words, with no ids, hashes, file paths, `s3://` links,
tool names or error output. A pull request may be named, in exactly the form `PR #1555`; a bare `#1555` is refused like
any other reference number. Without a plain ask the update is refused, saying what failed and what to do now. An ask
refused only for its length or sentence count comes back with a rewrite: its one request and the sentence saying what
happens after, which passes as it is. Retry once with it and put the rest in a comment (or, for a task given to a
person, below it in the body), so nothing is lost. When no shorter wording keeps the same decision (it asks two
things, including indirect or conditional requests, asks nothing, or the request alone is too long or not plain),
the refusal says exactly what to change instead. A second request such as "And sign the contract" needs repair;
an outcome such as "If yes, I'll sign it, send it and close the task" stays together. An affirmative "If yes" takes
priority over a negative branch such as "If you do not approve" or "If you agree not to merge";
nothing is sent and the task stays as it was. Every refusal is recorded and none counts toward a quarantine.
A task with an open structured question to the person (`hub task question`) is already the card: it waits on them as itself, and its note is only warned
about. Only the bot that owns the task or its managers name the person, and a private task can only ask a person who can read it,
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

A stall or status check leaves a task alone while its owner has a live session on it (a leased or running attempt)
or the task waits on something recorded: a blocker, an open subtask, a pending approval, an unanswered question, a due
date, a snooze, a stored resume, a pull request link armed with `--wake-once` or a milestone wait. A bot whose profile
has no stall sweeps is never checked. A task held only by its own stalled subtasks is still checked (below), and the Task
Manager's stalled-task list does not use these skips, so a task no sweep wakes still reaches its report.

For unblocked open or doing tasks, automatic stalled-task wakes are limited to three in a rolling
24 hours, spaced at least 30 minutes apart. Updating a note does not reset that limit. Repeated
stalls are escalated to BotOps with a separate diagnostic for each source task. If BotOps itself is
stuck or unavailable, the diagnostic goes to the human requester, or the team's default human for
bot-requested work. An unresolved diagnostic is reused on later days. Health warns when repeated
runs have escalated a public task without progress; an explicit run is still available.

A task blocked by its own subtask is allowed (a question for a person in a subtask works that way),
but relating it returns a warning: a subtask kept open until its parent finishes, such as a hold or
an acknowledgement, never clears. When every blocker is such a subtask, owned by a bot, stalled five
minutes and waiting on nothing of its own (not in review, no run of its own able to start or going, no blocker,
no unanswered question or person it is waiting on, no future due date, snooze, stored resume or milestone wait),
the task counts as stalled even while `waiting`, unless it is waiting on a person: its bot is woken with the way out, under
the same limits, and BotOps's stuck list shows it with `own_blockers` while its bot is idle. Health's
"Tasks not progressing" counts it once escalated, only when the viewer can read every live blocker.
A queued message to a bot still in setup counts as able to start only when it comes from a person or
that bot's manager. A queued run fenced by an interrupted run of the same task or a scope limiting the bot
to other work cannot move the blocker and does not exempt its parent. A blocker's review status exempts
its parent until the blocker leaves review; this check sets no review deadline.

Task worktrees have a separate lifecycle. The 100-worktree limit stays in effect, and cleanup
waits until the owning bot is idle so it cannot remove a checkout in use. A supported cleanup
request continues to occupy its slot until the runner removes it. When all linked pull requests
finish, clean worktrees are released even while the task awaits release or post-release QA.
Trees with unpushed work stay and show the reason. Keep the task at its real step; do not mark it Done or Closed just to free space.

## What bots may put on you

Bots can file a person more than they can decide. The ask filter judges each bot item before it lands on a person's
Needs you: a task filed for them, a question to them (on a task or not) and a bot task set waiting on them. People's
own writes (also through their Assistant), required approvals and Tico's own items are never filtered; they still
count toward how full the plate is. When your Tico has a [decision model](how-it-works.md), one call per item asks
the `person-ask-ingest` question set (`questions/person-ask-ingest.json`): is it clear, what kind of ask it is, which
of your lines it meets, whether it repeats one of your open items, how urgent it is and whether the bot recommends
something. Plain code then decides from the answers, your settings and your open items (snoozed ones left out):

| The item | What happens |
| --- | --- |
| Repeats one of your open items | Joins it: the bot keeps its own, and your answer there goes back to it once. |
| A progress note | FYI: stays with the bot; no wait is set. |
| A hands-only chore (grant access, merge, add a key) | **Hands needed**, its own group, up to its own budget (5). |
| Work the bot could do itself, or unclear | Sent back to the bot. |
| Anything else, with B open items as your budget (10) | Under B: reaches you. B to 3B: also meets one of your lines. 3B to 10B: also meets one of your lines and recommends something. 10B or more: only something that can't wait at all. |

Something urgent (an outage, a security exposure, money being lost) always reaches you, unless it is only a progress
note: that is FYI however urgent, and a repeat still joins your open item. Sent back is never a
refusal: the write succeeds and stays with the bot, and its reply says why and what to do, naming the bot's manager.
A question sent back from a bot's own task leaves that task's status and note as they were; the reason is a quiet
comment on it.
Anything the filter can't judge (no model, the spend or daily cap, an error, no answer in 10 seconds, an answer under
its threshold) reaches you as before. Each call is a `judge.call` row that never holds the item's text, and Decide's
readout counts the outcomes and what people let through.

**Settings → Tasks → What bots may put on you** sets Off, Rehearsal or On, the budget, the chores budget and up to
ten lines of what you want to be asked about; an owner or admin sets the team default everyone starts from, and
**Use the team default** goes back to it. A change applies to the next item; nothing already on a plate moves. It
ships as **Rehearsal**: nothing changes, and **Filtered for you**, a folded line at the foot of Needs you, lists what
On would have done. With it On, the same line lists what was kept off today, and **Let it through** puts an item on
your plate in one click and tells the bot once. **Replay a day** on the settings card judges one past day's bot
items with your settings and your plate as it was then, so you can see what On would have done before you turn it
on; nothing moves. A replay counts no chores already waiting (none were grouped before the filter), so it never shows
"chores full". A bot handing its own task to you (one or many) is judged like a task filed for you, and each item is
judged against your plate as it was just before it. An owner or admin replaying someone else's day sees only the
titles they may read.

API: `GET`/`PUT`/`DELETE /api/v2/ask-ingest/settings`, `GET /api/v2/ask-ingest/filtered?hours=24`,
`POST /api/v2/ask-ingest/items/{id}/let-through`, `POST /api/v2/ask-ingest/replay` (`person`, `date`, `offset`, a page
of up to 25; the person, or an owner or admin).

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
waiting for a person. A try with a resume or dismiss decision shows it beside its state, read from the decision
record each time so a later decision replaces it: "Cancelled: dismissed by BotOps at 09:50Z, <note>" (or Held,
Queued, Done for a resume), naming BotOps, the person or Tico's auto-reconcile. Once the job has moved to a later
try, the earlier try says only what was decided ("Resumed by BotOps at 09:50Z, <note>"); a later stop with no
decision of its own shows none. Under it are the subagents the try started, as a tree with each one's type, model, duration and
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

**Turn it on.** Go to **Settings > System** and switch on **Stale ticket review**. It is off by
default, and an owner or an admin can turn it on or off, pick how often it runs, or run it now. (A server started with
`TICO_STALE_REVIEW=1` has it on by default; a choice saved in Settings wins.)

**What it looks for.** Once an hour (or on the schedule picked in Settings > System), Tico checks every open, doing, waiting and review task that is
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

### The question card

Decide and the task's **Needs you** box show the same question card: the same text, picks, and buttons.
The header names the real asker, or **Tico** for a system check. It shows the question's age, such as
"asked 3 days ago", and **holds up:** with the parent task or roadmap item's title.
The question comes next, followed by **Why it matters:**.

To answer a question with several picks:

1. Review the checkboxes. Recommended picks start ticked and labelled **(recommended)**.
2. Tick the picks you want and untick any you do not want.
3. Optionally write in **Add a note** to explain what you want instead.
4. Click **Send ticked (n)** to send the ticked picks as yes. The number is how many picks you have ticked.

The bot receives each unticked pick as "not this one", along with your note.
For example, with four picks, tick 1, 2, and 4, then click **Send ticked (3)**.
The bot receives yes for those three and "not this one" for pick 3.
To say yes to every pick, click **Yes to all** instead. **Send ticked (n)** is disabled when no picks are ticked.

A single-choice question has one button per option. Click the option you want to send that answer.
Each pick and answer button has a short line saying what happens when you choose it.

If the question has a fallback, its **If you don't answer by…** line appears in small text at the bottom.
It describes what happens without your answer; it is not a recommended pick.
Open the collapsed **Details** for the full message and technical text, including ids, file paths, and error output.
In Decide, **Details** also includes a link to the task.
See [When nobody answers a bot's ask](#when-nobody-answers-a-bots-ask) for when questions close.
Approvals for spending or publishing keep their own card.

#### Reviewing files

`hub task question <person> "Is this cut ready?" --review cut.mp4` (repeatable, a path or `FILE[@N]`; MCP
`hub_task_question` with `review`) asks about exact file versions. A path is published first. The card shows the files
inline (pictures, video, audio, pages; four at most, the rest as chips) with **Approve** and **Request changes**, unless
the bot gives its own options. `--pick` (2–5 files) makes one option per file, each under its media, plus **Request
changes**. Request changes asks for a note. The person must be able to open each file. With your own `--option`s,
only one labelled "Approve" approves; any other label is recorded as `other`. The same question about other versions
is a new question; about the same versions while the first is open, it is refused as a duplicate.

The answer records `reviewed: [{file, version, digest, verdict}]` (`approve`, `changes`, `passed` for the files not
picked, `other`). `--spend "LABEL=AMOUNT:WHAT"` (`--spend-account`, default `gateway`) makes picking LABEL also
approve that spend: the answer stores `spend_approval`, a `spend` approval the bot requested and the person decided,
which the bot consumes once (`hub approval consume`). [`hub file approved`](files.md#approved-versions) reads the
verdicts. Where a bot moves a stage instead of a person, it reviews directly with `hub file review`.

Tico refuses a bot's question to a person, saying why and with an example, when:

- it lists picks in its words, or is `--multi`, but gives no options;
- an option has no "what happens" line (`then`; `--option "LABEL -> WHAT HAPPENS"`);
- it is `--multi` with none marked recommended (`--recommend LABEL`);
- the question, the why line or a pick's label, description or outcome has an id, hash, file path, tool name or
  error text.

A yes-or-no question needs no options; its Yes and No buttons say the bot hears that answer. The refusal is a writing refusal: it never counts toward a quarantine.

Decide orders cards by what they block:

1. Cards with a due or fallback date within two business days, including dates on work they hold up.
2. Cards holding up a roadmap item at **Approved** or **Building**.
3. The remaining cards.

Within each group, newest cards come first. Age never moves a card up.
This ordering applies to Decide; it does not change the order of Needs you elsewhere.

### When nobody answers a bot's ask

A bot's question may say what it will do if nobody answers (`hub task question ... --if-no-answer "Yes"
--default-kind reversible [--by <date>]`; MCP `if_no_answer: {do, kind, by}`), and the card says it: "If you don't
answer by Fri, Oct 16, Writer goes ahead: Yes". When that step is reversible and internal and the person has said
nothing by then (the team's days after the ask, 3 by default, or its later `by`; `by` is at most 14 days away and
`--default-kind` is required with `--if-no-answer`), Tico answers for them with "No answer from Ana by
Oct 16, so Writer goes ahead with what it said it would do: Yes", which wakes the bot with that choice. A send,
spending money or a delete never goes ahead without an answer: the card says so, and on its date the question closes and the bot decides without it. The owner turns this off,
or changes the days, in Settings > People (**Bots go ahead on their recommendation**).

A send, a spend, a delete, any default when the owner turned going ahead off, and any default once the person has
written since the ask (a comment, a Clarify tap) never goes ahead, but its question still closes on its date: the bot is
told nothing went ahead and decides without it. A question with no date that
is unanswered after two business days (Monday to Friday, Pacific) leaves the person's Needs you at the next weekday
morning's batch (8:00): each asking bot gets one "Still needed?" wake listing all of its questions that crossed the line,
and asks again (a fresh question with a new clock) or drops them (`backend/still_needed.py`). A question also closes
when its task or any task above it is done, closed or declined (a done task keeps only the structured question asking its
requester to accept the result, asked once it is done), when the person replies to it or writes on its task to the bot
that asked, and when the bot asks them a newer question on the same task.

A task a bot handed a person (they own it, the bot asked for it) that they have not touched in 14 days closes, unless it
is due later or waits on someone else, and the bot hears why. Hourly, `backend/ask_deadlines.py`.

## Wake rules

Whether a message about a task wakes its bot or waits is decided by wake rules, each with an id (`parked`, `own_write`,
`same_state`, `bot_to_bot`, `batch_*`, `assignment`, `stall`, ...). A person's message, an ask, an approval and a gate
verdict always wake (a Manager hears of a verdict, and of its subtasks' and blockers' results, as a note). A rule can be set for the whole team, a task type, a department, a bot or one task, and the most
specific wins (the **What wakes** automations on the Automations page). The message and the run both record the rule, and
`rule_weekly` in `hub sql` shows how often each woke a bot and how often that run did nothing.

An event no rule names waits as a note (`default_note`): the bot reads it on its next run. Work still wakes the bot: an
assignment or a task handed back to it, a review or QA request, a blocker, a routine, a schedule, a due date, a snooze
ending, a requested update, an unanswered ask restated, a release published, the Task Manager's report, a pull request's
checks finishing, a subtask or a blocker finishing, a stall check, and a task the bot asked for being finished, declined
or blocked on someone else (`task_blocked`). A plain
comment, another bot's status or step move, and the bot's own write (`own_write`, ignored) do not. A rule's outcome is
`ignore`, `note`, `batch`, `wake`, `ask` or `action`. **Ask** writes the message as a note and puts one item in the
Needs you list of the task's human (its requester if a person, else its owner's operator), once per task and rule.
**Action** runs `close`, `move_step`, `set_wait`, `relate`, `note`, `assign`, `add_tag`, `remove_tag`, `wake_bot` or `send_webhook` as the keeper, with the rule id in the task's history,
and writes the message as a note; if the action fails the bot is woken instead (`fail_open`). `assign` `{owner}` hands
the task to a bot or a person as a reassignment does (the new owner is woken); writing one needs the right to give that
bot tasks. A rule never changes a private task its writer cannot read, and never hands one to someone who may not read it: the action is not taken and the message wakes its bot as usual. `add_tag` and `remove_tag` `{tag}` take a tag or a list of existing tags (not template tags); a tag already
on, or already off, changes nothing. With a `tag` condition this makes label-driven rules, such as
`when {"tag": "billing"}` → `assign {"owner": "finance"}`. What an action writes is judged as news and never starts
another rule's action, so a rule cannot retrigger itself.

The person who last saved a rule's action, conditions, event, or enabled it supplies its private-task access;
the original creator stays in its audit record. An older edited rule with no recorded action writer wakes the bot
instead of changing a private task until someone saves its action again.

**Task Triggers and Actions.** The rules API also accepts the Triggers `task.created`, `task.step_changed`,
`task.done`, `task.declined` and `task.closed`. These run on the task change itself, including tasks owned by people.
Scopes are team, type, department and bot; the bot scope means the task's owner. Trigger conditions are `type`,
`step`, `tag`, `owner` and `requester`. Entering a step requires a `step` condition. Actions are `note`, `set_wait`,
`relate`, `move_step`, `close`, `wake_bot`, `create_task` and `send_webhook`, saved as `then: "action"` with
`params.action`. Existing Rules edit rights and Action permissions apply.

Every matching task Trigger runs its Action, most specific scope first (bot, department, type, team), then by
position. Actions run after the complete task save, inside the same transaction. Creation runs its Actions before
the initial step Trigger, including default placement into the first step. A repeated delivery of that transition
does nothing; reopening and finishing again is a new transition. An Action cannot start another task Trigger's
Action, including through a different rule. The task history names the rule and explains any skipped chain.

Task Actions use their existing domain checks; `set_wait` can record a `blocked_by` task, while a person wait
retains its existing owner and question requirements. Templates can read `{{task.id}}`, `{{task.title}}`,
`{{task.body}}`, `{{task.note}}`, `{{task.status}}`, `{{task.owner}}`, `{{task.requester}}`, `{{task.type_id}}`,
`{{task.step_id}}`, `{{event}}` and `{{actor}}`. The Action author must be able to read the source and any task it
links. A create-task Action accepts `owner`, `title`, optional `body`, `type` and `step`; it links the new task to
the source and keeps it private when the source is private. Wake and webhook Actions retain their recipients' read checks.

An Action failure preserves the triggering task save. Rule reads return `last_error` and `last_error_at`, including
later webhook delivery failures. A later successful webhook keeps the rule's last failure visible. Shared error
text contains no task content. At the 51st attempt within an hour,
the Action pauses: reads return `paused`, `paused_at` and `pause_reason` (`Paused: too many runs`). It stays paused
until an authorized editor explicitly enables it again; the hourly cap still applies. The Rules list exposes
`task_triggers`, `task_when_keys` and `task_actions` for editor clients.

**Events without a task.** Some events are not a message to a bot and have no task: `webhook.received` (an incoming
webhook was accepted) and `tico.notice`. A rule names one as its `event`
(or a prefix such as `webhook.*`) and `hub event-types` lists them. Rules on these events are judged by `judge_event`,
which fires every enabled row, bot rows before department rows before team rows, then by position, at most one row per
target. A bot's first row decides it, so a bot-scope `ignore` silences the team's wake for that bot and no other. An
`ignore` with no bot ends the rows after it for every target. A bare `*` row is for messages only and never reaches
these events. Scopes are team, department and bot (not type, task or `batch`), and `when` holds only `trigger` (the
webhook's id) and `type` (the event type the sender gave, a trailing `*` allowed). The same (event, trigger, delivery)
twice does nothing the second time; Tico forgets a delivery after 30 days. An error does nothing and leaves a
`rule.event_error` event. A row that does something is logged as `rule.event`.

Two actions work on these events and on message events. **`wake_bot`** `{bot, text}` writes `text` to the bot's main
room and wakes it; **`send_webhook`** `{webhook, type, data}` sends an outgoing webhook event to one
endpoint. `text` and the strings in `data` may use `{{path}}` placeholders, such as `{{type}}`, `{{data.order}}` or
`{{data.items.0.name}}`: a missing path is empty, nothing is evaluated, a substituted value is never read as a template,
and text is cut at 4 KB. A bot-scope `wake_bot` row wakes its own bot. Writing `wake_bot` needs the right to change that
bot; writing `send_webhook` is for a person, or a bot with outbound sending on, and never the Strategy Bot. A failed
`wake_bot` on a message event wakes the bot as usual (`fail_open`); a failed `send_webhook` is only logged.

A task entering a step is the event `step_entered`; a rule's `when` can name the `step` and the `role` (`owner`,
`requester`, `qa`, `release_manager`, `other`). A team with no `step_entered` rule writes nothing on a step move. A Dev
tickets type is given three editable rows when it is created or renamed: **QA Fixes** wakes the Builder that owns the
ticket, **Pending Release** is a note to the Release Manager, and **In QA** wakes QA only. Delete a row and it stays
deleted.

A rule's `step` names a step of its type, by the step's name or its id (case ignored). Saving a wake rule that names a
step its type does not have is refused (422), and the message lists the type's real steps. Renaming a step moves the
rules that name it. Deleting a step that a wake rule names is refused until those rules are changed or deleted, and the
message names them. A rule that already names a missing step is kept as it is and never fires; its automation shows a
warning on the Automations list and in its detail, and the automation's `problems` (`rule`, `step`, `why`) say which rule.

A bot's **profile** (Doer, Reviewer, Lead, Manager, Responder, Monitor, Custom) adds a few bot-level rows of its own: a Reviewer wakes for
its review and QA requests and people and notes the rest, a Lead gets everything but people, escalations and approvals in a daily
digest, a Manager wakes for people, tasks given to it, its own clock and its exceptions list and notes the rest, a Responder and a Monitor note the rest. The bot's **What wakes** automation shows them as the profile's, and a Custom bot's copies as its own
([Profiles](how-it-works.md#profiles)).

`GET /api/v2/event-types` (`hub event-types`, `hub_event_types`) lists the named events a rule or automation can use.

## Choosing the model a task runs on

A bot's task runs on the bot's task model, or on its own model while that is not set
([Run a bot's tasks on another model](harnesses.md#run-a-bots-tasks-on-another-model)). A task can name its own model,
effort and harness instead. In a bot's task window, **Runs on** picks them and **Apply** saves. When filing, use
`hub task create --model <id> --effort <effort> --harness <harness>`. To change a task that already exists, use
`hub task model <id> <model> --effort <effort> --harness <harness>`, or `hub task model <id> --clear` to return it to the
bot's task model. `hub_task_create`, `hub_task_model` and `POST /api/v2/tasks/{id}/model` (with `{"model": null}` to clear) do
the same. `hub bot task-model` and `hub_bot_task_model` read or set the bot's task model. `GET /api/v2/tasks/{id}` shows
the task's own choice as `run_model`, and `can_set_run_model` says whether the reader may change it.

Whoever may change the bot's model may change a task's model. A bot never does, not even on its own task. On a
[branch](creating-bots.md#branches) that is whoever may change the original's model, since the branch follows it; the
task's choice is the task's own and leaves the branch's definition as it is. A run uses the task's own model first,
then the bot's task model, then the bot's own model; a review or check subtask keeps its check model. A task that
changes owner loses its own model, however it moves: its history records the clear, and the new owner's bot runs it on
that bot's task model.

## Snoozing a task

**Snooze** sets a task aside until a date: it tells its bot or person that nobody plans to get to it
before then. Its requester, its owner, their managers, anyone on it by role, or a mover snoozes it from the task's
**…** menu (Tomorrow, Next week, In two weeks, In a month, or a typed date), with
`hub task snooze <id> --until 2026-11-02`, `hub_task_snooze` or `POST /api/v2/tasks/{id}/snooze`
with `{"until": ...}`. A bare date means the start of that day, Pacific; a date-time with a zone is
taken as it is. Snoozing again moves the date. The task keeps its status, owner, goal and place;
its `snoozed` hold ([Holds](#holds)) says until when and who. A snoozed task still counts toward its goal.
The snoozer may leave a **note** (`--note`, `note` on the API, `hub_task_snooze`): what to check when it ends.
It goes in the **Snooze over** wake, as `Your note:`, and is the snoozed hold's `reason` until then
(at most 500 characters; a longer one is refused).
When someone other than its requester snoozes a task, the requester is told. A role holder's
manager gains no snooze permission unless they also manage the owner or requester.

Until that moment:

- it is off the board, the Open list, Needs you, Decide and `hub task list`. The **Snoozed** tab on
  the Tasks page (shown while something is snoozed), `hub task list --snoozed only|all` and
  `GET /api/v2/tasks?snoozed=only|all` list it; a read by id or number always finds it.
- nothing automatic moves it: no stall wake or status check, no stranded or stuck sweep, no
  **Still wanted?** question and no **Due:** reminder. Tico's notices about it reach its owner bot
  deferred, read on the bot's next run instead of starting one, and so does another bot's comment.
  A person's comment still reaches the bot as usual, **Run now** still runs it, and notices to
  anyone else on it (a requester bot it is handed to for review) arrive as usual. A snooze survives
  reassignment: the new owner gets a deferred notice of the task and is woken when the date comes.

When the date comes, the task is back everywhere and its owner bot gets one **Snooze over** notice,
which also stands in for a due date that passed during the snooze. **Unsnooze** (the **…** menu, the
**Snoozed to** chip on the task, `hub task unsnooze <id>` or `POST /api/v2/tasks/{id}/unsnooze`)
brings it back early and wakes its owner bot once, with the note if one was left. Finishing, declining or
closing a task, or moving it to review, ends its snooze and clears the note.

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

An owner can reopen a Done task with `hub task update <id> --status doing`, or put it back
with `hub task update <id> --status open --note "why it goes back"`. On Dev tickets, open is
On Deck. A bot owner must include a note when returning work to open. Automatic starts wait
60 minutes after an owner puts work back, unless the task gets a comment, a new link, or a
blocker finishes. Ordinary note edits and automatic notices do not end that wait. A linked
blocker still holds the task. If a blocker arrives after Tico automatically started it and
no run has finished on it yet, Tico returns it to the queue as part of linking the blocker;
the owner does not need to take a turn.

Closing is a separate decision; it records `closed_at` and
`closed_by`, preserving the completion time if the work was already Done. Earlier history
is preserved, including tasks that older versions closed automatically. Use **Close task** in the
Done task’s menu, or bulk **Close**, to accept completion. A waking comment delegates further
work, not permission for the assignee bot to accept its own result.
Completed work does not block a new task with the same requester, owner and title; unfinished
duplicates are still refused, ignoring case and extra spaces, on create and on rename alike.

## Changing many tasks at once

Select tasks in the list (their boxes, Shift for a range) and the bar at the foot of the list offers **Status**,
**Owner**, **Tags** and **Close**. **Select all** in that bar takes every task the list shows; on the board, the
check-box button in a column's header selects every card in the column. A selection made that way is a filter, and
the change goes to the server once: `POST /api/v2/task-bulk`. A step column is its type, its step and the open
statuses a board shows; the list is its type and the open statuses, with an Owner, Asked by or Tag chip when only
one is picked. A view the server cannot filter the same way (Needs you, a person's plate, Done, a search, a Team or
Repo chip, a fold that hides some rows) sends the ids it shows instead, up to 100. Tasks picked one by one are
changed one update each, as before. Close can be undone for ten seconds either way.

```sh
hub task bulk step "Legal review" --where-type Marketing --where-step Draft --where-label launch --dry-run
hub task bulk close --where-owner me --where-status waiting --note "Not needed this quarter"
hub task bulk tags --add q4 --remove backlog --where-label backlog
hub task bulk status open --where-label q4 --where-status closed
```

`POST /api/v2/task-bulk` takes a `filter` with the filters of `GET /api/v2/tasks` (`owner`, `requester`, `status`,
`lane`, `label`, `type`, `step`, `number`, `updated_since`, `member`, `role`, `snoozed`, and `ids`, a list of up to
100 task ids or numbers), one `action` (`step`, `status`, `owner`, `tags` with `add` and `remove`, or `close`), an
optional `note` and `dry_run`. `hub_task_bulk` takes the same. The filter must name something: an empty one, or an
owner or requester who is nobody, is refused rather than read as every task. Each matching task is changed by the
same update a single change makes, with the caller's own rights: its events, wakes and refusals are that task's.
A task the caller may not change is skipped with the reason and the rest still change; a task already that way
counts as `unchanged`. The answer gives `matched`, `changed`, `unchanged` and `skipped`, and names the tasks changed
and skipped. One call changes at most 200 tasks; more matches than that refuse the whole call (`too_many`), so
narrow the filter. `dry_run` makes every change, counts it and undoes it: nothing is saved, told or woken. Tasks
cannot be archived or forked: those are not task actions, in bulk or one at a time.

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

A person's comment with no way named is a **Steer** when the bot owns the task (see Conversation below), and is never
deferred on a task with no bot on it. On a task a bot filed and a person owns, a comment is most often a progress note:
it is a **Defer** with a 24-hour deadline, reaching the bot that asked on its next turn on the task, or in a day at the
latest. A comment that @mentions a bot on the task (`@ops`, or its name) is a **Queue**; one from the person the task
waits on is a Steer. A comment from someone not on the task (not its owner, requester or a mover) is a Defer with no deadline,
so a reader never spends a bot's turns on a task that is not theirs. A person @mentioned in a
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

A mover can also make a custom type **steps only**: its tasks are finished or brought back by
moving to a step. Closing, declining, reopening or finishing one directly (`close`, or a `status`
without a `step` that enters or leaves done, closed or declined) is refused (`422 kind`), except
that an owner may reopen Done with doing or open (with a note for open). The task window offers
no Done, Close or Reopen on them. A task that should not
be on the board is deleted instead. Working statuses still change without a step (a bot's ask sets
waiting), and filing a task onto the type and the keeper's own automation are unaffected. Such tasks
are not asked "Still wanted?", their goal asks the bot to move them to a step and a met goal does not finish them, and Needs you says to move them
rather than marking them done or closed. Without steps only, a bare status lands the task in the first step
with that status, which on a board is rarely the one anyone meant.

Engineering boards have no required steps, and no Blocked column. A hold is a mark on the ticket, not a step:
a held ticket keeps the step it is in, and its status is `waiting`, which is what stops it being claimed or
woken and pauses its Building clock. The owner can hold a ticket by moving it into a waiting step the team
made (a step whose status is waiting) and writing the reason in its note:

```sh
hub task update <task-id> --step "Waiting for a test machine" --note "Waiting for a test machine"
```

A ticket parked by hand in a team's waiting step stays a declared hold, and clearing it means moving it back
to a working step. Deleting a waiting step returns its tickets to the step they were parked from, or to On Deck
when that step is gone, so no ticket is lost to a deleted column.

A refused software review gate, an open dependency or a person/milestone wait also blocks the task.
Review, QA and Security check children keep the ticket in its pipeline step; only an explicit
dependency on one of those checks holds it. A Review or QA check that is itself Waiting, or blocked by
an open task, holds its ticket at its step with the check's reason ("QA blocked: GitHub refused the remote
test run, waiting on Ana"), and the ticket's builder gets one notice in the ticket's room for that
hold. When the check works again, the ticket returns to its step.

A task filed to DevOps or a person about a blocker can name it: `--blocker <repo> <status> <endpoint>`,
for example `--blocker acme/app 403 workflow_dispatch`. When an open ask names the same repository, HTTP
status and endpoint, the new task is not a second ask: it stays with the bot that filed it, blocked by
the open ask, and the reply says so. Every task it blocks wakes when the ask is done. Unlinking it
(`hub task relate <id> <ask> --kind blocked_by --remove`) hands it to the owner it was filed for, as its
own ask. Endpoint case and slashes are significant; only surrounding whitespace is ignored. A different
endpoint is a different blocker. Private tasks never join or are joined. When DevOps
files the person ask for a blocker, its own open tasks for that blocker wait on it.
The card and task show what it waits for and since when. When an automatic hold clears, the task
returns to the step it left, without advancing through the refused gate; back at a check step, a verdict its check
gave during the hold moves it on then. An owner-declared hold
is cleared by moving back to a working step. The active Building clock pauses while blocked;
a separate blocked age alerts the type's manager after four hours, once per hold. The bot rail's
Blocked filter uses waiting tasks, and its default Doing list excludes them and Needs You tasks.

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

`GET /api/v2/pipeline` (`hub pipeline`, `hub_pipeline`) is a board's numbers in one read, counted over the same
tickets the board shows the caller: per working step the count, how many are snoozed, the oldest and median time
since tickets entered the step, owners, whom they wait on (a bot, a person, something external or nothing) and their
open pull requests. It also gives each bot the caller may read its running, queued, held, `dead` and uncertain jobs
(a dead job is queued behind an interrupted run nobody has reviewed, so it never runs until that run is resumed or
dismissed; a bot's page and Health's queue line count its work the same way, and Health names each part) and failed runs since `since` by cause (quota, capacity, restart, timeout, crash, stopped); past
5000 readable failures it stops, sets `truncated` and says in `complete_after` from when the counts are whole. Filters:
`type` (default the engineering board), `owner`, `label`, `updated_since`, `snoozed=all|hide|only` (default all),
`bot`, and `since` up to 7 days. Each bot also lists, in `decisions`, its stopped runs with a resume or dismiss
decision that still hold it or were made since `since` (newest first, 20 at most): the job's state, the decision, who
made it, when, the note and a `label` like "Cancelled: dismissed by BotOps at 09:50Z, <note>"; once the job has run
again the decision is history (`current` false, no `state`: "Resumed by BotOps at 09:50Z, <note>"). Counts and ids
only; no titles or bodies, except that a decision's note, which can name the task, is shown to a reader who may open
the run's conversation.

For the engineering type's manager, and nobody else, the same response includes `exceptions[]`: the board's exceptions
list ([Software factory](software-factory.md)), one row per subject and kind, oldest first, each with its `cause` and
`since`. A row about a ticket carries the ticket (with its step and owner), and `check` when the open check below it is
what stands; a failed merge carries its PR number and head.

Type create and update also accept `bots` (`parties`, `read` or `work`). With `work`, any bot may
change a task of that type and close it once it is in a ready step, so a deploy bot can move shipped
tickets to Done; moving a task into ready stays with people.

`task_types` and `task_steps` are readable through SQL. Join them to the caller's visible `tasks`
using `tasks.type_id` and `tasks.step_id`; the task visibility rules still apply.

## Subtasks and PRs

Subtasks can nest to any depth. Whoever files a subtask, person or bot, is its requester. When a
worker finishes a subtask under a bot-owned task, its result returns directly to the immediate
parent's owning bot, in that parent's task session. An active turn takes it as an input; otherwise
the parent's next turn resumes there. That holds while the parent is Waiting on its other open subtasks: they
do not hold one subtask's result back. Its other waits still do: a snooze, a person it waits on, an unanswered
question, an undecided approval, a worktree, or a blocker that is not one of its subtasks. The result does not move
the parent out of Waiting. There is no requester delivery session on the completed
subtask and no relay or `hub task run` needed. The worker keeps its own independent task session. A manager completing the worker's delegated
subtask returns its result to the parent in the same way.
A bot that filed the subtask may close it explicitly; until then it stays Done. The parent's
requester hears when the parent itself finishes, so a manager's task → builder task → review
subtask sends the review back to the builder, and the completed build back to the manager.

A review verdict on a subtask's comment or final reply takes the same direct path, and the parent owner's wake carries the
subtask's `verdict` and `verdict_head`. The parent's requester is told in notes, and woken only when the task is
declined, blocked or done. Its comment,
Done note and final reply are one delivery for that verdict and review cycle; reopening the
subtask permits a new review. Completed or closed parents receive late results deferred: no run starts. Private
subtask text returns on the parent only when that parent's readers may receive it; otherwise the
requester receives it on the private subtask as before. Ask and answer messages keep their own routing.
Subtasks never inherit a human's delegation anchor from a bot's request. BotOps acts with the
filing bot's rights when that bot asks it to work on a subtask.

Parent owners can track descendants they can read, without gaining Read on hidden bots.
Only the task's owner, requester, their managers or a human mover can re-parent it; bots must also own,
request or manage the new parent. Moving or detaching a subtask also requires its current
parent's owner, requester, their managers or a human mover. Moving a task moves its whole subtree,
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

New conflicts, requests for changes, review comments from a person and PRs closed without
merging wake the owner with the specific item, grouped into one notice within three minutes.
Conflicts wake once per head until a known clean result clears the marker. A single check finishing
sends nothing; when every check on a head has finished the owner gets one `pr_checks_done` (pass or
fail, naming the failures) for it. It wakes the owner while the ticket is at a working step (status open or
doing) and is a note at any other step; a link armed with `--wake-once` hears it at any step. A review or comment
from a bot or a GitHub App is a note, never a wake. A new push resets mergeability to Unknown while GitHub recomputes
it; label and text edits keep the last known mergeability. The bot's own comments do not wake it. Tico recognises
the GitHub App identity, configured bot login and PR author. A head pusher counts as the bot only when their login
matches the PR author.
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
API) it takes the company's check model (Settings > Models, [How it works](how-it-works.md)): the first in its
order from a provider other than the parent's authors and runnable on the bot's computer. The authors are every
maker on the parent's finished runs (a fallback counts) and the provider its owner runs on. Another provider is a
preference: with none that can run, it runs on an author's own, and the subtask's history says so. `--role` is `review` or `check` (the default). The API takes `check_model` and `check_role` on task create; the MCP tool is
`hub_task_child_create` with the same fields.

- The subtask is the filing bot's own, under a task it owns. Its turns run on the named runtime and model with no
  fallback to another provider, always in a session of their own, and read only the subtask's room: name the PR
  or files in its body. Its turns carry none of the bot's deferred tasks or messages.
- Only the runtime, model, harness and effort change. The bot's repository, tools, credentials and isolation are
  its own, so a check never runs with more than the bot has.
- Only a provider the company has enabled (Settings > Models) and a current model can be named, and the
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
  the next check model in the company's order (Settings > Models) that is not from a provider whose work it
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
  are unknown (a person's task with no runs) or no check model can run it, the subtask is filed as usual on the
  reviewer's own model, and its history says why. With no other provider able to run it, it runs on the authors'
  own provider rather than waiting: another provider is preferred, never required. Subtasks under its own tasks, and its top-level tasks, keep its
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
**Starting**, then **<Bot> is working…** with its newest step, opening to the steps so far, until the reply, or **Reply
failed** when the turn errors. It follows the bot your message went to, which on a task you own that a bot asked is that
bot, and the owner's own runs as before; a bot's message to another bot does not take it. Whoever manages the bot gets
**Stop** beside it, the same stop as in the bot's chat (`POST /v2/bots/{slug}/stop`). The box at the foot is the chat's
composer, and what you type there is a comment. Where a bot gets it, the send says how it arrives, in the words a
bot's chat uses ([Steer, queue or defer](using-tico.md#steer-queue-or-defer)): **Steer** goes into the bot's turn running on this task
now (right after it, if the bot's harness cannot steer); **Queue** goes right after that turn, or now when the bot is
idle; **Defer** wakes nobody and goes in with the bot's next turn on this task, at the latest after the time picked
under it (30m to 7d, or Never; 24h unless picked). A Defer sent while the bot works on this task goes in queued; a turn
on another task does not count. **Set as goal**, apart in the menu, makes the text the goal of the bot's session for
this task, as `/goal` does in a chat (no files, not public; [chat goals](chat-goals.md)). Only someone on the task (its
owner, requester or a mover) may steer, queue or set the goal; anyone else may defer. The caret opens the ways, and the
choice is kept per task in this browser. Only a choice made there is sent: with none, the comment goes the way the
paragraph on comments above says. Before you send, the box says what the chosen way does, and after, if the server
sent it another way (a defer that rode along). With no bot on the task (everyone on it is a person) the send is a plain arrow, and the box says no bot gets the
message. `GET /v2/tasks/{id}` carries
this view: each bot reply in `comments` has its `run`; `execution` is the owning bot's newest run in the task's own
room, or a newer one for a person's message there, shaped as in a conversation snapshot with `from_person`; `comment_to`
(`bot`, `wakes`, `mention`) is who your next comment reaches; and `comment_delivery` (`default`, `default_for`,
`steer`, `queue`, `steers`, `goal`) is how it can reach them: `default` is what a comment without a delivery does
(`steer`, `queue` or `defer`) and `default_for` that defer's deadline (`24h`, `never`), `steer` and `queue` say you may wake the
bot, `steers` says the bot's harness can take a message into a running turn, and `goal` says a goal can be set (null
and false without a bot). A task still in a chat room (one filed into a chat before task
rooms) has no `execution` and no goal of its own: that room's runs and goal belong to the chat, so `goal` is false.
`hub task comment <id> "<text>" --steer|--queue|--defer [30m|4h|2d|never]|--goal` (one at most), `hub_task_comment`
with `delivery` and `defer_for`, and `POST /v2/tasks/{id}/comments` with `delivery` and `defer_for` choose the same
ways; the answer's `sent_as` (`delivery`, `until`) says how it went. In v0.5.0 `queue` on a comment meant what `defer`
means now; it now wakes the bot after its turn. A question (`ask`) always wakes the one it is for, so it takes no
delivery; `needs_action` takes none or `steer`; a `goal` takes no files and cannot be public. A refused combination is
a 422, a steer, queue or goal by someone not on the task a 403, a goal on a task still in a chat room a 409, and nothing
is saved.

Tico spot-checks running work. A bot's open or doing task whose own session ran in the last day (or runs now) and
that has made no progress for 30 minutes (no checkpoint, comment, status change, message from its bot or finished
turn) gets one "Status check: checkpoint where you are, or mark it blocked or done." in its session. Thirty minutes
later with still no progress its bot is retried once; thirty minutes after that, still with no progress, it is reported
stuck in the main chat and BotOps gets a task to find out why. A stuck check never goes to a person as a question. These
share the stall watcher's spacing and its cap of three a day, after which BotOps gets a task to find out why.

An idle task unchanged since its owner's last settled checkpoint skips a plain automatic stall or status check.
Changed state or a missing checkpoint leaves the check eligible under the usual wake rules. Human messages still
wake the bot as before ([details](hub-sql.md#what-woke-each-run-and-what-it-did)). A running session is not sent status
checks or retries; they resume once its turn ends without progress.

With the team's **Stalled task check** switched on (Settings > System routines, on by default, needs Tico Decide), a
stalled task that stands as at its owner's last checkpoint is no longer left silent. Decide reads its last six messages
once for that state and answers one of four things. *Waiting on someone*: the wait it names is set (a task the messages
mention as a blocker, a date they mention as a snooze, or a person they name as the owner's own waiting-on, with a
one-line ask in a small subtask), and no run starts. *Finished but unmarked*: the task is closed with a note crediting
whoever spoke last; reopen it if that was wrong. *Stuck*: the one stall wake goes through with today's way-out text, under
the usual spacing and daily cap. *Unsure*, or any answer under its confidence, nothing changes and the Task Manager's next
report names the task with "class: unsure". Decide off, not set up, over its daily or hourly limit or failing leaves the
check silent, as before. Nothing else is automatic after the wake.

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
woken; a person's close note that differs from the bot's own done note reaches an active bot as a wake. Routine completions with nothing said are batched; paused bots read them on their next run.

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
 "why":"The client sees it on Friday.",
 "options":[{"label":"Approve","then":"It goes to the client"},
            {"label":"Request changes","description":"Say what to change","then":"Writer redoes it"}],
 "multi":false,"other":true}],"who":null}
```

Questions may be on a comment or a file version. An ask has one to four questions, each with
an id (up to 40 characters), header (30), question (300), and zero to six options. Labels are up
to 60 characters and descriptions up to 200. An option can link to a version using `file: "<id>@<n>"`.
`then` (up to 120) says what happens if it is picked, `recommended: true` marks the ones the asker advises, and a
question's `why` (200) says why it matters. `multi` defaults to false; `other` defaults to true. A bot asking a person
keeps the asking rules in [The question card](#the-question-card). Question ids and labels must be unique.
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
and `refs.answer`); questions offering only free text still accept ordinary text answers.
For a question with options, an authorized recipient's reply through `messages/{mid}/answer`
settles the decision only when it exactly matches one unique option label on a single targeted
question. It records the same structured answer as a card pick. Unmatched or ambiguous text is
saved as a note, leaving the options and any person wait open. Repeating the same structured
answer does not record another decision. Settling a question does not approve or execute a
separate publish, send, spend or hire action.
Needs you excludes closed, cancelled, archived and declined tasks, even with an open question.
The version's metadata points to its ask message. These routes use the usual Idempotency-Key contract:

- `POST /api/v2/tasks/{id}/files` accepts the existing `name` plus `text` or `content_base64`, and
  optional `note` and `ask`. The result includes `file_id` and `version`, alongside the existing
  `file` and `link` fields.
- `GET /api/v2/tasks/{id}/files` returns files, including archived files, with versions newest first.
- `PATCH /api/v2/files/{id}/versions/{n}` accepts `note` and `ask`, by the version's uploader.
- `POST /api/v2/tasks/{id}/comments` accepts `text`, `ask`, version references in `attachments`, `delivery`
  (`steer`, `queue`, `defer` or `goal`) and `defer_for` (see Conversation above).
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

The person picker beside **Filter** says whose tasks the first tab shows. **Me** is Needs you as
always, with its approvals and questions. Pick a teammate (people by team, each with a count; bots
fold behind one line) to see what is on their plate: an open task whose `waiting_on` is them, or
that they own when it waits on nobody, or a question asked of them. **Asked by them** shows what
they asked someone else to do. **Everyone** drops the person. The choice is in the address
(`?who=human:sam&side=asked`), so a link opens the same plate; the Owner and Asked by filters still
narrow it. On a plate, tasks blocked by an open task fold under **Blocked** at the end. A row's
status opens its statuses (or a typed task's steps) to move it; a person's due date is amber
within two days and red once passed.


## Company and private tasks

New ordinary tasks are readable by active company people and bots. Reading does not grant
permission to change, reassign or complete work. Type bot settings continue to grant comment,
subtask and work permissions; they cannot override Private or restricted bot activity.

Turn on **Private** in the existing task controls to limit future reads to the requester,
current assignee and the managers of any bot party. Tasks with only person parties remain
private to those parties and their guest. The assignee or its managers may tighten visibility; only the human requester
can publish again. Bots cannot publish a private task, including when acting for a person.
Bot-requested private work stays private until a future explicit human publication mechanism
exists. Mentions, administrator status, parent ownership and delegations grant no access.
Reassigning a private task gives the new assignee access and removes the previous assignee's
future access unless they remain its requester or manage a current bot party. Reassignment never clears Private.

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
- Only the bot that owns the task or its managers bring a guest in; the requesting bot cannot. Reassigning the task ends the
  guest's access; a new bot owner may then bring in one person of its own.
- When a private task above it has a person on it, only someone who reads that task above can become the guest. From
  the task's own private work the hand-off is refused; otherwise the person gets their subtask alone, as before, and
  does not become the guest.
- The guest's answer on their subtask or question comes back to the task, and wakes its owner, as on any task.
- Approvals stay refused from private work (they go to the default approver): ask the person with a subtask or
  `hub task question`.
- `POST /api/v2/tasks/dry-run` makes the same privacy checks as the create and returns the same refusal text.

### A private task with no person on it: a sealed subtask for another bot

The same task's owning bot (or its manager), from its own run, may hand work to another bot in a **sealed** private
subtask: `hub task create --owner <bot> --parent <task>`. The other bot reads that subtask, and only the words written
into it: never the parent (its title, words, comments or id), its other subtasks, or the run's other work. Each of
those stays "not found" for it, and its notices and run prompt leave the parent out.

- Every private task the run carries that the other bot cannot read must have no person on it (no person requester,
  owner or guest), nor any private task above it. Otherwise the create is refused, naming the person: ask them with
  `hub task question`, or have them file the subtask. When the person is on a task above that the filer cannot read,
  the refusal leaves their name out and says to ask the filer's own requester to file it.
- The subtask is private; a company-visible one is refused, as for any create from private work.
- Its words may not carry the private work: a secret-shaped value, or 12 or more words in a row copied from a private
  task the other bot cannot read, is refused naming the check (never the text). Rewrite it and file again; it does not
  count toward quarantine. A full commit id is fine after a commit word (`head <sha>`, `base <sha>`) or in a GitHub
  commit link, and so are branch, session and dated path names; a word mixing letters and digits inside a long name
  (`pr1714-...`) reads as secret-shaped, so write it apart (`pr-1714-...`).
- The parent's history records "Handed to <bot> in a private subtask"; the subtask's records who filed it.
- When the subtask is done, the parent's owner is woken on the parent, as for any subtask. Its words do not reach the
  parent's other readers.
- The other bot may file its own review or QA subtasks under the sealed one by the same rule.
- Comments and files from the private run to the subtask, and any company-visible work, stay refused, except the
  company-visible task below.

### A private task with no person on it: a company-visible task from private work

When the work belongs to the company (a repair anyone may pick up, see and follow), the same owning bot (or its
manager), from its own run, files it **from private work**: `hub task create --from-private --owner <bot> --parent
<task>` (or with no `--parent`). The task is company-visible and carries only its own title, body and acceptance
criteria; the private task stays sealed.

- The filer must own (or manage the owner of) the parent and every private task the run carries; a bot that is only a
  reader or guest of one is refused. A person on any of them, or on a private task above one, gets the same refusal
  as a sealed subtask, naming them and the route to them. The task is for a bot; the parent, when given, is private.
- Its words are checked before it is written, and a failed check is refused naming the check, never the text: no
  secret-shaped value (as for a sealed subtask), no 12 or more words in a row copied from the parent or any private
  task the run carries (title, body, note, acceptance criteria or comments), and no file, attachment or link of a private task. The checks
  read everything the task keeps: title, body, acceptance criteria, labels, links (a secret-shaped value in a link is
  refused too), public title and summary, change paths, blocker and the names of its roles (as sent and as stored).
  Rewrite it and file again; it does not count toward quarantine. Nobody signs off on the words.
- The title of the parent, or of any private task the run carries, is refused anywhere in that text, in any case.
- The copy check catches only 12 or more words copied in a row. A short name that is not a private task's title (a
  vendor, a person, a project) is not caught: that rests on the filing bot's judgement. So does the rest of the run's
  context: its messages that are not on a private task, the files it read and its tools' output are not compared, and
  a copy broken up with a word every few words passes. The checks catch careless copying, not a bot set on leaking.
- A company task under a private parent never sends the parent's state to the stale-work review.
- It is related to no private task but its parent (no blocker, related or duplicate link to one).
- A reader who cannot read the parent sees it as "a private task", with no title or id; its own readers see it as usual.
- History: the private task records "Filed a company-visible task from this private task: <title>"; the new task
  records "Filed from a private task", without the private task's title.
- Filed under the private task, it is a subtask: the private task waits on it, and when it is done the private task's
  owner gets its result, as for any subtask.
- Without `--from-private`, a company-visible create from private work is refused, and the refusal names this route.

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

A task always deletes, whatever was done on it, because closing it instead would count it as
finished work. What bots did on it (turns, jobs, approvals, a bot's own files, a routine occurrence,
a meeting delivery) stays where it is: no one can read the task's conversation while it is in the
trash, its queued bot jobs are cancelled, its subtasks lose their parent and the tasks it blocked
are freed. A restore links it all back. Files attached to the task or its comments are not work: they go with the
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

## Task settings

Open **Settings → Tasks → Task settings** to see what Tico does when a task is created, enters a step, finishes, or is checked on a schedule. Choose a task type to see its steps. Each action shows its source, whether it wakes a bot or stays quiet, and its current state. Edit and Add action open the existing Rules editor with that task type and trigger selected. Built-ins marked Always on have no switch.

