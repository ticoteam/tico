---
name: categorize-a-feed-row
description: "Categorize a bank or card row by an existing posting rule, a matched document or a repeated pattern, after checking it is not a transfer or a refund; ask one person when none of those decides it."
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
4. Find the pattern: `hub finance search --kind feed --q <counterparty>`. Read the accounts that its earlier rows were
   categorized to. Three or more rows with the same counterparty in the same account is enough to propose a posting
   rule with `rule_create`; a person approves it. Fewer is not.
5. Categorize with `categorize` (`feed_id` and `account_id`), keyed `categorize:<feed id>`.
6. If no rule, transfer, refund or pattern decides it, ask one person with the row: date, amount, counterparty, and
   the two account options you think fit. Do not pick the nearest account.

Watch for rows that are not expenses: payroll, owner draws, loan principal and interest, tax payments, and a card
payment that settles a card account. Check the account type before you call a row an expense.

Checks before you finish:
- A rule, transfer and refund check ran before any category was chosen.
- A posting rule was proposed only from three matching rows, each named by its id.
- Every row you did not decide has a question to a person, not a guess.
- No account or card number is in the note.

Example (invented, for illustration): "Row fd_881, 84.20, Lakeside Dental Supply, 2026-10-03. No posting rule. No
opposite row in another account. Three earlier rows from this counterparty in the same month all went to the supplies
account, so the row was categorized there and a posting rule was proposed for a person to approve."
