# Close entries: accruals, prepaids and the recurring list

For accrual books. On a cash book none of these are posted; check instead that no row is dated in the wrong month.

## What every entry carries

- A date (the last day of the month, or the day the event happened).
- A memo that says what and why in plain words, with the document's name.
- Lines that balance, each on the right account and with the right team or class tag.
- Support attached or named: the contract, the usage export, the timesheet, the statement.
- For an accrual: the date it reverses.

Post with the `journal` action (`tico finance act help --payload '{"kind":"journal"}'` gives the fields; the same
action reverses an entry by its id). Key each entry from what it is: `accrual:<vendor>:<YYYY-MM>`. Tico's rules decide
whether an entry needs a person: by default a bot's entry over 5,000.00 asks a finance admin (as of 2026-11-18).

## Accruals: used in the month, not yet billed

Look for them in this order. Each one that passes the "too small" limit in `knowledge/close-practice.md` is accrued.

| Look at | Accrue when | Amount from |
|---|---|---|
| Vendors that bill usage in arrears (hosting, AI providers, telephony, payment processors) | No bill dated in the month | The vendor's usage export for the month; if none, last month's bill, marked "estimate" |
| Contractors and agencies | Work was done in the month, no bill yet | Approved hours times the contract rate, or the milestone fee |
| Payroll | Days worked in the month are paid next month | The payroll register, days earned over days in the pay period |
| Bonuses, commissions | Earned in the month under a written plan | The plan's formula on the month's results |
| Interest on a loan | Always, if the lender bills later | The lender's schedule |
| Recurring vendors with no bill this month | The bill is simply late | Last month's amount, marked "estimate" |
| Open purchase orders | Goods or services were received | The receipt and the order price |

How to find the gaps: list the vendors paid in each of the last three months
(`tico finance report spend_by --by vendor` for each month) and compare with this month's bills. A regular vendor with
nothing this month is a question, not a saving.

Rules:
- **Every accrual reverses on the first day of next month.** When the real bill is entered it lands in full, and the
  reversal cancels the estimate. Post both entries in the same pass.
- **An accrual has support.** An estimate from last month's bill is allowed and labelled "estimate". An accrual
  because "there is usually something" is not.
- **Compare last month's accruals with what the bills turned out to be.** A vendor you keep getting wrong by more than
  10 percent needs a better source.
- **Do not accrue to hit a budget** or to smooth a month.
- Tico posts CI build time against Accrued expenses by itself. Do not accrue it again.

## Prepaid expenses: paid ahead

A bill that buys more than one month of service, above the "too small" limit, is an asset that comes out month by
month.

1. When the bill is entered, the part that belongs to later months is coded to prepaid expenses, with the service
   period on the line.
2. Each month, one month's slice moves to expense (`prepaid_amortize`). For a period that starts mid-month, count days.
3. The prepaid schedule lists each item: vendor, total, service period, monthly amount, taken so far, balance left.
4. The schedule's total equals the prepaid account. If not, list the difference by item.
5. A contract cancelled early: what cannot be refunded is expensed now; what will be refunded becomes a receivable
   from the vendor.

In Tico, budgets count expense in the month it is recognised, so an annual 1,800.00 bill counts 150.00 a month. A
prepaid that is expensed all at once breaks both the P&L and the budget.

## Fixed entries

- **Depreciation**: from the asset list (cost, date in service, months of life, monthly amount). The list ties to the
  fixed asset accounts.
- **Loan payments**: split each payment into interest (expense) and principal (reduces the loan) from the lender's
  schedule. The loan balance ties to the lender's statement.
- **Owner draws and contributions**: to equity, never to expense or revenue.
- **Sales tax collected**: a liability, never revenue.

## The recurring list

Keep one table in `knowledge/close-practice.md`. Read it at the start of level 3 and tick each line.

| Entry | Accounts | Amount from | Reverses? | Since |
|---|---|---|---|---|
| Hosting accrual | Hosting / Accrued expenses | Vendor usage export | Yes, day 1 | 2026-10 |
| Annual design tool | Software / Prepaid expenses | Schedule, 1/12 a month | No | 2026-07 |

The two rows are invented, to show the shape. A line is added the first time an entry repeats, and removed when its
contract ends.

## Unusual entries

A one-off entry outside this list (a correction, a reclassification, a write-off) needs the same support, plus a
sentence on why it is not a recurring item. A balance written off the balance sheet above the "too small" limit is an
OK from the owner, with the evidence shown.
