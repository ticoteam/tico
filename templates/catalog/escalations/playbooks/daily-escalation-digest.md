# Daily escalation digest

Schedule: weekdays at 08:30 team time (routine `daily-escalation-digest`), after setup. Budget 25 minutes.

Inputs: `knowledge/register.md`, `knowledge/escalation-rules.md`, `knowledge/escalation-practice.md`, the case files in
`knowledge/cases/`, the escalation tasks from `hub task list`, the ticket threads and engineering issues you can read, and the
case owners' notes.

Outcome: one page for the people who own escalations, in the shape of `knowledge/examples/escalation-digest.md`: the count by
severity, updates due today with the text ready, waiting cases with their dates, engineering status for each case, and cases
ready to close with their lesson. Nothing is sent.

Done when: every open case is in the digest with an owner and a next update date; every update due today has its draft on the
task for review; overdue updates are named to their owner; the digest is published; and the task note gives the counts and the path.

---

## 1. Refresh the register

Read `knowledge/register.md`. Add new escalations from `hub task list` (anything matching a trigger in
`knowledge/escalation-rules.md`) by following `playbooks/open-an-escalation.md`.

## 2. Read each open case

For each case: the ticket thread (mail read or the task), the engineering issue (read only), calls
(`hub meeting search "<customer>"`) and the case owner's notes. Append new facts to the case timeline with time and source.

## 3. Work out what is due

- The next customer update: last update time plus the severity's cadence. Due today or overdue goes to the top.
- Engineering: acknowledged or not, last movement, the next step they named.
- Cases where the customer confirmed the fix: ready to close.

## 4. Prepare the updates

For each update due, follow `skills/write-a-customer-update/SKILL.md`: the exact text, what happened since the last update,
what is next, and when they will hear again. Name the recipient and the case owner. A defect going to engineering follows
`skills/write-an-engineering-bug-report/SKILL.md`.

## 5. Write and hand over

Write `reports/YYYY-MM-DD-escalations.md` in the shape of `knowledge/examples/escalation-digest.md`, then
`hub file publish reports/YYYY-MM-DD-escalations.md`. Put each update on the task as its own item for review. Tell an owner
whose update is overdue with `hub message send <person> "<case, due time, link>"`. For closed cases, add the lesson to
`knowledge/lessons.md`. Commit, then `hub task update <id> --status done --note "..."` with the counts and the path.
