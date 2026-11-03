---
name: categorize-a-feed-row
description: "Categorize a bank or card row by a posting rule, a repeated pattern or what the counterparty plainly is, after checking it is not a transfer or a refund; ask for a missing chart line through an approval; ask one person per unclear counterparty."
---
# Categorize a feed row

When to use: an `uncategorized` item in a Books check, or any feed row you are asked to categorize.

Steps:
1. Run the posting rules first: `hub finance search --kind rule --q <counterparty>`. A rule that matches the row
   decides it. Categorize with the rule's account and do not override it.
2. Check for a transfer. A row that matches an opposite-sign row in another company account within a few days, for
   the same amount, is a transfer between the company's own accounts. It is not spend or income. Leave it for transfer
   matching and say so.
3. Check for a refund or reversal. A credit that matches an earlier debit with the same counterparty goes with the row
   it reverses.
4. Find the pattern: `hub finance search --kind feed --q <counterparty>`. Earlier rows from this counterparty in a
   line other than Other expenses decide it.
5. Otherwise, what the counterparty is decides it when its kind of business is plain from its name and the row: a law
   firm is legal and professional fees, a health insurer is insurance, a software company is software, repeated
   payments to a named person are contract labor. Read the chart (`hub api GET finance/chart`) and use the line that
   fits. Other expenses is never the fit.
6. No line fits: ask for one with `account_create`. It waits for the owner's approval. Ask for every missing line in
   one pass with plain standard names (Contract labor, Legal and professional fees, Insurance, Rent, Travel, Bank
   fees), never one line per vendor. Categorize those rows once the line exists.
7. Categorize with `categorize` (`feed_id` and `account_id`), keyed `categorize:<feed id>`. Finance asks for approval
   on a large amount; that approval is the check.
8. When the same counterparty lands in the same account three times, propose a posting rule with `rule_create`; a
   person approves it.
9. Ask one person only about a counterparty that fits two or more lines, or whose kind you cannot tell. Ask per
   counterparty, not per row: its name, how many rows, their total, and the two lines you think fit. Put every
   question in one comment, largest totals first.

Watch for rows that are not expenses: payroll, owner draws and contributions, loan principal and interest, tax
payments, and a card payment that settles a card account. A deposit is income only when a customer pays it; money
from an owner, an investor or a lender is not, so ask about a deposit you cannot place.

Checks before you finish:
- A rule, transfer and refund check ran before any category was chosen.
- No row was categorized to Other expenses.
- A posting rule was proposed only from three matching rows, each named by its id.
- Every row you did not decide is covered by one question about its counterparty.
- No account or card number is in the note.

Example (invented, for illustration): "Lakeside Legal LLP, 4 rows, 12,400.00. No posting rule, transfer or refund. A
law firm, so legal and professional fees; the chart had no such line, so account_create asked for one. Once it was
approved the rows were categorized there and a posting rule was proposed from the four rows."
