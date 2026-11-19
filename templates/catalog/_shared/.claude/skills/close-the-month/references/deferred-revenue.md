# Deferred revenue and revenue recognition

For accrual books. The idea in one line: **money a customer paid for something not yet delivered is a debt to the
customer, not revenue.** It sits on the balance sheet as deferred revenue and moves to revenue as the company delivers.

The standard behind this in the US is ASC 606 (IFRS 15 elsewhere says nearly the same). This file gives the working
method and the common cases. It is not the standard. The company's own policy in `knowledge/revenue-policy.md` is
checked by a CPA when first written and whenever a new kind of contract appears.

## The five questions, in plain words

Ask these once per kind of contract and write the answers in `knowledge/revenue-policy.md` as a numbered section.

1. **Is there a contract?** Both sides agreed, the terms and price are known, and the customer is likely to pay.
2. **What did we promise?** List each separate thing the customer could use by itself: the subscription, onboarding,
   a block of hours, hardware. A setup step the customer gets no separate use from is not its own promise.
3. **What is the price?** The total the company expects to keep, after discounts, credits and expected refunds. If the
   price can change (usage, a refund right, a bonus), count only the part unlikely to be reversed.
4. **How is the price split between the promises?** In proportion to what each would sell for alone. A discount on a
   bundle is spread across the bundle, not taken from one item.
5. **When is each promise delivered?** Over time (a subscription, day by day) or at a point (a delivered project, a
   shipped item). Revenue follows delivery.

## The default policy

Until the company's own is written, use this and mark the schedule "default policy".

| What was sold | Earned | Notes |
|---|---|---|
| Subscription, monthly or annual | Day by day over the service period | An annual plan billed 2026-10-16 earns 16/31 of a month in October |
| Usage or overage | As used, in the month of use | Bill in arrears, so accrue unbilled usage at month end |
| One-off service (onboarding, a project) | When delivered, or by milestone if the contract has them | Evidence: sign-off, delivery note |
| Setup fee with no separate use to the customer | Over the subscription it starts | Usually not its own promise |
| Prepaid credits or hours | As used; unused balance stays deferred until it expires under the contract | |
| Free months, ramp pricing, a first-year discount | Total price spread over the whole term | Revenue per month is level even if billing is not |

## The schedule

One row per contract (or per invoice line for simple monthly plans), kept as a sheet or a page the close links to.

| Contract | Customer | What | Total | Service start | Service end | Billed to date | Earned to date | Deferred |
|---|---|---|---|---|---|---|---|---|

Deferred = billed to date minus earned to date. If earned is more than billed, the difference is **unbilled revenue**
(an asset), not negative deferred revenue. Show the two apart.

## The monthly roll-forward

For each contract, then in total:

```
opening deferred
+ billings in the month        (invoices dated in the month, net of credit notes)
- revenue earned in the month  (by the policy; cite its section)
- refunds and cancellations    (the unearned part given back)
= closing deferred
```

1. Opening is last month's closing, to the cent. If someone changed last month's schedule after the close, stop and
   find out why.
2. Billings come from the billing export or `tico finance search --kind invoice` for the month. They tie to
   `reconcile-billing-to-the-books`.
3. Earned is computed per contract from its dates, not as a percent of the total.
4. Each contract foots. Then the total foots.
5. The total closing balance equals the Deferred revenue account on
   `tico finance report balance_sheet --end <last day>`, after this month's entry.
6. Any remainder is a listed difference with its cause: an invoice missing from the export, a wrong period, a credit
   note not yet applied, a contract changed mid-month. **Never a plug.**
7. Mark a row "estimated" if its dates or amounts are not from a signed document.

## The entry in Tico

Tico's default chart has no Deferred revenue account. Ask for one once: `account_create` with type `liability` (a bot's
new account asks a finance admin). Check how invoices post in this book by reading one invoice's entry
(`tico finance search --kind entry --q "<invoice number>"`):

- If an invoice posts its whole amount to Revenue, the month-end entry moves the **unearned** part out: debit Revenue,
  credit Deferred revenue, for the change in the schedule's closing balance.
- If invoices for annual plans post to Deferred revenue, the month-end entry releases the **earned** part: debit
  Deferred revenue, credit Revenue.

Either way the entry's amount comes from the schedule and the schedule is attached. Key it
`deferred-release:<YYYY-MM>`.

## Common cases

- **Annual plan, paid up front, starts mid-month.** Count days in the first and last month.
- **Upgrade or added seats mid-term.** Treat it going forward: the extra price earns over the remaining days. Do not
  restate past months.
- **Downgrade or cancellation with a refund.** The refund reduces deferred revenue. Revenue already earned stays.
- **Cancellation with no refund.** The rest is earned when the company has nothing left to deliver, under the
  contract's terms. Check the contract; do not release it the day the customer asks.
- **Credit note for a past month.** It reduces revenue in the open month, with a note; the closed month stays as it
  is.
- **Customer has not paid.** Deferred revenue follows the invoice, not the cash. A doubtful invoice is a collections
  question (`collect-receivables`), unless payment was never likely, in which case question 1 fails and no revenue is
  recorded until cash arrives.
- **Multi-year deal.** The part earned more than 12 months out is long-term deferred revenue. Show it apart.
- **Reselling someone else's product.** Whether to record the whole sale or only the margin depends on who controls
  the product before the customer gets it. Ask the accountant; do not guess.

## Ask the accountant, do not decide alone

- A bundle where the parts have never been sold separately.
- A refund right, a satisfaction guarantee, or a price that depends on a future result.
- Commissions paid to win a contract longer than a year (they may need to be spread too).
- Anything a lender, investor or acquirer will read as "ARR" or "bookings": those are not GAAP revenue, and the
  definition must be written down (`bridge-recurring-revenue`).

## Checks

- Every contract row foots; the total foots; the total ties to the ledger or each difference has a cause.
- Opening equals last month's closing.
- Each release cites a policy section.
- Unbilled revenue is shown apart from deferred revenue.
- No estimated row is unmarked.
