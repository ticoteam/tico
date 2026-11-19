---
name: set-and-test-prices
description: "Work out what the company should charge and bring the owner one recommendation to decide. Covers pricing strategy, the value metric (what a customer pays per), packaging and tiers, the price point from willingness-to-pay evidence, the break-even, the design of a live price test and its read-out, the rollout plan, raising prices on existing customers, and the discount policy. Use when a task says pricing, price, price increase, raise prices, price test, packaging, plans, tiers, good-better-best, add-on, value metric, per seat, usage-based, credits, willingness to pay, discount, coupon, promo, annual plan, grandfather, price page, or asks what to charge, whether a price is too low or too high, or why discounts keep growing. Prices, plan mix, discounts and margins are read through Tools in the same run. The bot never changes a price, for new or existing customers, and never starts a live price test on its own: the owner decides, then the bot carries out exactly what was OK'd."
metadata:
  function: growth
  roles: [pricing-analyst, growth-manager, cro-owner, segment-marketing-owner, product-lead, sales-operations-manager]
  actions: [read, draft, send, change-records]
  review: professional
---
# Set and test prices

When to use: a new product or plan needs a price; revenue per customer is flat while usage grows; discounts are
creeping; a competitor moved; costs to serve changed; or nobody has looked at pricing in six months.

Words used here: **the company** is the business whose prices these are. **The team** is everyone in Tico, people and
bots. **List price** is the published price. **Realised price** is what customers actually paid after discounts.
**Value metric** is the unit a customer pays per: a seat, a booking, a location, a credit. **A fence** is a rule that
keeps a customer who would pay more from buying the cheaper plan. **The recommendation** is the page the owner decides
from: what to change, for whom, from when, on what evidence, and what it costs if it goes wrong.

## What great looks like

An expert treats price as a product decision made on evidence, revisited on a schedule, and never as a number picked
once and defended.

- **Value sets the price. Cost sets the floor. Competitors set the context.** The price sits between what the
  customer's next best choice costs them and what the product is worth to them. Cost-plus leaves money behind;
  matching a competitor copies their mistakes.
- **How you charge matters more than how much.** The value metric and the packaging decide who pays what. Getting
  those right moves more revenue than any change to the number.
- **What people pay beats what people say.** Real transactions first, then conversations, then surveys, then a live
  test. Stated willingness to pay runs high.
- **Small, frequent, reversible moves on new customers. Careful, announced, segmented moves on existing ones.** The
  first can be undone tomorrow. The second cannot.
- **Discounts are a policy, not a habit.** Written, capped, each one traded for something, measured every month.
- **Every change has a break-even written before it ships**: how many customers the company can afford to lose and
  still come out ahead.
- **The owner decides every price. The bot makes the decision easy.** The bot does the research, the value metric,
  the packaging, the break-even, the test design and the rollout plan, and brings one clear recommendation. It never
  changes a price, for new or existing customers, and never starts a live price test on its own. After the OK it
  carries out exactly what was OK'd, no more and no less.

## First time at a company

Read `knowledge/pricing.md`. If it holds dated answers, skip this. Otherwise read what is already written
(`knowledge/our-prices.md`, `knowledge/competitor-prices.md`, `knowledge/discounts.md`, the public price page) and do
not ask what they answer. Ask the rest in one numbered message (`tico task ask <id> --to <owner> "<questions>" --version N`),
save each answer with its date, and work on the defaults until a reply comes. There is no question about how far
prices may move without asking: nothing about a price moves without the owner's OK.

1. **What should pricing do for the company in the next two quarters?** Default: raise revenue per customer, with new
   customers per month not falling more than 10% below the trailing three-month average.
2. **What is the lowest acceptable margin on a sale?** Default: no plan or deal below its cost to serve. Any plan
   under the company's gross margin gets flagged.
3. **What are the discount rules?** Default: cap of 20% off list; annual prepay earns two months free and no other
   discount; one discount per customer, no stacking; every discount has an end date and something given in return.
   These rules take effect when you OK them. Until then I give only offers already published.
4. **Where are prices, customers and signed contracts kept, and which places do you sell in?** Default: the billing
   Tool is the truth for prices and customers; contracts are in the CRM or contracts folder; US law applies; one
   currency.
5. **Is anything off limits?** Default: nothing, except promises already made to customers in writing, which are kept.

## Steps

Work through all of them for a full review. For a single question, do step 1 and then the steps that apply. A name
written `skills/<name>` is a narrower Tico catalog skill: use it if you have it; if you do not, the step here and
its reference say enough to do the work.

1. **Know where you are.** Read, in this run, with dates:
   - The price book from the billing Tool (`tico tool index`, then `tico tool show <service>`): every plan, price,
     billing period and add-on, including plans no longer sold that still have customers.
   - The price page as a buyer sees it. Note any gap between it and the billing Tool.
   - Plan mix and realised price: customers and revenue per plan, and the discount on each, from billing or a saved
     query (`tico db <database> --query <name>`). Read discounts with `skills/read-discounts-as-a-distribution`.
   - Who is at a limit: customers at or over a plan's cap, and customers using a small part of what they pay for.
   - Gross margin per plan from the P&L (`tico finance report pnl`, `pnl_by`). Name any plan below the floor.
   - Payback and retention per plan from the latest unit-economics page, if `measure-unit-economics` has run.
   - Competitors' public prices on the same basis, with `skills/compare-prices-like-for-like`. Public pages only.
2. **Write the strategy in one paragraph.** Who the product is for; their next best choice and what it costs them;
   what the product is worth to them in their terms (money made, hours saved, risk avoided); and the aim: grow revenue
   per customer, win share with a low entry price, or serve the top of the market at a premium. One aim. Save it in
   `knowledge/pricing.md`. Every later step is checked against it.
3. **Choose the value metric.** List 3 to 6 candidates. Score each on the six tests in
   `references/value-metric-and-packaging.md` using billing and usage data: it rises as the customer gets more value,
   bigger customers have more of it, the customer can predict the bill, billing can count it, it is hard to game, and
   cost to serve rises with it. Keep one metric, or a platform fee plus one metric. Changing the metric is the largest
   pricing change there is: it needs the owner's OK and a migration plan.
4. **Package it.** Group customers by what they need and what they would pay, from data and conversations, before
   drawing plans. Sort each feature per group into must-have, nice-to-have, and unwanted. Default to three plans, each
   aimed at one group, with one or two fences between them. A feature that a minority values highly becomes an add-on.
   Check the result against plan mix: the method is in the reference.
5. **Find the price point.** Climb the evidence ladder in `references/willingness-to-pay.md` and stop at the first
   rung that settles the question: what customers already paid (realised prices, upgrades, where deals were won and
   lost); willingness-to-pay conversations; a survey for a range; a live test. Where a teammate owns customer
   interviews, give them the questions as a task and read their notes. Write the candidate price with the
   evidence under it and what is still unknown. Asking customers about price changes no price: do it on your own.
6. **Size the change before it ships.** Use `skills/size-a-price-change`: revenue gained, the break-even loss of
   customers, and the range between best and worst case. For a rise of p, break-even revenue churn is p / (1 + p). For
   a new-customer price, break-even conversion is the current rate x the current price / the new price.
7. **Design the test and the rollout plan.** File the test as a task in the Growth Experiments pipeline (or the
   team's own pipeline for tests; failing that, a plain task) with a Go-live page that states the prediction as a
   number, the main measure, the guardrails, the stop rule and stop date, how customers who saw each price are
   treated, and what each outcome leads to, including "not settled". New customers only. One change at a time. The
   test does not start until the owner OKs that page (step 8). Use `references/price-tests.md`:
   - **Split test** when `skills/size-a-test-before-it-starts` says the traffic can settle it within eight weeks.
     Assign at random; a visitor keeps the price they first saw.
   - **Staged rollout** when it cannot: every new customer sees the new price from a set date, read against the
     weeks before and against the break-even, with a stop rule written first.
   The main measure is revenue per visitor (or per trial), net of refunds; on gross margin where plans differ in cost.
   Conversion alone is never the measure: a higher price that converts a little worse can still win.
   Where the change should reach existing customers too, write their plan as well, from
   `references/raise-prices-on-existing-customers.md`: segments, the contract read, waves, notice dates, the step cap
   and the stop rule. A new value metric or plan structure also needs a migration plan for every existing customer.
8. **Bring the owner one recommendation.** Publish the pricing page (Output) and ask once, bound to it:
   `tico task ask <id> --to <owner> --kind ok "<the change, for whom, from when>" --show page:<id> --version N`.
   The page gives the recommendation, one alternative, and "do nothing" with its cost; the evidence rung for each;
   the break-even; the test or rollout plan and its stop rule; and what result would change your mind. One
   recommendation per ask: a test, a new-customer price, an existing-customer rise and a discount policy are separate
   decisions, even when they arrive on one page. While the ask is open, no price moves and no test starts; research
   and the monthly read carry on. A no, or an OK with changes, is saved in `knowledge/pricing.md` with its date.
9. **Run exactly the test the owner OK'd, and read it.** The same arms, dates, customers and stop rule as the page.
   Anything different is a new ask. Stop on the date written at the start (`skills/read-a-test-without-peeking`).
   If the stop rule fires, revert the same day: the stop rule is part of what was OK'd. Read the main measure with its
   range, then the guardrails: refunds, 30-day retention, support contacts about price, plan mix. Publish the Results
   page. Anyone who saw a lower price and asks for it gets it, and when the test ends customers who paid the higher
   price in a losing arm are moved down; both are written into the Go-live page, so they are part of the OK. Read
   retention again at 90 days, because a price that wins on day 1 can lose on churn. The result is a new
   recommendation (keep, revert, or extend), back to step 8.
10. **Carry out an OK'd new-customer price.** Change the price in the billing Tool and on the price page exactly as
    OK'd (a task for the teammate who owns the site, if that is not you), then open the page as a buyer and confirm
    the price, the currency and the checkout all match. Update `knowledge/our-prices.md` with the date, the evidence
    and a link to the OK. Existing customers keep their price unless the owner OK'd step 11.
11. **Carry out an OK'd rise for existing customers,** following
    `references/raise-prices-on-existing-customers.md`: read every signed contract's price terms first; send to a
    first wave of about a tenth; give notice of at least 30 days on monthly plans and 60 days before an annual
    renewal, or longer where a contract or local law says; say what changes, when, why, and how to leave; watch daily
    for two billing cycles; pause the next wave if revenue lost in the wave passes half of break-even. The customer
    list, prices and wave dates are the ones on the OK'd page.
12. **Propose and enforce the discount policy.** If none is written, draft it from `references/discount-policy.md`
    and the saved answers, and bring it to the owner for an OK; then write it into `knowledge/our-prices.md`. Measure
    it monthly: the spread of discounts given, who gave them, what was traded for each, and whether discounted
    customers stay as long as full-price ones. A discount outside the policy is refused or sent for an OK. A change
    to the cap or the rules is a recommendation, not an edit. Save offers for customers who are leaving follow
    `skills/match-one-save-offer`.
13. **Record and schedule.** Log what changed, when, on what evidence, and the result in `knowledge/pricing.md`. Set a
    routine: discounts and plan mix monthly; a full review every six months, or sooner when cost to serve moves, a
    main competitor changes price, nearly all new customers choose one plan, or deals lost on price jump.

## Gates

- **No price change and no live price test without the owner's OK.** This covers list prices, new-customer prices,
  existing customers' bills, test arms, value metrics, plans, public promotions, the discount rules, and how prices
  or plans are shown on the price page. There is no band in which the bot acts alone. The one exception is fixing a
  price page or checkout that disagrees with the OK'd price book, back to what was OK'd; report it.
- **Do exactly what was OK'd.** The OK covers what the page showed. A different price, date, customer list, arm or
  wave is a new ask.
- **No price from a guess.** Every recommended price names its evidence rung and its date. Where there is no
  evidence, the output is a test, not a price.
- **Break-even first.** No change ships and no test starts without its break-even written down.
- **New customers before existing ones.** A price is proven on new customers before any existing customer's bill
  changes, unless the owner says otherwise in the OK.
- **A signed contract wins.** If a customer's contract fixes the price or caps increases, that customer is out of the
  change until renewal. The teammate who keeps contracts reads the terms; do not infer them.
- **Notice before charge.** No existing customer is charged a higher price without written notice, the notice period
  in step 11 or longer, and a clear way to cancel. Check the rules for where each customer is: they differ and change
  (`references/raise-prices-on-existing-customers.md`, read November 2026).
- **Lawyer check, once.** Before the first notice of a price rise goes out, and whenever the terms or the places sold
  in change, a lawyer checks the notice template and the price-change clause in the terms. It is not needed per send.
- **Never from personal data.** A price or a test arm is never assigned by who the person is: not by protected traits,
  not by device, location inside a market, or browsing history. Segments are by plan, size, region with its own
  published price, and time.
- **Never with a competitor.** No message to, from or through a competitor about prices, and no shared pricing tool
  that pools competitors' private data. Competitor prices come from public pages.
- **True reference prices.** A "was" price was really charged, recently, for a real period. A sale has an end date
  and ends on it. No fake countdowns.
- **The buyer's page matches billing.** After any change, the price page, the checkout and the invoice agree.
- **Below the floor is flagged on the page,** whether it is a plan, a test arm or a single deal, and never given
  without an OK.
- **Test honesty.** One change per test; no stopping early on a good day; "not settled" is reported as not settled.

## Autonomy

Do on your own: all research and reads; competitor price records; the value metric and packaging work; sizing and
break-evens; willingness-to-pay conversations and surveys with customers through the team's usual channels; designing
tests and rollout plans and filing them as ideas; the pricing page and the recommendation; the monthly discount read;
giving a discount the OK'd policy allows and refusing one it does not; fixing a price page that disagrees with the
OK'd price book; filing tasks for other teammates (the contracts read, the site change). After an OK, carry out
exactly what was OK'd: the test and its stop rule, the billing and price page change, the notices in waves.

Needs the owner's OK, bound to the page that shows the change
(`tico task ask <id> --to <owner> --kind ok "<one line>" --show page:<id> --version N`):

- **Any change to a price,** for new or existing customers: list price, a plan's price, a new plan structure, a new
  value metric, how prices or plans are shown, a public promotion.
- **Starting any live price test,** including a staged rollout.
- **Any change to what an existing customer pays,** and the notice that announces it. One OK covers the notice
  template, the customer list and the wave plan; each later wave does not need another unless the plan changes.
- **The discount policy** and any change to it, a discount beyond the cap, and anything below the floor.
- **Anything that conflicts with a signed contract** or a written promise to customers.

A slow reply never stops research, the recommendation or the monthly read. It does stop the change: while an ask is
open, no price moves.

## Output

1. **The pricing page**: `tico page publish reports/pricing-<YYYY-MM>.html --title "Pricing review" --as-of <date>
   --task <id>`. A page runs no script: charts are inline SVG and tables. Top to bottom:
   - One sentence with the recommendation, its break-even, and the decision asked for.
   - The options: the recommendation, one alternative, and "do nothing", each with its expected result and cost.
   - The plan table as a buyer would see it: now and proposed, side by side.
   - Plan mix as bars (customers and revenue per plan), with gross margin per plan.
   - Realised price as a distribution: how many customers pay what, against list.
   - The evidence: which rung, the numbers, the dates, what is unknown.
   - The size of the change: best, expected and worst case, and the break-even line.
   - The test or rollout plan: design, dates, stop rule, how customers who saw each price are treated.
   - For existing customers: the segments, the waves, the notice dates, the stop rule.
2. **Test pages**: a Go-live page before each test, OK'd before it starts, and a Results page after, on the test's
   task.
3. **Records**: `knowledge/pricing.md` (strategy, metric, recommendations, the owner's decisions and OKs, each dated), `knowledge/our-prices.md`
   (the price book, its history, and the discount rules as written), `knowledge/discounts.md` (each month's
   spread of discounts given).
4. **The notice** to existing customers, as a draft on the OK, then sent in waves only after it.
5. **Tasks** for other teammates, each with an owner and a date.

## Checks before you finish

- Every price, mix, discount and margin figure was read through a Tool in this run and carries its date.
- The strategy paragraph exists and the recommendation fits its one aim.
- Each recommended price names its evidence rung. Survey answers are not presented as what customers will pay.
- The break-even is written, and the test or change was read against it.
- No price changed and no live test started without an OK, and what was carried out matches the OK'd page exactly.
- The recommendation shows an alternative and "do nothing", and the decision asked for is one line.
- Tests touched new customers only, one change at a time, and ended on the planned date.
- No existing customer's bill changed without an OK, a contract read, notice and a way to cancel.
- The price page, checkout and billing Tool agree after the change.
- No price or test arm was assigned from personal data. No competitor was contacted.
- Discounts given are inside the policy, or have an OK.
- The next review date is set as a routine.

## Example

Invented, for illustration only. Acme sells booking software to fitness studios. Pro is $59 per location per month.

"Decision asked for: keep Pro at $69 for new customers. Break-even trial-to-paid rate was 25.7%; we read 26.3%.
Why the test was proposed: 41% of Pro customers were at the staff limit, realised price was 97% of list (few
discounts), and the three nearest competitors' like-for-like plans were $65 to $89 (public pages, read 2026-08-30).
Design, as you OK'd it on the Go-live page: a split test needed about 1,700 trials per arm to tell 30% from
break-even, and Acme gets about 300 trials a month, so eleven months. Staged rollout instead: all new trials saw $69
from September 7 for eight weeks.
Stop rule, written first and part of the OK: revert if the trial-to-paid rate is under 25.7% at week 8, or refunds
double.
Before (eight weeks to September 6): 120 of 400 trials paid, 30.0%; revenue per trial $17.70.
After: 104 of 396 paid, 26.3% (range 21.9% to 30.6%); revenue per trial $18.12, up 2%.
Refunds 3 of 104 against 4 of 120. Support contacts about price: 6.
Reading: not proven. The rate is above break-even but the range includes it, and a before-and-after read cannot rule
out the season. The stop rule did not fire, so $69 is still live, as the OK'd plan said.
Options: (1) recommended: keep $69 and read retention again on January 31; (2) go back to $59 now, giving up about
$0.42 per trial; (3) extend the read eight weeks before deciding. Nothing changes until you choose.
Existing customers: 1,150 on Pro at $59, untouched. A rise for them is a separate recommendation: break-even revenue
churn 14.5%, sized in the last section, with its own OK."
