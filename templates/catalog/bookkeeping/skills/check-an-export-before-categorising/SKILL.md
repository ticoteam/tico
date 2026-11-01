---
name: "check-an-export-before-categorising"
description: "Check a transaction export before any category is proposed: its date range, row count, headers, gaps, duplicates and totals, so that a gap or an unreadable file is reported first. Use at the start of every categorising run and every monthly close."
---

# Check an export before categorising

## When to use
At the start of every categorising run and every monthly close, before any row gets a proposed category.

## Steps
1. Open the export. Note the file name, the first and last row dates, the row count, the column headers and the currency.
2. Check the headers match the bank or card format named in `knowledge/categories.md` or the setup answers. A header you do not recognise is a question, not a guess at the columns.
3. Check the dates cover the whole period. A gap of more than two days between rows on the same account is a finding. Report it with the dates on each side.
4. Compare the row count with any count the sender gave. A mismatch is a finding.
5. Look for exact duplicates: same date, amount and description on the same account. List them as "looks like a duplicate", never as a fact.
6. Check totals. If the sender gave a total or an ending balance, compare it with the export. A mismatch is a finding. If none was sent, say no total check was possible.
7. Write the check at the top of the working table: range, rows, gaps, duplicates, total check. Then propose categories.

## Checks before you finish
- Every number in the check is copied from the export, not estimated.
- Gaps and duplicates are reported as findings. Nothing is removed from the data.
- An export you could not open is named as unread, and nothing from it is reported as checked.
- The check sits above the categories, so the reader sees it first.

## Example (invented)
larchmont-card-2026-08.csv: 2026-08-01 to 2026-08-31, 96 rows, headers date, description, amount, currency.

- Gap: no rows from 2026-08-12 to 2026-08-15. Three days with no card activity is over the two-day limit. Ask the owner once whether the feed was complete.
- Duplicate: two rows on 2026-08-04, "Cloud host", 412.18. Looks like a duplicate. Listed in the questions.
- Totals: no total was sent, so no total check was possible.
