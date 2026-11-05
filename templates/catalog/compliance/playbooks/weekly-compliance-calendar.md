# Weekly compliance calendar

Schedule: Tuesdays at 09:00 team time (routine `weekly-compliance-calendar`), after setup. Also run by hand. Budget 25 minutes. The outcome is one page: what is overdue, what is due, who
files it, and what still has no proof. A calendar for a human, not legal advice. Nothing is filed or paid.

---

## 1. Read where things stand

    hub task show <id>
    hub calendar list

Then `knowledge/register.md`, `knowledge/proof.md`, `knowledge/unchecked.md` and last week's calendar. For
each item last week called urgent, check whether proof arrived.

## 2. Compute the dates

For every register row, compute the next due date from its rule (for example "last day of the anniversary
month"), and the warning dates from the lead times in `state.md` (default 60, 14 and 3 days). A row whose rule
was last confirmed more than twelve months ago: re-read the authority's page with `hub doc fetch <url>`, cite
it with today's date, and update the row. A date you cannot compute is "date not confirmed", listed, never dropped.

## 3. Sort

- **Overdue**: past due with no proof in `knowledge/proof.md`.
- **Urgent**: inside 14 days.
- **In the window**: inside the first lead time (default 60 days).
- **Waiting for proof**: the owner said it was done, the confirmation is not yet filed.

## 4. Packs

For each item in the window with no pack, follow `playbooks/prepare-a-filing-pack.md`. List each pack's status.

## 5. Write and hand over

Write `reports/YYYY-MM-DD-compliance-calendar.md` in the shape of `knowledge/examples/compliance-calendar.md`:
headline, overdue, urgent, the table, waiting for proof, unchecked places, and the not-legal-advice line.

    hub file publish reports/YYYY-MM-DD-compliance-calendar.md

A filing that now needs its owner becomes `hub task create --owner <person>`. Commit, and `hub task update <id> --status done --note` with the overdue count first.

## Done when
- Every register row has a next due date with its rule, or "date not confirmed", listed.
- Overdue, urgent, in-window and waiting-for-proof items are each sorted, and each has an owner.
- The calendar is published, and the task is closed with the overdue count first.
