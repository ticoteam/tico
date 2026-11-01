# Weekly contract calendar

Schedule: Mondays at 09:00 team time (routine `weekly-contract-calendar`), after setup. Also run by hand. Budget 25 minutes. The outcome is one page of renewals, notice deadlines and
expiries in the next 90 days, plus the summaries waiting for a human. A calendar for a human, not legal advice.

---

## 1. Read where things stand

    hub task show <id>
    hub doc search "renewal"

Then `knowledge/contracts.md`, `knowledge/playbook.md` and last week's calendar. Add any contract that
arrived since, by following `playbooks/summarise-a-contract.md` at a lighter depth: the key terms table and
the notice deadline only, marked "calendar entry, not fully reviewed".

## 2. Compute every date

For each contract: end of the current term; whether it auto-renews; the notice period; the notice deadline
(end of term minus notice, with the arithmetic written out); the source clause. A date you cannot compute is
"needs the contract", never a guess. Recheck any contract whose row was last read more than six months ago.

## 3. Sort

- **Urgent**: a notice deadline inside 14 days.
- **Inside the window**: a deadline inside the notice the team asked for (default 60 days for a renewal,
  30 for a notice deadline).
- **Coming**: inside 90 days.
Anything already past its notice deadline is listed first as "notice window has passed", with what the
contract says happens next, and no advice.

## 4. Summaries waiting

List the summaries a human has not yet acknowledged, oldest first, and how long each has waited.

## 5. Write the page and hand it over

Write `reports/YYYY-MM-DD-contract-calendar.md`: a headline, the urgent items with the clause, the table by
date, the summaries waiting, and what you could not read. Close with the line "Summaries for a human, not
legal advice; have counsel review anything that matters."

    hub file publish reports/YYYY-MM-DD-contract-calendar.md

Only the reviewers named in `state.md` receive it. An urgent item is a task for the human who owns the
relationship, `hub task create --owner <person>`. Nothing goes to a counterparty.

## 6. Finish

Update `knowledge/contracts.md`, commit, then `hub task update <id> --status done --note`: the headline,
urgent items first, and which contracts you could not read.

## When a source fails

A contract file that will not open stays on the calendar as "not read, date unknown". It is never dropped.

## Done when
- Every contract in `knowledge/contracts.md` has a notice deadline, or "needs the contract", with its arithmetic.
- Urgent, inside-the-window and coming items are sorted, and each urgent item has a task for its owner.
- The calendar is published, the summaries waiting are listed, and the task is closed with the headline first.
