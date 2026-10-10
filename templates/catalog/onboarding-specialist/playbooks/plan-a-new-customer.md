# Plan a new customer

Triggered by a task naming a newly signed customer, or by the board finding one. Budget 20 minutes.
The outcome is `knowledge/customers/<customer>.md` and a kickoff agenda on the task.

---

## 1. Read the handoff

The deal record (CRM read, or the task), the order form or contract attached to it, and any sales
calls: `hub meeting search "<customer>"`. Write down, each with its source: who signed, who will use
the product, what they are replacing, what they said success looks like, and every promise made
(dates, integrations, training, data moves).

## 2. Size it and date it

Pick small, mid-size or large from `knowledge/milestones.md`. Set a target date for each standard
milestone from the start date. Add a milestone only if the deal promised something the standard list
lacks. First value gets the earliest date you can defend.

## 3. Name owners on both sides

For each milestone, who does it at {{company_name}} and who at the customer. Unknown is a gap to fill
at the kickoff, not a guess.

## 4. Draft the kickoff agenda

Thirty to forty-five minutes: their goals first (read back in their words), the plan and dates, what
we need from them and by when, how updates will reach them, and questions. Attach it to the task for
whoever runs the call. Send requested invitations with your Tools when a person has turned mail sending on in Tico; otherwise keep drafts.

## 5. Hand over

Save the plan, attach it and the agenda to the task, and ask one question if a promise cannot be met
as sold. Commit, and `hub task update <id> --status done --note`.
