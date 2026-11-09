# Weekly legal summary

Schedule: Mondays at 08:30 team time (routine `weekly-legal-summary`), after setup. Also run by hand. Budget 30 minutes. The outcome is one page for the owner: what is open, what
is due, which decisions are theirs, and who should take what next. A summary for a human, not legal advice.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/requests.md`, `knowledge/escalation.md` and last week's summary in `reports/`. If last week
named a decision for the owner, check whether it was made; say so in the first section.

## 2. Collect the week's requests

`hub task list --status open --status doing --status waiting` and any new task that is legal in kind (a
contract, an NDA, a notice, a privacy or employment question). Add each new one to `knowledge/requests.md`
with its kind, urgency (a date or "none"), risk in one line and proposed owner. Close rows whose task is done.

## 3. Read the legal team

For each legal bot in `hub team show`: `hub update list --bot <slug>` and its newest `reports/` file. Take:
- from `legal-review`: contracts waiting for a human and notice deadlines inside 30 days;
- from `compliance`: filings and renewals inside 30 days, and anything overdue;
- from `privacy`: open data subject requests and their response dates;
- from `legal-ops`, `paralegal`, `ip-paralegal`, `corporate-secretary`: blocked work and deadlines.
Where a bot is absent, read the same from `knowledge/obligations.md` and the open tasks, and say which.

## 4. Pick the owner's decisions

Two or three at most, in this order: anything with a deadline inside 7 days; anything `knowledge/escalation.md`
sends to a lawyer; a contract or policy that has waited on a human more than 10 days. For each: the decision in
one line, the date, the options as the record states them, and the lawyer to call if it needs one.

## 5. Routing and hiring proposals

For each request with no owner: the owner from the team lines in `AGENT.md`, with the reason. Create tasks for requested follow-through with your Tools. If one kind of request recurred with no bot to take it (three or more in four weeks), add a hiring
proposal as `AGENT.md` "Hiring" describes; it is asked on the task, never acted on here.

## 6. Write and hand over

Write `reports/YYYY-MM-DD-legal-summary.md` in the shape of `knowledge/examples/legal-summary.md`, ending with
the not-legal-advice line, then `hub file publish reports/YYYY-MM-DD-legal-summary.md`. Commit, and `hub task
update <id> --status done --note`: the headline, the path, the sources you could not read.

## When a source fails

Name it and what is therefore unknown, and keep going. A deadline you could not confirm is listed as
"date not confirmed", never dropped.

## Done when
- Every open request in `knowledge/requests.md` has a status, an owner and a sourced deadline, or "not confirmed".
- The summary's first line gives the count of open requests and the decisions that are the owner's.
- The report is published, the task is closed with the headline and the path, and the sources you could not read are named.
