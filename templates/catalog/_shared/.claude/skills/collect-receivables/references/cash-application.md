# Cash application

Matching money that arrived to the invoices it pays. Do it daily, before any chasing. A payment sitting unmatched makes
a good customer look late and the cash forecast look wrong.

## The matching order

Work down this list and stop at the first rule that fits.

1. **The payer said.** A remittance advice, an email, a portal note or the bank row's memo names invoice numbers.
   Apply exactly as stated, even if you would have chosen differently.
2. **One invoice, exact amount.** One open invoice for that customer equals the payment to the cent.
3. **Several invoices, exact sum.** A set of the customer's open invoices adds up to the payment exactly, and only one
   set does. If two different sets fit, ask.
4. **The customer's standing instruction** in `knowledge/customers.md` ("apply to the oldest first").
5. **Otherwise it is unapplied.** Record nothing against an invoice. List it and ask the customer the same day:
   "We received 3,200.00 on 2026-11-12. Which invoices does it cover?"

Never apply to the oldest invoice by default. Oldest-first hides a dispute on the invoice the customer skipped on
purpose.

Record each match with `invoice_record_payment` (`invoice_id`, `amount`, `feed_id`), keyed
`invoice-payment:<invoice id>:<feed id>`. One bank row paying three invoices is three records with the same `feed_id`.
When Tico cannot tell which payment a row belongs to it says `feed_match`; that link is made deliberately with the
`feed_match` action by someone who has checked, never by retrying.

## Who paid

- **A different name.** A parent company, a payment service, a founder's personal account. Match by the remittance or
  the invoice number, and add the payer's name to the customer in `knowledge/customers.md` so next time is easy.
- **No name at all.** Search open invoices for the exact amount across all customers. One match and a plausible date:
  ask that customer to confirm before applying. No match: it stays unapplied under "unidentified".
- **Not a customer.** A deposit from an owner, an investor, a lender or a tax refund is not revenue and not a
  receivable. Leave it for `categorize-a-feed-row`.

## Short pays

A payment for less than the invoice. Find out why before doing anything with the difference.

| Why | What you see | Do |
|---|---|---|
| Bank or wire fee | A small, odd shortfall; the remittance shows the full amount sent | Write off inside the small-leftover limit; if it repeats, ask the customer to send fees "ours" or add a line to the next invoice if the contract allows |
| Early-payment discount | The shortfall is the discount percent, paid inside the window | If the contract offers it and the date qualifies, record the discount. If not, the shortfall is still owed: say so once, politely |
| Tax withheld | A fixed percent, often from a customer abroad, with a certificate | Record the withholding to its own account and keep the certificate for the tax preparer. Not a write-off |
| Dispute | A line or a quantity left out; often no note | Open a dispute (step 16 of the skill) for exactly the shortfall. Do not chase it as late |
| Part payment | A round amount, or a note saying more follows | Record it, ask for the date of the rest, log the promise |
| Credit taken | The customer netted a credit note | Apply the credit note; if none exists, ask which credit they mean |
| Mistake | No pattern | Ask |

A shortfall is never written off just to close the invoice, except inside the small-leftover limit in
`knowledge/collections.md`.

## Overpayments and duplicates

- **Paid twice.** Tell the customer the same day. Offer a refund or a credit against the next invoice; do what they
  choose. A refund is money out and follows the company's payment rules.
- **Paid too much.** The extra is an unapplied credit on the customer's account, shown on the aging as a negative. It
  is not income.
- **A credit older than 90 days** with no instruction: ask again, then raise it with the owner. Unclaimed customer
  credits can fall under state unclaimed-property rules; the accountant decides.

## Payment processors

A processor (cards, wallets, bank debits) pays out in batches, net of its fees, a few days after the customer paid.

1. Apply each customer payment to its invoice at the **gross** amount, on the day the processor shows it as paid.
2. The processor's balance is its own money account. The payout is a transfer from it to the bank, not revenue.
3. Fees are payment processing cost (cost of revenue on the management P&L), from the processor's report.
4. Refunds and chargebacks come out of a later payout. A chargeback reopens the invoice and starts a dispute with a
   deadline set by the card network. Every chargeback is contested; no refund is made on that charge while the
   dispute is open. If your role has the `defend-a-consumer-claim` skill, contest it with that skill. If not, hand
   it off the same day to the teammate who has it (the Accounts Receivable or Billing Specialist), with the charge,
   the invoice, the reason code and the response deadline:
   `tico task create --owner <teammate> --title "Contest chargeback <id>"`.
5. Reconcile the processor balance monthly: payments minus fees minus refunds minus payouts equals the change in its
   balance.

## Timing

- A payment takes about one business day to show on both sides, and some methods take several days to settle. A bank
  debit can be returned days later for insufficient funds: the invoice reopens and the customer is told.
- Record the payment on the date the money arrived, not the date you matched it.
- At month end, unapplied cash is listed by customer and age on the close page. Unapplied over 30 days is escalated.

## Checks

- Every incoming row from a customer is applied, or listed as unapplied with the question asked and the date.
- No payment was applied on amount alone when two invoices could fit.
- Applied plus unapplied for the day equals the customer receipts in the feed for the day.
- No shortfall was written off outside the limit.
