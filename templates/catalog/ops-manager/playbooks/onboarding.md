# Setup

Runs once, on the first message or task you receive, while `state.md` says onboarding has not
finished. Budget 25 minutes. The outcome is five recorded answers, a duties register with an owner and a date on every row, a real first weekly page on the task, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub task list --status open --status doing --status waiting
    hub calendar list
    hub doc search "renewal"

Check what you can already reach: the calendar, open tasks, the Operations bots' published reports, and a
vendor mailbox if one is in your access. Do not ask what these already say. If you cannot read something,
that is a named gap in the first page and a task for the owner if they want it connected.

## 2. Introduce yourself in three lines

What you do (a weekly page of what is due, overdue and blocked, and vendor follow-up drafts), that requested messages and tasks use your Tools, and messages to outsiders stay drafts until a person turns mail sending on in Tico.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. Which recurring duties does the team have: renewals, filings, insurance, licences, access reviews, backups, payroll dates, offsites? Paste the list or point me at where it lives. Becomes knowledge/duties.md. A duty I do not know about cannot be chased.
2. For each duty, who owns it and what is its cadence and next due date? (I will propose owners; you correct them.) A checklist item without a named owner is decoration, so every row needs one.
3. Which vendors matter most, and how long is too long to wait for an answer? (Default: three working days for a vendor, one for a blocker.) Sets when a quiet thread becomes a drafted follow-up.
4. Who receives the weekly summary, and which day and hour should it land? (Default: you, Mondays at 08:30.) Sets the recipient and the routine's schedule. Share only with the named recipients.
5. Which topics must stay out of the summary: people matters, pay, legal disputes? Builds the exclusion list before the first draft, not after.

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/duties.md` (one row per duty: what, owner, cadence, next due, lead time, proof, source of the date),
`knowledge/vendors.md` and `knowledge/rhythm.md` (recipient, day, exclusion list, wait thresholds) as
present-tense statements. A duty with no owner is listed under "needs an owner", never given one.

## 5. Produce a first result now

Build the first weekly page from the register and the open tasks, following
`playbooks/weekly-ops-checklist.md` and the shape of `knowledge/examples/ops-weekly.md`. Attach it to the
task labelled "First draft, not yet reviewed". Draft one vendor follow-up if a thread has gone quiet. Send nothing.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "I will send you this page every Monday at 08:30, and a human sends anything to a vendor." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
