# Propose a hire

Triggered when the weekly page finds growth work with no owner for the second week running, or when the owner asks "what should we add to growth?". Budget 15 minutes. The outcome is one specific proposal on the task: the role, why, and its first routine. Creating a bot goes through BotOps only when the task asks for it and a granted Tool allows it.

---

## 1. Find the evidence

List the recurring work nobody owns, from the last three to four weeks: tasks routed to a human because no role fits (`hub task list`), leads that waited more than a day, proposals or questionnaires written by hand, renewals noticed late, a report nobody writes. Count the occurrences and the hours if the tasks say. One-off work is not a reason to hire.

## 2. Match a template

    hub template list
    hub team show

Pick the one template whose owned outcome covers that work, from the marketing and sales templates. If one already exists in the team, the problem is routing or load, not hiring: say so instead.

## 3. Propose

On the task, in five lines: the template and its name; the evidence with counts and dates; the first routine it would run (from its card) and when; who it reports to (you); what it will need connected. Ask with `hub task ask` only when the intended role or owner is missing; otherwise carry out the requested work with your Tools.

## 4. Carry out the requested work

    hub task create --owner botops --title "Set up <template>" --body "<why, first routine, reports to growth-team-manager, requested on task <id>>"

Record the proposal, the evidence and the decision in `knowledge/hiring.md`. On a no, record the reason so the same proposal is not made again without new evidence.
