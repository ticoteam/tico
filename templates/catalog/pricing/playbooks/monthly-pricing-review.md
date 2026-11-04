# Monthly pricing review

Schedule: the 1st of each month at 09:00 team time (routine `monthly-pricing-review`), after setup. Budget 45 minutes. The outcome is one page on prices, discounts and plan
mix for the Head of Product. Nothing is changed and nothing leaves the team.

Inputs: the watched competitors' public pricing pages; last month's closed-won deals (CRM read or an export on the task) with list price, charged price, term, segment and deal size; `knowledge/our-prices.md`, `knowledge/competitor-prices.md`, `knowledge/discounts.md`; last month's review.

Done when: the review is published in the shape of `knowledge/examples/pricing-review.md`; every competitor has a date read, or "not read" with the reason; the discount figures follow `skills/read-discounts-as-a-distribution/SKILL.md`; `knowledge/discounts.md` has this month's row; and the task is closed with the headline.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/our-prices.md`, `knowledge/competitor-prices.md`, `knowledge/discounts.md` and last
month's review.

## 2. Re-read competitors' public pricing

`hub doc fetch <url>` for each watched competitor. Read the page text yourself before you rely on a summary or a parser's label. Record plans, prices, limits and what is included, with the date. Compare with the last entry: a change is logged with both dates. A page you could not read is "not read", and says why. A page that refuses reads three days running goes to the owner as a question: a manual read, or another source. A price the team has changed that is not on the buyer's page after its effective date is a gap: report it once, with the page, the time and what you saw. Report a real change with `hub market report`.

## 3. Discounts actually given

Last month's closed-won deals (CRM read or export): list price, charged price, discount percent, term, segment, deal size. Run `skills/read-discounts-as-a-distribution/SKILL.md`: the share discounted, the median among discounted deals, the spread, and the share over the written cap, by segment and deal size where a group has about ten or more deals. Set these beside the written discount rule and last month's numbers, on the same definition. Use complete months only, and label a partial month as partial. Deals beyond the rule are listed by deal.

## 4. Plan mix and upgrades

New customers by plan, upgrades and downgrades, and customers at or over a plan limit (a sign the value
metric works, or that a tier is mis-sized).

## 5. One question

Pick the one pricing question the numbers raise ("is Starter too generous on locations?") and what would
answer it: a query, an experiment, or a willingness-to-pay study.

## 6. Write and hand over

Write `reports/YYYY-MM-DD-pricing-review.md` in the shape of `knowledge/examples/pricing-review.md`,
`hub file publish` it, update `knowledge/discounts.md`, commit, and `hub task update <id> --status done
--note`: the headline and what could not be read.
