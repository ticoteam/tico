# Price tests

A price test puts a real price in front of real new customers and counts money. The bot designs it and the owner
decides whether it runs: no live price test starts without the owner's OK on its Go-live page. The numbers in the
illustrations are invented.

## Before any test

- **New customers only.** Existing customers keep their price. They are handled in
  `raise-prices-on-existing-customers.md`.
- **One change.** The price, or the packaging, or the metric. Not two at once.
- **Break-even written first.**

  ```
  Break-even conversion = current conversion rate x current price / test price
  ```

  Illustration: 30.0% of trials pay at $59. At $69, break-even is 30.0% x 59 / 69 = 25.7%. Above that, the higher
  price earns more per trial.
- **The Go-live page** on the test's task in the Growth Experiments pipeline states, before the start: what changes
  and for whom; the prediction as a number; the main measure; the guardrails and their limits; the start and stop
  dates; the size needed; what happens on win, loss and "not settled"; and how a customer who saw the lower price is
  treated.
- **The owner's OK before the start.** The Go-live page is the ask. The OK covers the arms, the customers, the dates,
  the stop rule and how customers who saw each price are treated. Anything different needs a new OK. Flag any arm
  below the margin floor on the page.

## What to measure

**Main measure: revenue per visitor** (or per trial, per lead, per quote: whatever enters the test), net of refunds.

```
Revenue per visitor = paying customers x price paid / visitors who entered the arm
```

Use gross margin per visitor where the arms differ in cost to serve. For subscriptions, count first-period revenue at
the first read and cumulative revenue at the 90-day read.

**Guardrails**, each with a limit written on the Go-live page:

- Refund and chargeback rate.
- Retention at 30 days, and again at 90.
- Support contacts and reviews that mention price.
- Plan mix: a higher price on one plan can push buyers down to another.
- New customers per week, against the limit in the pricing goal.

Conversion rate is reported and is never the main measure. A lower rate at a higher price can be a win.

## Which design

Size the test with `skills/size-a-test-before-it-starts`, using the current rate and the break-even rate as the two
rates to tell apart. That gives the visitors needed in each arm. Divide by the weekly traffic.

- **Eight weeks or less: a split test.**
- **More than eight weeks: a staged rollout.** Most small companies are here. A split test that takes a year answers a
  question nobody is still asking.

Illustration: telling 30.0% from 25.7% needs about 1,700 trials in each arm. At 300 trials a month that is eleven
months for both arms, so a staged rollout.

## Split test

1. **Assign at random** when the visitor first sees a price. A visitor keeps that price on every later visit and at
   checkout. Assign by account or household where people share.
2. **Never assign by who the person is.** No arm by device, location within a market, traffic source, browsing
   history or any personal trait.
3. **Show the test price everywhere that visitor looks**: price page, checkout, emails, invoices.
4. **Run whole weeks**, at least two, to the planned size. Do not stop on a good day
   (`skills/read-a-test-without-peeking`).
5. **Read revenue per visitor with its range.** For a single price per arm:

   ```
   Standard error of revenue per visitor = price x square root of ( rate x (1 - rate) / visitors )
   Standard error of the difference      = square root of ( error A squared + error B squared )
   Range of the difference               = difference +/- 1.96 x its standard error
   ```

   Illustration: 240 of 4,000 pay at $59, revenue per visitor $3.54. 216 of 4,000 pay at $69, $3.73. The difference
   is $0.19 and its range is -$0.46 to +$0.84. The range crosses zero: not settled. Report it that way and
   recommend either running longer or deciding on the guardrails and the strategy.
6. **Where customers pay different amounts** (several plans, usage), use the revenue per visitor of each arm with its
   measured spread, or read the arms' totals over whole weeks and report the week-by-week differences. Do not apply
   the single-price formula.

## Staged rollout

Used when traffic is too low to split.

1. **Fix the comparison period**: the same number of whole weeks just before the change, with no promotion or launch
   inside either period. If the business is seasonal, also read the same weeks a year ago.
2. **Write the stop rule first**: revert if the conversion rate at the read is under break-even, or if a guardrail
   passes its limit. Name the date of the read.
3. **Switch every new customer** to the new price on the start date.
4. **Read against break-even, with the range** of the new rate:

   ```
   Standard error of a rate = square root of ( rate x (1 - rate) / visitors )
   Range = rate +/- 1.96 x standard error
   ```

   Illustration: 104 of 396 trials pay at $69: 26.3%, range 21.9% to 30.6%. Break-even is 25.7%. The rate is above
   break-even and the range includes it.
5. **Say what it can and cannot show.** A before-and-after read cannot separate the price from the season, a campaign
   or a product change. It can show a clear loss, a clear win, or "no sign of harm". Report which.
6. **Act on the stop rule, then recommend.** If it fired, revert the same day: the stop rule is part of what the
   owner OK'd. If it did not fire, the test price stays as the OK'd plan says, the 90-day read is scheduled, and the
   result goes to the owner as a recommendation to keep, revert or extend.

Other low-traffic options to put in the recommendation: test in one region or one channel first where each has its
own published price; or test packaging (which features are in which plan) at an unchanged price, which carries less
risk. Each still needs the owner's OK before it runs.

## Treating customers fairly during a test

- Anyone who saw a lower price and asks for it gets it, without argument.
- When a test ends, customers who bought at a higher price in an arm that lost are moved to the winning price from
  their next bill, and told.
- Do not test prices on customers in the middle of a sales conversation with a written quote. The quote stands.
- Keep a list of who saw which price for the length of the test, by customer key.

## Legal notes (US default; read November 2026, not legal advice)

- Showing different prices to new customers at random is generally lawful in the US. Problems come from how the
  price is assigned and what is said about it.
- New York requires a clear disclosure next to any price set by an algorithm using the buyer's personal data (in
  effect November 2025). Random assignment and published regional prices do not use personal data; keep it that way.
- A price difference that tracks a protected trait, even by accident through a stand-in such as postcode, can be
  unlawful discrimination. Random assignment avoids it.
- For physical goods sold to businesses that compete with each other, US price-discrimination law can apply. Have a
  lawyer check before testing prices between such buyers.
- In the EU, a price personalised by automated decision must be disclosed to consumers, and the rules on reference
  prices are stricter than in the US. This was not re-read for this skill: check locally before testing in the EU or
  the UK.

## The Results page

1. The recommendation in one sentence (keep, revert or extend), and whether the stop rule fired.
2. The prediction and what happened, side by side.
3. Main measure per arm or period, with counts and range.
4. Break-even and where the result sits against it, as one bar or line.
5. Each guardrail against its limit.
6. What the test cannot show.
7. The date of the 90-day read.

Label the task `result:worked-once`, `result:failed` or `result:watching`, as other growth experiments are.
