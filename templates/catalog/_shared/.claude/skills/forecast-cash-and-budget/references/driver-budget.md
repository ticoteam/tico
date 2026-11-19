# Default alive, the driver-based budget and the rolling forecast

## 1. The default-alive test

The question, from Paul Graham's 2015 essay: if costs stay where they are and revenue keeps growing at its recent
pace, does the company reach profit on the cash it has now? If yes it is default alive. If no it is default dead, and
the owner needs to know that before anything else on the page.

**Inputs** (all from the last closed month, each with its source):

- R: revenue for the month.
- m: gross margin, as a share of revenue, from `build-the-pnl`.
- C: operating costs for the month (everything below gross profit, leaving out one-off items, which are named).
- g: monthly revenue growth. The average of the last three closed months' month-on-month growth; the last six as a
  second case.
- Cash: bank available today.

**Method** (a table, one row per month n = 1, 2, 3 ...):

1. Revenue in month n = R x (1 + g)^n.
2. Gross profit = revenue x m.
3. Result = gross profit - C.
4. Cash at month end = last month's cash + result.
5. Stop at the first month the result is zero or more. That is the break-even month.
6. Alive if cash never goes below zero before then. Better: alive with margin if it never goes below the minimum
   cash line.

**Report**: the break-even month, the lowest cash point and its month, and the verdict. If dead: the month cash runs
out, and the growth rate that would just be enough (try rates in steps of 0.1 points until cash stays above zero).
Also show the other way out: the cost level at which today's growth is enough.

**Worked example (invented)**: R 70,800.00, m 68%, so gross profit 48,144.00. C 80,000.00. Cash 310,000.00.

| Growth a month | Break-even month | Cash used before then | Lowest cash | Verdict |
|---|---|---|---|---|
| 4.0% | 13 | 207,662 | 102,338 | Alive |
| 2.8% | 19 | 301,856 | 8,144 | Alive, barely; under any sensible minimum |
| 2.0% | 26 | 427,092 | Runs out in month 13 | Dead |

**Limits to state on the page**

- It holds costs flat. A company that plans to hire is testing a different question: run it again with the plan.
- It holds gross margin flat. If AI or hosting cost per unit is moving, use the margin trend.
- It treats profit as cash. Annual prepayments, slow-paying customers and debt repayments move the answer; the
  13-week forecast and the budget's cash view carry those.
- Growth as a percentage usually slows as a company gets bigger. Three months of history is thin. Show two cases.
- A company with little or no revenue cannot be tested this way. Say so, and show runway only.
- A company that is already profitable passes. Show instead how many months of costs its cash covers.

## 2. The driver tree

A driver-based budget says: these few numbers produce every line. Change a driver and the lines follow.

**Revenue**

- Recurring revenue = customers x average price per customer.
- Customers at month end = customers at start + new - churned.
- New customers = leads x conversion rate, or the recent monthly average while the funnel is not measured.
- Churned = customers at start x monthly churn rate.
- Expansion = customers x share that upgrade x upgrade amount, if the company has it.
- Usage revenue = units used x price per unit.
- Services revenue = projects x price, or hours x rate.

**Cost of revenue** (unit costs come from `build-the-pnl`; do not derive them a second way)

- AI services for delivery = units of work x AI cost per unit.
- Hosting = customers (or usage) x hosting cost per unit, plus the fixed base.
- Payment processing = card revenue x fee rate.
- Support and delivery people and bots = their cost, stepped up when customers pass a stated number.

**Operating costs, by function**

- People = headcount x loaded cost, with hire dates.
- Bots = number of bots x their AI services budget, plus the Computers they run on.
- Marketing = spend per channel, tied to the new-customer driver (`measure-unit-economics` holds the cost per
  customer).
- Software and contracts = the subscriptions report plus signed renewals.
- Everything else = the last six months' average, named as such.

**Cash**

- Receipts = revenue shifted by days to collect, plus annual prepayments in their month.
- Payments = costs shifted by days to pay, with annual contracts in their month and payroll on pay dates.
- Plus: tax payments, debt, equipment, owner draws or investment.

**Rules for drivers**

- Ten to twenty in all. If a driver moves no line by more than 1% of revenue, fold it into "other".
- Each has: its value now, where that came from, the assumption for the plan, and who can change it in real life.
- A driver's present value always comes from the book or a named system, never from memory.
- An assumption that departs from the last six months' trend carries its reason: a signed contract, a launched
  feature, a hire. "We expect improvement" is not a reason.
- Save them in `knowledge/forecast-drivers.md` in one table.

## 3. Cases

- **Base** is the forecast: what the evidence says will happen. Not the hope and not the fear.
- **Low** and **high** each change two or three drivers by amounts seen in the company's own history (the worst and
  best quarter of the last two years, if it has them). Name the drivers changed.
- **A named scenario** answers a question someone asked: "we lose our largest customer", "we hire two engineers in
  March", "AI cost per task falls 30%". One changed driver, the effect on profit, on lowest cash and on the
  default-alive verdict.
- Do not make a case by multiplying every line by a percentage. That tests nothing.

## 4. From the plan to budgets in Tico

In Tico Finance a budget hangs off a P&L line and can be split by team, teammate (person or bot) or vendor. What is
left in a budget is its amount, minus what has been spent, minus holds for scheduled payments and orders, minus
shares given out and not yet used. An expense counts in the period it belongs to: an annual 1,800.00 contract counts
150.00 a month.

For each line of the base case, propose:

| Field | How to choose |
|---|---|
| Amount and period | The base case for the line. Month for steady costs; quarter or year for lumpy ones |
| Owner | The teammate or bot who decides the spending. One owner per budget |
| At the limit | **warn** for costs that must not stop (hosting for customers); **ask** for discretionary lines; **decline** or **hold payment** where overspending is worse than stopping (experiments, video production, a new bot's AI services) |
| Warn level | Default 80% used |
| Rollover | Yes for project budgets; no for run costs |
| Children | By team, then by bot or person. Children sum to no more than the parent; leave a small unassigned part at the parent for surprises |

**Bots' AI services budgets** deserve their own table: each bot's real use over the last four weeks, its planned
work, and the proposed monthly amount. A bot that delivers to customers scales with volume: budget it per unit of
work x expected units, and let it warn, not stop. A bot doing internal work gets a fixed amount and may stop.

**Headroom, not padding.** Budget each line at the forecast, and hold one visible reserve at the top (default: 5% of
operating costs) that the owner releases. Padding every line hides the reserve and it gets spent.

Set them after the owner's OK: `budget_create` (new), `budget_update` (a new version from a date; history is kept),
`allocation_set` (a bot's, teammate's or card's share), `projection_set` (`account_id`, `month`, `amount`, and how it
was made: trend, run rate, recurring, fixed or manual). Run each with `--dry-run` first and read
`tico finance act help --payload '{"kind":"<kind>"}'` for the fields.

## 5. The rolling forecast

Each month, after the close:

1. **Actuals in.** The closed month replaces its forecast. Nothing from an open month counts as actual.
2. **Evidence first.** List what changed in the world: contracts signed or lost, hires, price changes, a unit cost
   that moved, a new channel. Each maps to a driver.
3. **Update those drivers only.** Leave the rest. A full rebuild every month is slow and hides what changed.
4. **Extend one month**, so the view is always 12 months ahead and the year end is not a cliff.
5. **Compare three ways**: against the budget (are we on plan), against the last forecast (what did we learn), and
   last forecast against actual (how good are we at this).
6. **Keep it apart from targets.** If the forecast says the target will be missed, that is the finding. The response
   is an action, not a higher forecast.
7. **Budgets do not move by themselves.** A forecast above budget is a request to the owner to raise the budget, cut
   the spend or accept the stop. Until then the budget stands.
8. **Save the version** with its date. Never edit an old one.

Large one-time projects (a migration, a launch, an office move) are forecast as their own block with their own
dates, so a slip in the project does not look like a change in the running business.

## 6. When to re-plan

Re-open the budget itself, not just the forecast, when one of these happens:

- The default-alive verdict changes.
- A driver in the base case is off by more than its low or high case for two months running.
- Revenue for the year is forecast more than 15% away from budget (default; tune it).
- The company raises money, takes on debt, or loses a customer worth more than 10% of revenue.
