# Monthly expense audit

Schedule: the 3rd of each month at 09:00 team time (routine `monthly-expense-audit`), after setup. Budget 40 minutes.

Inputs: the expense report export and the card export for the month just ended, with their receipts, plus
`knowledge/policy-rules.md`, `knowledge/exceptions.md` and `knowledge/approvers.md`.

Outcome: one audit for finance in the shape of `knowledge/examples/expense-audit.md`: a headline, the sources, one section
per owner, late claims, and what could not be read. Nothing is approved, rejected or sent.

Done when every line is checked or named under "Could not read", every duplicate candidate names both lines, the audit is
published to finance, and the task note carries the headline and the path.

---

## 1. Gather the month

    hub task show <id>

Note each export's date range and row count. A card or person with no rows is a finding under "Could not read", never a zero.

## 2. Check every line

Run the checks in `knowledge/policy-rules.md`, skipping any line that `knowledge/exceptions.md` covers. In this order:
1. **Receipt.** Over the threshold with no itemised receipt, or a receipt whose amount or date differs from the line.
2. **Limit.** Over the category limit in force on the date of the spend (meal per person, hotel per night, flight class).
3. **Never reimbursed.** Anything the policy lists as never reimbursed.
4. **Business record.** Travel or purchases that need an approval or a named client, and have neither.
5. **Duplicates.** Run `skills/find-duplicate-expense-candidates/SKILL.md` across reports, cards and people.
6. **Timing.** Claims submitted after the window; weekend or holiday charges the policy asks about.
7. **Card with no claim.** Card charges older than 30 days with no report or receipt.

## 3. Sort and size

Group by owner, then by rule. Small repeats become one line with the count and total. Put first what is large, repeated
or a duplicate candidate.

## 4. Write the owner notes

For each owner with an exception, write the note with `skills/write-an-owner-review-note/SKILL.md`. Each note goes in that
owner's section of the audit.

## 5. Write and hand over

Write `reports/YYYY-MM-expense-audit.md` and publish it with `hub file publish` (finance only). Commit. Then
`hub task update <id> --status done --note "..."` with the headline and the path. Owner notes leave the audit only as the
sending rules in AGENT.md allow.
