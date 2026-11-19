# The 13-week cash forecast

Thirteen weeks is one quarter: far enough to see a problem while there is still time to act, near enough that most of
it can be counted from real documents. It uses the direct method: receipts and payments by week, not profit adjusted
for timing.

## 1. Opening balance

- **Bank available**, per account, by name (never by number), with the time read: `tico api GET finance/pay-from`.
- Bank available is not the book's cash. The book may hold payments sent and not yet cleared, or deposits not yet
  landed. Show book cash as a separate line if it helps; never mix the two.
- Leave out cash the company cannot spend: a deposit held by a landlord, a processor reserve, money held for
  customers or for sales tax already collected. List each under the total as "not available".
- If balances cannot be read, the page opens with "balances not supplied" and everything below is labelled "forecast
  only".

## 2. Lines and their sources

Use 8 to 12 lines. Fewer hides the movement; more makes the page unreadable. Split a line only when the two halves
behave differently.

| Line | Weeks 1 to 4 from | Weeks 5 to 13 from |
|---|---|---|
| Collections from invoices | `tico finance report ar_aging`, each invoice moved by its customer's usual lateness | Expected billings x the company's collection pattern |
| Card and subscription receipts | The processor's payout schedule, net of fees | Renewals due x the recent success rate of charges |
| Other receipts | Signed documents only: a tax refund notice, a loan drawdown, an investment with a closing date | Nothing, unless signed |
| Payroll and contractors | The payroll provider's register and pay dates | Headcount and agreed changes x pay dates |
| Payroll taxes and benefits | The provider's schedule | The same pattern |
| Payables | Bills scheduled and approved (`tico finance report ap_aging`, the payment run) | Average weekly payables by vendor type, plus known contracts |
| AI services and hosting | Bills due, plus the last four weeks' run-rate (`tico api GET "usage?from=&to=&group=bot"`) | Run-rate x planned change in use |
| Subscriptions and renewals | `tico finance report subscriptions`, vendor `renewal_on` dates | The same; annual renewals land in one week |
| Rent and fixed contracts | The lease, the contract | The same |
| Card payments | The card statement's due date and balance | Average card spend, paid on the statement date |
| Debt | The loan schedule | The same |
| Taxes | The tax calendar (`stay-on-top-of-taxes`) | Estimated payments on their due dates |
| Other | Named items only | A small allowance from history, labelled |

Every figure on the page carries its source and that source's date. A line whose source was not supplied reads "not
supplied" and is left out of the total, and the total says what it leaves out.

## 3. Timing rules

- **Customers pay when they usually pay.** For each customer, the average days between due date and payment over
  their last six invoices moves the receipt. New customers: use terms, labelled as an assumption.
- **A promise with a date** from `collect-receivables` beats history for that invoice. A disputed invoice is left
  out until the dispute is settled.
- **Invoices over 90 days late are not forecast** unless there is a written plan.
- **Payments leave on the scheduled date**; a payment takes about one business day to show on both sides, so a
  Friday payment may show on Monday.
- **Payroll lands on the pay date**, and the taxes when the provider debits them. Months with three pay dates (for
  companies that pay every two weeks) are the classic surprise: mark them.
- **Annual items land in one week**: insurance, annual software, tax payments. List the next 13 weeks' annual items
  at the top of the working file.
- **Processor payouts lag sales** by the processor's schedule and come net of fees and refunds.

## 4. Low and high cases

The cases show how wrong the base could be. They are built on closing cash, week by week.

1. **Flex receipts and payments separately.**
   - Low: receipts lower or later, payments higher or earlier.
   - High: receipts higher or earlier, payments lower or later.
2. **Take the size of the flex from the forecast's own misses.** For each line, use the miss log: the size of error
   that covered four out of five of the last 13 weeks. Until there are eight weeks of history, use these defaults and
   label them defaults: documented receipts (weeks 1 to 4) 10% lower in the low case and 5% higher in the high case;
   estimated receipts (weeks 5 to 13) 20% either way; payments 5% either way; payroll, rent and debt not flexed.
3. **Add named risks to the low case as whole items**, not percentages: the largest customer pays 30 days late; one
   renewal is lost; a known bill arrives early. Say which are in.
4. **Carry each case forward**: a case's closing cash for a week is its own opening cash plus its receipts minus its
   payments. Do not compute a band around the weekly net change: when the net is negative, a percentage band turns
   upside down and "low" comes out above "high".
5. **Check the order** in every week: low at or below base at or below high. If it fails, the flex was applied to
   the wrong side.

## 5. The weekly roll

Every Monday:

1. Read actuals for last week from the bank, by line.
2. Put them beside last week's forecast. Compute the miss per line in money and percent.
3. For each miss over 10%: find the cause in a source (a named customer paid late, a bill was held, a renewal moved).
   Label it timing only if the item is now expected in a named later week, and move it there. If no cause is found,
   the line is re-based to the recent average and marked "re-based".
4. Log it in `knowledge/cash-forecast.md`: line, week, forecast, actual, miss, cause, owner, date. A miss with no
   cause stays open with an owner.
5. Drop last week's column. Add the new week 13. Move week 5 into the "documents" group by filling it from documents.
6. Re-run the cases.

## 6. Reading it

- **The lowest week** in each case, its closing cash and its gap to the minimum. Say what causes it: "payroll and
  the annual insurance land in the same week".
- **Weeks under the minimum**, by case. Under in the base case is a problem now. Under only in the low case is a
  risk to watch, with the event that would cause it.
- **Concentration**: any single receipt that is more than a quarter of a month's inflows is named, with its
  customer's payment history.
- **The trend**: closing cash in week 13 against today.

## 7. Levers when cash is tight

Show this block when the low case goes under the minimum in any week. One row per action, with real numbers from the
book. Order by how quickly cash arrives and how easily it is undone.

| Lever | Cash effect | Lands | Cost | Can it be undone? |
|---|---|---|---|---|
| Chase the largest late invoices harder (`collect-receivables`) | The invoices' total | 1 to 3 weeks | Goodwill if done badly | Yes |
| Invoice finished work sooner; bill milestones not month-ends | The unbilled total | 2 to 6 weeks | None | Yes |
| Move bills without a late fee to their due date or the next run | The bills' total, for a week or two | At once | None if inside terms | Yes |
| Ask key vendors for longer terms | One cycle of their bills | Next cycle | A favour used | Yes |
| Switch annual renewals to monthly | The prepayment not made | At renewal | A higher monthly price | Yes |
| Cancel unused subscriptions and seats | Their monthly cost | Next billing date | None | Mostly |
| Lower bots' AI services budgets; move work to cheaper models | The saving at current volume | Days | Slower or lower-quality work; test first | Yes |
| Pause planned purchases and hires | Their cost | From their planned date | Delay to the plan | Yes |
| Offer customers a discount to pay annually up front | The prepayments | 2 to 6 weeks | The discount; less cash later | No |
| Draw on a credit line | The draw | Days | Interest; covenants | Repayable |
| Raise prices | Depends on churn | A billing cycle or more | Churn risk; use `set-and-test-prices` | Hard |
| Cut people or contracts | Their cost after notice and severance | Weeks to months | Capability, morale, severance | No |

Never levers: payroll that is due, payroll taxes, sales tax collected, money held for customers. Missing these has
legal consequences for the company and sometimes for its officers personally.

The bot sets out the levers and recommends. The owner chooses any lever in the lower half of the table.

## 8. Checks

- Opening balance is bank available, per account, with its time.
- Each week: closing = opening + receipts - payments, and the next opening equals it.
- Weeks 1 to 4 trace to documents; weeks 5 to 13 are labelled estimates.
- Every line names its source and date; "not supplied" lines are outside the total.
- Low, base and high are in order every week.
- Last week's misses are logged with causes or marked open.
