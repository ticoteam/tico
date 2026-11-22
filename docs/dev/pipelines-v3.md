# Pipelines v3: one engine for every task

This is the build spec for Tico 0.6.0 pipelines. It was signed off Nov 9 2026 and is version 1.0. Code must match it. A
change to behavior changes this file in the same PR. Changes are named by number (C3, S7, D12). The checks that prove
the build are in [Pipelines v3: validation](pipelines-v3-validation.md).

Every task sits in a pipeline, at one stage, and always shows whose turn it is. One function moves tasks. One function
says what a task is waiting on. A check every 10 seconds asks what each task is missing, who has it, and whether they
are on it.

The build order is General, then Product, then Engineering. The spec has three sign-offs: A (the state machine), B (the
architecture) and C (the screens).

## The short version

### Why tasks get stuck today

- **Many movers.** Dozens of code paths change a task's stage or status, and five of them write the row directly. Each
  has its own idea of what comes next.
- **One-shot wakes.** A bot is woken when something happens. If it stops early or the wake is lost, nothing reliable
  sends it again. Four separate sweeps try, each with different rules.
- **Invisible waiting.** A task can be Blocked, Waiting, held or parked for a dozen reasons stored in different places.
  Nobody can see which one, or who has to act.
- **Many ways to ask you.** Questions, approvals, stale checks, stuck asks and goal asks are all different objects.
  Requirement reviews are not in Needs you at all.

### What v3 does

- **One engine.** Every task sits in a pipeline. General, Product and Engineering are the same parts with different
  settings.
- **One mover.** `move()` is the only thing that changes a task's stage, owner or status. The database refuses any other
  write.
- **One read.** `waiting_on()` says whose turn it is and what is missing. Every screen, every bot and the health page
  read it.
- **A never-stuck check.** Every 10 seconds: what is missing, who has it, and are they on it? If not, it acts.
- **One ask.** Anything you are asked is one sentence in Needs you, with the thing to look at and the buttons to answer.
- **One hold.** Snooze. There is no Blocked.

> **The promise the build is tested against.** No open task is ever waiting on nothing. A task whose bot stops early
> costs at most 7 runs, about 3 hours, then the ladder rests and the quiet-task rule tells its manager. A person can
> always move a task back, close it or snooze it, whatever state it is in.

## Decided

### Settled

- Anyone, bots included, may push past unmet requirements with a reason.
- Validate loops have no limit.
- One feature owner takes a Product task from a chat to production, in one task and one session; there are no
  Builders (Nov 14). Engineering work no feature owns goes, through the Engineering Manager, to the owner of its area.
- No migration and no rollback.
- A stage with no requirements moves tasks on by itself when the next stage has room.
- Engineering ends at release. Product's Validate checks production.
- The default Review stage uses two models. The reviewer files the second review as a check-model subtask.
- No chats, emails or pushes to people about a task.
- An ask is a requirement with a person on it. @ in the conversation only marks unread. Work for a person is a sub-task.

### Words

- "Task" everywhere. Pipelines, not boards.
- No Blocked. Waiting is always named: a requirement, a person, another task, or room.
- Snooze is the only hold. It needs a reason. It ends on a date, or has no end ("on ice").

### Build decisions

- **All tasks** sit in a pipeline, in this build. General is a visible pipeline with a generic manager.
- **General:** when the bot says done, it is Done. It can be reopened with a note.
- **Never stuck:** a bot gets 3 tries (15 min, 15 min, 50 min; 2 min for the first after a run that was cut off),
  then its manager bot; then the ladder rests and the quiet-task rule tells the manager.
- **One ask:** everything you are asked is the task. You see the page itself, then Approve, or Send back with notes. The
  notes go to the bot.
- **Person approvals** on Product: the Spec, the QA report and the Release report. The rest is bots and the decision
  model. There is no pitch: a feature starts as a chat, and its first approval is the Spec (Nov 16).
- **Stage approvals name a document** (Nov 16). A stage that asks a person to approve names the task document it
  approves (a page requirement with `settings.document`: Spec, QA report, Release report, Invoice, Proposal,
  Results or Checked in production, `task_requirements.APPROVED`) and who approves it: any person (people only) or one person. The stage editor (✎) sets
  both. The bot writing that document while the task is in the stage is the submission (`task_pages.submit`, from
  `POST /api/v2/tasks/{id}/files` with `document`); no bot asks for an OK, and a bot adding an OK on such a stage is
  refused. The decision model checks the submitted version: a first fail goes back to the bot without telling anyone, a
  second fail in the same visit goes to the person with the reason beside it, and a version it could not check reaches
  the person marked "not checked". The Spec's check only advises. The approval's key is (task, visit, stage, document,
  version): Flow shows the document at that version on its tab, with one muted version line, and Approve sends the
  visit and `<file id>@<version>` back. A stale key is refused (`task_changed`, with the version submitted now in
  `approval`), and Flow shows the newer version. A newer version moves the approval to it. Not approving is a reply in
  the chat; approving moves the task on.
- **Person OK setting:** "People only" or "People, with exceptions". Default: with exceptions. Product's person
  approvals are people only. An approval that is people only and names nobody is any person's: Product's Spec and
  Release report.
- **Push past** is a switch on each requirement.
- Tasks leave Product's **Ideas** by hand, except a request a person files: it goes into Spec when Spec has room. Later
  is dropped. Growth's and Success's Ideas hold every idea, a person's too, until someone starts it.
- **Many PRs** per task. All must merge.
- **The other existing orgs** are rebuilt from scratch on v3.
- **Build order:** General, then Product, then Engineering.
- **Proof:** a fault-injection rig, plus 10 real tasks from idea to Done on the new Tico Team with zero hand database
  fixes.
- **Task looks:** every state gets one clear look. Title, who it is for, what is asked, the simplest way to do it. Admin
  details second.

## Calls

Close calls that change behavior, C1 to C31. They are part of sign-off A. Text in italics says what was there before.

- **C1.** A bot should give an end date when it snoozes, and its wake says so. It is not required. A bot's snooze with
  no end date shows as "Snoozed", the manager bot is told once, and it is listed on the manager's rail. After 3 snoozes
  by a bot in one stage, the next goes to the manager bot. *Draft 4 refused a bot's snooze with no end date.*
- **C2.** A snoozed task gives up its slot in a stage that has a limit, and so does one that waits only on a person's OK ("Limits").
- **C3.** Push past is off by default for **PR merged** and **In a release** only. It is on for everything else, Checks
  pass included. A task that can't be pushed past can still be moved back or closed.
- **C4.** What counts as progress, and resets the 3 tries: a move, a requirement changing state, a new commit, a new
  page version, an ask, a snooze, a sub-task filed. A comment alone does not count, so a bot can't loop on "working on
  it".
- **C5.** Late has two steps and no extra settings. At the stage's expected time the manager bot gets the task. At 3
  times that, the ladder rests, once. A person's turn (all the task waits on is people's answers to asks; a Connect
  GitHub hold is not one) is never Late, and the bot's clock starts again at the person's last answer.
- **C6.** A task owned by a person is never chased. It shows Late only after its due date.
- **C7.** Asks never expire and tasks never close by themselves. *Today: questions expire after 7 days or go ahead on a
  default answer, and done Engineering tasks close after 24 hours.*
- **C8.** A bot can't close a person's request by itself. It asks "I can't do this because…" and you pick Reassign or
  Close. Bots can close tasks that bots asked for.
- **C9.** General has one stage, To do. *No Doing, Waiting or Review stages. The line under the title says what is going
  on.*
- **C10.** Ideas: nobody is told when Spec has room, in any pipeline. A request a person filed that still waits in
  Ideas takes the room by itself, oldest first; a bot's own idea waits for a person to pick it. *Changed Nov 12: the
  "has room" wake did no work (none of 33 was ever delivered, no idea moved because of one) and one idea's re-fired
  every few minutes.*
- **C11.** Held actions (send this email, spend, publish) become an Approve ask on the task the bot is working on. If
  there is no task, Tico files one for you in General.
- **C12.** Stale checks are dropped. *Today a model flags tasks that look forgotten and lists them for you.* Bot tasks
  are covered by the never-stuck check. Your own old to-dos just stay in your list.
- **C13.** A repo with no checks: if GitHub shows no checks for a commit 10 minutes after the push, Checks pass is met
  and says "No checks on this repo". Only for a draft, or a repo none of whose saved PRs ever ran a check: on a repo
  with CI a ready PR waits for its checks (an Actions outage or a skipped run is never a pass), and 2 hours with none
  is the owner's turn.
- **C14.** Forward moves go one stage at a time. Dragging a task several stages ahead counts as pushing past every stage
  skipped, with one reason.
- **C15.** A bot can have one requirement of its own open on a task at a time. If it asks again, the new one replaces the
  old one. People have no such limit. An OK bound to an action (send this email, mail this letter) is not counted: it
  is keyed by what it approves, so each send on a task keeps its own OK and a later ask never replaces it.
- **C16.** The person at the top of the ladder is the first person above the pipeline's manager bot on the org chart. If
  there is none, the org owner. A task with a Human ([tasks.md](../tasks.md#the-tasks-human)) falls to them first.
- **C17.** From phase 1 until the proof, the other orgs stay on today's release. A fix for them comes from a branch off
  that release. Only the new Tico Team takes new releases.
- **C18.** Version: phases 1 to 3 go to the new Tico Team as 0.6.0 pre-releases. 0.6.0 itself is published when
  Engineering works end to end. The 10 proof tasks ship in 0.6.x.
- **C19.** A task waits only on requirements. An ask is a requirement with a person on it: set on the stage, or added to
  one task. Work a person has to go and do is a sub-task. *Draft 6 kept a bot's question in its own table.*
- **C20.** Sub-tasks nest at most 5 deep.
- **C22.** Your OK is for the commits you were shown. A rebase or conflict fix after it that changes the same lines
  keeps it. The bot that wrote the latest Review page reads changes to other lines. Its OK keeps yours, and the PR
  merges without asking you again. Its send back goes to the Builder, and you are asked again after the next read.
  The task shows "Changed after OK" and the manager bot is told. *Until Nov 12 a commit after your OK merged once green; on Nov 12
  any commit after it asked you again, which at ~100 PRs a day meant an ask for every rebase.*
- **C23.** A row is the title and a mark, on one line. In your own list it is the ask, as a sentence. Who has it and what
  is missing is on the task. *Draft 5 put those words on the row.*
- **C24.** A person never has to type a reason to snooze, move back, reassign or close. Moving on with something missing
  (a push past) still needs one, and sending an ask back still needs notes. A bot always gives a reason. Close has no
  dialog: it closes, with Undo for 10 seconds.
- **C25.** A pipeline manager doesn't own tasks. It is woken about a task its owner couldn't move, a Late one, or one
  with no owner. It decides: reassign, move back, move on, snooze or close. The owner stays the owner. *Confirmed Nov
  9.*
- **C26.** Done and Closed are not columns on a pipeline. They are behind the archive button, with Snoozed.
- **C27.** A stage header is a name and a number: how many tasks, or "2/3" where the stage has a limit. Against a limit
  the number counts tasks that hold a slot, and beside it a person mark and a number count those waiting only on a
  person's OK; the words ("15 wait on a person") are its label and tooltip only, on desktop and phone. The
  setting is called Limit. The word "pool" is gone.
- **C28.** Needs you is the one place to check, and it holds only what someone asks of you: every requirement with your
  name. An @ alone is not on it (Nov 19): a person's @ of you is in the threads sidebar's Needs attention until you
  reply. A task you own with nothing asked of you is in Tasks. A bot's @ on a task only marks it unread: a bot that
  needs you has to use a requirement. A task never waits on an @, and an @ sends no push. A bot that @s someone and stops
  is still the one whose turn it is. *Today an @ on a task reaches no list. Channels are not changed.*
- **C29.** Only a requirement a person can meet with a button can name a person: OK from someone, a page's OK. "Checks pass" can't. If a person has to make it pass, that is a sub-task.
- **C30.** Anyone working a task can add an OK from someone to it. It lasts for this stage.
- **C31.** In a release on a repo that publishes no releases is met once the PR merged, with the line "This repo
  doesn't publish releases". A repo publishes releases once Tico has read one published release of it (a pre-release
  counts). Until Tico has read the repo's releases once, the line is "Reading releases". *Before, the task waited in
  Release for a release that never came.*

## A. The state machine

Sign-off A: the parts, the moves, whose turn it is, the never-stuck check, asks, and the three default pipelines.

## The model: six parts

Customers and manager bots can edit all of it, except how releases are cut: In a release's Release with, Pre-releases
count and release repo are a person's to change, and a bot's change is refused whatever the route (API, CLI, MCP).
General, Product, Engineering and the Growth and Success pipelines are presets built from these parts.

- **Pipeline.** A line of stages with a name, a one-line goal and a manager bot. It ends in two built-in stages: Done and
  Closed.
- **Stage.** A name, a one-line purpose, an owner, what a task needs to leave, an expected time, and an optional limit on
  how many tasks it holds.
- **Requirement.** One thing a task needs before it leaves a stage. Six kinds, listed below. It is set on the stage, or
  added to one task. It can name a person.
- **Task.** One pipeline, one stage, one owner right now. Stored: stage, owner, version. Everything else you see is
  worked out from those and the metadata.
- **Link.** Ties a task to a task it filed: a sub-task, or a handoff into another pipeline. A task waits on the tasks it
  filed. At most 5 deep.
- **Snooze.** Sets a task aside until a date, or with no end. Nothing chases it and it holds no slot.

An ask is not a seventh part. It is a requirement with a person on it. See [Reaching a person](#reaching-a-person).

### Stage settings

- **Owner:** a bot, a person, "the task's assignee", or "One of several" (`pick`, backend/stage_pick.py). If nobody is
  set, the pipeline's manager bot is woken to pick one (C25).
- **One of several:** the bots to pick from, Take turns or Random, an optional Limit each, Prefer (Anyone, New to the
  task, or A different model family from everyone who worked on it) and whether the pick becomes the task's owner (its
  assignee, for life: Build) or holds only this stage (`visits.holder_id`: Review). Tico picks the moment a task enters,
  with no model call: a task it had someone here before goes back to them (a task with an owner keeps it); Prefer
  narrows the list unless that leaves nobody free; then turns or random among bots that can run and are under their
  limit (each bot on the list reads a private task while it holds the stage: "Private tasks" below). Everyone full: the task waits ("Waiting for a free Reviewer", state `pick`) and nobody is
  woken; the never-stuck pass gives the oldest to the first with room. Nobody can take it (all paused, or none can read
  a private task): the manager is woken, told why. Each pick is a line on the task's history ("Build given to Builder 2
  · took turns"). Only a person changes who a stage picks from; the record shows `pick_next`, who gets the next task.
  A task filed into a pipeline whose first stage picks may name no owner (the stage picks one); naming one always wins.
- **To leave:** a list of requirements. All must be met.
- **Tasks leave by hand:** a switch. Tasks stay until someone moves them. Nothing there is late and nobody is chased.
- **A person's request moves on:** a switch on a by-hand stage only (`requests_move_on`), off by default. When on, a
  person's request (a task a person filed that no task filed) on its first visit is not held: it moves on when the
  next stage has room, oldest first. Moved back into the stage, or snoozed there, it is held like any other. On in
  Product's Ideas; off in Growth's and Success's Ideas and in Monitoring's Watching. With the switch on, work handed
  over to the owner bot (filed there by a bot that is not the owner as its requester, or moved back there by such a
  bot) is the owner's turn: "Move to Spec, or snooze to park it", due 24 hours after it entered, with one wake. Past
  that it is Late and the manager bot is woken once. An owner's own ideas rest as before.
- **On enter:** nothing, "file a linked task into pipeline X", or "queue the PRs to merge". There are no other actions. A
  stage that files a task always has Linked work done among its requirements.
- **Expected time** and **limit** (how many tasks fit). The first stage of a pipeline has no limit.

That gives three kinds of stage, and no others:

- **Work stage:** has requirements. It is the owner's turn until they are met.
- **No requirements:** Tasks move on by themselves when the next stage has room. Engineering's On deck is one.
- **By hand:** tasks leave when someone moves them. Product's Ideas is one; a person's request is not held there.
  Growth's Ideas and Monitoring's Watching are others, where it is.

### The six requirement kinds

1. **Owner says done.** The owner marks the stage's work finished. This is the whole of General.
2. **OK from someone.** A named person or bot approves. Nothing is attached.
3. **A page.** The owner attaches a page or file. Jev checks it against a "what to check" line. Optionally a person then
   OKs it. The page is HTML: the attach refuses a version that is not (Markdown or plain text) with what to do instead,
   and a person's OK view draws a non-HTML page attached before that as Markdown.
4. **An item.** Something a source keeps, read through one of the source's conditions. A source is a Python module
   under `backend/item_sources/`; GitHub is the only one with conditions (Jira's issues are linked and their state read,
   with no conditions yet, see "Outside items" below). Its item is a PR and its conditions are:
   - **PR open.** At least one PR is linked, and none was closed without merging.
   - **Checks pass.** Every linked PR is green at its newest commit: no check failed, none is running, and at least
     one ran and passed. Tico's own check runs are ignored.
   - **PR merged.** Every linked PR has merged.
   - **In a release.** Every merge is inside a published release.
5. **Linked work done.** At least one task filed during this visit ended Done, and none are open or deleted. Closed
   tasks stop blocking when another task filed during the same visit ends Done.
6. **Related input.** Jev picks the one or two bots the work touches, and each comments once. It never blocks. See
   [Related input](#related-input).

Two switches sit on a requirement:

- **Can be pushed past.** On, except for PR merged and In a release (C3).
- **Who OKs**, on kinds 2 and 3: "People only" or "People, with exceptions". With exceptions is the default: a bot may
  push past with a reason, and the task shows it. People only: no bot OKs it or pushes past it, and only a person
  changes or removes it in the settings. A people-only page that names nobody waits on a person after Jev passes it.

Requirements are guard rails that keep work visible. They are not security. No refusal about a task counts toward
quarantining a bot: not a requirement, room, a cap or a rule.

### Related input

Kind `related_input` (backend/related_input.py), on the Product pipeline's Related input stage. Before a person OKs a
spec, the bots whose areas the work touches get one say.

- **Jev scores.** A Heartbeat step (`related_input`) sends Jev the task's pitch (title and first message) and the
  newest page attached in an earlier stage (the Spec page), with the company's bots: each one's name, job line and
  team. Jev scores each bot from 0 to 1 (`questions/related-bots.json`, one question per bot). No transaction is held
  during the call. Jev writes no prose, so each score's "why" is the bot's own job line.
- **Never asked.** The task's owner and assignee, the pipeline's manager, and every bot that holds one of the
  pipeline's stages (a fixed owner, or a bot on a pick list, such as the reviewers and QA). Also never asked: the
  General Manager and the helper bots, a bot that cannot run, and a bot that could not post its comment: one that
  cannot read the task (the engine never adds a reader) or one the task's bot refuses through its Write access or
  contact setting. Any bot that reads a task may comment on it, as a person may, so a pick needs no party role.
- **Picks.** The top `most` bots (default 2) that score at or over `bar` (default 0.8). Each pick gets one wake on the
  task: read the spec page and comment once, from its area, with `tico task comment`. Its run needs no turn (the
  wake key's kind is `related`), and nobody chases it.
- **Met** when every pick has commented, when no bot clears the bar or there is no bot to ask, when Jev failed three
  times, or when `wait_hours` (default 4) have passed since the task entered the stage. Until then it waits on Jev
  ("Jev is picking related bots", then "Waiting on comments from Billing Owner"), so no ladder runs and nobody is
  Late. A timeout is met, never stuck: "4 hours passed: no comment from Mail Owner".
- **On the task.** One history line for the picks and their scores ("Related input: Jev picked Billing Owner (0.91)"),
  or why nobody was asked, and one for how it ended. Every score, why and pick is kept in `related_inputs`, one row
  per visit and requirement.
- **Settings** on the requirement: `bar` (over 0, up to 1), `most` (1 to 5) and `wait_hours` (over 0, up to 72). A
  value out of range is refused at save.

### A requirement on one task

- Anyone working a task can add a requirement to it: an OK from a named person (C30).
- It lasts for this visit to the stage. The task doesn't leave until it is answered, withdrawn, or pushed past with a
  reason.
- A bot can have one open per task, beside any OKs bound to an action. Asking again replaces it (C15).
- Moving back or closing ends it. Nothing is left behind.
- A bot takes back only an ask a bot added. An ask a person added is taken back by a person.
- Only an OK can name a person (C29). To hand it to someone else, put their name on it.

The three ways to reach a person are in [Reaching a person](#reaching-a-person).

## How a task moves

The life of a task, in a line: First stage → Stage → Last stage → Done.

- Forward arrows: the engine moves the task when everything is met and there is room, or someone pushes past with a
  reason.
- **Back:** from a later stage to an earlier one.
- **Reopen, with a note:** from Done back to a stage.
- **Close. Never refused.** From any stage to Closed.

### The moves

- **New.** A task always starts in its pipeline's first stage.
- **Forward.** To the next stage, by the engine, the moment every requirement is met and the next stage has room.
- **Push past.** Forward with requirements unmet. Anyone, with a reason. Refused only by a requirement whose switch is
  off, or by a people-only OK when a bot tries. The task shows "Pushed past" and the manager bot is told.
- **Back.** To any earlier stage. Anyone. A bot gives a reason. Never refused by requirements or room. One exception:
  only a person moves a task back into a stage that queues PRs (Merge); a bot moves it back to the stage before.
- **Done.** Leaving the last stage. Only Done counts as output.
- **Close.** From any stage. A bot gives a reason. Never refused. Its open sub-tasks close with it. "Declined" is Closed
  with a reason.
- **Reopen.** From Done or Closed back to a stage, with a note. A reopen (or a restore from the trash) that would land
  in a stage that queues PRs lands in the stage before it, so a new commit is reviewed before it is queued.
- **Reassign.** Changes the owner for this visit. It goes through the same function, so it is logged and resets the
  tries. The old owner's queued wakes for the task are dropped.
- **Move to another pipeline.** The same task, with its number, label and history, into a stage of another pipeline
  (`task_engine.refile`). A re-file, not a forward move: the old stage's requirements don't hold it, and entering the
  new stage is a normal entry (owner rule, stage rules, room, wake). The old visit ends `moved`, its asks withdrawn; a
  snooze ends; the old stage's on-enter action is undone as by Back, but sub-tasks stay open where they are. A Done or
  Closed task lands in the new pipeline's Done or Closed. Anyone who reads the task; a bot with a reason, never past a
  people-only OK. Refused while a bot's turn on the task runs (`task_turn_running`) and into a full stage.

Every move records who, when and why, and raises the task's version. A move made against an old version is refused with
"changed since you looked". A bot's command always carries the version from its wake.

### Side stages

A side stage sits after its nearest main work stage in pipeline order. It can have a name, purpose, owner and stage
rules. It comes after at least one main work stage and always leaves by hand. Automatic moves, wakes and the flow
map follow the main path.

People and bots can move a task into a side stage, back onto the main path, or from a side stage to Done without a
reason or a requirement to meet. Entry rules and the destination's task limit still apply. Only a person can move a
task from a side stage into a stage that queues its PRs. `next` from a side stage goes to the next main stage, or to
Done at the end. Close and Reopen keep their usual rules, and reopening returns to the side stage where the task ended.

Visits record these plain moves as `side`. The task read's `came_from` names its latest main work stage in the same
pipeline while it is in a side stage.

### Entering a stage

- The owner becomes the stage's owner.
- The on-enter action happens once per visit. Coming back to the stage later is a new visit.
- The owner is woken once, with what is missing.
- Moving back or closing undoes the action: PRs leave the merge queue, and tasks filed in that visit close with a note. A
  filed task whose PR already merged finishes its release. An undo is the visit's, not the PR's: it is skipped while an
  open visit (the task's next one, or another task's) still wants the same PR queued at the same head, and one that
  took such a PR out anyway queues it again.

### What a requirement can say

- **Met.**
- **Waiting** on something named: a person, Jev, GitHub, or another task.
- **Not met.** It is the owner's turn, and the requirement says what is missing.

There is no "out of date", "missing", "not passed" or "skipped". A review of an old commit is Waiting on the reviewer,
with "new commits since the review".

### Whose turn it is: the ten states of an open task

`waiting_on(task)` returns what the task waits on. The first match in this order is the headline you see. The task page
lists all of them. The list is never empty for an open task. Each state's look is in [S1](#s1-a-task-row-every-look).

1. **Snoozed**
   - Starts: Someone snoozes it.
   - Ends: The date arrives, or anyone unsnoozes it (a bot's own idea, filed snoozed: only a person).
   - If not: With no end date it stays until a person brings it back.
2. **A person's turn**
   - Starts: A requirement names them, a bot asks them, the task is theirs, or the ladder reached them.
   - Ends: They answer, or the task leaves the stage.
   - If not: It stays in their Needs you. No reminders.
3. **Paused**
   - Starts: It is a bot's turn but the bot can't run: paused, out of credit, over a limit, quarantined, no computer, or
     a login to reconnect.
   - Ends: The bot can run again.
   - If not: One row per cause in the list of the person who can fix it.
4. **A bot's turn**
   - Starts: A requirement is not met and the owner is a bot.
   - Ends: The bot meets it, asks, files a task, snoozes or closes.
   - If not: 3 tries, then the manager bot, then the ladder rests (the quiet-task rule tells the manager).
5. **Jev is checking**
   - Starts: A page is attached.
   - Asked: a check Tico ships (Spec, Review, QA, Checked in production) is asked as its named parts, each a yes/no
     in the same one call (backend/pipeline_judges.py `CHECKS`); a company's own rubric is asked whole. Each check has
     its bar in `BARS` (0.5 yes, 0.2 no; QA 0.75 yes, raised by the eval; set from 826 real verdicts and what happened next:
     evals/pipeline_judge/results/bars-2026-11-13.md). Met when every part is yes, not met when any part is no,
     unsure when none is no and one can't be told. The reason names the missing parts in one line each, at most
     three ("No out-of-scope list found."), never a probability or the check's wording. (Changed Nov 13, task #351.)
   - Ends: Pass: met, or on to the person's OK. Fail: the bot's turn, with the missing parts; on a page a person OKs
     anyway, a second fail in the same visit is that person's to decide. A Spec a person approves is never sent back
     by the decision model: a missing part goes to the person beside the approval, as "Decision model: <part>". Unsure: on a page a person OKs anyway (people only, or naming
     someone), that person at once, every version, shown nothing from Jev; after a send back, the person who sent it
     back, with their words. On a stage a bot holds: the pipeline's manager bot looks once per page version, with the
     part Jev can't tell, and OKs it or sends it back and says why; the page's bot is not sent back to rewrite it.
     The manager decides even on a stage it holds itself. With no manager bot, a person as before. A bot never asks its own OK of a
     page a person OKs already, nor a yes/no that shows that page: it attaches the new version. A plain yes/no
     question is still its to ask.
   - If not: Retried 3 times. Then, on a stage a bot holds and a page that is not people only, the pipeline's
     manager bot decides it and says why, even on a stage it holds; with no manager bot, a person once
     per visit, then the owner. A page a person OKs anyway goes to that person, shown nothing from Jev.
6. **GitHub**
   - Starts: Checks are running, the PR is in the merge queue, or it merged and waits for a release.
   - Ends: The metadata arrives, by webhook or by the 5-minute read.
   - If not: After 2 hours of checks it is the bot's turn; a drop from the merge queue is the owner's turn at once. A
     release that never comes turns Late.
7. **Other tasks**
   - Starts: It filed tasks that are still open; or, in a stage that queues PRs (Merge), it goes after a task that
     has not merged yet ("After #40: waits for it to merge", see Merge).
   - Ends: The last one ends. The owner is woken once. For an order: the earlier task merges (or is Done) and the PRs
     are queued; or it is closed without merging, and it is the owner's turn once ("#40 closed without merging. Queue
     it, or send it back.").
   - If not: Those tasks have their own turn. A task waiting on its order wakes nobody and is not Late.
8. **A full stage**
   - Starts: Everything is met and the next stage is full.
   - Ends: A slot frees. Oldest first.
   - If not: It turns Late and the manager bot gets it.
9. **Waiting for a free bot**
   - Starts: It entered a stage whose owner is One of several, and every bot it picks from is at its Limit each.
   - Ends: One has room: the oldest waiting task is picked first, and its bot woken.
   - If not: Nobody is chased; Late still applies.
10. **A by-hand stage**
   - Starts: It enters a stage that tasks leave by hand. Where a person's request moves on, one on its first visit is
     not held: it is in state 8.
   - Ends: Someone picks it, closes it or snoozes it.
   - If not: Nothing. It is a place to wait. (Product's Ideas is not by hand: an idea moves on to Spec by itself,
     after Jev's duplicate check, see docs/tasks.md.)

Two labels can sit on top of any state: **Late** and **Pushed past**. Two states end a task: **Done** and **Closed**.

### How a bot's run may end

A bot working a task must leave it in one of six states. Its wake says so.

1. Done with the stage: the requirement is met, or it moved the task.
2. Asking a person, on the task.
3. Waiting on a task it filed.
4. Snoozed, with a reason, and to a date wherever there is one: only to look again on a date. For example "couldn't
   reproduce, try again Nov 16".
5. Closed, with a reason.
6. Blocked: depends_on #N (`tico task relate <id> depends_on <task>`), when another task must end first. It has
   no date and its owner is woken when that task ends. Never snooze for a block (the stage rule Snooze only for a real
   wait refuses it).

While a task's own pull request checks run, its turn is GitHub's: that is a way to stop, and their end wakes the owner.

Anything else is "stopped early", and the never-stuck check wakes it again.

### What a stage is handed

A stage's own requirements never name what earlier stages made: the Spec, the PR, each Review page, what the Builder
said it fixed. So every wake, and the task's own read (`inputs`, which `tico task show` prints under "From earlier
stages"), lists them, oldest first (backend/stage_inputs.py):

- **The PR**: each PR linked to the task, open with its newest commit, merged with its commit, or closed. A Product task
  also lists the PRs of the Engineering tasks it filed, with the release each went out in.
- **Each earlier page**: the page that counted for each page requirement on an earlier visit (the newest per
  requirement), as `page:<file id>@<version>` with the command that reads it. The filed Engineering task also gets the
  Product task's pages from before it was filed (the Spec as it was OK'd).
- **What was said at done**: an owner's note when it said an earlier stage's work was done ("fixed ...; declined ...").
- **What is missing**: a page requirement of a stage before this one that no visit ever attached, because the task was
  pushed past it or moved over it.
- **What a person said when they signed off filed work**: a Product task (In development while it waits, then
  Validate) gets each OK or send back a person gave on a page or OK of an Engineering task it filed (QA's
  pre-release sign-off): who, which, the notes and the page version. Left out for a filed task the reader may not name.

A wake that hands on a PR says to read its checks with `gh pr checks <its link>`. When every PR the task and the work it
filed link is in one repository, the bot's turn also has `GH_REPO` set to it, so a bare `gh pr checks 46` reads that
repository even from a copy with no git remote (stage_inputs.repository, runner-rs/src/env.rs).

A task in a pipeline whose stages read PRs (Engineering) is in the repositories In use from the moment it is filed,
before any PR: its wake opens with `Repository: owner/name` and `tico task worktree add owner/name`, `tico task show`
names it, and with one, `GH_REPO` is set to it. With several, the wake lists them and the bot works in the one the spec
names (stage_inputs.repositories).

A bot that lacks an input it needs does not say done. It moves the task back to the stage the input comes from, saying
what is missing; its wake says so. A done said with nothing changed is not refused (a review with no findings needs no
change), but the note is handed on, so the next stage and the person who OKs QA read what was and was not done.

### Limits

- A stage's limit counts its open tasks that are not snoozed and hold a slot. A task holds a slot unless every wait on
  it is a person's OK it asks a person for, and none is a bot's, Jev's, GitHub's or a sub-task's: bots
  keep working while people review. A task the ladder brought to a person, or whose bot is Paused, still holds its slot:
  that is stuck work, not review. Every check for room counts this way: requests leaving Ideas,
  filling free slots, moves by hand and by the engine, "No room in …" and the Heartbeat's over-the-limit check.
  *Changed Nov 12: on Tico Team Spec sat at 20 of 20 with 15 waiting on one person's OK, and nothing left Ideas.*
- When a task's turn becomes a person's, its slot frees at once, as when a task leaves: the oldest waiting task takes
  it. Nobody is told about the room. When a person's answer gives the turn back to a bot
  (sent back, or answered with work left), the task keeps its place: the stage may sit over its limit, as after Back.
- Only forward moves need room. Back, Close and Reopen never wait.
- When a slot frees, the oldest waiting task takes it. This runs right after the move that freed the slot, and again on
  the Heartbeat.
- Pushing past does not push past the limit. Change the limit instead.
- Back, Reopen and unsnooze can leave a stage one over its limit. It takes no new task until it is under again.
- A sub-task needs no room in the stage of the task that filed it, while that task is still on the visit that filed it:
  the filer waits on it, so it comes in on the filer's slot. The stage may then sit over its limit, as after Back.
  *Changed Nov 12: on Tico Team a Build task waited on a fix it had filed, and the fix waited for room in that full
  Build.*

### Snooze

- Anyone who can work the task can snooze it. A bot gives a reason. A person doesn't have to (C24).
- It ends on its date, or when anyone unsnoozes it. The owner is then woken once.
- While snoozed: no wakes, no Late, no slot held, and it leaves Needs you.
- A bot's snooze never hides a person's open ask: that ask stays in the person's Needs you, and their answer ends the
  snooze. Only the person's own snooze takes it out of their list.
- A person can also snooze one ask for themselves (Flow's Snooze on an ask, backend/ask_snoozes.py): it leaves their
  own Needs you and count until the time they pick (1 hour, 1 day, 1 week, 1 month or a time of their own, at most a
  year), or until the ask is asked again. Everyone else's list, the task and its bot are unchanged; the bot is not told.
- Finishing or closing a task ends its snooze.
- A bot's snooze on a task that waits on tasks it filed ends when the last of them ends, and the owner is woken with how
  each ended. A person's snooze stays. *Changed Nov 11: Initech's SEO Owner slept a week after the person said the
  change was applied.*
- A bot's snooze in a stage that checks the release production runs (Product's Validate), on a task whose filed work is
  in the repository the company's Tico server runs, ends when that server starts on another release, and the owner is
  woken with it. A bot waits for a release, never for a guessed release time. *Added Nov 11: Tico Team's Product Owner
  slept three tasks to "the next release window" while the release they waited for landed minutes later.*
- A bot's snooze in any stage ends, and whoever has the turn is woken once with the reason, when the checks on the
  newest commit of the task's PR finish, the PR starts or stops conflicting, or a PR the reason names ("waiting for
  #3417 to land") merges or closes. Merge adds its own news (see Engineering, Merge). A person's snooze stays, and the
  bot's snooze count is as it was. *Added Nov 12: Tico Team's Builder slept in Fixes 1 to 08:00 waiting for #3417,
  which merged at 03:01.*
- A bot's snooze on its own task never holds it once everything the stage asks is met: the checks it waited for
  passing end the snooze even when that leaves nobody a turn, and the task moves on at once when the next stage has
  room (else it waits for room like any other). The never-stuck check does the same on each pass for such a snooze no
  event ended. A person's snooze, or another bot's, stays. A move the engine refuses is in the server log with its
  rule, the task and the stage. *Added Nov 13: Tico Team's Build tasks sat for hours snoozed "until checks pass", their
  checks passed and Review 1 unlimited, showing "Moving to Review 1", until a person moved them.*
- Run now on a snoozed task ends the snooze, in the name of whoever pressed it, as Wake does.
- A bot should give an end date. With none, the task shows "Snoozed" and the manager bot is told once (C1). A bot's own
  idea in Product's Ideas is filed snoozed, and only a person ends that snooze (Ideas: say it once).
- After 3 snoozes by a bot in one stage, the next goes to the manager bot. A bot can't put a task off forever.
- To wait for a customer, use `tico task snooze` with `--wait-thread-id`, `--wait-sender`, `--wait-after-seq`
  and `--until`. Choose an outside participant and an outbound message on the thread's customer connection.
  Use the message's global sequence or use `--wait-provider-reply-id` with its saved provider ID. Bots must
  choose a fallback within 30 days. A reply from that sender on the same connection, in the thread or one under
  it, ends the wait as intake saves the reply. The visit starts again at zero tries and the bot whose turn it is
  wakes now. If the reply arrived first, the snooze is refused. Customer waits are exempt from the bot snooze
  count and stage snooze rules. A person can end the wait early with Unsnooze or Run now. A move to another open
  stage ends the wait and makes the task due. The agent can set the wait again if a reply is still needed.

### Handoff: Product to Engineering

- Entering In development files one Engineering task, linked. The owner may file more.
- The filed task has the Product task's title and first message, and a link to the Spec page at the version that was OK'd
  (`page:<file id>@<version>`), then what each person said with their OK, marked as their notes on the spec. Its
  requester is the Product Owner. It starts in On deck.
- After any sign-off, what the person said with their OK is in every wake of the stages that follow, up to the next
  sign-off or a move back: the Release Owner reads the pre-release OK's notes in Merge and Release.
- The Product task leaves when every task filed in this visit ended Done.
- If one ends Closed, the Product task stays and it is its owner's turn, with the reason shown.
- **Not validated:** the owner moves the task back to In development with a reason. That is a new visit, so a new
  Engineering task is filed. No limit.
- **Not yet:** the Validate page must name the version production runs. If that is older than the task's release, Jev
  says "not yet", nothing is filed, and the bot snoozes.
- A deleted task counts as Closed for whatever waits on it.
- Sub-tasks nest at most 5 deep (C20). A sixth level is refused, so a bot can't put off the ladder by filing tasks
  forever.
- A pipeline can't be saved if its handoffs form a loop.

## The never-stuck check

It runs on the Heartbeat, every 10 seconds. It reads the database only and makes no model calls. For every open task it
asks three things: what is missing, who has to supply it, and are they on it?

### The ladder

Every 10 s, for each open task: what is missing, who has it, and are they on it?

1. **The owner bot.** Woken with what is missing. Stops early: woken again after 15 min (2 min when its run was cut
   off: a crash or a limit), then 15 min, then 50 min.
2. After 3 tries with no change: **the manager bot.** Told who tried and what is missing. Same 3 tries. Also gets tasks
   that are Late.
3. After 3 tries with no change: **a person.** One item in Needs you: Try again, Reassign, Move back, Close or Snooze. No
   repeats.

At any of the three steps: if the task changed, the count goes back to zero and the check asks again whose turn it
is. While the owner bot has it, a person's comment that wakes the bot also puts the count back to zero: the run it
starts is the first of a new count, so a bot that says "I'll carry on" and stops is woken again 15 min later. A comment
the Assistant or Agent Ops writes for a person does not.

### What the check does for each kind of wait

- **Everything is met and there is room:** it moves the task.
- **A bot's turn:** if the bot has a live run or a wake queued for this task, nothing. Otherwise the ladder above. A run
  is live only while its computer checks in, every 90 seconds. A run that is held, cut off or lost is over.
- **Paused:** one row per cause shows in the list of the person who can fix it, not one per task. The tasks say "Paused:
  Builder is out of credit". They carry on by themselves when the bot can run.
- **A person's turn:** the task is in their Needs you, once. No reminders.
- **Jev:** a failed check is retried on the same 3 tries, then goes to a person as "Jev couldn't check this".
- **GitHub:** the PR is read again if the last read is over 5 minutes old. Checks still running after 2 hours become the
  owner's turn with the reason; a PR that fell out of the merge queue is the owner's turn at once (Merge).
- **Other tasks:** nothing. Those tasks have their own turn. When the last one ends, the owner is woken once with how
  each ended.
- **Room:** the oldest waiting task takes the next free slot.
- **By-hand stages and Snoozed:** nothing, by design.

### Late

- A task is Late once it has been in a stage longer than the stage's expected time. Snoozed time doesn't count.
- Late is a label on the task. It also hands the task to the manager bot, once per visit, on the same 3 tries.
- A task that waits only on people's answers stays in their lists. The manager bot is woken only when a bot may push
  past one of those asks, and it is not offered a snooze. When every ask is people only, no bot is woken.
- Still there at 3 times the expected time: the ladder rests, once (C5): nobody more is woken for it, and, since the
  manager was told it was Late, a person who reads it is asked what should happen next. If it is already a person's
  turn, nothing changes.
- This is the backstop for work that keeps changing but never finishes, and for waits on the outside world.

### Long work: what 24 hours looks like

There is no "In progress". A task that takes a day still says, at every moment, what is missing and who has it. Three
cases.

#### A bot builds for a day

- One run lasts at most 60 minutes. That is today's limit, a setting on the bot.
- The run ends with new commits and the stage not finished. Something changed, so no try is counted. The bot is woken
  again 15 minutes later.
- A day of building is up to 23 runs, back to back. The row shows a spinner. The task's top line says "Builder working".
- A run that ends with nothing changed is "stopped early", try 1 of 3.
- At 1 day the task is Late and the Eng Manager gets it. At 3 days it is in your list.

#### A task waits a day on a sub-task

- The parent's bot is not running and is not woken. No tries are counted against it.
- The parent's top line names what it waits on, as a link: "Waiting on E-214 Build checkout flow".
- The check does nothing for the parent. E-214 has its own turn and its own check.
- When E-214 ends, the parent's owner is woken once with how it ended.
- The parent's own clock still runs. Past its stage's expected time it is Late, and its manager bot looks once.

#### A task waits a day on you

- It sits in your list. Nothing runs and nothing reminds you.
- Everyone else sees an amber "!" with your picture on the row, and "Waiting on Ana" on the task.
- It is never Late: the bot is not behind. Past a day, your Needs you row says when it was asked ("Since Nov 16").
- Past the stage's expected time the manager bot looks once. It can push past with a reason if the requirement allows
  that. If no bot may push past it (people only), no bot is woken. A bot's snooze never takes it out of your list.
- Once you answer, the bot has the stage's whole expected time again. It is Late only if it then sits that long.

#### Where "what is missing" shows

- **On the row:** a mark only. Pointing at it shows "Builder · missing Checks pass".
- **On the task:** one line under the title, and "To leave Build" at the side with every requirement, met or not.
- **Last change:** the task's header says when it last changed, and History says what changed. That is how you tell slow
  from stuck.
- **In the bot's wake** and in `tico task show`: the same list.

A wait on the outside world, such as a vendor, is a snooze to a date. It is the one wait the check leaves alone.

### Why this can't strand a task

- **It looks at state, not events.** A lost wake, a missed webhook or a crash only delays things until the next pass, 10
  seconds later.
- **Every wait ends somewhere.** A bot's turn ends in the ladder. GitHub and Jev have time limits that turn into a bot's
  turn. A wait on another task follows that task's own turn. Room frees as the tasks ahead move. The ladder ends at a
  person.
- **It is bounded.** One start plus 3 tries for the owner, 3 for the manager: 7 runs at most, then no more until a person
  acts.
- **It only wakes whoever has the turn.** A bot is never woken about a task that waits on a person, GitHub, another task
  or room.
- **Doing it twice is harmless.** Each wake, ask and GitHub call has a key made from the task's visit, so a repeat is a
  no-op.

### Rules that hold at all times

1. Every open task has at least one named wait, and none waits on a task it filed that waits for room in the stage the
   filer holds.
2. Every bot's turn has a live run, a queued wake, a next try time, or has gone up a level.
3. Every person's turn shows exactly once in that person's Needs you. The list is a read, so there is nothing to create
   or withdraw.
4. Stage, owner and status change only through `move()`.
5. An on-enter action happens once per visit, and is undone by moving back or closing.
6. A forward move never overfills a stage.
7. Nothing wakes a bot about a snoozed, Done or Closed task.

The fault rig checks these after every step. The same checker runs hourly in production and shows "tasks waiting on
nothing: 0" on Health.

## Asks

Anything a person is asked is one sentence in their list. Opening it shows the thing itself and the buttons that answer
it. There is no second place to look.

### Reaching a person

Three ways, by weight. A task waits on the last two and never on the first.

**@ in the conversation.** Example: "@Sam the fixture moved."

- From a person, it is in Sam's Needs you until Sam replies. From a bot, the task is only unread for Sam.
- The task doesn't wait. A bot that @s someone and stops still has the turn, and the check wakes it again (C28).
- No push.

**A requirement with your name.** Example: "OK from Ana."

- The task stays in its stage until you answer, or someone pushes past with a reason.
- It is in your list as one sentence, with buttons.
- Set on the stage for every task, or added to one task by a bot or a person.
- It ends with the task's visit to the stage. Nothing to keep in step.

**A sub-task.** Example: "Sign the FCC filing."

- Real work that takes time, might be handed on, and is counted.
- A task in General, owned by you, linked to the task that needs it.
- The parent waits on it through Linked work done.

You don't have to tell the last two apart. Both are one sentence in your list. It can't be
snoozed by itself: snoozing it snoozes the task. Talk about it is the task's conversation.

A bot's question is none of these. It is a ❓ on the bot's chat message: the person's reply in that chat answers it, and nothing is added to the task.

Approvals win over questions: one card per person per task. While a stage's page waits on your approval, a bot's ❓ to
you on that task is refused (the approval is its ask), and your OK or send back clears any ❓ to you there.

### What lands in your list

Five kinds on one screen. Only a requirement added to one task stores anything. The rest are read from what already
exists, so they can't go stale or double up.

In the list they sit under today's three headings. Answer: questions. Review: things to approve. Do: one-tap fixes.
A task you own with nothing asked of you is not in the list: it is in Tasks.

- **Approve:** a stage's requirement names you, or a rule holds an action such as "Send this email?". Buttons: Approve,
  Send back.
- **Answer:** a bot's ❓ in its task's chat. There are no buttons: a reply in the chat answers it.
- **Do:** a bot can't run and you can fix it, such as Connect Google. One per cause, not one per task. It goes when the
  cause goes. A stage reading PRs with no GitHub App installed is one: **Connect GitHub**, for the company's owner,
  links to Tools, GitHub (**Choose a repository** once installed with none In use); its tasks wake nobody until then,
  and each bot is woken once after (docs/github-app.md).
- **Let them see:** a private task waits on someone who can't open it. Buttons: Let them see it, Reassign, Move back,
  Close.

A task the never-stuck ladder could take no further is in no list: it stays with its bot, and the quiet-task rule tells
the pipeline's manager, who asks a person when it needs one.

Stored: a requirement added to one task (a held action, an OK someone added). Read: a stage's OK (from the
stage), Do (from the bot's state), Let them see (from the task's readers).

### The shape of an ask

- **One sentence** that says what you are asked. A bot can't ask without it. For a stage's OK, Tico writes it: "Approve
  the spec for Checkout flow".
- **The thing:** the page, email, PR or picture, shown on the screen. Never only a link.
- **The answer:** buttons, never a status menu.
- **Notes:** optional on Approve, required on Send back. They go to the bot word for word.

### What your answer does

- **Approve:** the requirement is met. If it was the last one, the task moves in the same step. Undo shows for 10 seconds
  and is an ordinary move back.
- **Send back:** the task stays in its stage and it is the bot's turn, with your notes. The next version comes back
  marked with what changed, next to your notes. On a stage's OK, the owner saying done again asks you again: it is back
  in your Needs you.
- **Answer:** the reply in the chat is saved as the answer and clears the ❓. The bot is woken once with it. A paused bot gets it when it can run.
- **Give to…:** the requirement gets the other person's name and moves to their list.
- **If the task moves back or closes,** the ask ends with it and leaves your list.

The screen, in all five kinds, is [S3](#s3-the-ask-screen).

### What this replaces

- **The requirement review queue.** Gone. Those reviews are in Needs you. Its play button stays, as Flow.
- **Question tasks.** Today a bot's question is a separate task that holds the first one. Now it is a ❓ in the bot's chat.
- **Approvals** (send, spend, publish, merge and the rest). Today they are their own objects. Now each is an Approve ask
  on a task (C11).
- **Stuck.** Today a sweep files a question task. Now it is the last step of the ladder: the task itself, in a person's
  list.
- **Reconnect, repo and settings asks.** Now one row per cause, read from the bot's state.
- **Stale checks.** Dropped (C12).
- **The Goals page list.** It links to the tasks.

Two things stay outside on purpose. **Confirm buttons in Assistant chat** are chat formatting. **Unread chat threads**
are chat, not asks. The setup checklist is onboarding and is not touched.

## Default pipelines

Eleven presets built from the same parts, in four factories. Customers and manager bots can change any of it.

| Factory | Pipelines |
|---|---|
| Product | Product |
| Growth | Growth Experiments, Growth Monitoring; Lead → Opportunity |
| Success | Customer; Success Experiments, Success Monitoring |
| Corporate | Legal; Finance (both private) |
| none | General |

A new org gets General at its first start, and the rest once its setup finishes (Product only if it builds software). A company set up before a preset existed gets it at its next start. A preset the company retired or
renamed is never made again. Legal and Finance are made only once their owner bot exists (the Legal Owner, the
Finance Owner), the moment it is made; never with the General Manager standing in. Growth and Success
Monitoring are made only where their factory's Experiments pipeline is.

A preset the company has keeps its stages and requirements on an upgrade, but its wording nobody edited follows the
preset: at each start, a goal, stage purpose, requirement line or check line that still reads a text the preset shipped
with before moves to the current text. Anything else was edited and stays. A purpose follows only where the stage keeps
the preset's requirements and every stage of the preset is there. The move is logged as Tico's save of the pipeline
(the pipeline's Log shows it), and raises its settings version unless nobody has changed it yet. **When you change a preset line, add the text it had
to `EARLIER`** (`backend/pipeline_settings.py`, or `backend/pipeline_presets_more.py` for the presets there), then run
`python -m backend.tests.test_preset_texts --write`; `backend/tests/test_preset_texts.py` fails until both are done.

Product replaced the Product and Engineering pair on Nov 14 (0.6). A company set up before keeps the pair, so its open
tasks finish where they are, and the pair's upkeep goes on (`pipeline_settings.LEGACY`); a start never makes Product
beside it, since the old Product holds the id `product`. A person (the company's owner or an Admin) adds Product by
hand, under another id, name and prefix while the old ones are in use:

    POST /api/v2/pipelines/features/from-preset   {"preset": "features", "name": "Features", "prefix": "F"}

It is made as a new company's would be (its roles' bots, its people OK's person, the Product factory); a stage id the
old Product holds gets a number (`product-spec-2`, `product-validate-2`). Then retire each of the pair once it holds no
open task (`POST /api/v2/pipelines/{id}/retire`, refused while one is open), and rename Features to Product. No code
moves a task between them.

The Product and Engineering presets both use Build → Review → Fixes → QA. Review needs one page covering the newest
commit of every linked PR, with both reviews and their models, and a finished second-review subtask on a different
model. The reviewer files it with
`tico task child <id> --check-model <runtime>:<model> --role review --title "Review: second opinion"`
and includes the PRs and full head commits in `--body`. In Fixes, the owner addresses both reviews and says done.
The task moves on when the checks pass.

Existing pipelines keep their Review and Fixes stages on startup, including Review 2 and Fixes 2 where present.
Single-review pages keep their rubric.

One stage is added on an upgrade, once (`pipeline_settings._add_qa`): an Engineering pipeline made before QA had a stage
of its own gets QA after Fixes 2, owned by Fixes 2's owner, with Checks pass and the page a person OKs moved there from
Fixes 2 as it was set (its person, its switches, its line and check). Fixes 2 gets Owner says done. When the pipeline
still has its second review stage and Fixes 2 is left with only Checks pass and Owner says done, its old shipped
purpose is updated to match. A task waiting on that page in Fixes 2 then waits on the Builder's done. The never-stuck
check wakes the Builder, which attaches the QA page in QA, where the person is asked to OK it again. A pipeline whose QA
stage was removed, that has no Fixes 2 or no page a person OKs on it, or that already has a live stage named QA is left as it is.

### Private pipelines

Every task in a private pipeline is a private task. When a task enters one (filed, filed by a stage, or restored) it is
made private whatever was asked, and its manager and the company's owner go on its reader list. Its requester and owner
read it as any private task's do. Nobody else: not the manager's group, not an Admin. It can't be made public while it is
there. The pipeline itself (name, goal, stages, manager, counts) is seen by everyone, so anyone can file into it.

- Private is set as a pipeline is made (a preset, or a new one), or while it holds no task; never changed on one that
  holds tasks. Only a person turns it off.
- Its manager reads its tasks, so it is never the General Manager (which reads no private task) and never empty. The
  never-stuck ladder stops at the manager.
- Its stages have no limit (a limit counts tasks a reader can't see) and no OK from a person.
- Legal: prefix LG, "Contracts and compliance questions answered before they hold up the business". Intake → Review →
  Rules check. Manager and every stage: the Legal Owner.
- Finance: prefix F, "Money in, money out, and the books that show both". Intake → Doing → Review. Manager and every
  stage: the Finance Owner (Finance Owner). The Finance Owner's own tasks (its checks, digests and routines) are filed here,
  at Review: they are filed already taken in and done, so one done finishes each, as in General.

### General

Where every task goes unless it belongs to another pipeline. Manager: the General Manager bot.

Stages: To do → Done · Closed

- **To do.** Owner: the task's assignee. To leave: Owner says done. No limit. Expected time: 1 day for a bot. For a task
  with no assignee, the General Manager is woken to pick one.
- **Done.** When the bot says done, it is Done. Nobody checks it. You can reopen it with a note.

Roles are found by the seat's template, never by a name: the Product Manager (`product-manager`), the Engineering
Manager (`engineering-manager`), Reviewer 1, Reviewer 2, the QA Owner and the Release Owner (and on the pair, the
Product Owner and the Builder). A software company has all of them live; a role nobody fills goes up to the pipeline's
manager, and a manager nobody fills to the General Manager, until the role's bot appears. The stages a feature owner
holds are the task's assignee's. The person on a people OK that names one is the first person above the pipeline's
manager bot on the org chart, else the org owner. Product is made for a company that answers yes to "Do you build
software?" (a blank answer is yes); an untouched one is retired when it answers no.

### Product

From a spec to something checked in production, in one task. Manager: the Product Manager bot.
Preset `features`, id `product` (`pipeline_settings.PRODUCT`).

Stages: Spec → Related input → OK spec → Build → Review → Fixes → QA → OK release →
Merge → Validate → Done · Closed

The stage ids are those Tico Team's live Product has where it has the stage: `product-spec`,
`product-build`, `product-review-1`, `product-fixes-1`, `product-qa`,
`product-merge`, `product-validate`; and `product-related`, `product-ok-spec`, `product-ok-release`. Each stage's
purpose names the company skill it runs on: write-the-spec, build-and-open-a-pr and hand-off-to-review,
fix-review-findings, make-the-qa-page, validate-in-production.

One feature owner, the task's assignee, holds every stage but Review, QA and Merge, in one task and one session
(`session: task`). A task filed with no assignee waits in Spec for the manager
to pick one; the owner a task leaves an assignee-held stage with becomes its assignee if it has none. After the
pipeline's first stage, a reassign names who holds that visit only: the assignee holds the stages after it, and a stage
rule that refuses the assignee leaves only that stage with nobody (Tico Team, Nov 14: Build fell to the Engineering
Manager). Build's PR open counts a PR that is open, or one linked since the task entered Build: an old PR that merged
before it does not (a task skipped Build on one).

- **Spec.** Owner: the assignee. A feature starts as a chat. To leave: the task's Spec (the visual-spec format) with the
  goal, the scope and acceptance criteria, which the decision model checks. Writing it here submits it. Expected time:
  2 days.
- **Related input.** Owner: the assignee. Jev picks the 1 to 2 bots the feature touches over a bar (0.8 to start), and
  each adds one round of comments to the spec page (`related_input`). It never blocks: with nobody over the bar, or
  after 4 hours, the task moves on. Expected time: 4 hours.
- **OK spec.** Owner: the assignee, who writes the Spec's next version with what the comments changed. To leave: **a
  person approves the Spec**: any person, never a bot. The decision model shows the person what is missing; it never
  sends the Spec back. Expected time: 2 days.
- **Build, Review, Fixes, QA, Merge:** as on Engineering below, with the assignee in the
  Builder's place and the QA Owner holding QA. QA's report is approved by its named person, as on Engineering.
- **OK release.** Owner: the assignee, who writes the Release report: what is shipping, the PRs it merges at their
  newest commits, and what a person should know first. To leave: **a person approves the Release report**: any person,
  never a bot. Then it merges. An OK that names nothing to show (on a company's own stage) shows the task's page from
  the stage before, with that stage on its meta line; with no earlier page it shows the task's title and text (#3784).
  A task Merge sends back to QA keeps the person's QA OK as on Engineering (C22), and the release is asked again.
- **Validate.** Owner: the assignee. To leave: a "Checked in production" page that names the version production runs,
  checked against the release that holds the task's own PRs. No person. Not validated sends the task back to Build,
  in the same task. A bot's snooze here ends when this server runs a new release. Expected time: 1 day.

### Work no feature owns

There are no Builders. Engineering work no Product task holds (main is red, CI, dependencies, a refactor across areas)
goes to the Engineering Manager, who gives it to the owner of that area as a task: a red main to the QA Owner; CI and
dependencies to the Code Hygiene Owner when the company has one, else CI to the Infrastructure Owner and a vulnerable
dependency to the Security Owner; a refactor or a bug in one area to that area's feature owner, as a Product task.
A product bug a test or a deploy exposes goes the same way (templates/catalog/engineering-manager, qa-owner,
release-owner).

### Product and Engineering (before 0.6)

The pair a company set up before 0.6 keeps until its tasks finish. Nothing new is made with it.

#### Product (the pair's)

From an idea to something checked in production. Manager: the Product Manager bot.

Stages: Ideas → Spec → In development → Validate → Done · Closed

- **Ideas.** Owner: Product Owner. Tasks leave by hand. Nobody is told when Spec has room. To park an idea, snooze it with
  no end date. A person's request moves on (the stage's switch is on): it goes into Spec as soon as Spec has room,
  oldest first. Work a bot hands over (Bug Triage files a bug, or moves one back) is the Product Owner's turn, due in
  24 hours; past that it is Late and the Product Manager is woken once.
- **Spec.** Owner: Product Owner. To leave: a Spec page with the goal, the scope (in and out) and acceptance criteria
  someone can check. Jev checks it, then **a person OKs it** (people only: no bot
  OKs it or pushes past it). Limit 2. Expected time: 2 days.
- **In development.** On enter: file an Engineering task. To leave: Linked work done. Owner: Product Owner. Expected
  time: 5 days.
- **Validate.** Owner: Product Owner. To leave: a "Checked in production" page that names the version production runs
  and shows the change working there: what was run or looked at, and what it gave. Jev checks it. No person. Expected time: 1 day.
  The Product Owner takes the page's screenshots itself, in a read-only view of the app
  ([computers.md](../computers.md#let-a-bot-view-the-app-read-only)).

#### Engineering

Build through Merge here is what Product's Build through Merge does, with the Builder's part the feature owner's.

From a task to a published release. Manager: the Engineering Manager bot. Builder is one bot, working each task in its
own thread.

Preset stages: On deck → Build → Review → Fixes → QA → Merge → Release → Done · Closed

- **On deck.** Owner: Builder. No requirements. Tasks move into Build when it has room, oldest first.
- **Build.** Owner: Builder. To leave: PR open, Checks pass. Limit 3. Expected time: 1 day.
- **Review.** Reviewer 1 reviews the newest commit of every linked PR and files a [check-model subtask](../tasks.md#a-sub-task-on-a-check-model)
  to review those same commits on a different model. The stage waits for the subtask to finish and a Review page that
  names both models and the commits they reviewed. Expected time: 2 hours.
- **Fixes.** The Builder addresses both reviews and says done. The stage waits for Checks pass. Expected time: 4 hours.
- **QA.** Owner: Builder. To leave: Checks pass, and the QA report: what changed, before and after screenshots of the
  running app (or the output that proves a change with nothing to see), how it was checked with the checks at the
  newest commit, what both reviewers found on the commit they reviewed, risks, and the PR with its head commit. The
  Builder builds it with its `build-the-page` skill (the QA template: each acceptance criterion with its result and proof); approving it ships the change. Jev checks it against the linked PRs'
  newest commits (any page on a task that links PRs is checked against them; a PR Tico has not read yet is left out),
  then **a person OKs it** (people only). Expected time: 4 hours. Commits pushed after the Review page are reviewed
  before the person is asked: the bot that wrote the newest review reads only those commits against what
  the reviews found and the spec, and OKs them or sends them back as an answer on the page, at the commits it read. A
  send back is the Builder's to fix; its next commits are a new round. After 2 rounds in a visit, or with no other bot
  that reviewed the task, the person is asked anyway and the ask says "not reviewed by a bot". With no commits after
  the Review page the person is asked at once. The person's OK is for the commits it was given at (C22). New commits after
  it that change the same lines (GitHub's diff of each from where it branched off, without line numbers or context,
  as `git patch-id` reads it) keep it. The same reviewer reads the other commits, with the 2 rounds counted from the OK:
  its OK keeps the person's; its send back goes to the Builder, and the person is asked again once the next commits
  were read. Until Tico has read what the new commits change, the page waits on GitHub. A new version of the same page
  published at the commits the person OK'd keeps the OK: nobody is asked again (Nov 13); another page is asked again.
- **Merge.** Owner: Release Owner. On enter: queue the PRs. To leave: PR merged. Can't be pushed past. Expected
  time: 2 hours. If GitHub takes a PR out of the queue, the owner queues it again as it is, pushes its own fix to the
  PR's branch (a rebase, a conflict, a flaky or broken test, a small code fix) and queues it again, or sends it back
  when the fault needs the Builder. A bot queues a new commit only when it changes the same lines as the one the task
  came into Merge with; otherwise it sends the task back to QA, where the last reviewer reads it and the person's OK
  comes back with the task (C22). A person may queue any commit. Besides what ends a bot's snooze in any stage (see
  Snooze), a bot's snooze in Merge ends when GitHub takes the PR out of the queue or merges it, or its base branch
  moves (once per base commit; not while the PR is queued or its checks run, nor when the reason names a PR to wait
  for). Only in Merge: before it, every merge into main would wake the bot. A person's snooze stays. *Added Nov 11: Tico Team's Release Owner slept to guessed clock times, up to 90
  minutes past the merge it waited for.* A task that goes after another one ("after #40": `tico task after 42 40`,
  or `--after` when filing) is not queued while #40 is open and has not merged; see "GitHub", Merge. *Added Nov 12:
  in conflict-storm drill B the order "B6, then B7" lived on a side task, and B7 merged first.*
- **Release.** Owner: Release Owner. To leave: In a release. Can't be pushed past. Expected time: 1 day. The Release
  Owner cuts the release with `tico task release` (see "GitHub", Release).

A review finding on a task's own PR (a bug, a security flaw, a risky path) goes back through the review: blocking on the
Review page, or a send back in QA, so the task goes to Fixes and the Builder fixes it. It never becomes a task for
a person; what only a person decides (the Spec OK, the pre-release OK) is asked on the task itself. Tico refuses
(`review_route`) a bot that gives a person a General task linking or naming a PR an open pipeline task links, with the
task it belongs on. Seen live: Reviewer 2 sent a real bug on its task's PR "privately" through the Engineering Manager
to a person's Needs you.

Also:

- An Engineering task that turns out to need no code is closed with a reason.
- A person can own an Engineering task. It goes through Merge and Release the same way.
- One Builder is a single point of failure. If it can't run, every task it owns says Paused and one row lands in a
  person's list.

### Growth and Success

Each pipeline's manager is its team's Manager: the Growth Manager (`growth-manager`) for Growth Experiments, Growth
Monitoring, Lead and Opportunity, and the Success Manager (`success-manager`) for Customer, Success Experiments and
Success Monitoring. Who manages a pipeline is that pipeline's setting, and a person can point it at any bot. A stage's owner is found by its role's template; until that bot exists, the stage is the manager's, and a manager nobody fills is the General Manager
(the ladder above). Built in `backend/pipeline_presets_more.py`.

- **Growth Experiments** (GX) and **Success Experiments** (SX). Test ideas, keep what works, drop the rest. Every
  stage belongs to the task's assignee, the teammate running that experiment; with nobody assigned, the manager is
  woken to pick one.
  Stages: Ideas → Building → Running → Done · Closed
  - **Ideas.** By hand. One line each: what to try and why. Ideas rest here, a person's too: nothing wakes, chases
    or marks them Late, and a freed slot in Building wakes nobody. The owner starts one of its own when Building has
    room ("No room in Building. It holds 6 a bot is working on: move it again when one leaves." when it has none); a person can start any.
  - **Building.** The real thing, not yet live: the email, the page change, the ad. Limit 6. To leave: a **Go-live
    page** (the thing that goes live, its copy or a picture of it; the metric, the prediction as a number, the cost,
    how long it runs, when to stop early, and the brand check passed or, with no brand card published yet, a plain
    "no brand card yet"). Jev checks it, then **a person OKs it** (people only); when Jev sends back a second version
    in the same visit, the person decides it with Jev's reason. Expected time: 3 days.
  - **Running.** The owner puts it live and snoozes the task to its end date; it wakes then, to measure. To leave: a
    **Results page** (the number before and after with dates and sources, and the result: `result:failed` when it
    did not work; when it worked, `result:watching` if what was kept needs watching or `result:worked-once` if there
    is nothing to watch; worked means the baseline beaten and 60% of the prediction reached). Jev checks it. On
    `result:watching` the owner first files a Monitoring task as its follow-up, and the page names and links it (Jev
    reads the page's text, so it sees the name, not the link). Expected time: 2 days, snoozed time not counted. Done is a measured experiment; Closed is one stopped before.
- **Growth Monitoring** (GM) and **Success Monitoring** (SM). Keep what worked working. One task per kept thing: what
  it is (a link), its KPI and range, and the experiment it came from. Every stage is the assignee's.
  Stages: Watching → Slipping → Done · Closed
  - **Watching.** By hand: the resting stage. Nothing is Late; nobody is woken. The owner moves it to Slipping when its
    number leaves its range.
  - **Slipping.** To leave: Owner says done. Fixed, the owner moves it back to Watching. Not fixable, the owner files
    an idea and closes this task with the reason. Expected time: 3 days.
- **Growth channel packs.** Six Owners work Growth Experiments and Growth Monitoring, one per channel, each its own
  template: `seo-owner`, `content-owner` (blog and social posts), `email-owner`, `cro-owner`, `search-ads-owner`,
  `social-ads-owner`. A pack is the same in every channel:
  - **The stage playbooks** (`playbooks/watch.md`, `find-ideas.md`, `build-a-bet.md`, `run-and-measure.md`,
    `fix-a-slip.md`) are one copy in `templates/catalog/_shared/playbooks/` that all six cards name (`shared_files`).
    AGENTS.md's "Your pipelines" is the same text in all six: change all six together.
  - **The channel playbook** (`knowledge/channel-playbook.md`) is what differs: the channel tag, the KPI, what Watch
    checks, where ideas come from, the upkeep the Owner does by itself and what needs a person. The Owner publishes it at
    setup as the page "<Channel> playbook" (`tico page publish --scope bot`); a person changes it by asking on a task.
  - **Two routines**, both off until setup: **Watch** checks every Watching task with the channel's tag, does the
    allowed upkeep, moves one whose number left its range to Slipping, and logs the KPI. **Find ideas**, Tuesdays,
    files at most 3 ideas and skips one already in the pipeline (open, or tried in the last 90 days). Starting
    setup (or going live) switches on both, and the Owner's onboarding only checks they are on.
  - **One KPI** the Owner makes at setup (`tico kpi create`) and puts under the North Star as an input.
  - **A channel tag** `channel:<key>` on every task, the keys of `backend/company_file/chart_formula.json`.
  - **No Tool to make the change:** after the person's OK the Owner files `Apply: <change>` for the person the playbook
    names: a one-sentence ask first, the exact change below it. Refused, it fixes it and files again; it never says it
    filed what was not. No pack ships an ads or email write Tool.
  - **The brand check** is a sub-task to the Design Owner (`design-owner`), answered `Brand check: passed` or not; a
    video goes from it to the Video Owner, who gets a person's OK (`tico task ask --kind ok`) on a brief with the
    script, the shots and the cost before it makes anything. Writing for another channel's bet is a sub-task to the
    Content Owner.

  | Pack | Tag | KPI | Watch | Needs |
  |---|---|---|---|---|
  | SEO | `channel:seo` | Organic clicks, brand terms left out | Mondays: clicks and rank per page set, AI answers | A public website |
  | Content | `channel:content` | Signups through a page or post | Mondays: each kept series on schedule, signups per series | A place to publish |
  | Email | `channel:email` | Conversions from email | Daily: bounces and complaints; Mondays: conversions per flow | An email platform, a list with consent |
  | CRO | `channel:cro` | Visits that become signups | Daily: conversion per funnel step | Product analytics; a test that can finish in 4 weeks, else known fixes, said so |
  | Search Ads | `channel:search-ads` | Cost per new customer from search ads | Daily: spend, cost per result; Mondays: wasted search terms excluded | An ads account with spend |
  | Social Ads | `channel:social-ads` | Cost per new customer from social ads | Daily: spend, cost per result; Mondays: tired creative swapped | An ads account with spend |

  **Cutoffs.** Email pauses when the complaint rate passes N; ads pause when cost per new customer passes N times its
  30-day average. Rules has no cutoff a template can ship switched off, so none is made: each paid pack's channel
  playbook lists it under "Cutoffs: set these in Rules", and the Owner may suggest it (`tico rules add`) with the
  number a person gives. An Owner may always pause; it never raises a budget outside an approved experiment.
  **Offered at setup** in Marketing, never pre-checked: each pack is `suggest: common`, and Marketing's answer ("How do
  customers find you today?") ranks the ones it names first. The picker cannot see whether a team has a website or a
  connected tool; a pack's required tool shows as set up on hire.
- **Lead** (L). Find leads and bring the qualified ones to Opportunity.
  Stages: Targets (Lead Generation Owner) → Outreach → Replies → Qualified (Outbound Sales Owner) → Done · Closed.
  Each of the first three: Owner says done. **Qualified** files an Opportunity on enter; to leave: Linked work done,
  so a lead is Done when its deal is won.
- **Opportunity** (O). Turn leads into customers. Stages, all the Inbound Sales Owner's: Talking → Proposal →
  Closing → Done (won) · Closed (lost). **Proposal:** a Proposal page, Jev checks it, then **a person OKs it**.
  The others: Owner says done. Nothing is filed on Won.
- **Customer** (C). Customers reach their goals; referrals are the main result. Stages, all the Success Owner's:
  Onboarding → Active → Referral asked → Done (referred) · Closed. Each: Owner says done.

## B. The architecture

Sign-off B: one writer, one read, one check. Events are hints. Metadata is a saved copy of the outside. The database
refuses everything else.

### The picture in words

- **What happens (hints)** comes in from four places: a person or bot acts (app, tico CLI, MCP); a GitHub webhook (may be
  lost, late or doubled); a Jev verdict, a run ending, a release published; the Heartbeat (every 10 s, no matter what).
- **The task engine** holds three parts: `move()`, the only writer of stage, owner, status; the never-stuck check, which
  looks at state, wakes, and calls `move()`; and metadata, saved copies of the outside: PRs, checks, releases, Jev verdicts,
  can a bot run.
- Under the engine, a **database trigger** refuses any other write.
- Out of the engine, **`waiting_on()`** is the only read of "whose turn". It is read by: task row, task page, Needs you,
  pipeline view, factories map, hub and MCP, the wake text, the check itself, Health.

### The five rules

1. **One writer.** `move()` is the only code that changes a task's stage, owner or status. Reassign, Done, Close and
   Reopen are all `move()`.
2. **The database enforces it.** A trigger on the tasks table aborts any change to those three columns unless `move()`
   opened the guard in the same transaction. A lint test also fails the build on any other write.
3. **One read.** Nothing else works out who has the turn. Screens don't have their own logic for it.
4. **Events are hints.** A webhook or a button press only makes the check run sooner. The check works from stored state,
   so a lost event costs at most 10 seconds, or 5 minutes for GitHub metadata.
5. **Effects travel with the move.** The on-enter action and the owner's wake are written in the same transaction as the
   move. Requirements added to the old visit end in it too. Each has a key made from the visit, so running twice does
   nothing.

**Card slots (pilot: Video Production).** In a pipeline named in `card_slot.SWITCHED`, a card's work for an agent runs
one run at a time per visit (`jobs.slot`, `<visit>:<agent>`), for messages in the card's own room whose provenance does
not narrow that room's audience. While a slot's job has a run, its other jobs wait and then run one at a time by the
usual pick. A slot's run carries its own job alone: nothing is steered into it, its completion coalesces nothing, and
no slot job is steered into or coalesced by another run, nor read in its history. So a run that ends after a move or a reassign settles nothing of
the new visit's or agent's work. While any agent has live or unsettled work in the card's room from before the switch,
the card stays on the older path. Other pipelines are unchanged; the switch goes once every pipeline runs by slot.

### Metadata

Metadata is Tico's saved copy of something that lives outside the task tables. Requirements read metadata. Nothing calls
GitHub, Jev or a computer while a task is being moved.

- **A PR:** its newest commit, its checks, whether it is in the merge queue, whether it merged. From GitHub.
- **A release:** its tag and commit. From GitHub.
- **A page:** its version, Jev's verdict on that version, and who OK'd it.
- **A bot:** whether it can run right now, and if not, why.
- **A run:** whether one is live for this task.

Three rules keep them honest:

1. **Two ways in.** An event brings metadata fast: a webhook, Jev finishing. A re-read on a timer brings it for sure: every
   5 minutes for any PR a task waits on.
2. **Newest wins.** All metadata carries a version and the time it was read. An older read can't replace a newer one.
3. **Read from the database only.** `move()` and `waiting_on()` never wait on the outside. A slow GitHub can't stall a
   move or the check.

Example, Checks pass: GitHub says the checks finished. The metadata is saved. The event nudges the check. `waiting_on()`
reads the metadata and finds Checks pass met. `move()` moves the task. If the webhook is lost, the 5-minute read saves the
same metadata and the rest is identical.

#### Outside items

An item outside Tico is (connection, source, item type, key). The connection is the `tools` row it came through
(`tools.record_owner` says whether that tool or Tico owns its records), '' for none: every GitHub item, the company's one
GitHub App. A link to a registered source's item is stored typed on `task_links` (`source`, `item_type`, `item_key`,
`connection_id`): a GitHub PR when it is in the App's organization; a Jira issue
(`https://<site>.atlassian.net/browse/PROJ-12`, key `PROJ-12`) with the connection serving its site: the one tool whose
base URL and sites are on that host and no other (a tool on several hosts serves none). A link that names a connection
for another site is refused, so is one on a site two connections serve unless it names one, and a link on a site none
serves stays a plain link. A task holds one typed row per item, whatever URL names it; a plain link to the same URL
becomes that row, or goes, once the site has a connection. `item_metadata` rows, metadata reads, the task lookups and the due reads all key on the connection, so two Jira sites'
`PROJ-12` never read, wake or move each other's tasks. At most one task holds an item's primary link
(`task_links.is_primary`, the task an intake made for it). `GET /api/v2/tasks?link=jira:issue:PROJ-12@<tool id>` (or the
item's URL; `tico task list --link`) finds the tasks that link one item; a Jira item always names its connection.

A connection id is a tool's name, not a secret: links, records and SQL show it to anyone who reads them; using the
tool (calling it with its key) needs access to it. Once outside references name a tool its base URL (scheme and host in
any case, path as written) and hosts stay as they are (409 `connection_sites`): another endpoint is a new tool. A
deleted tool's id is never given to a new tool while an outside reference, a deleted task's restorable link included,
still names it. A task links a URL through one connection only; a task created with a link whose site two connections
serve keeps it plain; a merge keeps the kept task's link to a URL both tasks have and copies the rest as stored, connection
included. A source registers in `backend/item_sources/__init__.py` with `match_link(url)` and, optionally, `match_key(item_type, text)` and `site(url)`. A Contacts entity's records in other
systems are `entity_external_refs`, one per (system, type, id, connection), written by
`POST /api/v2/entities/external-refs` (`tico contacts record`): the answer holds ids only and a retry shows it only while
the caller still sees that entity, a record on another entity is refused without naming it, and a record erased at its
owner's request is not written again on its connection, '' being its own (one erased before connections, on any).

**A Jira issue's state** is read in the background only through a record integration: an API tool for everyone on one
Jira Cloud site (`<site>.atlassian.net`, the same check a primary link makes) that an administrator marked as holding
records (`record_owner` outside or tico; `tico tool edit <id> --record-owner outside`; only a tool everyone sees can be
marked, and making it Only listed clears the mark), not one a bot keeps for itself, with a shared company API key (no
sign-in, no holder). Tico sends `GET <base URL>/rest/api/3/issue/<key>?fields=status,updated` with that key; nothing
else is read without someone asking, and no one's own access is borrowed. `item_metadata.fields` holds `state` (open,
or closed when Jira's status category is done), `status`, `category`, `updated` (Jira's stamp) and `connection_id`; no
summary is fetched, and the task API returns the link rows only. An issue is never final, since issues reopen. There
is no Jira webhook yet: the heartbeat's own `jira_items` step, after the merge queue's, reads the issues open tasks link
every 5 minutes (one marked due first, across connections), at most 10 per connection and 30 seconds of reading a pass.
Each request has one deadline (10 seconds, or what is left of the pass when it is sent) over the lookup, connecting,
TLS and the answer, and at most 2 are out at once (one that timed out counts until its thread ends). A read clears the
due mark only when it is the mark the read began under, so a mark made mid-read is read next pass. A read Jira refuses,
or whose answer is not an issue's, keeps the metadata as it was with `read_error` and is tried again after 5 minutes. An
issue moved to another project gets a new key: a task filed for the old key keeps it, and one filed for the new key
shows as a duplicate to merge.

#### Withdrawn or closed outside

Jira's last definitive standing of a task's primary issue alone decides whether its release close-out may run
(backend/release_closeout.py), kept on the link and changed only by a read Jira answered (a timeout changes nothing):
active in an In Progress category status or in its Ready status (the status it was filed in, saved at intake from
`primary_status`), withdrawn in another To Do status, closed in a Done category status, gone when Jira answers 404. A
link filed without `primary_status` has no saved Ready, and every To Do status counts as active for it: it never
pauses for a withdrawal. Anything but active when the task leaves Release freezes the close-out: nothing is queued,
and the visit is kept (`detail.pending`) so the Jira pass keeps reading the issue of the Done task (as it does while a
close-out Jira write has not ended; the sender itself holds a write whose issue was withdrawn since, an hour at a
time, until it is back; the issue closed in another Done status than Tico's own target asks the owner even then). A
task with more than one primary Jira link (after a merge) closes none out, and its owner is
asked once; a merge also drops the saved Ready of the link it moves, which then never pauses (merges of tasks filed
for Jira issues are rare). The close-out sends no Slack reply until Slack sends move into the outbound gate. After each
answered read, a non-active standing asks the task's owner (a person; else its bot's manager, then the company's
default person; only someone who can read the task) a people-only OK on the task (backend/owner_asks.py), one per
standing: a new standing replaces the ask. That is how the owner hears of it; no answer unfreezes anything. Putting
the issue back in Jira does: active again takes the ask back (the same task moves on) and runs a pending close-out.
Secondary links are informational.

#### Intake

Find-or-create is the ordinary create: `POST /api/v2/tasks` with `primary_link`, a Jira issue for now
(`jira:issue:PROJ-12@<tool id>` or the issue's URL; `tico task create --primary-link ...`, MCP `tico_task_create`),
and optionally `primary_status`, the status the issue is filed in (its Ready, `--primary-status`).
Only a record integration's issue can be named (a hidden or missing tool answers the same). The org rule check runs
as for any create. Then, in the same write, a task the caller may read that already holds that
item's primary link comes back (`found: true`) as it is, with nothing filed, reopened or moved, and the steps only
filing needs (ask ingest's judgment, the owner's cap, reach) are skipped. A task the caller may not read is never named:
the create runs every check a filing makes and is refused with 409 "This item is already filed". Otherwise the task is
filed in its pipeline's first stage and linked to the item as its primary link (the item, not the title, makes it one
task). The partial unique index `task_links_primary_item` makes a second primary task impossible, so two creates at once
give one task. The dry run (`POST /api/v2/tasks/dry-run`) checks only the primary link's form; `tico task create --dry-run` with
`--primary-link` is refused. A merge moves the
primary link to the kept task (with the exact connection-scoped row); a deleted task restored after another task was
filed for its item gets its link back as a secondary one.

#### Routing

Work that needs a person, with no owner named, is filed with `route: true` (`tico task create --route`, MCP
`tico_task_create`), optionally with `entity` (the relationship). Tico picks the person before anything is written
(`routing.person_for_create`), and the task is filed as theirs, owner and assignee, so the cap, reach, contact checks,
stage entry and create hooks all run once for them; the cap, reach and the org rule check judge that person as the
recipient, before the write. A routed create a company rule would hold is refused, never held ("Held by a company
rule; file it without route or assign someone"), so Send anyway never picks someone the check did not judge; a write
that would route a create the check judged for nobody (a primary link's claim gone meanwhile) is refused 409, send it
again. A saved retry's answer comes back without routing; a primary link a readable task already holds returns that
task before anything is routed. Refused (422): with an owner, with a live meeting, private work
(explicitly, under a private parent even with `private: false`, in a private pipeline or by the filer's private
default: "Private tasks aren't routed; assign someone who can see it"), in any pipeline but Support ("Routed work goes to Support"), with Support retired or changed so its first stage is
not the task's assignee's or is moved by hand, when nobody
can sign in, or when a stage rule holds the person out on entry (nothing is filed). It goes on the company's Support pipeline (preset `support`), made the first time a routed task is filed: one work stage, Open, held by the
task's assignee and not by hand. Its default rules give overdue waits to its manager (the General Manager, else the company's person): Support
waits (24 h quiet, in the hourly batch) and Urgent support waits (1 h quiet on a task tagged `urgent`: the manager is
woken in the same pass, in its one wake of that pass: everything waiting when its hour is open, else only the urgent
tasks, which do not use up its hour). A timer rule with a tag
condition marks whether the task had the tag, so a tag added to a quiet task is noticed. The dry run names the person routing would pick, or reports a filed item's task as found. A
Late label from a due date does not quiet the Support timers: only their own marks dedupe them. They tell the manager
even when the manager owns the task (the company's person can be both), and one urgent quiet period is told once: Support
waits passes over a task Urgent support waits told in the same quiet period. A private Support refuses routed work.

The pick is the first of: the newest person who worked another recent company-visible task of the relationship (its
history or comments, the last 48 hours, at most 20 tasks, only those the filer reads, the person a carried request is
from when there is one) and is on, not counting the filer; the relationship's owner
(`entities.owner_*`) when on, or async and the work is not tagged `urgent`; anyone on in that owner's roster team,
the first by id; else the inbox fallback, the pipeline's manager or owner when a person, else the company's
person (`waiting_on.company_person`). Every one, the fallback included, must be an active person.

A person's availability (`humans.availability`: on, async or off; `availability_until` ends it) is theirs to set, or any
admin's: `PUT /api/v2/humans/{id}/availability` (`tico human availability-set`, MCP `tico_human_availability_set`), read
with `GET` by whoever may see them on the team chart (`tico human availability`). Unset or ended, it comes from their
effective Calendar working hours (`calendar_api.work_settings`: saved, else weekdays 08:00-18:00) in Calendar's zone
for them (`calendar_api.effective_zone`: saved, else their Google calendar's, else UTC): on inside them, async outside.
Routing makes no Google call inside a write: a Google zone not held from the last hour is unknown, and the person is
on. The end is computed at read: nothing rewrites the row.

### Modules

- `task_engine.py`: `move()` and the guard. Also the only writer of snoozes and of requirements added to a task.
- `requirements.py`: the six kinds, on a stage or added to one task. Each one answers met, waiting on what, or not met
  and why.
- `waiting_on.py`: the one read. Batched for lists and pipeline views.
- `never_stuck.py`: the check, the ladder, Late, filling open slots.
- `item_sources/github.py`: the GitHub source. PR and release metadata, the 5-minute GitHub read, webhook
  intake, its conditions and its one action, queue.
- `item_sources/jira.py`: Jira issue links, and their state read through a record integration.
- `item_metadata.py`: the saved metadata of every source's items, per connection, and what waits on it.
- `source_actions.py`: a source's actions on entering a stage (act), their undo, and the retries.
- `stage_effects.py`: file a linked task, queue and dequeue PRs, undo.
- `answers.py`: a person's answer, Give to…, and undo. Needs you itself is a read of `waiting_on()`.
- `pipeline_settings.py`: one record for a pipeline, its stages and requirements. One schema for the editor, the API, hub
  and MCP. Versioned, with who changed what.

### Data

```text
pipelines     name, goal, manager, private, factory, settings_version
stages        pipeline, position, kind (work | done | closed), name, purpose,
              owner (a bot | a person | 'assignee'), by_hand, limit, expected_minutes, on_enter
requirements  stage or visit, kind, source, condition (on an item), settings, can_push_past, who_oks (none | people_only | people_with_exceptions),
              person, added_by, line, shows (page + version | email | PR | action), withdrawn_at
              on a stage: every task. on a visit: this task, this time. unique (visit, added_by) for a bot
tasks         pipeline, stage, owner, assignee, version, status (open | done | closed; written by move() only),
              prefix (its label's letters, from the pipeline it was made in; kept when it moves)
visits        task, stage, entered_at, left_at, how (met | pushed_past | back | closed | reopened | moved), by, why,
              tries, level (owner | manager | person), next_try_at, progress_mark, last_change_at,
              late_told_at, late_person_at, bot_snoozes
answers       visit, requirement, by, answer, notes, page_version, at
snoozes       task, by, why, until (empty = no end), ended_at
links         from_task, to_task, visit, kind (sub-task | handoff)
item_metadata source, item type, item key, title, fields, metadata_version, read_at
releases      repo, tag, commit, published_at
effects       visit, kind (act | undo), source, action, item, key (unique), state, tries, last_error
wakes         existing messages table + wake_key (unique: task, visit, level, try)
```

Table and column names follow Rails conventions. Today's `task_types`, `task_steps`, `stage_requirements` and
`stage_visits` are reshaped into these. No data is carried over.

### move()

```python
def move(task, to, by, why=None, seen_version=None):
    BEGIN IMMEDIATE                                  # SQLite: one writer at a time
    t = load(task)
    if by.is_bot and seen_version is None:  refuse("say which version you saw")
    if seen_version is not None and t.version != seen_version:
        refuse("changed since you looked")
    kind = forward | back | closed | reopen          # reassign: same function, same visit (below)
    if kind != forward:                              # never held by requirements or room
        if by.is_bot or kind == reopen:  require(why)    # a person only has to say why on reopen (C24)
    else:
        for r in unmet(t):                           # the stage's, ones added to this task, any stage skipped
            if not why:              refuse("not met: " + r)
            if not r.can_push_past:  refuse(r + " can't be pushed past. Move back or close.")
            if r.people_only and by.is_bot:  refuse(r + " needs a person")
            record_pushed_past(t, r, by, why)
        if to.kind == work and not has_room(to):  refuse("no room in " + to)
    if kind in (back, closed):  undo_effects(t.visit)   # dequeue PRs, close tasks filed this visit
    close_visit(t, kind, by, why)                    # requirements added to it end with it
    guard.open()
    t.stage, t.owner, t.status, t.version = to, owner_for(to, t), status_for(to), t.version + 1
    guard.close()
    v = open_visit(t)
    if to.on_enter.file_into:  file_linked_task(v)       # same transaction
    if to.on_enter.act:        add_effect(v, act)        # row now; the source's call is made by the check
    add_wake(t.owner, v)                             # row now, keyed by visit
    COMMIT
    fill_open_slots(); push to screens
```

Reassign goes through the same function and the same guard, and keeps the visit. It changes the owner, raises the
version, resets the tries and drops the old owner's queued wakes. The on-enter action does not run again, and
requirements added to the task stay.

### The check

```python
def never_stuck(now):                                # Heartbeat, every 10 s. No model calls.
    run_effects()                                    # GitHub calls still owed. Keyed. Retried.
    refresh_facts()                                  # re-read PRs that tasks wait on if read_at is over 5 min old
    end_snoozes(now)
    for t in open_tasks_not_snoozed():
        waits = waiting_on(t)                        # what is missing and who has it. Never empty.
        if waits.all_met and has_room(next(t)):  move(t, next(t), by=system);  continue
        for w in waits:
            # a person, a paused bot, Jev, GitHub, another task, room: nobody to wake. Screens read the same waits.
            if w.is_bot and not w.bot.cannot_run and not live_run_or_queued_wake(t, w.bot):
                if progress_mark(t) != t.visit.progress_mark:  reset_tries(t)
                if now >= t.visit.next_try_at:  wake_or_go_up_a_level(t)   # 2 min, 15 min, 50 min
        if late(t, now):  hand_to_manager_once(t);  hand_to_person_once_at_3x(t)
    fill_open_slots()                                # oldest waiting task first
```

Target: one pass over 10,000 open tasks in under 200 ms, measured in CI. A pass that overruns just starts the next one
late. Nothing depends on the exact 10 seconds.

### GitHub

- **Many PRs.** A task links any number of PRs. PR open, Checks pass, PR merged and In a release each look at all of
  them.
- **Metadata is read, not trusted to arrive.** Webhooks are dropped if their delivery id was seen before. Any PR a task
  waits on is re-read every 5 minutes, at most 50 PRs per call.
- **No stale writes.** Each PR's metadata has a version, compared inside the transaction. An old read can't overwrite a newer
  one.
- **Checks pass** ignores Tico's own check runs. No checks at all after 10 minutes counts as met (C13).
- **How checks are read.** Each check run and commit status at the PR's newest commit. A check run is its name inside
  its check suite: two workflows or two Apps that both name a run `checks` are two checks, and a red one fails the PR
  whichever GitHub lists first. A re-run stays in its suite, so only its newest run counts (a re-run that goes green
  passes; one that goes red fails). A skipped or neutral run fails nothing, but it is not a pass: when every check was
  skipped, Checks pass is not met, and it is the owner's turn ("No checks ran on ...: every check was skipped").
- **A new commit during Review** puts the review back to Waiting on the reviewer. A commit that lands after the
  review page was attached wakes the reviewer at once, once per commit.
- **A merge conflict before Merge** is the Builder's (the Build stage's owner bot), whoever holds the stage: "No merge
  conflict" is not met and is the Builder's turn. The Builder is woken at once, once per PR and conflicting commit, to
  rebase on the PR's base branch, keep both sides' work, push with `git push --force-with-lease` (never a plain force)
  and say so; the task stays in its stage. A bot holding the stage (a reviewer) gets one quiet line that it is not its to fix. The ladder tries the Builder, then the manager
  bot. With no Builder bot it is the stage owner's, as before. In Merge, the merge queue's drop says it.
  While the stage waits on a person's OK or answer, the conflict is held: it stays not met, the person's ask adds one
  line ("The PR has a merge conflict: Builder fixes it after your OK."), and the Builder is not woken, so the ask does
  not change under the person. When the person answers, the Builder is woken once to rebase: after an OK it is told to
  leave the OK'd page as it is, and a send back reaches it with the conflict, so one turn can fix both. A rebase that
  changes the same lines keeps the person's OK. The last reviewer reads changes to other lines (QA, C22).
- **Merge.** Tico queues each PR at the commit that was checked (GitHub's merge queue, or auto-merge where the repo has
  none). No bot runs a merge command. A call GitHub fails is tried 3 times, 1, 5 and 15 minutes apart, then it is the
  owner's turn with GitHub's reason. When GitHub takes a PR out of the queue without merging it (a check failed, a new
  commit), Tico saves why and which check failed, PR merged says "Dropped from the merge queue: <reason>", and the
  owner is told once. Tico never queues it again by itself. The owner (the Release Owner, who may push to PR
  branches) queues it again (`tico task queue`) at the commit the task came into Merge with, or sends the task back to
  QA (after pushing its own fix, if one is needed). Queuing again is refused while the PR's own checks on its
  newest commit are running or failing, with the reason. For a bot, it is also refused when the PR's newest commit
  changes other lines than the one the task came into Merge with ("<ref> has new changes since the person's OK. Move
  the task back to QA, where Reviewer 2 reads them."), and while Tico has not read what it changes yet ("Try
  again in a minute"). Tico reads that from GitHub once per commit (its compare from where the commit branched off),
  for the PRs of a task a person OK'd or in Merge whose commits moved.
  A PR GitHub says conflicts with its base branch is never queued, by the stage or by a queue-again: Tico reads
  GitHub's `mergeable` right before it queues, and a conflict fails the queue at once while `UNKNOWN` (GitHub still
  working it out) is read again 1, 5 and 15 minutes later. Queuing again is refused while the PR's link says it
  conflicts. The refusal, and a removal for a conflict, tell the owner to send the task back to QA, where the
  Builder rebases it ("No merge conflict" before Merge).
  **An order between tasks** (backend/merge_order.py). A task may go after other tasks: `tico task after <task>
  <earlier>` (`--remove` takes it off), `--after` on `tico task file` and `tico task create`, the MCP tools'
  `after`. Whoever may change the later task sets it; the earlier one need only be readable. A loop ("#40 already
  goes after #42") and an order on or after a task that has ended are refused. Before Merge it changes nothing: both
  build and are reviewed side by side. In Merge, one check holds the queue (`merge_order.held`, asked on entering,
  when a PR's metadata comes late and by `tico task queue`): while a task it goes after is open and has not merged
  its PRs (a task with no PR: until it is Done), its PRs are not queued. Its turn is that task's, "After #40: waits
  for it to merge" (PR merged says "Not queued until #40 merges"); nobody is woken and it is not Late. A bot's `tico
  task queue` is refused ("Waits for #40 to merge"); a person may queue it anyway, and its history says "Queued
  before #40 merged". When #40 merges a PR or moves on, the tasks after it are read again: one no longer held is
  queued then, and if GitHub now says it conflicts, the owner's send-back to QA names #40 for the Builder to
  rebase onto. When #40 is closed without merging, the order ends and it is the owner's turn once: Tico does not
  queue it by itself. Both tasks show the order, read-only: "After #40 · Merge" and "Before #42 · Build" (to a
  reader who cannot open #40: "After a task you can't open").
- **A commit after the person's OK** does not merge on that OK unless it changes the same lines. Otherwise the last reviewer
  reads it in QA (from Merge, the Release Owner sends the task back; the person's OK comes back with it, on the
  page they OK'd). The reviewer's OK keeps the person's. A send back goes to the Builder, who attaches the page again,
  and the person is asked after the next read. The task shows "Changed after OK" and the manager bot is told once
  (C22). Sent back while a PR is still at the commit it was last queued at, the OK that came back does not count:
  the page waits on the Builder ("Merge queue ran 463f2e7 and it failed (…): push a fix."), with the mover's note,
  until there are new commits, and then the rules above decide. A page attached on that visit is judged as any other.
  *Added Nov 13: four tasks went straight back to Merge and were queued again at the commit that had just failed.*
- **A PR linked in Merge.** While a task is in a stage that queues PRs, only a person links another PR to it: a bot is
  told "Send the task back before linking another pull request.", and a bot's worktree branch PR is not linked there.
- **`tico/merge-ok`.** Before queuing a PR, the server posts a check run `tico/merge-ok` = success on exactly the commit
  it queues (from the queue action or a queue-again, never otherwise); taking the PR out turns it to failure on that
  commit. When GitHub's merge queue asks for the checks on a group's commit (the `merge_group` webhook), Tico answers
  success only if its Merge stage queues every PR in the group at its current head, failure when the group holds a
  Tico task's PR the Merge stage did not queue there, and neutral when the others are PRs no Tico task links (a repo
  shared with people: their PRs are not Tico's to pass or fail). Only the server's own token writes checks; no bot's
  token does. A repo ruleset that requires the check, pinned to the company App, stops a bot merging
  or queuing a PR around the Merge stage ([GitHub App](../github-app.md#require-ticos-merge-check)). It means "Tico's Merge
  stage queued this PR after the person's OK": a commit pushed after the OK is queued by a bot only when it changes the
  same lines, or after the last reviewer OKs it (C22), and by a person as they choose. A post that fails queues nothing and
  is tried again like any failed call.
- **Leaving Merge backwards, or closing,** takes the PRs out of the queue. That call doesn't exist today and gets built.
  If GitHub merges one first, the task shows "PR merged anyway" and the manager bot is told once.
- **In a release** means the merge commit is in the commit history of a published release of the release repo: not a
  draft. Pre-releases count by default, including tags with a dash; explicitly turn off In a release's
  "Pre-releases count" setting to require a stable release. Time
  plays no part. A repo that publishes no releases: met once merged (C31). Checked when a release publishes and on the 5-minute read.
- **Release.** In a release's setting **Release with** says how Tico cuts one: `workflow:<file>` (Tico runs that
  workflow on the default branch; the App's Actions: write), `tag` (Tico creates the tag the Release Owner names at the
  merge commit, and the repo's workflow publishes it; Contents: write), or empty (the repo releases by itself). With a
  way set and nothing asked yet, it is the Release Owner's turn: "No release of <repo> asked for yet". It asks with
  `tico task release <task>` (MCP `tico_task_release`, `POST /api/v2/tasks/{tid}/release`), and the Heartbeat carries it
  out through the App, never a bot's token: the Release Owner's raw `gh workflow run`, tag and `gh release` stay
  denied. Allowed for the stage owner, the pipeline's manager or a person, only on a stage with In a release, and for
  a bot only when a person's OK stands on the people-only sign-off before it (QA). History: `release_asked`,
  `release_cut`, `release_refused`, `release_found`. One workflow run asked after a task's merge covers every task
  waiting on that repo. Never a second release: before the ask is carried out (and before `tico task release` takes
  one), Tico reads the repo's newest releases; one already published with the merge meets the requirement instead
  (`release_found`, the ask moot), and the tool answers "Already in <tag> of <repo>".
  GitHub refusing, or no release an hour after the ask, is the Release Owner's turn with why (it asks again or says why
  not); a second failure since the merge, or a permission the App lacks, is a person's, and health warns "Tico can't cut
  releases" while a task waits on a Release with that needs it. Only a person sets Release with.
  With no way set (the repo releases on its own, or a person cuts each release, as on a repo that tags rcs by hand),
  the task shows "Waiting for the next release of <repo>" and only that release ends it: nobody's turn, no wake, no
  ladder and no Late for a bot. After a week (or the stage's expected time, when longer) it is Late and a person is
  asked to cut the release, once. A week, because a weekly stable train is the slowest cadence a repo keeps on purpose; a repo
  cutting pre-releases several times a day never gets near it.

### What gets deleted

- **Pipeline movers, about 5,200 lines:** step_flow, verdict_moves, check_pools, merge_arm, move_retry, roadmap_flow,
  review_gate, step_age, step_backlog.
- **Rewritten into the engine, about 5,000 lines down to about 2,000:** pipeline_engine, pipeline_links,
  pipeline_review, `stuck_cards`, release_flow's moves.
- **Task upkeep that the check replaces, about 7,000 lines across 17 files:** task_blocked, task_holds,
  task_check_backs, stale_review, stranded_resume, task_loops, task_allocator, auto_tasks, task_roundup,
  milestone_waits, question_tasks, ask_deadlines, approval_expiry, blocker_asks, ticket_notices, still_wanted,
  task_snooze (rewritten small).
- **Not changed:** Jev, factories and their counts, pipeline stats, who may see what, quarantine, the runner.

Each of these is listed with where its behavior goes in the [tick list](#tick-list-every-task-and-pipeline-feature).

## C. The screens

Sign-off C: every screen this touches, numbered S1 to S18. The mock-ups use the app's dark look. Where a screen says
"Today", that is the app now; "New" is what gets built.

### The rules every screen follows

- **A row is its title.** One line. Nothing under it. Beside it only who has it and how long.
- **Marks, not words.** A spinner: a bot is working. An amber "!": it needs a person. A red dot: it can't run. Late is a
  small tag. No mark: nothing needs anyone right now.
- **The words are on the task.** Who has it and what is missing is one line at the top of the task, and it shows when you
  point at a row.
- **No text that explains the screen.** No subtitles, no helper lines, no summaries in headers.
- **Search and filter on every list.**
- **Buttons, not menus.** You never pick a status.

### How the mock-ups are drawn

These words are used in every screen below.

- **Mark:** at the left of a row, before the title. One of: a green spinner, an amber circle with "!", a red dot, or an
  empty space the same width (no mark), so titles line up.
- **Late tag:** a small rounded tag reading "Late", in amber, right after the title.
- **Avatar:** a small round picture of the task's owner, at the right of a row (the mark and the line say whose turn it is).
- **Age:** a short, dimmed time at the far right of a row: how long, such as "58m", "2d", "30h".
- **Main button:** filled green. Other buttons are outlined. Where a list below names the main button, it says "(main)".
- **Icons** (no words beside them): search (a magnifier), filter (the shared filter icon), list view, columns view,
  archive (opens Snoozed, Done and Closed), Flow, + (new task, filled green), edit (a pencil; opens the
  pipeline editor).

### S1. A task row: every look

Each look is one row, on one line, in this order: mark, title, anything after the title, avatar, age.

1. **A bot is working:** spinner · "Phone view shows one stage" · avatar · "58m".
2. **Needs a person:** amber "!" · "Checkout flow" · the owner's avatar · "2d".
3. **Can't run:** red dot · "Refund flow" · avatar · "1h".
4. **Nothing needed:** no mark · "Saved carts" · avatar · "5h".
5. **Late:** spinner · "Calibration export" · Late tag · avatar · "30h".
6. **Pointed at:** no mark · "Assistant can approve a spec" · a small dark tooltip "Builder · missing Checks pass" ·
   avatar · "2h".
7. **In your list:** "Approve the spec for Checkout flow" · avatar · "2h". No mark and no mark space.
8. **A question:** "Should the launch note go out on Friday?" · avatar · "1d". No buttons: a reply in the chat answers it.
9. **One tap:** "Connect Google for Scout" · button "Connect" (main) · avatar · "3h".
10. **Snoozed:** "Carrier contract", dimmed · "Nov 14". No mark, no avatar.
11. **Done, Closed:** "Saved carts", dimmed · "Nov 9". No mark, no avatar.

Rules:

- "Nothing needed" covers a task that is queued, has checks running, or waits on another task or a full stage.
- In your own list a row is what you are asked, as a sentence. It has no mark, because everything there needs you.
- Snoozed, Done and Closed are behind the archive button.

### S2. Tasks › You

Today: the Tasks page, You view, with the groups Answer, Review and Do.

New:

- **Header,** left to right: the title "Tasks"; a view button "You 7 ▾", with the 7 in amber; then, at the right, icons
  for search, filter, archive, Flow and + (new task).
- **Group "Answer", count 3:**
  - "Raise Home Pro to $479 for December?" · "Yes" (main) · "No" · avatar · "1d"
  - "Which plan do new signups get?" · avatar · "2h"
  - "Export retries is stuck" · avatar · "3h"
- **Group "Review", count 2:**
  - "Approve the spec for Checkout flow" · avatar · "2h"
  - "Send this email to Dana about her replacement?" · avatar · "3m"
- **Group "Do", count 2:**
  - "Connect Google for Scout" · "Connect" (main) · avatar · "3h"
  - "Sign the FCC filing" · Late tag · "1d"
- Group headings are small, upper case and dimmed, with the count beside them.

Rules:

- Today's layout and its three groups stay: Answer, Review, Do.
- New in the list: specs and review pages to approve, and stuck tasks. Today they live in a separate queue or not at all.
- Flow's mark (two play shapes) opens the first one and steps through them (Flow, S3).

### S3. The ask screen

Today: the requirement review queue. Today the page to review is a link. New: the page is on the screen. Today: Approve,
Mark met, Message Product Owner twice. New: Approve and Send back.

This screen is **Flow**. It opens from Flow's mark: beside Tasks in the sidebar while something waits on you, the Flow
tab in a phone's bottom bar (grey, jade while something waits; with nothing waiting it opens on All done), in the
Tasks header (a pipeline's items on a pipeline, all of Needs you elsewhere) and on each stage with items.

**New, desktop.** An approval of a page:

- **Top bar,** one line: where you are ("All mine", "All mine / Product" or "All mine / Product / Spec"), a menu of the
  pipelines and stages where something waits on you (and Other, for items in no stage), each opening that walk; Flow's
  mark; a quiet mic (voice is not built: pressing it shows a wave and "Voice is coming soon"); then ‹ (previous), › (next),
  ✕ (close). No count, and no score is shown. In a walk of more than 5, items not yet seen this visit come highest score
  first; the one on screen and the ones seen keep their place.
- **Left, the page itself,** shown in full: the heading "Checkout flow: spec", then "Guests can pay without making an
  account. The order summary stays visible on every step." The second sentence is highlighted: it changed since the
  version you last saw. The rest of the page follows.
- **Right: the task's chat.** Its own thread, as on the task page (a message here is there too, and the reverse), with
  the ask as the asker's last message: who asked and when on one line (the name cut first), "#80 ↗" at its right end
  (a paused or stuck asker is a small mark, never a card), then "Approve the spec for Checkout
  flow". Under the chat's box only "Approve" (main; "Approve with comments"
  while the box has words, which go with the approval, as its note or, on an OK bound to exactly what it shows, as a
  comment after it), the snooze icon and ⋯. The box's arrow sends the words: on an OK that is its send back (the
  answer "no", the words its note, as before). A bot's ❓ is answered by a reply in its chat, and the reply clears it.takes the words as its answer.
  A page's OK always asks its question ("OK the spec?"), then one line of what changed (else Jev's take) and your
  points; your note on the version before is your own message above the ask, never inside it. A Validate or QA OK
  shows its question, then the verdict as up to 3 short check rows (what the decision model passed on the page it
  shows, one line per part of its check), else one line, and Proof, which opens the Proof tab on a phone; a long
  review is the asker's message above it. A task's chat is named for the task as it is now, so its box reads
  "Message <task title>".
  An action's OK holds a small receipt in the ask (the amount large, then To, From, When and the document), and its
  forward button says what it does: "Pay $1,240.00", "Send email"; a Validate or QA OK's says "OK".
  On a phone one row holds the task's title and two tabs: Chat, where each item opens (nothing is remembered from the
  last), and a second named for what it holds: Spec, Proof (a Validate or QA OK), Invoice or Email (an action's OK), else
  Task. It shows the task open, never behind a chevron: title, text, page, files, a live browser. The chat sits on its
  box: the ask last, older messages above. One bottom row: Snooze left, the forward action stretched in the middle, ⋯
  at the far right (a sheet from the bottom edge with a grab line and 52 px rows over a dim backdrop; a tap
  outside or Esc closes it); a question with no button action has the chat's box as
  that middle. With words typed on an OK the arrow goes quiet, "Approve with comments" the one filled control. A
  browser ask's forward action is "Open browser", then "Done" once back from it.

**Opening a task opens its ask.** A task waiting on your own answer (any of its waits, not only the first) opens on this screen from anywhere (the board, a
task list, a `#/task/<id>` link, a link in chat, a bot's page), with ‹ › through the rest of Needs you; any other task
opens its page. "Open task #N ↗" opens the task's page, which stays open; its ask comes back here.

The chat's box is the one every chat has. A file added with 📎 goes up to the task's Files when the arrow sends, and
the words end with each file's name and id, so the bot's wake lists them; one that fails is named and nothing is sent.

The phone view is in [S17](#s17-on-a-phone). What changed since the version you last saw is highlighted in the page.

**The other kinds:**

- **An email to approve:** heading "Send this email to Dana about her replacement?". The email itself: "To: Dana", "Subject: Your replacement order", "Hi Dana, your replacement ships Monday…".
  "Approve" (main); a reply in the chat sends it back.
- **A question from a bot (❓):** the chat at the marked message, with its composer. A reply there answers it and clears the ❓.
- **A one-tap fix:** heading "Connect Google for Scout". Line "Its Google login expired Nov 8." Button "Connect" (main).
- **A stuck task:** heading "Export retries is stuck". Line "Checks fail: lint, unit. Builder and Eng Manager each tried 3
  times." Buttons "Try again" (main), "Reassign", "Move back", "Close".
- **⋯, last on every item's row:** **Close task** and **Give to…** (on an ask in its chat, all ⋯ holds), when you may close the task (whoever
  may work it: its owner, its requester, their managers, its pipeline's manager or owner, someone who moves tasks). It
  closes the task with what is typed in the item's box as the reason, with no confirm, and moves on; ↶ reopens it. The
  reason goes on the close in the task's history, and a bot that asked for the task (not the one that owns it) hears
  "Closed: <title>" with it on its next run.
- **After an answer:** the next item shows at once, and a line reads "✓ Approved with comments · Undo" (what was
  done) for about 5 seconds; it stays while Undo has the keyboard's focus. The line wraps to two lines and is never cut.
- **Snooze:** one menu, 1 hour, 1 day, 1 week, 1 month (each exactly that long from now) and Other (a date and time of
  your own, in the same menu). On an ask it is a quiet clock icon at the end of the answer row, in Skip's place, and
  snoozes the ask for you alone (#3769): it stays in everyone else's Needs you and its bot is not told. It leaves Flow
  until then, with ↩ "Snoozed" in the top bar, which brings it back. Mentions, stuck tasks and fixes keep Skip.
- **The end:** "All done", and "2 snoozed" counting every ask you have snoozed; it opens their list, each with ↩ to
  bring it back now.

Flow has no 👍 or 👎.

### S4. The task

Today: the task window, in two columns.

- Same window as today, same two columns.
- Left, in order: title, one line, description, conversation.
- Right: the fields, what the stage needs, sub-tasks, links, history.
- New: a person's face on a requirement that waits on them. + adds an OK from someone.
- @ from a person puts the task in that person's Needs you until they reply. From a bot it only marks it unread.
- Gone: Status, Type and Step menus, Public text, Runs on. The last two move under ⋯.

**New, desktop:**

- **Top bar:** "Product › Engineering › Build" at the left (where it is). At the right: "12m ago" (when it last
  changed), ⋯, ✕.
- **Left column, in order:**
  - Title: "Calibration export retries", with the Late tag after it.
  - The one line: spinner · "Builder working".
  - The description: "Retry the calibration export when the device is offline. Keep the last good file until the new one
    is written."
  - Group heading "Conversation". Each message has an avatar, the name, the time, then the text:
    - "Builder 11:02": "Tests fail on a missing fixture. Looking for it."
    - "You 11:10": "@Builder it moved to tests/fixtures/devices." (the @Builder is a link)
    - "Builder 11:31": "Found it. Pushed 4c1e9a."
  - A "Message" box.
- **Right column, in order:**
  - Fields, name then value: Owner "Builder"; Asked by "You"; Due "Add" (dimmed); Tags "Add" (dimmed); Part of "P-12
    Checkout flow" (a link).
  - Group heading "To leave Build", with a + icon at its right.
  - Requirement rows, mark then name, with a detail at the right:
    - ✓ "PR open" · "#812" (a link)
    - ○ "Checks pass" · "lint, unit"
    - ○ "Retry how many times?" · Sam's avatar (it waits on Sam)
  - Group heading "Sub-tasks", count "3/3".
  - Group heading "History", then dimmed lines: "11:31 Builder pushed 4c1e9a", "09:15 Builder started", "09:14 Moved to
    Build".

⋯ holds Snooze, Move back, Move on, Reassign, Close, Move to… (another pipeline), Who can see, Runs on.

**The one line under the title, in every state:**

1. Amber "!" · "Approve the spec" · button "Open" (main), at the right.
2. Amber "!" · "Waiting on Sam".
3. Spinner · "Builder working".
4. No mark · "Builder tries again in 12 min".
5. No mark · "Checks running".
6. No mark · "Waiting on E-214 Build checkout flow" ("E-214 Build checkout flow" is a link).
7. No mark · "Build is full".
8. Red dot · "Builder is out of credit" · button "Add credit", at the right.
9. No mark · "Snoozed to Nov 14", dimmed · button "Wake", at the right.
10. No mark · "Done · Nov 9 · 0.6.2", dimmed.
11. No mark · "Closed · duplicate of P-88", dimmed.

"Watch", "Wake now" and the list of what comes next are gone. What is missing is the "To leave" list at the side.

### S5. The small dialogs

- **Snooze:** buttons "Tomorrow", "Next week", "Pick a date", "No end".
- **Reassign:** buttons "Builder", "Eng Manager", "Someone else…".
- **Move back:** one button per earlier stage, such as "On deck", "Build".
- **Close:** no dialog. A bar "Closed" with an "Undo" button at the right.
- **Move on, something missing:** the line "Move to Review without Checks pass?", then a "Why" box and a "Move" button
  (main) on one row.
- **Can't move on:** "PR #812 hasn't merged" with a "Move back" button at the right.

Rules:

- One tap each. A person never has to type a reason, except to move on with something missing (C24).
- Close has no dialog. It closes, and Undo shows for 10 seconds.

### S6. New task

- A "Title" box.
- A taller "Details" box.
- One row: a button "For: Scout ▾" (who it is for), a button "General ▾" (the pipeline), and at the right "Create"
  (main).

Rules:

- No due date here. It can be added on the task.
- A new task starts in its pipeline's first stage.

### S7. A pipeline: columns, list, search and filter

Today: the Engineering and Product pipelines.

**New, columns** (Product):

- **Header,** left to right: "Tasks"; a pipeline button "Product ▾"; the edit icon. At the right: a "Search" box with the
  search icon; icons for filter, list, columns (selected), archive; the play icon in amber with "1" beside it; +.
- **Columns,** each with a header (the stage name, and its number at the right) and its tasks as small one-line boxes:
  - "Ideas", 12: "Gift receipts", "Order tracking page", "Saved payment methods", "Reorder in one tap".
  - "Spec", 2/2: "Checkout flow" with amber "!" and an avatar; "Saved carts" with a spinner.
  - "In development", 2: "Guest checkout"; "Refund flow" with the Late tag.
  - "Validate", 1: "Address lookup" with a spinner.
- In a column, the mark sits after the title.
- A box (task #220) is the title on up to two lines, then a meta line: the asker's face and name, the marks, time in
  this stage ("3d") and, when someone else owns it, the owner's face at the right ("Owner: name" when pointed at). The
  marks sit on that meta line. Pointing at the box shows the whole title. A phone's stage shows the same boxes.

**New, list, grouped by stage** (Engineering):

- **Header:** "Tasks"; "Engineering ▾"; the edit icon. At the right: the "Search" box; icons for filter, list
  (selected), columns, archive; +. No play icon.
- **Groups,** each a stage name and its number, then rows as in S1:
  - "On deck", 2: no mark "Saved carts" · avatar · "20m"; no mark "Order export" · avatar · "1h".
  - "Build", 3/3: spinner "Phone view shows one stage" · avatar · "58m"; no mark "Calibration export retries" · Late tag
    · avatar · "30h"; no mark "Assistant can approve a spec" · avatar · "2h".
  - "Review", 1: spinner "Guest checkout" · avatar · "14m".
  - "QA", 1: amber "!" "Address lookup fix" · the person's avatar · "5h".
  - "Merge", 1: no mark "Refund flow" · avatar · "40m".

**New, search and filter** (Engineering, list view):

- **Header:** "Tasks"; "Engineering ▾". At the right: the search box holding "export"; filter (on); list (on); columns;
  +.
- **Filter chips** under the header: "Waiting on: Sam ✕" and "Late ✕".
- **Result:** group "QA", 1: amber "!" "Order export totals" · Late tag · the person's avatar · "2d".

Rules:

- The list is the columns turned on their side: grouped by stage, in order. Each run of empty stages, even one, is
  one quiet line of stage chips in its place (names only; a chip opens the stage's editor), and a pipeline with nothing
  open is one empty state: a drawing, "No open tasks in Engineering" and New task. Neither while searching or
  filtering, nor while more tasks are still to load. The columns and the phone keep every stage.
- Search and filter work on both views and on every pipeline, General included.
- Filters: Owner, Waiting on, Stage, Asked by, Tag, Due, Late, Pushed past. "What can Sam do?" is Waiting on: Sam.
- Done and Closed are not columns. They are behind the archive button, with Snoozed.
- The play button shows only when something in this pipeline needs you.
- A stage shows its first 30 boxes, then "Show 250 more", which shows the rest of that stage. Its number counts them all.

### S8. A stage's header

Examples, as drawn: "Ideas 12", "Spec 2/2", "Build 2/3", "Review 0".

- A name and a number. Where the stage has a limit, the number is "how many hold a slot / the limit", then a person
  mark and how many wait only on a person's OK, when any do (C27). Its label and tooltip say "15 wait on a
  person"; the phone's head shows the same mark for the stage on screen.
- No other words. Tapping it opens the stage's settings (S11).

### S9. A pipeline's state

It is the pipeline's `mark` in `GET /api/v2/factories`: `cant_run` when a bot in it can't run, `needs_person` when it
needs a person, otherwise none. No screen draws it: each task's row carries its own mark, and the pipeline's own header
says nothing about it.

### S10. The manager rail

Today: a pipeline with the manager rail at the side.

**New:**

- **Top:** the manager's avatar, "Eng Manager" and the close button.
- **Chat:** the direct chat with the manager, filling the rest of the column: a message from "Eng Manager", "1d":
  "Calibration first-try is 84 percent. The target is 95 and the fix is in ring 3."
- A "Message Eng Manager" box, always at the bottom of the window; the messages scroll above it, newest in view.

Rules:

- The rail is the chat alone. The Log, Tasks and Learned tabs and the Late list are gone (Nov 13).
- The rail is as tall as the window, never as tall as the board; scrolling the board does not move it.
- Late tasks show on the board's cards and stage headers. The Log (each push past, each snooze with no end date, as C1
  and F30 describe) stays in the API, GET /api/v2/pipelines/{id}/log, for bots and the CLI; no screen reads it.
- A phone has no rail: the board's Ask bar opens the same chat as a sheet.

### S11. Pipeline editor and stage settings

Today: the pipeline editor.

**New** (the Product pipeline):

- **Heading:** "Product".
- **Pipeline fields,** name then value: Goal "Discerning, high-quality specs that ship"; Manager "Product Owner ▾";
  Factory "Product ▾"; Prefix "P". (No Private switch in 0.6.0-rc.3: a pipeline is private from its preset, or as it
  is made through the API. See "Private pipelines".)
- **Stages,** one panel each, closed (▸) until clicked:
  - "▸ Ideas"
  - "▾ Spec", open, with buttons "↑", "↓" and "Remove" at the right. Its fields: Purpose "A spec a builder can build
    from"; Owner "Product Owner ▾"; Tasks leave "When requirements are met ▾"; Limit "2"; Expected time "2 days"; On
    enter "Nothing ▾". Then the label "To leave", with one requirement row: "▸ Spec page", and at the right, dimmed,
    "Jev, then Ana". Then a button "+ Requirement".
  - "▸ In development"
  - "▸ Validate"
- **Bottom row:** "+ Stage" at the left, "Save" (main) at the right.

**A requirement, open:** fields, name then value: Needs "A page ▾"; Jev checks "States the goal, the scope (what is
in and what is out), and what done looks like as acceptance criteria someone can check. Only these three count: a page
with all three meets it however long it is and whatever else it holds."; Then a person "Ana ▾"; Who OKs
"People only ▾"; Can be pushed past "On".

Rules:

- A stage opens when you click it.
- "Room" is now "Limit". The word "pool" is gone: Ideas is a stage whose tasks leave by hand.
- Done and Closed are not listed. Every pipeline ends in them.
- Going away from today's editor: Judge picker, Stuck after, Ask a person after, Questions expire, Then…, On ice.

### S12. Factories on Strategy › Org

Under the people chart, a **Factories** section: one locked box per factory, named on its tab. Its Factory Manager sits
on top when it has two or more pipelines, then one Pipeline Manager card per pipeline, in the order work files from one
pipeline into the next. A card's name opens its bot; its chip opens the pipeline on the Tasks page. Pipelines in no
factory share one box, "Not in a factory", after them.

Rules:

- The cards come from the pipeline and factory setup: none moves, folds or opens an edit menu.
- The company's owner and Admins have a quiet **+** beside the title: **New factory** asks a name and a goal
  (`POST /api/v2/factories`). For them the section shows before the first factory; for anyone else it shows only once
  there is one.
- How full each stage is, and the rates in and out, are on Company's Floor card (docs/overview.md).

### S13. Home

Home is not touched in this work.

### S14. A bot's page

**Bot page · Builder:**

- The top line: red dot · "Out of credit" · button "Add credit" at the right.
- Group "Tasks", count 3. Each row is the title and, at the right, dimmed, its stage:
  - "Phone view shows one stage" · "Build"
  - "Calibration export retries" · Late tag · "Build"
  - "Refund flow" · "Merge"

On a bot's page the words Blocked, Waiting on a human and Waiting on a bot go away.

### S15. Health

**Health · Tasks:** five lines, each a big number then words:

- "0" waiting on nothing
- "3" on a retry
- "1" with a manager
- "1" at the ladder's end
- "6" can't run

### S16. What bots see

Bots read the same line people do. Every refusal says what is missing and which commands are allowed next.

**A wake:**

```text
Task E-214 "Build checkout flow"
Engineering · Build · your turn (try 2 of 3)
Missing: Checks pass. 2 failing: lint, unit.

When you stop, the task must be one of:
done, asking a person, waiting on a task
you filed, snoozed to a date, or closed.
```

**tico task show E-214:**

```text
E-214 Build checkout flow   v12
Engineering · Build · owner Builder
Turn: Builder (stopped early, try 2 of 3)
To leave Build:
  met      PR open      #812
  not met  Checks pass  lint, unit at 4c1e9a
Next: Review (Reviewer 1)
Filed by: P-12 Checkout flow
```

**The commands:**

```text
tico task done E-214 -m "note"
tico task move E-214 next|<stage> -m "why"
tico task close E-214 -m "why"
tico task ask E-214 --to chris "one line"       # a ❓ on the task's chat
tico task ask E-214 --to chris --kind ok "one line" [--show page:12]
tico task attach E-214 --req "Review page" page:12 --version 4
tico task file --parent E-214 --pipeline general "title"
tico task snooze E-214 [--until 2026-11-14] -m "why"
tico task ask E-214 --withdraw
```

**A refusal:**

```text
$ tico task move E-214 next
Not moved. Not met: Checks pass (lint, unit).
You can:
  fix the checks, then it moves by itself
  tico task move E-214 next -m "why"   push past
  tico task ask E-214 --to ...          ask a person
  tico task close E-214 -m "why"
```

A refusal never counts toward quarantine.

MCP tools mirror these one for one. There is no `tico card`.

### S17. On a phone

Four phone screens:

1. **Tasks › You.** Header: "Tasks"; "You 7 ▾" with the 7 in amber; at the right, play and +. Group "Answer", 3: "Should the launch note go out on Friday?" · "1d"; "Which plan do new signups get?" · "2h". Group "Review", 2: "Approve the spec
   for Checkout flow" · "2h"; "Send this email to Dana about her replacement?" · "3m". Rows show the title and age only:
   no buttons, no avatar.
2. **An ask.** Top bar: "All mine", Flow's mark, the mic, ‹, ›, ✕. The heading "Approve the spec for Checkout flow". The page:
   "Checkout flow: spec", "Guests can pay without making an account. The order summary stays visible." with the second
   sentence highlighted. A "Notes" box. Buttons "Approve" (main) and "Send back".
3. **A pipeline.** Header: "Tasks"; "Engineering ▾"; at the right, search and +. A row of stage tabs: "On deck 2",
   "Build 3/3" (selected), and "Review" with one task. The selected stage's tasks: "Phone view shows one stage" with a spinner;
   "Calibration export retries" with the Late tag; "Assistant can approve a spec".
4. **A task.** Top bar: "Engineering › Build"; at the right ⋯ and ✕. Title "Calibration export retries" with the Late
   tag. The one line: spinner · "Builder working". The description: "Retry the calibration export when the device is
   offline." One message: "Builder 11:31": "Found it. Pushed 4c1e9a." A "Message" box. Then "Details ▸".

Rules:

- A long sentence wraps on a phone.
- On an ask the screen scrolls as one: the thing asked about, then the question, Notes and Attach. Approve, Send back
  and Snooze (or the ask's own buttons) sit in a bar pinned to the bottom, solid, above the keyboard when it is up.
  "✓ Approved · Undo" shows above that bar, never over it. An ask with nothing to show opens on its question.
- On a phone Send back is always on; with Notes empty it sends nothing and puts the cursor in Notes, "Say what to change".
- After an answer or Skip, the next item's buttons take no taps for 400 ms, so a second tap does not answer it unread.
- A pipeline shows one stage at a time.
- A pipeline has an Ask bar above the bottom bar, in place of the manager's rail: a line saying what goes with the
  message ("Product › Spec · 3 tasks on screen"), a button for the manager (picked first) and one for the stage's
  holder ("Spec · Product Owner"; none when the stage is held by whoever the task is for, once when it is the manager),
  and the box, "Ask Product Manager…". The message goes to that bot's direct chat and carries the pipeline, the stage
  and the ids of the tasks on screen (`refs.board`, at most 50, search and filter applied), shown as one card under it.
  The chat opens over the board, the chat alone; ✕, Escape or a swipe down puts it away.
- A card on a pipeline opens its task at its conversation, the box reading "Ask Builder about this task…"; Details
  shows the rest.

### S18. What goes away

- **The review queue screen,** with Mark met, Not validated and the two Message buttons. Its play button stays, as Catch
  up.
- **The status menu:** Open, Doing, Waiting, In review, Ready to ship, Declined. And the "Block task" dialog.
- **The Type and Step menus** on a task.
- **Words on rows and tiles:** the amber "Waiting" tag, "Blocked 7h", "Building 4317m", "70h in step", requirement dots.
- **Verdict chips** (Approved, Passed, Changes, Failed) and "QA waived".
- **Requirement words** Out of date, Not passed and Missing.
- **The "Approvals and stale checks" section** and the Approval, Stale check and Question pills.
- **The Done column** on a pipeline.
- **The Goals page's own list of asks.** It links to the tasks.
- **Bot states** "Waiting on a human", "Waiting on a bot" and "Blocked".

## Looking back: 31 failures

The list of 31 failure cases, each checked against this design. This is a check on paper. The fault rig turns each one
into a test in phase 1.

- **Prevented:** 19. It can't happen in v3 as written in draft 4.
- **Caught:** 1. It can start, and ends with a person inside about 3 hours.
- **Tightened:** 10. The case showed a gap, and this draft adds a rule for it. Each says what is new.
- **Visible:** 1. Allowed by decision, and shown.

### Live incidents, Nov 3 to 9

- **F1. Bots quarantined by the message cap.** Tightened. Bots don't escalate by message now. The check writes the
  manager's wake itself, so no message cap applies. New: no refusal about a task counts toward quarantine, caps and rules
  included. A quarantined bot's tasks say "Paused · quarantined" on the pipeline, and one row lands in a person's list.
- **F2. Held runs never resumed.** Tightened. The check doesn't wait for a run's report. New: a bot is "on it" only while
  its computer checks in, every 90 seconds. A run that is held, uncertain, cut off or lost is over, and the bot is woken
  again with "your last run was cut off, check what it did". Quarantine is still per bot, but it shows as Paused.
- **F3. Follow-up timers flooded the queue.** Prevented. There are no check-back wakes. A bot is woken only when it is
  its turn and it has no live run: 7 runs at most per visit. A task that waits on a person, or is owned by one, wakes
  nobody. There are no "Find why" tasks. At the end of the ladder the task rests with its bot, and the quiet-task rule
  tells the manager, in one batched wake.
- **F4. 173 tasks stranded in "doing".** Prevented. There is no "doing". A run that ends without one of the five endings
  is "stopped early", and the bot is woken again in 15 minutes (2 when its run was cut off).
- **F5. A stage whose owner never pulls work.** Caught. Bots don't pull. The engine wakes the stage's owner. An owner
  that does nothing gets 3 tries, then the manager, then the ladder rests. One wake per task, so 134 jobs can't queue. Post
  Release QA itself is dropped (D46).
- **F6. Asks that never leave.** Prevented. An ask belongs to one visit and ends when the task moves, goes back or
  closes. One question per bot per task, so no copies. "Reconnect" rows are read from the bot's state, one per cause, and
  go when the cause goes.
- **F7. The Writer woken on every release.** Tightened. In the engine a bot is woken once per visit. A release updates
  metadata and wakes nobody. New: the docs automation (D46) keeps one open task and adds later releases to it.
- **F8. The review gate refuses moves, and a second mover retries.** Prevented. One mover. Nothing retries a refused
  move. The bot finishes its work, and the engine moves the task when Checks pass is met.

### Audit findings

- **F9. Engineering task closed while Product waits.** Prevented. Linked work done needs Done. The Product task stays, it
  is its owner's turn, and the reason shows.
- **F10. Close held by a requirement.** Prevented. Close is never refused.
- **F11. Closed counted as shipped.** Prevented. Only Done counts as output.
- **F12. Handoffs form a loop.** Tightened. A pipeline whose handoffs loop can't be saved. New: sub-tasks nest at most 5
  deep (C20), so a bot can't put off the ladder by filing tasks forever.
- **F13. A stage with no requirements moves on when its linked task closed.** Tightened. New: a stage that files a task
  always carries Linked work done. The editor adds it and won't save without it.
- **F14. The linked task is deleted.** Tightened. New: a deleted task counts as Closed for whatever waits on it. The
  waiting task stays, and it is its owner's turn.
- **F15. Closing Product leaves Engineering open.** Prevented. Closing a task closes the open tasks it filed, with a
  note. One whose PR already merged finishes its release.
- **F16. A PR merged after the release was cut still counts.** Prevented. In a release looks at commits: the merge must
  be in the release's history. Time plays no part.
- **F17. Closed in Merge, and the PR still merges.** Tightened. Closing takes the PRs out of the queue in the same step.
  New: if GitHub merges one first, the task shows "PR merged anyway" and the manager bot is told once.
- **F18. A stale GitHub read overwrites a newer one.** Prevented. All metadata has a version. An older read can't replace a
  newer one.
- **F19. Person-owned PRs take a different path.** Prevented. Merging is the Merge stage's on-enter action, done by the
  engine. Who owns the task makes no difference.
- **F20. An answer lost while the manager was paused.** Prevented. An answer is saved on the task, not relayed as a
  message. A paused bot gets it in its next wake.
- **F21. Six clocks wake about one late task.** Prevented. One check, one clock. Late hands the task to the manager once
  per visit.
- **F22. Two clients move the same task.** Prevented. One writer at a time, and a version on every task. The second move
  is refused with "changed since you looked".
- **F23. Structure: no requirements, stages found by name, six movers.** Prevented. Every default work stage has an owner
  and a requirement. No code finds a stage by its name. Setup and restage go through `move()`. The database refuses any
  other writer.

### Holes the three spec reviews found

- **F24. Round 1's Engineering task satisfies round 2.** Prevented. Linked work done counts only tasks filed in this
  visit.
- **F25. Validate runs before production is updated.** Tightened. New: the Validate page must name the version
  production runs, and Jev checks it is at or after the task's release. If not: "not yet", nothing is filed, and the bot
  snoozes. This one leans on Jev reading a page, not on code.
- **F26. Moved back out of Merge, and the PR still merges.** Prevented. Same as F17. Moving back takes the PRs out of the
  queue in the same step.
- **F27. The reviewed commit and the merged commit differ.** Tightened. A PR is queued at the commit that was checked.
  C22 (Nov 12): a commit added after your OK merges on it only when it changes the same lines or Reviewer 2 OK'd it;
  the task shows "Changed after OK", and the manager bot is told once.
- **F28. Checks pass waits on Tico's own check run.** Prevented. It ignores them.
- **F29. A crash right after a move.** Prevented. The on-enter action is written in the same transaction as the move,
  keyed by the visit. It happens once or not at all, and a retry does nothing.
- **F30. A Builder pushes its own task past Review.** Visible. Allowed, by decision. It shows as "Pushed past" on the
  task, on the manager's rail with the reason, and in the factory's count. The manager bot is told each time. There is no
  automatic alarm for a pattern. A requirement can be switched to "can't be pushed past" or "People only" at any time.
- **F31. The old owner moves the task after a reassign.** Tightened. Reassign raises the task's version. New: it also
  drops the old owner's queued wakes, and a bot's command must carry the version from its wake. The old owner's move is
  refused with "changed since you looked".

Not checked: the audit's other findings (B-F2, B-F6, B-F9, B-F11, C-F5, C-F6, C-F8, C-F9, D-F4 to D-F7, A-F1 to A-F6).
The list names them without describing them, so they are not re-checked here.

## Things that don't obviously fit in a pipeline

How each one works on the engine.

- **A quick to-do for yourself.** A General task you own. It sits in Yours. Nobody chases it.
- **A question.** Not a task. It is a ❓ on the bot's chat message.
- **A bot needs a person to do real work.** A General task filed for that person. The bot's task waits on it.
- **A bot asking a bot.** A General task filed as a sub-task. The asking bot's task waits on it and is woken when it
  ends.
- **Recurring work.** A Routine files a new General task each time it runs.
- **Waiting on the outside world.** A vendor reply, a date, a long job: Snooze to a date with the reason.
- **A request made in chat.** Stays chat unless the bot files a task. If the bot hits a rule that needs your OK and has
  no task, Tico files one.
- **Work for "someone".** A General task with no assignee belongs to the General Manager, who assigns it.
- **Action items from a meeting.** General tasks.
- **Moving a task to another pipeline.** The same task moves, keeping its number and label, into the stage picked (the
  first by default) ([The moves](#the-moves)).
- **Goals.** Not tasks. A goal links to the tasks that serve it.
- **Private tasks.** Same engine. Who can see them doesn't change, except that entering a private pipeline adds its
  manager and the company's owner. The bot a pipeline names for a stage (its fixed owner bot, or a bot on its "One of
  several" list; never a person, a group, an "assignee" stage's assignee or the General Manager) reads a private task
  while it holds that stage, as its owner, and its managers with it. That read is not on Who can see, ends when the
  task moves on, and is a line on the task's history ("Reviewer 1 can read this while it holds Review"). A bot that
  reads only that way shares nothing: it adds no reader, gives the task to nobody, and what it files from the task is
  private with the task's readers, for itself or for someone who already reads the task. The filer keeps reading what
  it filed after it leaves the stage, as any requester does, and shares that no more than the task it came from. On a pipeline holding an open private task only a person changes who holds a stage.
  Anyone else a stage needs (a person asked for an OK, an assignee, the General Manager) must already
  read the task: until someone shares it, the task waits and the line names who can. Whoever files a private task
  into a pipeline is told, before Create, who reads it that way and, when a stage waits on an open PR, that its code
  and pull request go to GitHub: a line in the new-task window, under Private in Visible to, and in `tico task create`
  and its dry run. It never refuses the filing, and the task's history keeps what they were told
  (backend/private_readers.py).
- **Work that needs no code but was filed in Engineering.** Closed with a reason, or moved to General.
- **An idea you don't want yet.** Snooze it with no end date. It leaves the stage's count.

Nothing here needs a second system. The one thing left outside on purpose is chat.

## Tick list: every task and pipeline feature

Each feature that exists today, and where it goes.

### Kept: works as today, on the engine

- **D1.** Sub-tasks. Closing a parent closes its open sub-tasks. A parent waits on the sub-tasks it filed.
- **D2.** Snooze, with "no end date" added. The owner is woken once when it ends.
- **D3.** Due dates. Shown, and they set Late. No reminders.
- **D4.** Tags, private tasks, followers, who can see, pages, files, sessions, runs, the per-task model. 
- **D5.** "Done looks like" on a task. It moves under Details.
- **D6.** The daily cap on how many tasks one sender can give another.
- **D7.** Quarantine. A quarantined bot's tasks say Paused.
- **D8.** The General Manager bot. It becomes General's manager.
- **D9.** Jev checking pages, with a "what to check" line on each requirement. Jev only, no model picker.
- **D10.** GitHub check runs that mirror a task's requirements on its PR.
- **D11.** Factories and their output counts.
- **D12.** Waiting for a free worktree. It shows as Paused, with that reason.
- **D13.** The daily release hour and Release now. A routine on the Release Owner.

### Folded in: the engine or the check does it

- **D14.** Restarting a task whose bot stopped. Today: a 10-minute sweep, 3 resumes, then the manager. Now the ladder.
- **D15.** Reopening a task that waits on nothing. Today: hourly, after 2 hours. Can't happen now.
- **D16.** The stuck sweep that files a question task. Now the last step of the ladder, on the task.
- **D17.** Starting a bot's open task when it has a free turn. Now the check wakes whoever has the turn.
- **D18.** "Blocked over 4 hours", time-in-step alerts, backlog alerts, the manager's late list. Now Late.
- **D19.** Retrying a move that was refused for room. Now "the oldest waiting task takes the slot".
- **D20.** Follow-up times a bot sets on a task. Now Snooze to a date.
- **D21.** Waiting for a PR to merge or a release. In Engineering these are requirements. Elsewhere the task waits on the
  task that owns the PR, or snoozes.
- **D22.** Question tasks. Now a ❓ on the bot's chat.
- **D23.** Approvals for send, spend, publish, merge and the rest. Now an Approve ask on a task.
- **D24.** Reconnect, repo, settings and shared-blocker asks. Now one row per cause, read from the bot's state.
- **D25.** Status words: Open, Doing, Waiting, In review, Ready to ship, Declined. Now the state line. The database keeps
  open, done, closed.
- **D26.** Blocked, in all six causes. Now a named wait, or Snooze.
- **D27.** The requirement review queue. Now asks in Needs you.
- **D28.** Product to Engineering sync, filing on enter, the parent settling. Now the on-enter action and Linked work
  done.
- **D29.** Arming and re-arming the merge. Now the Merge stage's on-enter action, with the same limits.
- **D30.** The gate on entering Review. Now Checks pass on Build and Fixes.
- **D31.** The public roadmap. It reads the Product pipeline's stages directly.

### Dropped: on purpose

- **D32.** Asks that expire, go ahead on a default, or close after 7 days (C7).
- **D33.** Approvals withdrawn after 14 days, and held messages given up after 24 hours. An ask ends when you answer or
  the task moves on.
- **D34.** "Close 24 hours after done". Done stays Done.
- **D35.** Stale checks (C12).
- **D36.** The task roundup sent to each bot three times a day. A bot is woken for its own turn. The manager has its
  rail.
- **D37.** The loop flag for 20 status changes in a day. The ladder is bounded.
- **D38.** The allocator that gives a task to the least busy bot in a pool. One owner per stage.
- **D39.** Check pools, check rounds, and passing a check by timeout. That passed work nobody reviewed.
- **D40.** Review verdicts that route a task. A review is a page. Fixes comes next either way.
- **D41.** Another repo writer merging for the owner. The Merge stage's owner does it.
- **D42.** Moving a task back a stage when the merge queue drops it. It stays in Merge and says why.
- **D43.** "Only for label X" on a requirement, and skip tags.
- **D44.** Counting a bot's own test receipt as passing checks. Checks pass reads GitHub only.
- **D45.** "Started", "Blocked" and "Done" lines posted to the parent task. The parent shows the child's live state.
- **D46.** The QA wake after a release, and Post Release QA. The docs task becomes an automation on "release published".
  It keeps one open task and adds later releases to it.
- **D47.** "Still wanted?" asks. Already unused. Deleted.
- **D48.** Structured questions: choices, typed answers, "Recommended", Tico's pick and line (its hidden score stays),
  "Look again", 👍 and 👎 with their rules, and the Strategy Owner's duplicate merge. A question is a ❓ in the bot's
  chat.

### Where each old pipeline mover goes

- **Verdict routing on pass or fail** (verdict_moves): Review is a stage that leaves when the review is attached.
- **After green: next check or merge wake:** the Checks pass requirement and the check.
- **PR merged moves the task to the merge step from anywhere:** PR merged is a requirement on Merge. An early merge is
  just met when the task gets there.
- **Check timeout passes with no verdict:** dropped.
- **Timeout refiles to a free pool bot:** Late, then the manager.
- **Check filed per commit, superseded on a new one** (check_pools): a review page is tied to a commit. A new commit puts
  it back to Waiting.
- **Arm auto-merge, re-arm when stale** (merge_arm): Merge on-enter. 10 minutes, 3 tries.
- **Conflict or queue failure files a manager task:** the Builder's turn, with the reason. Late goes to the manager.
- **Merged catch-up over 14 days:** the 5-minute read.
- **Release found by the name "pending release"** (release_flow): In a release, checked by commit.
- **Release publish moves tasks on:** the check does, when the metadata arrives.
- **Wake rules on entering a step** (rules): `move()` wakes the owner once.
- **Direct writers** (pipeline_setup, task_blocked, hubdb retire_held_steps, shared_bots, settings_admin): through
  `move()`, or deleted.

## Roadmap

Built directly, not through the Tico factory. Every change is reviewed by Codex with this spec in hand. Order: General,
Product, Engineering. The checks for each phase are in [Pipelines v3: validation](pipelines-v3-validation.md).

### Phase 0: sign-offs, then "go"

A, B and C are approved. This page is the build spec in the repo, and the checks below are in the validation doc.

### Phase 1: the engine and General

- **Builds:** the tables and the database trigger. `move()`. `waiting_on()`. The never-stuck check with the ladder, Late
  and Paused. Snooze. Asks. The General preset. The tico task commands. Screens S1 to S6 and S13 to
  S17. The fault rig.
- **Deletes, in the same phase:** every other writer of stage, owner and status, the 17 upkeep files, question tasks,
  approval objects, stale checks, and the old pipeline movers. Product and Engineering pipelines don't run on main until
  phases 2 and 3.
- **Done when:** the rig is green on 10,000 seeded runs. The lint test and the trigger show one writer. The new Tico Team
  org exists and runs General on 0.6.0-rc.1. The old Tico Team stops taking releases.

### Phase 2: Product

- **Builds:** the settings record and editor (S11). Stage owners, limits, by-hand stages. The page requirement with Jev,
  then a person, with versions and send back. Push-past switches. Links, "file a linked task", Linked work done.
  Pipeline views, stage and pipeline states, the manager rail, factories (S7 to S10, S12).
- **Done when:** the rig passes its page, limit, by-hand and handoff scenarios. One real idea goes from Ideas to Done on
  the new Tico Team, with In development filing a General task for the Builder.

### Phase 3: Engineering

- **Builds:** PR and release metadata, the 5-minute read, webhook de-duplication. PR open, Checks pass, PR merged, In a
  release. Many PRs per task. Merge with queue and dequeue. The Engineering preset. Product hands off into Engineering.
- **Done when:** the rig passes with a fake GitHub that loses, delays and repeats webhooks and drops PRs from the queue.
  One real PR goes through Merge and Release. **0.6.0 is published.**

### Phase 4: proof, then rebuild the orgs

- 10 real tasks go from idea to Done on the new Tico Team with zero hand database fixes. A hand fix is a bug: it gets
  fixed and the count starts again.
- "Waiting on nothing" on Health reads 0 the whole time.
- Then the other orgs are rebuilt from scratch on 0.6.x.

## Proof

### The fault rig

A simulator that runs the real engine against a fake clock, fake bots and a fake GitHub. Each run takes a seed, so any
failure can be replayed exactly. It runs in CI, not on your machine.

- **Fake bots** finish, stop early, comment and stop, crash mid-run, ask, snooze, file sub-tasks, or never answer.
- **Faults:** a crash between any two statements of a move. Lost, late, repeated and out-of-order webhooks. Wakes that
  vanish. Bots paused, quarantined or out of credit. Two movers at once. Clock jumps.
- **After every step** it checks the seven [rules that hold at all times](#rules-that-hold-at-all-times).
- **At the end of every run** it stops the faults and checks that every task reaches Done, Closed, Snoozed, a by-hand
  stage or a person's turn. Nothing may be left waiting on a bot forever.
- **It counts runs.** No task may use more than 7 bot runs without a change before a person is asked.

### Scenarios the rig and the spot checks must pass (49)

Numbered in order so they can be cited (scenario 1 to 49).

1. A bot stops early with nothing changed: woken at 15 min, 15 min and 50 min, then the manager, then the ladder rests
   and the quiet-task rule tells the manager. At most 7 runs.
2. A bot comments "working on it" and stops: counts as stopped early.
3. A wake is lost: the next pass queues it again. Never two.
4. A crash at any point inside a move: after restart the task is fully moved or not moved. The on-enter action happened
   once.
5. Two clients move the same task: the second is refused with "changed since you looked".
6. A direct database write to a task's stage: refused by the trigger.
7. A bot with 6 tasks runs out of credit: 6 tasks say Paused, and one row is in a person's list.
8. A person answers while the bot is paused: the answer is delivered when the bot can run.
9. 11 refusals about tasks for one bot (requirements, room, caps, rules): it is not quarantined.
10. A bot snoozes with no end date: allowed. The task shows On ice, the manager bot is told once, and it is on the
    manager's rail.
11. A snoozed task in a full stage: its slot goes to the oldest waiting task.
12. A bot tries to close a person's request: refused, told to ask instead.
13. A held action with no task: a General task is filed for the person.
14. A person sends a page back: the task stays in its stage, the bot gets the notes word for word, version 2 comes back
    marked with what changed.
15. A bot pushes a task past Review with a reason: it moves, shows Pushed past, and the manager is told once.
16. A bot tries to push past a people-only OK: refused.
17. Anyone tries to push past PR merged: refused, offered Move back and Close.
18. A task is dragged two stages ahead: every skipped requirement is recorded as pushed past, with one reason.
19. A new org gets the presets: every work stage has an owner and at least one requirement. On deck has none. Tasks leave
    Ideas by hand, except a person's request in Product.
20. Spec empties while Ideas holds tasks: a person's request goes in by itself; else the Product Owner is woken once.
21. Close a Product task in Spec with the spec unmet: it closes at once with the reason.
22. Close a Product task in a factory: the Done count doesn't change.
23. Close the Engineering task of a Product task in In development: Product shows it closed with the reason, and it is
    the Product Owner's turn.
24. Close a Product task that has an open Engineering task: that task closes with a note. One whose PR merged stays open.
25. Save a pipeline where A files into B and B into A: refused with "would loop".
26. Not validated four times in a row: four new Engineering tasks. Nothing stops a fifth.
27. Production runs an older version than the release: Jev says "not yet". Nothing is filed.
28. A new commit lands during Review: the review goes back to Waiting on the reviewer.
29. A task has two PRs and one merges: it stays in Merge. Both merge: it moves.
30. The merge queue drops a PR: the task stays in Merge, and it is the Release Owner's turn with the reason.
31. Close an Engineering task in Merge: its PRs leave the queue and never merge.
32. A stale PR read arrives after a newer one: the recorded commit doesn't change.
33. A PR merges after a release is cut: the task stays in Release until a release that contains the merge.
34. A repo with no checks: Checks pass is met after 10 minutes, with the note.
35. A run hits the 60-minute limit with new commits: the bot is woken again and no try is counted.
36. A run is held or cut off: it doesn't count as on it. A new wake goes out with "your last run was cut off".
37. A bot's fourth snooze of a task in one stage: the task goes to the manager bot.
38. A sub-task six levels deep: refused.
39. A task is reassigned while the old owner is running: the old owner's next command is refused, and its queued wake is
    dropped.
40. A bot asks a question (a ❓) and the checks then pass: the task moves on as its requirements say. The ❓ stays in the
    person's Needs you until they reply.
41. A task with an open ask moves back: the ask leaves the person's list in the same step.
42. A bot @s a person in the conversation and stops: it is still the bot's turn. The ladder runs as for any early stop.
43. A person @s another person on a task: it is in their Needs you until they reply. The task's turn doesn't change.
44. Someone puts a person's name on Checks pass: refused, offered a sub-task.
45. A person adds "OK from Steven" to one task: it is in Steven's list, the task stays in its stage, and it ends when the
    task leaves.
46. A bot marks a second ❓ in the same chat: the first is replaced. Never two from one bot in one chat.
47. A closed task's PR merges anyway: the task shows it and the manager bot is told once.
48. A commit lands after the person's OK: a rebase with the same lines keeps the OK; other changes are read by
    the last reviewer, whose OK keeps it and whose send back asks the person again after the next read. The task shows
    "Changed after OK".
49. A linked task is deleted: the waiting task treats it as Closed.

### Ten real tasks

On the new Tico Team, ten real product ideas go from Ideas to Done: spec approved by you, built, reviewed twice, review
page approved by you, merged, released, checked in production. Zero hand database fixes. Every time you are asked, it is
in Needs you with the thing shown and buttons that work.

### In production, for good

The same checker runs every hour on every org and shows on Health (S15). Any task found waiting on nothing is a bug
report, filed by Tico against itself.
