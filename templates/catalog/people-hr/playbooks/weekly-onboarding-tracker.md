# Weekly onboarding tracker

Schedule: Mondays at 09:00 team time (routine `weekly-onboarding-tracker`), after setup. Also run by hand when a hire is confirmed. Budget 30 minutes. The outcome is one page:
for each person starting in the next 30 days, where the checklist stands, what is late and who owns it.
Send requested messages to the intended new hire with your Tools.

---

## 1. Read where things stand

    hub task show <id>
    hub team show
    hub calendar list

Then `knowledge/onboarding-base.md`, `knowledge/hand-offs.md`, last week's tracker and the open tasks
labelled for onboarding (`hub task list --status open --status doing --status waiting`).

## 2. Find who is starting

People with a start date in the next 30 days and people in their first 90 days. Use a first name or a
reference, a role, a start date and a manager. Nothing else about the person goes in a file. If a start date
is unknown it is "needs a date", not a guess.

## 3. Build or update each checklist

Start from `knowledge/onboarding-base.md` and cut what does not apply. Order by when it must be true:
- **Before day one (two to four weeks out):** equipment ordered, accounts and access requested, paperwork
  sent by whoever owns it, a buddy named, the manager's first-week plan drafted, a welcome message drafted.
- **Day one:** who greets them, tools working, a walkthrough of the handbook pages that matter, first lunch.
- **First week:** role and expectations conversation, the questions for the manager about 30, 60 and 90 day
  goals, introductions, an end-of-week check-in.
- **30, 60 and 90 days:** a check-in booked with the manager each time, and a short survey at 90.
Each item: what, owner role, due date. Five to nine items per stage; the rest in an appendix.

## 4. Mark status

For each item say done (with the record), open, or late (past its date, with the owner). Mark nothing
done without a dated record or the owner's word on the task. Late items go at the top.

## 5. Handbook gaps

Note any question a new hire will need answered that the handbook does not cover, and send it to
the Librarian as one task (`hub task create --owner librarian`).

## 6. Write the page and hand it over

Write `reports/YYYY-MM-DD-onboarding-tracker.md` in the shape of `knowledge/examples/onboarding-tracker.md`.

    hub file publish reports/YYYY-MM-DD-onboarding-tracker.md

Write and send requested welcome messages with the exact text and recipient using your Tools; messages to outsiders stay drafts until a person turns mail sending on in Tico. Chase each late item's owner with one
line on its task. Then
`hub task update <id> --status done --note`: the headline, counts (late, due), what you could not read.

## When a source fails

If the roster or calendar cannot be read, say so and list only what people gave you on tasks. A missing
start date is never inferred from a calendar guess.
