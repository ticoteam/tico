# Propose a hire

Triggered when the weekly summary finds recurring finance work with no owner, or when the owner asks "should we add a bot
for this?". Budget 15 minutes. You never create a bot.

Inputs: the requests or tasks of the same kind in the last 30 days, `hub template list`, and `hub team show`.

Outcome: one proposal on the task in five lines. For a requested hire, a BotOps task as well.

Done when: the proposal is on the task, the decision is in `memory/decisions.md`, and for a requested hire the BotOps task
exists. A no is recorded with its reason.

---

## 1. Establish the gap

List the evidence: the requests or tasks of the same kind in the last 30 days with their dates, how long each waited, and
what slipped because of it (a close day, a late invoice, a missed receipt). Fewer than three occurrences, or one late week,
is not a gap; note it in `memory/learnings.md` and stop.

## 2. Pick the role

    hub template list
    hub team show

Choose the one template in `team_templates` whose card owns that work, and check it is not already in the team. Examples:
invoices issued late or wrong goes to `billing`; bills paid twice or late to `accounts-payable`; expense reports sitting
unreviewed to `expense-auditor`; payroll corrections after the run to `payroll`; a filing missed or nearly missed to `tax`;
deferred revenue done by hand at close to `revenue-accountant`. Work outside finance goes to that group's head as a note,
not a proposal.

## 3. Write the proposal

On the task, five lines:
- **Work**: what recurs, how often.
- **Evidence**: the dated tasks or reports.
- **Role**: the template's name and id.
- **First routine**: its card's `first_routine` title and cadence, and what it would have produced last month.
- **Reports to**: finance-lead.

## 4. Carry out the requested hire

Only when the owner asked for the hire and your Tools allow it, create the BotOps task:

    hub task create --owner botops --title "Set up <template>" --body "<the five lines>" --parent <id>

Record the decision in `memory/decisions.md`, add the role to `knowledge/team.md` as "requested", and route its work to it
once it appears in `hub team show`. On a no, record the reason and do not propose the same role for 60 days unless the
evidence doubles.
