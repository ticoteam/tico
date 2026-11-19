---
name: forecast-cash-and-budget
description: Say where the company's cash is going and set the plan it spends against. Covers the default-alive test (does the company reach profit before the cash runs out), the 13-week cash forecast by week and line with low and high cases, the weekly roll with forecast against actual, levers when cash is tight, a driver-based budget, the monthly rolling forecast, and variance commentary against budget and forecast. Use when a task says cash forecast, 13-week, cash flow, runway, burn, default alive, will we make payroll, how long does the cash last, minimum cash, budget, annual plan, set budgets, reforecast, rolling forecast, scenario, what if, budget versus actual, variance note, or forecast accuracy; every Monday for the weekly cash summary; and each month after the close.
metadata:
  function: finance
  roles: [finance-owner, fpa-analyst, controller, strategy-analyst, fundraising-manager, investor-relations-manager, chief-of-staff]
  actions: [read, draft, change-records]
  review: none
---
# Forecast cash and budget

When to use: every Monday for the cash forecast; once a month after `close-the-month` for the rolling forecast and the
budget note; once a year, or when the plan breaks, for the budget; and whenever someone asks how long the cash lasts
or what a decision would do to it.

Words used here: **the company** is the business. **The book** is its ledger in Tico Finance. **Bank available** is
cash that can be spent today. **Burn** is cash out minus cash in for a period. **Runway** is how many months the cash
lasts at the current burn. **A driver** is a number the business controls or observes that moves revenue or cost: new
customers, price, churn, cost per unit. **The budget** is the spending limit per line that the owner has agreed.
**The forecast** is the honest best guess of what will happen. **A lever** is an action that changes cash.

## What great looks like

A great forecast is one people act on. The owner reads one page and knows three things: whether the company survives
on its present course, the week cash is lowest and by how much, and what could be done about it. Every figure names
where it came from. Last week's forecast sits next to what happened, so everyone can see how far to trust this one.

An expert does five things an amateur skips.

- **Answers the survival question first**, with simple arithmetic, before building any model.
- **Forecasts cash by counting it**: dated receipts and payments by week, not profit adjusted backwards.
- **Keeps the forecast and the target apart.** The forecast is not negotiated and is never changed to match a goal.
- **Builds the budget from drivers**, so "what if we lose two customers" is one changed cell, not a rebuild.
- **Scores its own misses** every week and fixes the method when one line keeps missing in the same direction.

In Tico the budget does a second job: it is the limit inside which bots spend without asking. A budget that is too
tight stops work; one that is too loose spends cash nobody chose to spend.

## First time at a company

Read `knowledge/cash-thresholds.md` and `knowledge/forecast-drivers.md`. If they have answers, skip this. If not, ask
the owner these in one numbered message, save the answers with the date, and work on the defaults until they reply.

1. **What is the least cash the company should ever hold?** Default until answered: a working floor of four weeks
   of average cash out (one pay cycle and one payment run), marked on the page as a default, not the owner's figure.
2. **When should runway raise an alarm?** Default: under 6 months, the level Tico's built-in daily finance check
   already alerts on.
3. **Budget year and how far ahead to look.** Default: the calendar year; the rolling forecast always looks 12 months
   ahead, so it runs past the year end.
4. **Which growth rate describes the present course?** Default: the average month-on-month revenue growth of the
   last three closed months, with the last six as a second case. Both are shown.
5. **What are the targets, and where do they live?** Default: none recorded. Targets are kept on their own page; the
   forecast is never set equal to a target.
6. **When does a difference need a written reason?** Default: the limit in `knowledge/pnl-design.md` (10% of the line
   and 1% of the month's revenue, both); for a weekly cash line, 10%.

The drivers are not asked for. Work them out from the book and the P&L (`references/driver-budget.md`), show them to
the owner once, and save them in `knowledge/forecast-drivers.md`. The forecast's lines, sources and miss log live in
`knowledge/cash-forecast.md`.

## Steps

Tools: `tico api GET finance/pay-from` (bank accounts and balances), `tico finance metric
cash|burn|runway_months|ap_due|ar_overdue`, `tico finance report cash_flow|ar_aging|ap_aging|subscriptions|pnl|budget_vs_actual`,
`tico finance inbox`, `tico api GET "usage?from=<day>&to=<day>&group=bot"` (AI use by bot), and `tico finance act`
for writes (`budget_create`, `budget_update`, `allocation_set`, `projection_set`). Amounts are integer minor units:
315000 is 3,150.00. `tico finance act help --payload '{"kind":"<kind>"}'` lists a kind's fields. A total marked
`excl: true` leaves out lines you may not see: say so next to it.

**A. Start from real cash**

1. **Fix the opening balance.** Bank available by account, by name, with the time read. If the bank figure, the
   book's cash and `tico finance metric cash` disagree, run `reconcile-a-cash-figure` before going on. A balance that
   cannot be read is "not supplied", never zero, and the page opens by saying so.
2. **Read last week's page and the miss log** in `knowledge/cash-forecast.md`. Check whether last week's decisions
   were made.

**B. The survival question**

3. **Run the default-alive test** (`references/driver-budget.md`, section 1). Take revenue, gross margin and
   operating costs from the last closed month. Hold costs flat. Grow revenue at the rate from question 4. Month by
   month, find the first month gross profit covers costs, and the cash used up until then. If cash on hand covers it,
   the company is default alive. If not, it is default dead: give the month cash runs out and the growth rate that
   would be enough. A company already making a profit passes; say so in one line.
4. **Give runway beside it**: cash divided by average net burn of the last three closed months, inputs shown. Compare
   with `tico finance metric runway_months` and explain a difference. Runway assumes no growth; the test assumes
   recent growth continues. Show both because they answer different questions.

**C. The 13-week cash forecast**

5. **Set 8 to 12 lines**, each with a named source (`references/thirteen-week-cash.md`): collections, other receipts,
   payroll, payroll taxes, payables, AI services and hosting, rent, card payments, debt, tax, other.
6. **Fill weeks 1 to 4 from documents**: invoices due, bills scheduled, the pay dates, the tax calendar. Fill weeks 5
   to 13 from history and commitments, and label them estimates.
7. **Time collections from how each customer really pays**, not from invoice terms. Time renewals from
   `tico finance report subscriptions` and vendor renewal dates. Take AI services and hosting from the last four
   weeks' run-rate and any planned change in use.
8. **Add a low and a high case.** Flex receipts and payments separately, then carry each case's closing cash forward
   week by week. In every week low is at or below base, and high at or above. Check it.
9. **Roll it weekly.** Drop last week, add week 13, and put last week's actual beside its forecast for each line. A
   line that missed by more than 10% gets a cause found in a source, or is re-based and marked re-based. Log every
   miss: line, week, forecast, actual, cause, owner.
10. **Find the lowest week** in each case and its gap to the minimum. Name what makes it the lowest.
11. **If the low case goes under the minimum in any week, add the levers block**: each action with its cash effect,
    the week it lands, what it costs and whether it can be undone. Payroll and taxes are never levers. Tell the owner
    the same day; do not wait for Monday.

**D. The budget**

12. **Build the driver tree** (`references/driver-budget.md`, section 2): revenue from customers and price; cost of
    revenue from units and unit costs taken from `build-the-pnl`; operating costs by function from people, bots and
    contracts. Ten to twenty drivers, each with its present value, its source and its assumption.
13. **Make three cases** by changing two or three drivers, not every line: base (the forecast), low and high. Run the
    default-alive test on each.
14. **Turn the base case into cash** by month: profit adjusted for when customers pay, when bills are paid, prepaid
    annual contracts and one-off items. The first three months must agree with the 13-week forecast.
15. **Propose budgets per line and owner** from the base case. For each: the amount, the period, who owns it, and
    what happens at the limit (warn, ask, decline or hold payment). Give bots' AI services budgets by bot from their
    real use and planned work. Children sum to no more than their parent.
16. **Get the owner's OK on the total, then set it**: `budget_create` or `budget_update` per line (a change is a new
    version from a date), `allocation_set` for a bot's or card's share, `projection_set` for each line's monthly
    forecast. Use `--dry-run` first.

**E. The rolling forecast, monthly**

17. **After the month is closed**, replace that month's forecast with actuals. Only closed months count as actuals.
18. **Update only the drivers whose evidence changed**, and write the evidence beside each. Do not rebuild every
    line. Extend the horizon by one month so it stays 12 months ahead.
19. **Keep versions.** Save each month's forecast as it was. Show this forecast against the last one and the budget:
    what moved, and which driver moved it. Never overwrite a forecast to hide a miss.

**F. Explain and learn**

20. **Write the budget note** for each line over the limit, in four parts: headline, driver, outlook, action
    (`references/variance-commentary.md`). Take the split of each difference into drivers from `build-the-pnl`; do
    not compute it a second way. About 120 words for the note as a whole. Anything without a found cause is named
    "unexplained".
21. **Before raising an alarm** about a spike or a shortfall, run `check-a-spend-spike-before-alarm`: timing,
    duplicates, a prepaid, a one-off.
22. **Score the forecast**: for each line, last period's error and its direction over the last six periods. A line
    wrong in the same direction four times running has a method problem: change the method and say what changed.
23. **Publish** (see Output) and file a task for each action that has an owner.

## Gates

- **No forecast without a confirmed opening balance.** "Will we make payroll" is a balance question.
- **Never invent a number.** A figure with no source is left out and named as missing. "Not supplied" is never zero.
- **Weekly cash uses bank actuals; the budget and rolling forecast use closed months only.**
- **The forecast is not the target and not the budget.** It is not moved toward either, in either direction.
- **Low is at or below base, and high at or above, in every week.**
- **Drivers of a difference sum to the difference.** What is left is shown as unexplained.
- **"Timing" is used only when the reversing item was found.**
- **A changed budget is stated.** A budget raised mid-month explains some variance by itself.
- **Estimates are labelled** wherever they appear: weeks 5 to 13, every driver assumption, every case.

## Autonomy

Do on your own:
- Build, roll and publish the cash forecast, the rolling forecast and the notes.
- Set and update projections (`projection_set`).
- Move budget between children of one parent when the parent's total does not change and the budget's owner is you
  or has agreed on the task. Tico asks the parent budget's owner when a change goes past the parent.
- File tasks for teammates and bots whose lines missed, and for actions with an owner.
- Raise the alert when cash, the lowest week or runway crosses a threshold. An alert is information: send it at
  once, with the levers.

Needs an OK (`tico task ask <id> --to <owner> --kind ok "<line>" --show page:<id> --version N`):
- **A new budget total, or raising any top-level budget.** It is the limit bots spend inside, so the owner sets it.
- **Changing what a budget does at its limit** from decline or hold payment to something looser.
- **Any lever that cannot be undone or that touches people or vendors**: delaying a vendor past terms, drawing on a
  credit line, cutting a contract, pausing a bot's work for a customer, a price change.
- **Sending a forecast outside the company** (a lender, an investor, a board).

Never: adjust actuals; change a closed month; present a target as a forecast.

## Output

1. **The weekly cash page**: `tico page publish reports/cash-<YYYY-MM-DD>.html --title "Cash" --cadence weekly
   --as-of <date>`. On it:
   - Five figures: bank available, lowest week and its date, gap to minimum, runway in months, alive or dead.
   - A line chart of closing cash for 13 weeks: base, with low and high as a band, and the minimum as a flat line.
   - The week-by-line table, weeks 1 to 4 marked as documents and 5 to 13 as estimates, with each line's source.
   - Last week: forecast against actual by line, misses with causes.
   - The levers block when the low case goes under the minimum. Decisions asked for.
2. **The monthly forecast page**: `reports/forecast-<YYYY-MM>.html`, `--cadence monthly`. On it:
   - The default-alive chart: gross profit and costs by month crossing, with cash below, for each growth case.
   - Budget, last forecast and this forecast by line for the year, with actuals to date.
   - A waterfall from last forecast to this one, by driver.
   - The driver table: value, source, assumption, what changed.
   - The budget note, and the forecast's own accuracy by line.
3. **Budgets and projections** set in Tico Finance, after the OK.
4. **`knowledge/cash-forecast.md`** (lines, sources, miss log) and **`knowledge/forecast-drivers.md`** updated.

## Checks before you finish

- The opening balance names its accounts and the time it was read.
- Every line and every driver names a source and a date. Nothing unsourced is in a total.
- Each week's closing cash is opening plus receipts minus payments, and next week's opening equals it.
- Low, base and high are in order in every week.
- Last period's forecast was compared with actuals before the new one was written.
- The default-alive result shows its three inputs, the growth rate used and where it came from.
- The first three months of the budget's cash view agree with the 13-week forecast, or the gap is explained.
- Budget children sum to no more than their parents.
- Every note's numbers match the report, and its drivers sum to the difference.
- Anything that needs a decision is asked for by name, once.

## Example (invented, for illustration)

Kiln & Co, Monday 2026-11-16. Bank available 310,000.00 across two accounts, read 08:35. Minimum cash 80,000.00.

- **Default alive.** October closed: revenue 70,800.00, gross margin 68% (gross profit 48,144.00), operating costs
  80,000.00, so a loss of 31,856.00 a month. Costs held flat. At 4% growth a month (three-month average), gross
  profit passes costs in month 13, after about 207,700.00 of cash is used: cash bottoms near 102,300.00. Default
  alive. At 2% (six-month average), break-even is month 26 and needs about 427,100.00: cash runs out in month 13.
  Default dead. The growth needed is about 2.8% a month. Runway at the three-month average burn of 31,900.00: 9.7
  months.
- **This week.** Opening 310,000.00; collections 16,400.00 (aging; two customers usually pay 10 days late);
  payables 21,300.00 (payment run of 11-17); no payroll (next pay date 11-27). Closing 305,100.00.
- **Lowest week** in the base case: week of 2027-01-18, closing 233,900.00; low case 221,300.00, high 240,500.00.
  All above the minimum. No levers block.
- **Last week.** Collections forecast 18,000.00, actual 14,200.00, a miss of 21%. Cause: one payment of 3,800.00
  arrived on 11-16 (bank row dated 11-16). Timing; logged.
- **Budget note, October.** AI services 10,950.00 against a budget of 9,000.00 (+1,950.00, +22%). Driver: a model
  change raised cost per task (rate), with a small rise in tasks (volume), per the P&L's split. Outlook: 11,000.00 a
  month at this rate, about 4,000.00 more over budget by year end. Action: the owner is asked to choose between
  raising the budget from December and moving two bots to a cheaper model; both shown with their cost.
