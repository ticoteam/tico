---
name: build-the-pnl
description: Build the management P&L the owner runs the business from. It is cut by segment or product, splits gross margin by revenue type, puts hosting, payment fees, support and only the AI that delivers or supports the product in cost of revenue, shows cost and profit per unit, and explains every large movement with drivers that add up to the total. A GAAP mapping is a secondary view. It is not a tax return. Use when a task says P&L, profit and loss, income statement, monthly report, margins, gross margin, unit economics, unit cost, cost per customer, AI cost, hosting cost, COGS, segment or product profitability, "why did profit move", "where are we making money", or a variance against last month, last year or the budget.
metadata:
  function: finance
  roles: [finance-owner, controller, fpa-analyst, revenue-accountant, pricing-analyst, strategy-analyst]
  actions: [read, draft, change-records]
  review: none
---
# Build the P&L

When to use: each month after the close, on request for a flash view of an open month, and whenever someone asks where
the business makes or loses money.

Words used here: **the company** is the business whose books these are. **The team** is everyone in Tico, people and
bots. **The book** is the company's ledger in Tico Finance. **A unit** is the thing the company sells one of: a paying
customer, an order, a job, a seat. **A segment** is one cut of the business the owner makes decisions about: a product,
a plan, a customer type, a region.

## What great looks like

The owner reads one page and knows three things: which parts of the business earn money, what one more unit costs to
serve, and what changed since last month and why. Then they decide something: raise a price, cut a product, move an AI
workload to a cheaper model, stop a discount.

An expert's management P&L differs from the bookkeeping P&L in four ways.

- **It is cut the way decisions are made.** Segments are columns. A blended total hides a product that loses money
  behind one that earns it.
- **Cost of revenue is honest.** Everything it takes to deliver to a customer who already bought is above the gross
  margin line: hosting, the AI that delivers or supports the product, payment fees, support, delivery labour. AI
  used to run the company (marketing, finance, engineering bots) is operating expense. Nothing is parked in
  "Software" to make margin look better.
- **It shows units, not only totals.** Revenue per unit, each cost per unit, profit per unit, with a 12-month trend.
  Totals tell you what happened. Unit costs tell you what will happen when you grow.
- **Every movement is explained by drivers that add up.** Volume, price, mix, one-time, timing. The pieces sum to the
  movement to the cent, each names a document, and what nobody can explain is shown as unexplained.

It also ties. The page's bottom line equals the book's net income. If it does not, it is not a P&L, it is a slide.

This P&L is for running the business. It is not a tax return and it is not a set of audited statements. The GAAP view
in step 10 is a mapping for outsiders who need it.

## First time at a company

Read `knowledge/pnl-design.md`. If it has answers, skip this. If not, ask the owner these in one numbered message
(`tico task ask <id> --to <owner> "<questions>" --version N`), save the answers there with the date, and build on the defaults until
they reply.

1. **How do you want the business cut?** Default: by product or revenue stream, using the tags the book already has
   (class, project, location, item). With no tags: one segment per revenue account. With one revenue account: one
   segment, and the page says so.
2. **What is one unit?** Default: a paying customer in the month for a subscription; an order or a job for one-off
   sales. Name where the count comes from (the billing tool, the product database, the KPI on Goals).
3. **What counts as cost of revenue?** Default: the list in `references/pnl-layout.md`: hosting and infrastructure, AI
   and model usage that serves customers, payment processing fees, support and onboarding, third-party software or data
   inside the product, delivery labour. Not sales commissions, marketing, building the product, or overhead.
4. **Which bots and teams deliver or support the product, and which run the company?** Default: read the team chart.
   Bots and people in support, onboarding and delivery are cost of revenue. Sales, marketing, product, engineering,
   finance and the built-in helper bots are operating expense. This answer splits the AI bill.
5. **Shared costs: allocate or leave?** Default: shared delivery costs (one hosting bill for two products) are split by
   a usage measure if one exists, else by revenue share, and labelled "allocated". Shared operating expenses are never
   pushed into segments; they sit in a column called Shared.
6. **When does a movement need an explanation?** Default: a line that moved more than 10 percent **and** more than
   1 percent of the month's revenue, against last month, the same month last year, or the budget. Both tests must pass.

## Steps

Tools: `tico finance report`, `tico finance search`, `tico finance metric` and `tico finance act` (the MCP tools
`tico_finance_*` take the same inputs). Amounts are integer minor units: 315000 is 3,150.00, so divide by 100 before
you write a figure. Every call runs with your own Finance view, so a total you may not fully see comes back marked
`excl: true`.

1. **Check the month is fit to report.**
   - `tico finance report close --month <YYYY-MM>`. A closed month is actuals. An open month is a **flash**: build it if
     asked, and put "Flash, month not closed" in the page title.
   - `tico finance metric uncategorized_count` and `tico finance metric unattributed`. Rows waiting for a category, or
     lines with no team, distort every split below. Clear them first with `categorize-a-feed-row` and the `attribute`
     actions, or state the amount still unplaced at the top of the page.
   - Look at Other expenses. If it holds more than 2 percent of the month's expenses, recategorize before you go on.
   - Note the basis the report returns (`cash` or `accrual`). A management P&L wants accrual, so revenue and its costs
     land in the same month. On a cash-basis book, say so in the first line: annual prepayments will show as spikes.
2. **Pull the book.** For the month, with the prior month:
   `tico finance report pnl --start <first day> --end <last day> --compare prior`. Then the same month last year, and
   `tico finance report budget_vs_actual --start <first day> --end <last day>`. Keep each result's as-of time.
3. **Pull the cuts.**
   - Segments: `tico api GET "finance/reports/pnl_by?by=class"` (or `project`, `location`, or a pair such as `location_class`).
   - Who spent it: `tico finance report spend_by --by team`, `--by bot`, `--by vendor`, `--by account`.
   - AI by bot: `tico api GET usage?from=<YYYY-MM-DD>&to=<YYYY-MM-DD>&group=bot`. This is an estimate from tokens, not
     a bill. Use it for the shares; take the total from the book.
   - Units: the count from the source named in question 2, with its date. Never derive a unit count from revenue.
4. **Map every account to one management line.** Use the layout in `references/pnl-layout.md` and keep the mapping
   table in `knowledge/pnl-design.md`: account code, account name, management line, rule. Each account maps to exactly
   one line, or to a written split rule. Run the mapping check: the management lines add up to the book's net income
   for the month. If they do not, an account is unmapped or mapped twice. Stop and fix the map.
5. **Split the mixed accounts.** Some accounts hold both cost of revenue and operating expense.
   - **AI services.** Tico's default chart puts all AI under Cost of revenue. Only the AI that serves customers belongs
     there: the product's own model calls, and the bots named in question 4 as delivery or support. AI for a marketing
     bot is marketing expense; AI for an engineering bot is engineering expense. Split the booked total by the shares
     from `spend_by --by bot` and Usage.
   - **Hosting.** Production is cost of revenue. Staging, CI and development are engineering expense.
   - **People and contractors.** By what they do, from the team chart and question 4. Payroll's private lines stay
     private: if the payroll total comes back `excl: true`, show the line as "partly hidden" and never back into it.
   - **Payment fees.** If the processor pays out net, gross the revenue up and show the fee as cost of revenue.
   Write each split rule once in `knowledge/pnl-design.md` and apply it the same way every month.
6. **Build the segment columns.** Direct revenue and direct cost go to their segment by tag. Shared delivery costs are
   allocated by the rule from question 5 and labelled. Whatever has no honest home goes in Shared or Unallocated; do
   not force it. Check: the columns add across to the total, line by line.
7. **Split the gross margin.** Show revenue, cost of revenue and gross margin percent for each revenue type separately:
   subscription, usage, services, one-off sales. Then for each segment. A services line at 35 percent blended into a
   subscription line at 75 percent tells the owner nothing about either. Show the blend last, as the sum.
8. **Compute the unit lines.** For each segment: revenue per unit, each cost of revenue line per unit (hosting, AI,
   payment fees as a percent of revenue, support), gross profit per unit, and the same for the last 12 months. Where a
   finer unit exists, add it: AI cost per task, per conversation, per 1,000 requests. State each formula on the page.
   A unit cost that rose three months running is a finding even inside the threshold.
9. **Explain the movements.** For each line that passes the test in question 6, split the movement into drivers with
   `references/variance-drivers.md`: volume, price or rate, mix, new or discontinued, one-time, timing, currency.
   - The drivers plus the unexplained remainder equal the movement, to the cent.
   - Each driver names its document: the invoice, the contract, the usage export, the payroll run.
   - Each driver is labelled **timing** (reverses next month), **one-off** (will not recur) or **run-rate** (will
     recur, so the forecast must change).
   - A driver that looks like an error goes to `review-the-books-for-unusual-entries`, not into the story.
   Revenue movements in a subscription business use `bridge-recurring-revenue` for new, expansion, contraction and
   churn.
10. **Add the GAAP view, second.** One small table maps management lines to GAAP captions (Revenue, Cost of revenue,
    Sales and marketing, Research and development, General and administrative, Other income and expense) and lists each
    place the two differ, with the amount. Any figure that is not a GAAP figure (contribution margin, anything
    "adjusted", anything with an item left out) is labelled as such and reconciled to net income on the same page. See
    `references/pnl-layout.md`. If the company has no outside reader, the table is three lines; do not grow it.
11. **Tie out.** All five must hold before you publish:
    - Management lines add up to the book's net income for the month (step 4).
    - Segment columns add across to the totals (step 6).
    - Revenue ties to billing. If `reconcile-billing-to-the-books` ran in the close, cite it. If not, compare invoiced
      in the month with booked revenue and name the gap.
    - Drivers plus remainder equal each movement (step 9).
    - The AI cost in the book is within reason of the Usage estimate. A gap is normal (one is bills, one is an
      estimate), so state both numbers and the cause you found.
12. **Turn findings into work.** For each finding that needs a decision or a fix, file a task for the teammate who owns
    that line: `tico task create --owner <teammate> --title "<the decision, with the number>"`. A finding with no owner
    and no task is decoration. Run-rate drivers go to whoever keeps the forecast (`forecast-cash-and-budget`).
13. **Publish the page** (see Output), then note on the task: the as-of time, the basis, what is unexplained, and the
    tasks filed.

## Gates

- **No tie, no P&L.** If the management total does not equal the book's net income, nothing is published.
- **An open month is a flash** and is titled that way. It never feeds a budget, a board update or a forecast.
- **No invented numbers.** A missing unit count, a missing export or a hidden total is written as "not supplied" or
  "partly hidden". It is never estimated in silence and never shown as zero.
- **One blended gross margin is not allowed** when the company has more than one revenue type.
- **An allocation is labelled as one.** If more than a quarter of a segment's cost of revenue is allocated rather than
  direct, the segment's margin carries the note "mostly allocated".
- **Definitions do not move to make a number move.** A change to what counts as cost of revenue, a segment or a unit is
  the owner's decision. When it changes, restate the prior 12 months the same way and say so on the page.
- **Non-GAAP is labelled and reconciled** wherever it appears.
- **A closed month is not edited.** A correction goes in the open month with a note, unless the owner reopens.
- **Outside readers.** A P&L that goes to a lender, an investor or a tax preparer is checked by the company's
  accountant first, and sending it is an OK (see Autonomy).

## Autonomy

Do on your own:
- Build, tie out and publish the P&L page to the team every month, and a flash whenever asked.
- Refine the account mapping and split rules inside the saved design, and record each change with its date.
- Categorize rows, attribute lines to teams and bots, and propose posting or attribution rules, as Tico's Finance
  rules allow. If an action comes back `needs_approval`, Tico has already asked the right person; carry on with the
  rest and note what waits.
- File tasks for line owners and for the forecast keeper.

Needs an OK (`tico task ask <id> --to <person> --kind ok "<line>" --show page:<id> --version N`):
- Sending the P&L or the GAAP view outside the team.
- Reopening a closed month. Tico always asks the owner for this; no rule turns it off.

A question, not an OK: changing the design (segments, the unit, what counts as cost of revenue). Ask once, with your
recommendation and what it does to the last 12 months, and keep reporting on the current design until answered.

## Output

One HTML page, published with
`tico page publish reports/pnl-<YYYY-MM>.html --title "P&L <Month YYYY>" --cadence monthly --as-of <last day> --task <id>`.
Numbers first, words second. In this order:

1. **Five figures** across the top, each with its change on last month: revenue, gross margin percent, operating
   income, cash, runway in months (`tico finance metric cash`, `runway_months`).
2. **The segment table**: lines down, segments across, a Shared column and a Total column.
3. **Gross margin by revenue type**: one bar per type, the percent on the bar.
4. **Unit economics**: a small table per segment and one 12-month line chart of cost per unit by cost type.
5. **What moved and why**: one row per explained line with its drivers, their labels, the document, and the
   unexplained remainder.
6. **Decisions**: the tasks filed, each with its owner.
7. **GAAP view and tie-outs**: the mapping table and the five checks with their results, at the bottom.

Also: an updated `knowledge/pnl-design.md` when a mapping or rule changed.

## Checks before you finish

- The page states the month, the basis, the as-of time, and "Flash" if the month is open.
- Management total equals book net income, and the page shows both numbers.
- Segment columns add across. Drivers plus remainder equal each movement.
- Every unit count names its source and date. Every driver names a document. None says "probably".
- AI is split between cost of revenue and operating expense by the saved rule, not left where the chart put it.
- Each revenue type has its own gross margin.
- No amount was copied in minor units.
- Every finding has an owner and a task, or is marked "no action".
- Nothing left the team without an OK.

## Example (invented, for illustration)

Harbor Desk sells a scheduling app by subscription and sells onboarding as a service. October 2026, accrual, closed.

| | Subscription | Onboarding | Shared | Total |
|---|---|---|---|---|
| Revenue | 61,800 | 9,000 | | 70,800 |
| Hosting | 4,120 | | | 4,120 |
| AI and model usage | 7,828 | | | 7,828 |
| Payment fees | 1,854 | 270 | | 2,124 |
| Support | 3,090 | | | 3,090 |
| Delivery contractors | | 5,400 | | 5,400 |
| **Gross profit** | **44,908** | **3,330** | | **48,238** |
| Gross margin | 72.7% | 37.0% | | 68.1% |
| Sales and marketing | | | 14,200 | 14,200 |
| Product and engineering | | | 11,500 | 11,500 |
| General and administrative | | | 6,300 | 6,300 |
| **Operating income** | | | | **16,238** |

Split: the AI services account held 10,950. Usage by bot put 7,828 on the product and the support bot (cost of
revenue) and 3,122 on marketing, engineering and finance bots (inside the three operating expense lines).

Units: 412 paying customers (billing export, 2026-10-31). Per customer: revenue 150.00, hosting 10.00, AI 19.00,
support 7.50, gross profit 109.00.

What moved: AI and model usage +1,828 on September (6,000 at 400 customers, 15.00 each).
- Volume: 12 more customers x 15.00 = 180. Run-rate. Billing export.
- Rate: 4.00 more per customer x 412 = 1,648. Run-rate. Usage export: the auto-reschedule feature, live 2026-10-06,
  makes about three model calls per booking.
- Remainder: 0. Check: 180 + 1,648 = 1,828.

Task filed for the Feature Owner: "AI cost per customer rose from 15.00 to 19.00 after auto-reschedule. Route it to a
smaller model or price it as an add-on." Sent to the forecast keeper as a run-rate change.

Tie-out: operating income 16,238, less a one-time legal fee 2,000, plus interest 150 = 14,388, the book's net income.
