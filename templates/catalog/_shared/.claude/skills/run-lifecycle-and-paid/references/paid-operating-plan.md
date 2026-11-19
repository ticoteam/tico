# Paid search and social: the operating plan

Platform rules and thresholds change often. Figures here were checked or taken from the named source on 2026-11-18.
Read the platform's own help page before relying on any of them.

## 1. The numbers you must have first

| Number | How to get it | Used for |
|---|---|---|
| Gross margin per customer, first 12 months | Billing records: revenue per customer over their first 12 months, times gross margin, averaged over customers who started 12 to 24 months ago | Break-even |
| Break-even cost per customer | Equals the margin above. Paying more loses money on the customer inside a year. | The line never to stay above |
| Target cost per customer | Default: one third of break-even, until the owner sets another | What to scale toward |
| Conversion rate from lead to customer | Records, last 90 days, by channel if the counts allow | Turning a cost per lead into a cost per customer |
| The tracking gap | Platform-reported conversions divided by customers in the records tagged to that platform, last full month | Reading platform numbers honestly |

A young company without 12 months of records uses what it has, says so, and re-computes each month. If the owner's
payback rule is different (some want their money back in 6 months, some accept 18), use the owner's. If the team has
`measure-unit-economics`, take these numbers from its latest run instead of computing them again.

## 2. Structure

- **A brand campaign is a standing line, by default.** Whenever paid search has a budget, the plan carries a
  campaign on the company's own name, to protect it: it keeps the company's ad above any competitor bidding on the
  name. This is the owner's standing decision (2026-11-18). It runs inside the paid search budget, without a pause
  test and without waiting for a competitor to appear.
- **Brand and non-brand are separate campaigns, and reported apart**, always. Brand terms convert cheaply because
  those people were already looking for the company. Every report shows brand on its own line and non-brand on
  its own line; any total for paid acquisition, cost per customer or return is given for non-brand, with brand
  beside it, never blended. The brand campaign's cost per customer is never used to justify spend elsewhere.
- **Existing customers are excluded** from acquisition campaigns (customer list upload, where the platform and the
  consent on record allow it).
- **Few campaigns.** Automated bidding learns from conversions. A campaign split ten ways gives each part too little
  to learn from. Split only when the budget, the promise or the landing page really differs.
- **One promise per ad group and page.** The ad's words appear on the page it lands on.
- **Bidding by volume.** Use `choose-a-bidding-strategy-from-volume`. Value-based and target-cost bidding need a
  steady flow of conversions (that skill uses about 15 conversions in 30 days for target return and about 30 a month
  for target cost); below that, bid for clicks or conversions without a target and say the campaign is still
  learning.
- **Count the right thing.** The conversion the platform optimises for is the closest event to a paying customer that
  happens often enough. Import real customers back from the records where the platform allows it.

## 3. The monthly plan (a page)

One table: campaign, its job (capture demand, create demand, win back), monthly budget, expected customers, expected
cost per customer, and the rule that would move money.

- **About 70% on proven campaigns, about 30% on tests.** A test is a new audience, promise, format or channel with a
  prediction written down.
- **A test budget is sized to reach a verdict**: at least three times the target cost per customer per test. If the
  test share cannot cover that, run fewer tests.
- The plan never exceeds the monthly budget. Unspent money is not carried into a spending rush at month end.

## 4. The weekly check

Same day each week, same order, for each campaign. Stop at the first step that fails and give the verdict.

| Step | Look at | Fails when |
|---|---|---|
| 1. Spend | Spend against plan for the period | More than 15% over or under, or the daily budget caps before noon |
| 2. Traffic | Impressions, clicks, cost per click, share of impressions won | A sudden change of more than a third week on week with no change of yours behind it |
| 3. Conversions | Platform conversions, and customers in the records tagged to the campaign | Cost per customer above break-even after a fair run; or the two counts disagree by more than the usual gap |
| 4. Creative | Each ad's click rate and conversion rate over time; how often the same person sees it | The best ad's click rate has fallen for three weeks while frequency rose (`spot-creative-fatigue`) |
| 5. Landing page | Loads, matches the ad, form works on a phone, conversion rate against its own history | Any break; or a drop with no traffic change |
| 6. Tracking | Tags fire, links carry campaign tags, conversions reach the records | Anything missing (`check-campaign-tagging`) |

The percentages in this table are starting defaults. Tune them to how noisy the account is and record the change.

**Verdicts.**

| Verdict | Means | Do |
|---|---|---|
| HOLD | Inside target, enough data | Nothing. Edits restart learning. |
| FIX | A known fault, a known remedy | Fix it today and log it |
| TEST | Working, with a specific idea to beat it | File it in Growth Experiments with a prediction |
| INVESTIGATE | The numbers cannot be trusted or explained | Freeze budget changes for that campaign. Find the cause first. Overrides the others. |
| Too early | Below the fair-run threshold | Wait. Note the spend at which it will be judged. |
| MISSING | An input could not be read | Say which, and file the access task |

**A fair run** (default): the campaign has spent three times the target cost per customer, or has 30 conversions.
Before that, only clear faults change the verdict.

## 5. Scaling and cutting

- **Raise** a campaign that pays back under target by about 20% at a time, three to five days apart. Bigger jumps
  restart the platform's learning and the cost often rises. Watch the cost of the added customers, not the average.
- **Cut** what stays above break-even after a fair run. Before cutting, check steps 5 and 6: a broken page or tag
  makes a good campaign look bad.
- **Between target and break-even**: HOLD the budget and work the page and the creative.
- **Search terms**: weekly, use `read-search-terms-for-waste`. Add negatives for terms that spend with no intent.
- **Creative**: keep two to four live ads per ad set, built from different angles of the spine in
  `knowledge/messaging.md`. Replace the weakest when a new one is ready, not all at once.
- **Social learning phase.** Meta's delivery settles after roughly 50 optimisation events per ad set in 7 days
  (third-party summary of Meta's guidance: check Meta Business Help). An ad set that cannot reach that on its budget
  should optimise for an earlier event or be merged with another.

## 6. Reading the result honestly

- **Platforms overcount.** Each claims the customers it touched, and several can claim the same one. The report uses
  the records. Use `check-attribution-before-a-claim` before saying a channel "drove" anything.
- **Would they have come anyway?** Retargeting mostly reaches people already on their way. When the spend is large
  enough to matter, test it: pause the campaign in some regions or weeks and compare customers from the records.
  Use the test rules in `testing.md`. The brand campaign is not paused to test it; the same question is answered by
  reporting it apart, so its cheap customers never flatter the paid total.
- **Cost per customer by segment**, when counts allow: `read-cost-per-customer-for-a-segment`.
- **Benchmarks from blog posts** (average click cost, average conversion rate) are not targets. The company's own
  break-even is the target.

## 7. Hygiene

- The payment method, account access and billing threshold are the owner's. This skill never adds or changes them.
- Ads in restricted categories (housing, jobs, credit, health, alcohol, gambling, politics) follow the platform's
  special rules. Read the current policy before targeting.
- Customer lists uploaded for matching or exclusion need a lawful basis for that use. Check `knowledge/` for the
  consent on record, and ask the legal owner if it is not there.
- Every change (budget, bid strategy, audience, creative) is logged with its date in
  `knowledge/lifecycle-and-paid.md`, so next week's read can line results up with causes.
