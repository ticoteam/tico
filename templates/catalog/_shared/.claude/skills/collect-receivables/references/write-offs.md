# Write-offs and doubtful accounts

A write-off says: the company no longer expects this money. It cleans the receivables list so the number can be
trusted. It does not cancel the debt and it does not stop a later recovery.

## Two kinds

**1. Small leftovers.** A bank fee, a rounding difference, a currency difference. Inside the limit in
`knowledge/collections.md` (default: 25.00 or less per invoice, 250.00 in total per month) you write these off on your
own. Keep a running total for the month on the receivables page. When the month's total is used up, the rest wait for
an OK. A customer who short-pays by a small amount every month is not a leftover: it is a pattern to raise with them.

**2. Uncollectible invoices.** Always an OK. Propose a write-off when one of these is true and you can show it:

| Reason | What you must show |
|---|---|
| The customer is closed or insolvent | The notice, the filing, or returned mail and a dead domain. In a bankruptcy a lawyer files the claim; the write-off waits for their view |
| No response | More than 180 days late, at least four attempts on record across two channels and two contacts, the final notice sent, no reply |
| The cost to collect is more than the balance | The balance, and the agency share or filing fee and hours it would take |
| A dispute was settled for less | The settlement in writing; the forgiven part is the write-off |
| The invoice was wrong | This is not a write-off. Issue a credit note so revenue is corrected |

## The evidence trail

Attach to the task, before asking:

- The invoice, the contract or order, and proof of delivery.
- Every reminder with its date and delivery result, every call, every reply.
- Promises made and broken, with dates.
- Which reason from the table applies.
- What was tried instead: plan offered, hold applied, agency considered.
- The customer's revenue over the last 12 months and whether they are still served.

Then ask: `tico task ask <id> --to <owner> --kind ok "Write off invoice 2019, 2,400.00, Elm Studio: closed in
September, four attempts, no reply" --version N` with the trail shown.

## Recording it

- Use `invoice_write_off` with the invoice and the amount, keyed `invoice-write-off:<invoice id>`. Run
  `tico finance act help --payload '{"kind":"invoice_write_off"}'` for its fields. Tico may route it to a person under
  the company's Finance rules; if it answers `needs_approval`, report who it waits on and stop.
- A partial write-off leaves the rest of the invoice open.
- Add the invoice to the **written off, still owed** list in `knowledge/customers.md`: customer, invoice, amount,
  date, reason. The customer still owes it unless the owner agreed to forgive it in writing.
- A customer on that list gets no new credit: new work is paid in advance until the owner says otherwise.
- **If it is paid later**, record the recovery, do not net it against new invoices. Ask the bookkeeper or controller
  how the company books recoveries (commonly a reversal of the bad-debt expense) and follow that every time.
- Sales tax already remitted on a written-off invoice may be recoverable in some states. Flag it for the tax owner;
  do not adjust a return yourself.

## Doubtful accounts at month end

The write-off records a loss when it is certain. The allowance records the loss the company expects on what is still
open, so the balance sheet does not overstate receivables. The close asks for this figure each month.

1. **Build the company's own loss rates.** For each aging bucket, take invoices that sat in that bucket at month end
   over the last 12 to 24 months and work out the share that was later written off. With little history, say so and
   use the specific method only (step 3).
2. **Apply the rates to today's aging.** Bucket balance x loss rate, summed.
3. **Add specific accounts.** Any customer known to be in trouble (closed, in dispute over the whole balance, broken
   three promises) is assessed on its own, at the amount you expect to lose, and taken out of the bucket math.
4. **Compare with the allowance in the book.** The difference is the month's adjustment. Give the close the table and
   the proposed entry; the close posts it.
5. **Show the working**: buckets, rates and where each rate came from, specific accounts and why.

Never use a percentage from an article or another company. A rate that does not come from this company's history is
labelled "placeholder, no history" on the page.

US accounting rules ask for an estimate of expected credit losses on receivables (ASC 326). The method above is a
common practical one for a small company; the company's accountant confirms the policy once and it is saved in
`knowledge/collections.md`.

## Tax

Whether and when a bad debt is deductible depends on the company's accounting method and the facts. A cash-basis
company that never counted the invoice as income generally has nothing to deduct. Give the tax preparer the list of
write-offs with their evidence at year end and let them decide. US default; check locally.

## What a write-off is not

- Not a way to tidy the aging before a board meeting.
- Not a discount to win a renewal. That is a concession and a different decision.
- Not a correction of a wrong invoice. That is a credit note.
- Not forgiveness. Forgiving a debt is a separate, written decision by the owner.
