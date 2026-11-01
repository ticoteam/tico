# Propose a new people bot

Triggered from the weekly summary, or by a task, when recurring people work has no owner. Budget 15 minutes.

Inputs: the unowned requests of one kind from the last 30 days (`hub task list`, the routing log); `hub template list`;
`hub team show`; and the card of the candidate template.

Outcome: one specific proposal the owner can say yes or no to. When the work requests it, one task for BotOps as well.

Done when: the proposal is on the task with its evidence, the decision (yes or no) is in `memory/decisions.md`, and, on a yes,
the bot is added to `knowledge/team.md` and `knowledge/routing.md` once BotOps confirms it exists.

---

## 1. Show the work is recurring

Count the unowned requests of one kind in the last 30 days and name three with dates. One-off work is not a reason to add a
bot; route it to a human.

## 2. Pick the template

    hub template list
    hub team show

Choose one template from `team_templates` in this bot's card that owns exactly that work and is not already in `hub team show`.
Read its card: its summary, its first routine and what it needs to start.

## 3. Propose

On the task, in five lines: the recurring work and how often; the evidence; the template and its name; its first routine as
the card states it; and that it reports to you and starts parked until its own setup. For requested hires, create the BotOps
task below directly. Ask only if the role or scope is missing.

## 4. Carry out the requested hire

    hub task create --owner botops --title "Set up <template>" --body "<why, the evidence, its first routine, reports to people-lead>"

Record the decision in `memory/decisions.md`. On a no, record why, so the same proposal is not made again next week.
