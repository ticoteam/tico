# Acting on your own (the Strategy Bot, level 3)

Only when `hub goal strategy-bot` says `level` 3 ("Acts on its own"), which only the owner chooses. Below it, the server
refuses every decision: at level 2 a red goal gets a recovery proposal (`playbooks/recoveries.md`), at Passive a
check-in question at most. Read the level at the start of every run; the owner may have lowered it since.

Inputs: `hub goal strategy-bot` (the level and `asked_today`), `hub goal list --all`, `hub health check`, the Heads' reports and `state.md`.

A **decision** is one reason and 1 to 10 changes to bot tasks, made together and logged with an Undo for the owner:
`hub goal strategy-bot-decide "<reason>" '<changes json>' --goal-id <goal>`. The changes are:
- `{"do": "create", "owner": "<bot slug>", "title": "...", "body": "..."}`: a new task for a bot, on the goal;
- `{"do": "reorder", "task_id": "..."}`: a bot's task to the top of its queue;
- `{"do": "reassign", "task_id": "...", "owner": "<bot slug>"}`: a bot's task to another bot;
- `{"do": "close", "task_id": "...", "note": "why"}`: a bot's task closed.

The reason is one sentence the owner reads in the Strategy Bot log: what you chose and the goal it serves. Always pass
`--goal-id` when a goal is behind it. The answer is your receipt: what was `done`, and what was `left` and why. A part
the server would not let you do alone (a person's task, a bot that is paused or offline, a private task, a bot you
may not give work to) is not dropped: it is one proposal in the owner's Needs you, filed by the server. Don't file it
again yourself and don't try it another way. Note each decision's id, goal and reason in `state.md`: you can't read
the log (it is the owner's), and the Monday note and your next run need them.

## A red or stalled goal
In the run that finds it (the KPI pass's status pass, a task or message about the goal), not later.
1. Read the goal and what is behind it, from the record:
   - `hub goal show <id>`: owner, KPIs with target and colour, the reason line, tasks, recent check-ins;
   - `hub kpi show <kpi>` for each KPI on it: the last readings and whether one is stale or missing;
   - the Heads' reports: for each Head with tasks on the goal (`hub team show`, a bot that reports to you on the
     chart), its newest report (`hub file list --bot <head>`) and its notes on the goal's tasks (`hub task show`);
   - Health: `hub health check`. A bot the goal waits on that is broken is a BotOps ask (below), not a new task.
2. Decide what gets the goal moving, using bot tasks only: create at least one task for the bot that owns that work,
   or move its most useful open task to the top of its queue. Reassign or close only where a bot's task is plainly in
   the wrong queue or no longer serves a current goal. A person's task is theirs: leave it out.
3. Look before you act: if `state.md` has a decision for this goal since it last turned red, don't repeat it; one
   decision per goal per time it goes red or stalls. The owner's Undo stands: a change they undid in an earlier
   decision (the same new task for the same bot, or the same reorder, reassign or close of a task) is never made
   alone again. The server makes it a proposal and your answer's `left` says "the owner undid this same change".
   Don't try it another way: say in the goal comment what you would do and leave it to them.
4. Make it: `hub goal strategy-bot-decide "<reason>" '<changes>' --goal-id <id>`.
5. Post one comment on the goal, in your own name: what you did and why, in two or three sentences, with the numbers
   that made you act: `hub goal checkin <id> "<what you did and why>"`. No `--signal` and no `--from`: it shows
   under your name, it is never the owner's words, and it must not colour the goal. One comment per decision.

## A clash between two Heads' tasks
Two Heads' tasks clash when both can't go ahead as they stand: they pull one bot or one resource two ways, one undoes
what the other builds, or their order decides which goal moves first. Settle it only when both tasks serve current
goals (`goal_id` on each task, a goal that is live in `hub goal list --all`). A task with no goal, or a goal that is
done or dropped, is not yours to rank: leave it to its Head.
1. Choose by the goals: the task serving the goal that is further behind, or ranked higher by its owner, goes first.
   Say which goal the choice serves.
2. Make it one decision with `--goal-id` set to that goal and a reason that names the choice:
   `"<first task> goes before <other task>: it serves <goal>, which is red"`. Usually one `reorder` of the first task;
   `close` the other only when it can no longer serve a current goal, with a note saying so.
3. Tell both Heads, once each, with the same words: what you chose, the goal it serves, and that the owner can undo
   it in the Strategy Bot log. `hub message send <head> "<text>" --ref task:<first> --ref task:<other>`. One message per
   Head, never one per task, and none to a person: a person's task is never in a clash you settle.

## A bot's question to you
Bots message you in your goal work. Answer a question yourself only when a recorded rule or decision covers it, and
cite it. Look in these, in order, and nowhere else:
1. **Decisions**: `memory/decisions.md` in this repository (the Decisions view on your page). Another bot's memory
   is not yours to read: a decision only the asking bot recorded is not a source for you.
2. **Team rules**: the working rules at the top of your `memory/learnings.md`; the team's docs the owner wrote down
   as a rule or policy (`hub doc search`, or `hub doc ask` for the Librarian's cited answer, only when it says
   `covered`).
3. **Approved proposals**: `hub proposal list --status confirmed`: a target, a Why, a recovery the owner approved.

Covered: `hub question answer <message-id> "<the answer>. Per <source>: <quoted line, with its date>"`. Quote the
source; never stretch it to a case it does not name.
Not covered, or two sources disagree: you don't decide it. Answer
`hub question answer <message-id> "No recorded rule or decision covers this; ask the owner." --unknown "the owner's
decision"`, so the bot asks the person itself, on its own task. Never guess, never answer as the owner, and never
pass a question off as decided because it looks like an old one. When several bots then ask the owner the same
thing, merge them (`playbooks/recoveries.md`).

## A broken bot
`hub health check` lists what is wrong with the bots, most urgent first, each with its fix. When one a current goal
waits on is broken (failing runs, stuck, offline, out of credit), ask BotOps to fix or restart it: one task,
`hub task create --owner botops --title "Fix <bot>: <the problem in a few words>" --body "<what Health says, the goal
waiting on it, and what you need back>"`, after `hub task list --owner botops` shows there is not one open already.
Don't pause, resume, change or restart a bot yourself. Anything the fix needs that costs more (a model, a routine,
waking more often) or changes a bot's instructions stays the owner's: the server turns it into a proposal, and
BotOps says so on the task.

## What stays the owner's
At every level: anything that raises cost, publishing, sending outside the company, adding or removing bots,
changing a goal or a KPI target, the vision or the description, a bot's instructions or settings, and work for a
person. A change to a goal's words, a KPI or a target is a proposal you file (`hub proposal create`,
`playbooks/README.md`). Work for a person goes in a decision as a `create` (or `reassign`) with the person as owner:
the server never makes it and files it as a proposal in the owner's Needs you. Never give a person work with
`hub task create --owner <person>`: that command makes the task, so it is only for a check-in question
(`playbooks/check-ins.md`). For the rest, the server refuses your try and files it as the proposal. Never try one
another way. A proposal counts toward the owner's 5 a day.

**Done when:** each red or stalled goal in this run has one decision with its comment, or a reason it was left alone; each clash is settled and both Heads are told; and `state.md` has each decision's id.
