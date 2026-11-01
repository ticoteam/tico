# Accountant request

Triggered by a task carrying the accountant's request for documents or answers: a year-end list, a quarterly checklist, or a
notice to respond to. Budget 30 minutes.

Inputs: the request on the task, the team's own records (finance bot reports, exports attached to earlier tasks, team docs),
and `knowledge/tax-calendar.md`.

Outcome: a prepared package and a list of what is still missing. The package is a draft on the task.

Done when: every item asked for is in the package or named as missing with its owner, the mismatches are listed, and the
request and its deadline are recorded in `knowledge/tax-calendar.md`.

---

## 1. Read the request

    hub task show <id>

List every item asked for, with its period and the deadline. An item you do not understand is a question for the requester,
not a guess.

## 2. Gather

For each item, find the team's own record: reports from the finance bots (`hub file list --bot <slug>`), exports attached to
earlier tasks, `hub doc search "<item>"`. Note the file and its date. Never produce a figure the records do not contain.

## 3. Check

Periods match what was asked; totals agree between documents (sales in the export equal sales in the books); every contractor
form is present. List each mismatch.

## 4. Hand over

On the task: the package as a list (item, file, period, status), the mismatches, and what is still missing with who owes it.
The package goes to the accountant only when the task asks for it, mail sending is on in Tico, and a granted Tool allows it.
Otherwise it stays a draft. Record the request and its deadline in `knowledge/tax-calendar.md` so next year's request starts
from this one.
