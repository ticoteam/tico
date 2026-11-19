---
name: measure-unit-economics
description: "Compute and judge unit economics from live data. Covers cost to acquire a customer (CAC), blended and paid; lifetime value (LTV) on gross margin; payback in months with churn and expansion counted; cohort retention matrices for customers and revenue; net and gross revenue retention (NRR, GRR); and channel attribution under one fixed model, settled by holdout tests. Use when a task says unit economics, CAC, LTV, LTV to CAC, payback, cohort, retention curve, churn, NRR, net revenue retention, attribution, incrementality, holdout, is this channel worth it, can we afford to spend more, or asks for the monthly growth numbers, a board or investor metric, or a comparison with a benchmark. Every input is read through Tools in the same run (Growth, Finance, the database, billing) and nothing is typed in. Verdicts come from the company's own history and targets. Outside benchmarks are shown with their date, as context only."
metadata:
  function: growth
  roles: [growth-manager, segment-marketing-owner, product-analyst, fpa-analyst, strategy-owner, investor-relations-manager]
  actions: [read, change-records]
  review: none
---
# Measure unit economics

When to use: each month after Finance closes it, before any decision to raise or cut acquisition spend, before a number
goes to a board or an investor, and whenever someone asks whether a customer is worth what it costs to get one.

Words used here: **the company** is the business being measured. **The team** is everyone in Tico, people and bots.
**Growth** is Tico's Growth page and its read (`tico growth read`). **A cohort** is the customers who first paid in the
same month. **Age** is months since that first payment: month 1 is the first paid month.

## What great looks like

An expert answers one question: does a customer pay back more than it cost to get them, how fast, and how sure are we?
Five habits separate that answer from a dashboard.

- **Every figure is a division, and both halves match.** Same customers, same period, same segment, top and bottom.
  Most wrong unit economics are a mismatch: this month's spend over last quarter's customers, revenue LTV over loaded
  cost.
- **Cohorts, not averages.** A blended churn rate hides that new customers leave fast and old ones never do. The
  expert reads what each cohort actually paid, month by month, and only then projects.
- **Margin, not revenue.** A customer is worth the gross margin they bring, because the cost of serving them is not
  available to pay back the cost of getting them.
- **Two CACs, always.** Blended CAC says whether the business works. Paid CAC says whether the next dollar works.
  Showing one alone misleads.
- **Honest about cause.** Attribution is a fixed rule, written down, never changed to flatter a channel. When the rule
  and a platform disagree and money rides on it, a holdout test settles it.

The page ends in a decision: spend more, spend less, hold, fix retention first, or fix the data first.

## First time at a company

Read `knowledge/unit-economics.md`. If it holds dated answers, skip this. Otherwise read Growth first
(`tico growth setup`, `tico growth segment list`, `tico growth source list`, `tico growth settings`) and do not ask what
it already answers. Ask the rest in one numbered message (`tico task ask <id> --to <owner> "<questions>" --version N`), save each
answer with its date, and run on the defaults until a reply comes.

1. **When is someone a customer, and when have they churned?** Default: a customer from their first successful
   payment. Churned on the day a subscription ends, or after 90 days with no paid order where there is no
   subscription. A segment's own rule in Growth (`churn_days`) wins.
2. **Which system is the truth for customers and revenue, and which for costs?** Default: the billing Tool or the
   product database for customers and revenue; Tico Finance for costs and margin.
3. **What counts as the cost of getting a customer?** Default: fully loaded. Ad spend, promotions at their real cost,
   agencies and software used to acquire, pay for sales and marketing people, and the run cost and hours of the bots
   that own channels. Money spent keeping customers is retention spend and stays out.
4. **What is cost of revenue, for gross margin?** Default: what the P&L puts above gross profit. If
   `knowledge/pnl-design.md` exists (the `build-the-pnl` skill), use its list.
5. **Which touch gets the credit for a customer?** Default: the last tagged, non-direct touch in the 30 days before
   first payment; else a referral or promo code; else the customer's own answer to "how did you hear about us"; else
   Unknown. For sales cycles over 30 days: a 90-day window, with the first touch kept in a second column.
6. **What payback and LTV to CAC is the company aiming for?** Default: no target. Judge against the company's own
   last twelve months and the floors in step 12 until the owner sets a target on a goal.

## Steps

Formulas and worked arithmetic are in `references/formulas.md`. Keep every query in `queries/` so next month runs the
same one. A name written `skills/<name>` is a narrower Tico catalog skill: use it if you have it; if you do not, the
step here says enough to do the work.

1. **Read the settings and the month.** `knowledge/unit-economics.md`, then `tico growth read --scope all --detail
   retention,series`, and the same with `--scope segment:<slug>` for each segment. Work on the last full month. A
   current month is partial and gets no verdict.
2. **Check every source is live.** `tico growth source list` shows each source as fresh, waiting, stale or failing.
   `tico finance report close --month <YYYY-MM>` says whether the month is closed. A stale or failing source is fixed
   or named before anything is computed from it. A month Finance has not closed is labelled "pre-close".
3. **Pull the customer and revenue rows.** One row per customer per month: customer key, first paid month, segment,
   channel, which rule named the channel (`utm`, `code`, `asked` or `unknown`), plan, month, net revenue (refunds,
   credits and discounts taken off, taxes out). Use a saved query (`tico db <database> --query <name>`) or the billing
   Tool. Run the sanity check in
   `skills/define-a-metric-as-a-query`: the month's revenue from these rows against `tico finance report pnl` for the
   same month. Within 2% it ties. Beyond that, name the difference; the page says "not reconciled".
4. **Pull acquisition cost.** By channel and month, from Finance (`tico finance report campaigns`, `spend_by`) and
   Growth's cost rows. Finance amounts are in minor units: 315000 is 3,150.00. Mark each cost paid (money spent to buy
   reach: ads, sponsorships, affiliates, promotions) or not paid (people, bots, tools, content). Leave out retention
   spend.
5. **Read gross margin.** `tico finance report pnl` for the month and the trailing three months; by segment with
   `pnl_by` where the book is tagged. Margin = (net revenue minus cost of revenue) divided by net revenue. If the P&L
   has no cost of revenue split, stop and file a task for the Finance Owner. Until it lands, use Growth's
   `margin_pct` and label every figure that depends on it an estimate.
6. **Compute both CACs.** Blended CAC = all acquisition cost divided by all new customers, including organic,
   referred and Unknown. Paid CAC = paid cost divided by the new customers the attribution rule gives to paid
   channels. Show each twice when effort is material: money only, and with people and bot cost. By segment, use
   Growth's direct CAC (the segment's own channels) for payback and loaded CAC (plus its share of company-wide spend)
   to compare segments. If the median time from first touch to first payment is over 30 days, lag the cost by that
   many whole months and say so.
7. **Build the two cohort matrices.** Rows are cohorts, columns are ages. Customer retention: the share of the cohort
   still paying at each age. Revenue retention: the cohort's net revenue at each age divided by its month 1 revenue;
   it can pass 100%. Layout and reading rules are in `references/cohort-matrices.md`. A cell the cohort is too young
   for is "not yet". A cohort under the minimum size (30 unless Growth's `min_cohort` says otherwise) shows counts.
8. **Compute NRR and GRR.** NRR = this month's recurring revenue from the customers who were paying twelve months ago,
   divided by what those same customers paid then. GRR is the same with each customer capped at what they paid then,
   so it never passes 100%. New customers are in neither. If the opening set is under 30 customers, show dollars and
   accounts. If one customer is over 10% of the opening revenue, show the figure with and without them. For the
   monthly movement, use `skills/bridge-recurring-revenue`.
9. **Compute LTV on gross margin.** Observed LTV at age m = the cohort's cumulative net revenue per starting
   customer through month m, times gross margin. Lead with observed LTV at 6, 12 and 24 months, using only the ages
   a cohort has reached. Projected LTV carries
   the last observed monthly decay forward, capped at 60 months as Growth does, and is always labelled projected.
   Growth's own observed LTV is net revenue: multiply by margin before using it. Churn and expansion are already in
   these numbers, because they are what the cohort really paid.
10. **Compute payback in months.** For each cohort, find the first month where cumulative gross margin per starting
    customer reaches that cohort's CAC. Do it for blended and for paid CAC. Beside it, write the share of the cohort
    still paying at that month. A cohort that has not got there reads "not yet: 62% recovered at month 5". If the
    projection never reaches CAC within 60 months, it reads "does not pay back at current retention". Show the simple
    figure too (CAC divided by monthly gross margin per customer), labelled simple: it ignores churn and reads
    short. Where customers prepay a year, add cash payback as its own line.
11. **Apply the attribution rule and test it.** One rule, from the settings, for every customer and every month. Count
    customers in the system of record, never in an ad platform. Put platform-claimed and self-reported counts in
    their own columns. Report Unknown's share. Then check the three holdout triggers in
    `references/attribution-and-holdouts.md`; if one fires, plan the test there. Before any sentence says a channel
    "drove" something, run `skills/check-attribution-before-a-claim`.
12. **Judge each figure, in this order.** (a) Against the company's own history: this month against the trailing
    three-month average (Growth's `vs_3mo`: within 10% is flat) and against the same cohort age a year ago. (b)
    Against the target on its goal (`tico kpi show <id>`), if one is set. (c) Against the floors that hold for any
    business: at least half the cohort is still paying at the payback month; payback is shorter than the runway
    (`tico finance metric runway_months`); LTV to CAC of 1.0 on gross margin is break-even, not good. (d) Outside
    benchmarks last, each with its source, year, sample and definition, from `references/benchmarks.md`. A
    benchmark never sets the verdict and never supplies a word like "strong" or "anemic".
13. **Choose the decision.** Take the first that applies, and say why in one sentence.
    - *Fix the data first*: revenue is not reconciled, Unknown is over 25%, or a main input is an estimate.
    - *Fix retention first*: a floor in step 12 fails, or newer cohorts keep fewer customers than older ones did at
      the same age.
    - *Spend less*: paid CAC is up against history and paid cohorts miss the target (or, with no target, a floor).
    - *Spend more*: paid cohorts pay back inside the target (or pass every floor), paid CAC is flat or down, and no
      holdout trigger is open on the channel that would get the money.
    - *Hold*: none of the above.
    The decision goes on a task for whoever owns the channel's budget. If that is you and the move is inside the
    budget, make it.
14. **Record and publish.** Push what Growth should show from the same rows (`tico growth rows push`,
    `tico growth costs push`; the read's `you_may` says whether you may). Find the company's KPIs for these figures
    (`tico kpi list`) and log each reading you own with its period and evidence (`tico kpi log <id> <value> "<how
    computed>" --period-start <date> --period-end <date>
    --evidence <query or page> --quality measured`); a correction uses `--supersedes`. Publish the page (Output).
    File one task per gap, for the teammate who owns the source.

For one segment's weekly read between monthly runs, `skills/read-cost-per-customer-for-a-segment` is enough.

## Gates

- **Nothing is typed in.** Every input was read from a named system, view or saved query in this run, with its read
  date. A number someone said in chat, or a Growth month whose origin is typed or `estimate`, makes every figure built
  on it an estimate, labelled so, and files a task to connect the source. A computed number is never entered by hand.
- **Matching halves.** No ratio mixes scopes: the cohort, the segment, the period and the kind of CAC are the same on
  both sides, and the page names them.
- **Margin or it is not LTV.** A revenue figure is labelled "revenue per customer" and is never divided by CAC.
- **Both CACs or neither.** Blended and paid are shown together, with what each counts.
- **Mature cohorts only.** No rate from a cohort younger than the window or smaller than the minimum. "Not yet" and
  "too few", with the counts. Blank is never zero.
- **One attribution rule.** It changes only by a proposal the owner confirms, and then the last twelve months are
  restated under both rules side by side. Platform counts are never added together and never replace the count from
  the system of record.
- **Unknown over 25% of new customers:** paid CAC and every channel figure carry "attribution weak", and the next
  action is `skills/ask-how-they-heard` and a tagging fix, not a budget move.
- **Revenue ties to Finance** within 2%, or the page says "not reconciled" and names the gap.
- **Benchmarks carry a date and a definition,** and none colours a verdict.
- **Customer keys only.** No names, emails or phone numbers in a query result, a file or the page.

## Autonomy

Do on your own: read every source; save queries; compute; push rows and costs to Growth where `you_may` allows; log and
correct the KPI readings you own; publish the page inside the team; file tasks for source owners; propose a source
(`tico growth source propose`); design and run a holdout on a channel you own, inside its budget, since a holdout
spends less, not more.

Someone else decides:
- **Numbers leaving the team** (an investor, a lender, a board pack): an OK bound to the page as shown,
  `tico task ask <id> --to <owner> --kind ok "Send October unit economics to <who>?" --show page:<id> --version N`.
- **A holdout on a channel another teammate owns:** file it as a task for that owner. A bot owner decides itself.
- **Changing a definition** (customer, churn, cost of revenue, the attribution rule) or a KPI's target:
  `tico proposal create`, confirmed by its owner. Tico refuses the direct change.
- **Loading customer facts or connecting a synced source** is the owner's or an admin's: propose it and carry on with
  what is readable.

Never wait on a person to publish the monthly page. A gap is written on the page, not held back.

## Output

1. **The page**: `tico page publish reports/unit-economics-<YYYY-MM>.html --title "Unit economics" --cadence monthly
   --as-of <last day of month> --task <id>`. A page runs no script: charts are inline SVG and tables. Top to bottom:
   - One sentence with the decision, then tiles: blended CAC, paid CAC, gross margin, payback (cohort method),
     observed LTV at 12 months, LTV to CAC, NRR, GRR. Each tile carries its counts, its cohort, its verdict against
     own history, and "estimate" or "pre-close" where that applies.
   - Payback curve: cumulative gross margin per customer by age, one line per recent cohort, with horizontal lines at
     blended and paid CAC.
   - The two cohort matrices as shaded tables.
   - Twelve months of blended and paid CAC on one chart.
   - Channel table: new customers by which rule named them, cost, CAC, platform-claimed count, self-reported count,
     and any holdout result with its date.
   - Health: Unknown share, no-segment and mismatch counts, reconciliation, stale sources.
   - Method: each definition in one line, each source with its read date, each benchmark with its year.
2. **Records**: Growth rows and costs, KPI readings with evidence, `knowledge/unit-economics.md` (definitions, the
   attribution rule, the holdout log, decisions, each dated).
3. **Tasks**: one per data gap or holdout, each with an owner.
4. **The closing line** on the task: the decision, the two CACs, payback, and what could not be read.

## Checks before you finish

- Every figure on the page names its source, read date, cohort and counts.
- Blended and paid CAC are both there, and each ratio's halves share one scope.
- LTV and payback are on gross margin; the margin's source and month are named.
- No immature or undersized cohort shows a rate. No blank reads as zero.
- Payback shows the share of the cohort still paying at that month.
- The attribution rule is the one in `knowledge/unit-economics.md`, and Unknown's share is on the page.
- Each verdict comes from own history or a target. Each benchmark has a year and a definition.
- Revenue ties to Finance, or the gap is named.
- Nothing was typed in; every estimate is labelled and has a task.
- No customer name, email or phone number appears anywhere.

## Example

Invented, for illustration only. Acme sells home-cleaning subscriptions; segment Consumer; October 2026, closed.

"October: hold paid spend. Paid CAC is up 12% on its trailing three months; no floor fails, so it is not a cut.
New customers 240 (billing, read 2026-11-03): 120 named by a tagged paid touch, 70 by referral code or their own
answer, 50 Unknown (21%). Acquisition cost $18,000: $14,000 paid, $1,000 other money, $3,000 people and bots.
Blended CAC $75.00 (18,000 / 240); trailing three months $70.67; flat. Paid CAC $116.67 (14,000 / 120); trailing
$104.00; up 12%. Gross margin 35% (P&L, October, closed).
April cohort, 200 customers, $120 a month while active, kept 100%, 70%, 60%, 55%, 52%, 50% in months 1 to 6.
Cumulative gross margin per starting customer: $42.00, $71.40, $96.60, $119.70, $141.54, $162.54.
April blended CAC $80: paid back in month 3, with 60% of the cohort still paying. April paid CAC $125: paid back in
month 5, with 52% still paying. Simple payback reads 1.9 months (80 / 42) and is shown as simple.
Observed LTV at 6 months $162.54 on gross margin; LTV to CAC 2.0 blended, 1.3 paid, at 6 months observed.
NRR 74% and GRR 68% over twelve months (October 2025 customers, 310 accounts).
Benchmarks, context only: none uses this business model; see the method note.
Not read: social ads holdout, planned for January (task 5120)."
