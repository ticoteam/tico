# Propose a hire

Triggered when the weekly summary finds sales work with no owner for the second week running, or when
the owner asks "what should we add to sales?". Budget 15 minutes. The outcome is one specific proposal
on the task: the role, why, and its first routine. Create requested bots through BotOps with your Tools.

---

## 1. Find the evidence

List the recurring work nobody owns, from the last three to four weeks: tasks routed to a human because
no role fits (`tico task list`), leads that waited more than a day, proposals or questionnaires written by
hand, renewals noticed late, a CRM report nobody writes. Count the occurrences and the hours if the tasks
say. One-off work is not a reason to hire.

## 2. Match a template

    tico template list
    tico team show --team sales

Pick the one template from `team_templates` whose owned outcome covers that work: SDR for unanswered
leads, Account Executive for deals without next steps, Account Manager for renewals and expansion, Sales
Operations Manager for data and forecast, Sales Engineer for technical questions, Partnerships Manager for
partner deals, Sales Enablement Manager for ramp and win/loss. If it already exists, the problem is
routing or load, not hiring: say so instead.

## 3. Propose

On the task, in five lines: the template and its name; the evidence with counts and dates; the first
routine it would run (from its card) and when; who it reports to (you); what it will need connected.
Ask with `tico task ask <id> --to <person> "<question>" --version N` only when the intended role or owner is missing; otherwise carry out the requested work with your Tools.

## 4. Carry out the requested work

    tico task create --owner botops --title "Set up <template>" --body "<why, first routine, reports to sales-lead, requested on task <id>>"

Record the proposal, the evidence and the decision in `knowledge/hiring.md`. On a no, record the reason
so the same proposal is not made again without new evidence.
