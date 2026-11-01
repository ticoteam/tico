---
name: run-a-duty-register-check
description: Put each row of the recurring-duties register into one state (overdue, in its lead window, due this week, blocked, fine, needs a date or needs an owner), with its date and source, before the weekly ops page is written.
---

# Run a duty register check

## When to use

Read at the start of every weekly ops page, and when someone asks whether a duty is covered. Run it on every row of
`knowledge/duties.md`, including the rows that look fine.

## Steps

1. Read the row: what, owner, cadence, next due, lead time, proof needed, and the source of the date.
2. Check for an owner. With no owner, the state is "needs an owner". Do not pick one for the row.
3. Check the date has a source and a read date. With no source, the state is "needs a date". Do not work out a date
   from the cadence and call it sourced.
4. Look for proof: the record that the latest due occurrence was done, with its date. A duty with no proof for its
   latest due date is overdue, even when the register already shows a later date.
5. Pick one state. The first that matches wins:
   - overdue: past its date with no proof of completion
   - in its lead window: the notice period or lead time has opened
   - due this week
   - blocked: waiting on a named person or vendor for longer than the agreed wait in `knowledge/rhythm.md`
   - fine
6. Write one line for each row that is not fine: what, state, owner, date, source. Count the fine rows; do not list them.
7. Count each state for the headline.
8. If a source could not be read, the row says "not read". Its state is "needs a date", never "fine".

## Checks before you finish

- Every row has exactly one state.
- No row is "fine" without a date, a source and proof, or a next due date still in the future.
- No duty is marked done. Only its owner or a dated record closes it.
- Every source that could not be read is named on the page.

## Example

Invented example. Row 4, quarterly access review, owner Odile Rivera. The last proof is a signed note dated
2026-06-12. The cadence is quarterly, so the next due date was 2026-09-12, and no proof is on file since. State:
overdue. The line on the page reads: "Quarterly access review, Odile Rivera, overdue since 2026-09-12 (duties.md,
row 4). Proof needed: signed review note." The row stays open.
