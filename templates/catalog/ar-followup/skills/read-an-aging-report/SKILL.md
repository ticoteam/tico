---
name: "read-an-aging-report"
description: "Read an invoice aging export before anything is summarised or called late: confirm the as-of date, tie the total to the ledger, net unapplied payments and credit notes, and compute DSO with its formula."
---
# Read an aging report

When to use: at the start of every weekly pack, and whenever a person asks whether a customer is late. An aging report can make a customer look late
when a payment is simply not yet applied, so tie it out first.

Steps:
1. Note the as-of date in the export and the date you read it. More than 3 days apart: say so in the first line of the pack.
2. Tie the total. The sum of open balances should equal the ledger's receivable total at the as-of date, if the ledger total is given. If it is not
   given, say that the check could not run.
3. Find unapplied cash: payments received but not matched to an invoice. Net them against the customer's oldest open invoice in the pack, and
   name the net. Do not call a customer late on an invoice a payment already covers.
4. Net credit notes: a credit note reduces the balance it refers to. An open credit with no invoice is a finding, not a balance to chase.
5. Find duplicates: the same invoice number twice, or the same amount, customer and date on two rows. Name each pair and treat the second as
   a duplicate until checked.
6. Bucket what is left: not yet due, 1 to 30, 31 to 60, 61 and over, each with count, amount and the change on last week's pack.
7. Compute DSO with the formula written in the pack: DSO = accounts receivable at the as-of date / credit sales over the last 90 days x 90.
   Name the sales figure and its source. If credit sales are not in the export, give the share of receivables over 30 days instead, and say
   DSO was not computed.

Checks before you finish:
- The as-of date, the tie-out result and the source file name appear in the pack.
- Unapplied cash and credit notes are shown as separate lines with their amounts.
- No customer is described as late on an amount that is netted to zero.
- DSO, or the reason it was not computed, is stated.

Example (invented, for illustration): "Aging as of 2026-09-28, 41 open rows, total 60,310. Ledger receivables at the same date: 60,310, tied.
Unapplied cash of 1,200 from Harbour Pilates (receipt 2026-09-25) nets against invoice 2057 (1,150): not late. DSO 42 days (AR 60,310 /
credit sales 129,000 over 90 days x 90)."
