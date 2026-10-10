# Accountant request

Triggered by a task carrying the accountant's request for documents or answers (a year-end list, a
quarterly checklist, a notice to respond to). Budget 30 minutes. The outcome is a prepared package and
a list of what is still missing, to send with Tools when a person has turned mail sending on in Tico.

---

## 1. Read the request

    hub task show <id>

List every item asked for, with its period and the deadline. An item you do not understand is a
question for the requester, not a guess.

## 2. Gather

For each item, find the team's own record: reports from the finance bots (`hub file list --bot
<slug>`), exports attached to earlier tasks, `hub doc search "<item>"`. Note the file and its date.
Never produce a figure the records do not contain.

## 3. Check

Periods match what was asked; totals agree between documents (sales in the export equal sales in the
books); every contractor form is present. List each mismatch.

## 4. Hand over

On the task: the package as a list (item, file, period, status), the mismatches, and what is still
missing with who owes it. Send requested packages to the accountant with your Tools when a person has turned mail sending on in Tico; otherwise keep the draft. Record the request and its deadline in `knowledge/tax-calendar.md`
so next year's request starts from this one.
